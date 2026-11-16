"""Gera js/snapshots.js a partir do SQLite (etapa 3e).

Uso:
    python -m pipeline.export_snapshots --saida ARQUIVO.js [--db BANCO.db]   # padrão do banco: data/rigora.db

`--saida` é obrigatório (Bloco 3) e não pode ser o js/snapshots.js em uso pela interface. O exportador
só gera arquivos; quem publica é `python -m pipeline.publicar`, com verificação, backup e substituição
protegida. Se a saída já existir, ela é lida como "arquivo anterior" (regras abaixo) e substituída.

Contrato preservado (lido por js/app.js; ver diagnóstico no pipeline/README.md):
    window.RIGORA_SNAPSHOTS = {
      sidra: {source, query, collectedAt, municipiosMG, fields, mg: {ano: [rend, área, prod]},
              municipios: [{c, n, meso, y: {ano: [rend, área, prod]}}]},
      inmet: {collectedAt, data: {"3148103": {"dd/mm/aaaa": registro | {manha, tarde, noite}}}} | null,
      exportacao: {...}   # ACRÉSCIMO: rastreabilidade; lida pela aba Fontes (linha Procedência, B7)
    };

Regras:
- Só entram dados de execuções com status 'sucesso' (garantido pela INT-07/MET-14).
- IBGE sem dados no banco: mantém a seção do arquivo anterior (o dado anual continua válido).
- INMET: só a previsão mais recente, se coletada há até 24 h, e só datas de hoje em diante
  (horário de Brasília). Sem previsão atual -> inmet = null (a interface mostra "indisponível").
  Dia com temperatura ou umidade ausente não é exportado (a interface não aceita nulo nesses
  campos); o motivo vai para exportacao.inmet.datasOmitidas. Nada vira zero.
- Escrita atômica: arquivo temporário -> validação -> substituição. Em erro, o anterior fica intacto.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import config
from .extract_sidra import pam_url
from .transform import mg_totals

log = logging.getLogger(__name__)

SNAPSHOTS_PATH = config.ROOT / "js" / "snapshots.js"
# A média de MG só é recalculada se o banco cobrir ao menos 90% dos municípios com produção que o
# arquivo anterior declarava. Evita trocar a média do estado inteiro pela média de uma coleta parcial
# (ex.: banco montado só com os 60 maiores). A tolerância acomoda revisões normais do IBGE.
MIN_COBERTURA_MG = 0.9
BRASILIA = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019; evita depender do tzdata no Windows
DIAS_SEMANA = ["Segunda-Feira", "Terça-Feira", "Quarta-Feira", "Quinta-Feira", "Sexta-Feira", "Sábado", "Domingo"]
SIDRA_SOURCE = ("IBGE - Produção Agrícola Municipal (PAM), SIDRA tabela 1613, Café (em grão) Arábica (c82/31619); "
                "112 rendimento médio kg/ha; 216 área colhida ha; 214 quantidade produzida t")
SIDRA_FIELDS = ["rendimento_kg_ha", "area_colhida_ha", "producao_t"]
HEADER = """/**
 * Rigora - Cópias locais (snapshots) de dados OFICIAIS, com data de coleta.
 * IBGE/SIDRA (sidra): base oficial exibida na aplicação. Ao abrir a página, a consulta ao vivo ao
 *   SIDRA só confere o rendimento de Patrocínio, sem trocar valores; divergência vira aviso.
 *   O botão "Atualizar do IBGE" traz a série ao vivo só para a tela; este arquivo não muda.
 * INMET (inmet): a previsão vem da consulta ao vivo. Esta cópia é reserva para quando a consulta
 *   falha e só é usada se tiver até 24 h, com os dias de hoje em diante (regras da M1).
 *   null = sem previsão atual no banco: sem consulta ao vivo, a interface mostra "indisponível".
 * Nunca são exibidas como "ao vivo": a interface mostra a data de coleta.
 *
 * GERADO AUTOMATICAMENTE por `python -m pipeline.export_snapshots` a partir do SQLite.
 * Não edite à mão: a origem de cada seção está em `exportacao`.
 */
"""


class ExportError(RuntimeError):
    pass


# =============================================================================
# Leitura do banco
# =============================================================================
def latest_success(conn: sqlite3.Connection, fonte_prefix: str):
    return conn.execute(
        "SELECT * FROM execucao WHERE fonte LIKE ? AND status = 'sucesso' ORDER BY id DESC LIMIT 1",
        (fonte_prefix + "%",),
    ).fetchone()


def _num(v):
    """REAL do SQLite -> número JSON; inteiro quando não há casas decimais (como no arquivo original)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def _raw_meta(arquivo: str, raw_dirs) -> dict:
    """Metadados da coleta (.meta.json ao lado do bruto). Ausentes -> {} (o banco já guarda data e hash)."""
    name = Path(arquivo).with_suffix(".meta.json").name
    for d in raw_dirs:
        try:
            return json.loads((Path(d) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def _validation_result(row) -> dict | None:
    """Resumo da validação gravado na execução (etapa 3c/3d): resultado, erros e avisos."""
    try:
        msg = json.loads(row["mensagem"] or "{}")
    except ValueError:
        return None
    keys = ("resultado", "erros_impeditivos", "avisos", "registros_com_aviso", "registros_analisados")
    return {k: msg[k] for k in keys if k in msg} or None


def build_sidra(conn: sqlite3.Connection, raw_dirs=(config.RAW_DIR,), top_n: int = config.TOP_N_MUNICIPIOS):
    ex = latest_success(conn, "IBGE/SIDRA")
    if ex is None:
        return None, {"modo": "indisponivel", "motivo": "nenhuma execução do IBGE/SIDRA com sucesso no banco"}
    df = pd.read_sql_query(
        "SELECT codigo_ibge AS cod_ibge, municipio, regiao, ano, rendimento_kg_ha, area_colhida_ha, producao_t, execucao_id "
        "FROM pam_municipio_ano", conn)
    if df.empty:
        return None, {"modo": "indisponivel", "motivo": "tabela pam_municipio_ano vazia"}
    years = sorted(int(y) for y in df["ano"].unique())
    ref_year = max(years)
    # Mesmo critério de transform.select_top (maior área colhida no último ano), com desempate pelo
    # código IBGE para a ordem ser sempre a mesma (o arquivo manual de 14/11/2026 segue essa ordem).
    ref = df[(df["ano"] == ref_year) & (df["area_colhida_ha"] > 0)]
    top = ref.sort_values(["area_colhida_ha", "cod_ibge"], ascending=[False, True])["cod_ibge"].head(top_n).tolist()
    tot = mg_totals(df)
    producing = df[(df["area_colhida_ha"] > 0) & (df["producao_t"] > 0)]["cod_ibge"].nunique()

    by_code = {c: g for c, g in df.groupby("cod_ibge")}
    municipios = []
    for code in top:
        g = by_code[code].sort_values("ano")
        first = g.iloc[0]
        municipios.append({
            "c": code,
            "n": first["municipio"],
            "meso": None if pd.isna(first["regiao"]) else first["regiao"],
            "y": {str(int(r.ano)): [_num(r.rendimento_kg_ha), _num(r.area_colhida_ha), _num(r.producao_t)] for r in g.itertuples()},
        })
    mg = {str(int(r.ano)): [_num(r.rendimento_kg_ha), _num(r.area_colhida_ha), _num(r.producao_t)] for r in tot.itertuples()}
    meta = _raw_meta(ex["arquivo_origem"], raw_dirs)
    url = meta.get("url") or ""
    section = {
        "source": SIDRA_SOURCE,
        "query": url if url.startswith(config.SIDRA_BASE) else pam_url(),
        "collectedAt": _iso_z(ex["data_coleta"]),
        "municipiosMG": int(producing),
        "fields": SIDRA_FIELDS,
        "mg": mg,
        "municipios": municipios,
    }
    exported_ids = sorted(int(i) for i in df[df["cod_ibge"].isin(top)]["execucao_id"].unique())
    info = {
        "modo": "sqlite",
        "fonte": ex["fonte"],
        "execucaoId": ex["id"],
        "execucoesNosDadosExportados": exported_ids,
        "dataColeta": ex["data_coleta"],
        "arquivoBruto": ex["arquivo_origem"],
        "hashSha256": ex["hash_sha256"],
        "origemDaColeta": meta.get("url"),
        "statusExecucao": ex["status"],
        "validacao": _validation_result(ex),
        "municipiosExportados": len(municipios),
        "anos": [min(years), max(years)],
        "registrosExportados": sum(len(m["y"]) for m in municipios),
        "municipiosNaMediaMG": int(producing),
        "observacao": ("média de MG calculada sobre os municípios com produção presentes no banco; "
                       "com a coleta completa do IBGE corresponde ao estado inteiro"),
    }
    return section, info


def _iso_z(ts: str) -> str:
    """'2026-11-14T16:13:28.004+00:00' -> '2026-11-14T16:13:28.004Z' (formato do arquivo original)."""
    return ts[:-6] + "Z" if ts.endswith("+00:00") else ts


def _record(r) -> dict:
    data = datetime.strptime(r["data_previsao"], "%Y-%m-%d").date()
    return {
        "resumo": r["resumo"],
        "temp_max": _num(r["temp_max_c"]),
        "temp_min": _num(r["temp_min_c"]),
        "umidade_max": _num(r["umidade_max_pct"]),
        "umidade_min": _num(r["umidade_min_pct"]),
        "int_vento": r["vento_intensidade"],
        "dir_vento": r["vento_direcao"],
        "dia_semana": DIAS_SEMANA[data.weekday()],  # derivado da data (calendário), não vem do banco
    }


REQUIRED_NUMERIC = ("temp_min_c", "temp_max_c", "umidade_min_pct", "umidade_max_pct")


def build_inmet(conn: sqlite3.Connection, now: datetime, codigo: str = config.INMET_MUNICIPIO):
    ex = latest_success(conn, "INMET/")
    if ex is None:
        return None, {"modo": "indisponivel", "motivo": "nenhuma execução do INMET com sucesso no banco"}
    coleta = datetime.fromisoformat(ex["data_coleta"])
    idade_h = (now - coleta).total_seconds() / 3600
    base = {"fonte": ex["fonte"], "execucaoId": ex["id"], "dataColeta": ex["data_coleta"],
            "arquivoBruto": ex["arquivo_origem"], "hashSha256": ex["hash_sha256"],
            "statusExecucao": ex["status"], "validacao": _validation_result(ex),
            "idadeHorasNaGeracao": round(idade_h, 1)}
    if idade_h > config.INMET_MAX_IDADE_HORAS:
        return None, {"modo": "indisponivel", **base,
                      "motivo": f"previsão mais recente coletada há {idade_h:.0f} h (limite {config.INMET_MAX_IDADE_HORAS} h); não é exibida como atual"}
    today = now.astimezone(BRASILIA).date().isoformat()
    rows = conn.execute(
        "SELECT * FROM inmet_previsao WHERE codigo_ibge = ? AND data_coleta = ? ORDER BY data_previsao, "
        "CASE periodo WHEN 'manha' THEN 0 WHEN 'tarde' THEN 1 WHEN 'noite' THEN 2 ELSE 3 END",
        (codigo, ex["data_coleta"])).fetchall()
    data, omitted, exported = {}, [], 0
    by_date: dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r["data_previsao"], []).append(r)
    for iso, recs in by_date.items():
        br = datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        if iso < today:
            omitted.append({"data": br, "motivo": "data anterior à geração do arquivo (já passou)"})
            continue
        missing = sorted({c for r in recs for c in REQUIRED_NUMERIC if r[c] is None})
        if missing:
            omitted.append({"data": br, "motivo": f"valores ausentes ({', '.join(missing)}); a interface exige esses campos"})
            continue
        if all(r["periodo"] == "dia" for r in recs):
            data[br] = _record(recs[0])
        else:
            data[br] = {r["periodo"]: _record(r) for r in recs if r["periodo"] != "dia"}
        exported += len(recs)
    if not data:
        return None, {"modo": "indisponivel", **base, "datasOmitidas": omitted,
                      "motivo": "nenhuma data atual com dados completos na previsão mais recente"}
    section = {"collectedAt": _iso_z(ex["data_coleta"]), "data": {codigo: data}}
    return section, {"modo": "sqlite", **base, "registrosExportados": exported, "datasExportadas": list(data),
                     "datasOmitidas": omitted,
                     "camposNaoFornecidosPelaFonte": ["precipitação (mm e probabilidade)", "velocidade do vento em número"],
                     "camposDerivados": {"dia_semana": "calculado a partir da data"}}


# =============================================================================
# Arquivo anterior, renderização e validação
# =============================================================================
LINE = re.compile(r"^  (sidra|inmet|exportacao): (.*?),?$")


def parse_snapshots_js(text: str) -> dict:
    """Lê um snapshots.js no formato do contrato (uma seção JSON por linha)."""
    if "window.RIGORA_SNAPSHOTS = {" not in text or not text.rstrip().endswith("};"):
        raise ExportError("arquivo não está no formato window.RIGORA_SNAPSHOTS = { ... };")
    out = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            out[m.group(1)] = json.loads(m.group(2))
    if "sidra" not in out or "inmet" not in out:
        raise ExportError("arquivo sem as seções sidra e inmet")
    return out


def read_previous(path: Path) -> dict | None:
    if not path.exists():
        log.info("sem arquivo anterior em %s (primeira geração)", path)
        return None
    try:
        return parse_snapshots_js(path.read_text(encoding="utf-8"))
    except (OSError, ExportError, ValueError) as exc:
        log.warning("arquivo anterior ilegível ou ausente (%s): %s", path, exc)
        return None


def render(snap: dict) -> str:
    j = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    return (HEADER + "window.RIGORA_SNAPSHOTS = {\n"
            f"  sidra: {j(snap['sidra'])},\n"
            f"  inmet: {j(snap['inmet'])},\n"
            f"  exportacao: {j(snap['exportacao'])}\n"
            "};\n")


def _is_num_or_null(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def validate_contract(snap: dict) -> list[str]:
    """Confere o contrato lido por js/app.js. Retorna a lista de problemas (vazia = válido)."""
    p = []
    s = snap.get("sidra")
    if not isinstance(s, dict):
        p.append("sidra ausente")
    else:
        for k, t in (("source", str), ("query", str), ("collectedAt", str), ("municipiosMG", int),
                     ("fields", list), ("mg", dict), ("municipios", list)):
            if not isinstance(s.get(k), t):
                p.append(f"sidra.{k} ausente ou do tipo errado")
        for y, v in (s.get("mg") or {}).items():
            if not (re.fullmatch(r"\d{4}", y) and isinstance(v, list) and len(v) == 3 and all(map(_is_num_or_null, v))):
                p.append(f"sidra.mg[{y}] inválido")
        if not s.get("municipios"):
            p.append("sidra.municipios vazio")
        for m in s.get("municipios") or []:
            if not (isinstance(m.get("c"), str) and re.fullmatch(r"\d{7}", m["c"]) and isinstance(m.get("n"), str)
                    and (m.get("meso") is None or isinstance(m.get("meso"), str)) and isinstance(m.get("y"), dict)):
                p.append(f"município inválido: {m.get('c')}")
                continue
            for y, v in m["y"].items():
                if not (isinstance(v, list) and len(v) == 3 and all(map(_is_num_or_null, v))):
                    p.append(f"município {m['c']} ano {y} inválido")
        try:
            datetime.fromisoformat(str(s.get("collectedAt")).replace("Z", "+00:00"))
        except ValueError:
            p.append("sidra.collectedAt não é data ISO")
    i = snap.get("inmet", "ausente")
    if i == "ausente":
        p.append("chave inmet ausente (use null quando não houver previsão)")
    elif i is not None:
        if not isinstance(i.get("collectedAt"), str) or not isinstance(i.get("data"), dict) or not i["data"]:
            p.append("inmet sem collectedAt ou data")
        for code, days in (i.get("data") or {}).items():
            if not days:
                p.append(f"inmet.data[{code}] vazio")
            for date, entry in days.items():
                if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", date):
                    p.append(f"inmet data fora do formato dd/mm/aaaa: {date}")
                recs = [entry[k] for k in ("manha", "tarde", "noite") if k in entry] or [entry]
                for r in recs:
                    for k in ("temp_min", "temp_max", "umidade_min", "umidade_max"):
                        if not isinstance(r.get(k), (int, float)) or isinstance(r.get(k), bool):
                            p.append(f"inmet {date}: {k} precisa ser número")
                    if not isinstance(r.get("dia_semana"), str):
                        p.append(f"inmet {date}: dia_semana ausente")
    if not isinstance(snap.get("exportacao"), dict) or "geradoEm" not in snap["exportacao"]:
        p.append("exportacao sem geradoEm")
    return p


def build(db_path: Path, previous: dict | None, now: datetime) -> dict:
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sidra, sidra_info = build_sidra(conn, (Path(db_path).parent / "raw", config.RAW_DIR))
        inmet, inmet_info = build_inmet(conn, now)
    finally:
        conn.close()

    prev_n = ((previous or {}).get("sidra") or {}).get("municipiosMG")
    if sidra is not None and isinstance(prev_n, int) and sidra["municipiosMG"] < MIN_COBERTURA_MG * prev_n:
        sidra_info = {"modo": "indisponivel", "candidatoDoBanco": sidra_info,
                      "motivo": (f"cobertura parcial: o banco tem {sidra['municipiosMG']} municípios com produção e o "
                                 f"arquivo anterior tem {prev_n}; a média de MG ficaria errada")}
        sidra = None
    if sidra is None:
        if previous and previous.get("sidra"):
            sidra = previous["sidra"]
            old = (previous.get("exportacao") or {}).get("sidra") or {}
            sidra_info = {"modo": "mantido_do_arquivo_anterior", "motivo": sidra_info["motivo"],
                          **({"candidatoDoBanco": sidra_info["candidatoDoBanco"]} if "candidatoDoBanco" in sidra_info else {}),
                          "dataColeta": sidra.get("collectedAt"), "execucaoId": old.get("execucaoId"),
                          "origemAnterior": old.get("modo", "arquivo sem registro de exportação (gerado antes da etapa 3e)")}
        else:
            raise ExportError(f"IBGE/SIDRA sem dados no banco e sem arquivo anterior válido: {sidra_info['motivo']}")
    if inmet is None and inmet_info.get("motivo", "").startswith("nenhuma execução") and previous and previous.get("inmet"):
        prev = previous["inmet"]
        age = (now - datetime.fromisoformat(prev["collectedAt"].replace("Z", "+00:00"))).total_seconds() / 3600
        if age <= config.INMET_MAX_IDADE_HORAS:
            today = now.astimezone(BRASILIA).date()
            days = {code: {d: v for d, v in ds.items() if datetime.strptime(d, "%d/%m/%Y").date() >= today}
                    for code, ds in prev["data"].items()}
            if all(days.values()):
                inmet = {"collectedAt": prev["collectedAt"], "data": days}
                inmet_info = {"modo": "mantido_do_arquivo_anterior", "motivo": inmet_info["motivo"],
                              "dataColeta": prev["collectedAt"], "idadeHorasNaGeracao": round(age, 1)}
    return {
        "sidra": sidra,
        "inmet": inmet,
        "exportacao": {
            "geradoEm": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "geradoPor": "python -m pipeline.export_snapshots",
            "banco": Path(db_path).name,
            "sidra": sidra_info,
            "inmet": inmet_info,
            "aviso": "dataColeta = quando a fonte respondeu; geradoEm = quando este arquivo foi escrito. "
                     "Este arquivo não indica coleta ao vivo: é sempre uma cópia salva.",
        },
    }


def write_atomic(content: str, dest: Path) -> None:
    """Escreve num temporário ao lado do destino, valida e só então substitui."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8", newline="\n")   # \n também no Windows (o verificador B5 exige "};\n")
        problems = validate_contract(parse_snapshots_js(tmp.read_text(encoding="utf-8")))
        if problems:
            raise ExportError("arquivo gerado não passou na validação do contrato: " + "; ".join(problems[:5]))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def is_published_snapshot(dest: Path) -> bool:
    """O destino é o js/snapshots.js em uso? (caminho resolvido, sem diferença de maiúsculas no Windows)."""
    dest = Path(dest)
    try:
        if dest.exists() and SNAPSHOTS_PATH.exists() and os.path.samefile(dest, SNAPSHOTS_PATH):
            return True
    except OSError:
        pass
    return os.path.normcase(str(dest.resolve())) == os.path.normcase(str(SNAPSHOTS_PATH.resolve()))


def export(db_path: Path, dest: Path, now: datetime | None = None) -> dict:
    """Gera o snapshot em `dest` (obrigatório). Recusa o js/snapshots.js em uso: publicar é com pipeline.publicar."""
    if is_published_snapshot(dest):
        raise ExportError(f"{dest} é o snapshot em uso pela interface; o exportador não grava nele. "
                          "Para publicar, use: python -m pipeline.publicar")
    if not Path(db_path).exists():
        raise ExportError(f"banco não encontrado: {db_path}. Rode antes: python -m pipeline.run_pipeline")
    now = now or datetime.now(timezone.utc)
    snap = build(Path(db_path), read_previous(dest), now)
    write_atomic(render(snap), dest)
    return snap["exportacao"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=config.DB_PATH, help="banco SQLite (padrão: data/rigora.db; aberto só para leitura)")
    p.add_argument("--saida", type=Path, required=True,
                   help="arquivo a gerar (obrigatório; não pode ser o js/snapshots.js em uso: publique com python -m pipeline.publicar)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        info = export(args.db, args.saida)
    except (ExportError, sqlite3.Error) as exc:
        log.error("exportação cancelada; %s mantido sem alteração: %s", args.saida, exc)
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
