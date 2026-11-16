"""Backfill histórico dos grupos da PLA-05 (etapa 4).

Grava em validacao_grupo e validacao_grupo_membro os grupos de uma execução do IBGE/SIDRA gravada
antes da etapa 3b (no banco real: a execução 3), reconstruídos a partir dos brutos já coletados.
Não coleta nada, não acessa a internet, não faz UPSERT em pam_municipio_ano e não chama
record_validation: só insere nas duas tabelas de grupos, por load_sqlite.record_pla05_groups.

Uso:
    # 1) verificar (padrão, somente leitura): reconstrói em memória e roda todas as conferências
    python -m pipeline.backfill_pla05 --db data/rigora.db --execucao 3 --localidades-execucao 2

    # 2) gravar (só se todas as conferências passarem e houver backup com o mesmo SHA-256 do banco)
    python -m pipeline.backfill_pla05 --db data/rigora.db --execucao 3 --localidades-execucao 2 \\
        --gravar --backup data/rigora_antes_etapa4.db

    # 3) conferir gravados (somente leitura): depois da gravação, compara os grupos e membros gravados
    #    com a reconstrução feita a partir dos brutos, campo a campo
    python -m pipeline.backfill_pla05 --db data/rigora.db --execucao 3 --localidades-execucao 2 --conferir-gravados

Garantias da gravação:
- Uma transação só (BEGIN IMMEDIATE ... COMMIT): criação das tabelas novas (se faltarem), inserção dos
  grupos e membros e todas as verificações. Qualquer falha desfaz tudo, inclusive a criação das tabelas.
- As cinco tabelas históricas (execucao, pam_municipio_ano, validacao_regra, validacao_ocorrencia,
  inmet_previsao) têm o conteúdo comparado antes e depois, dentro da transação.
- Só INSERT simples; uma execução que já tem grupos é recusada (e a chave/unicidade também recusa).
- Se o próprio COMMIT falhar, o ROLLBACK só é tentado com transação ativa, e o erro original do COMMIT é
  o que aparece (CommitError); uma falha secundária do ROLLBACK é registrada sem esconder a causa.

Os níveis vêm da classificação atual (config.PLA05_FAIXAS_HA, hoje 10/500 ha) aplicada a dados
anteriores à etapa 1: é uma classificação retroativa, e os limites usados ficam gravados em cada grupo.
O texto histórico da regra em validacao_regra e o relatório JSON da execução não são alterados.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import load_sqlite
from .extract_sidra import validate_localidades_payload, validate_pam_payload
from .transform import attach_localidades, localidades_to_frame, pam_rows_to_frame
from .validate import ValidationReport, to_storage_frame, validate_pam

log = logging.getLogger(__name__)

REGRA = "PLA-05"
FONTE_SIDRA = "IBGE/SIDRA"
FONTE_LOCALIDADES = "IBGE/Localidades"
TABELAS_HISTORICAS = ("execucao", "pam_municipio_ano", "validacao_regra", "validacao_ocorrencia", "inmet_previsao")
TABELAS_NOVAS = {"validacao_grupo", "validacao_grupo_membro"}
# Objetos que a gravação pode criar: as duas tabelas, seus dois índices e os índices internos que o SQLite
# cria sozinho para as chaves primárias e UNIQUE (sqlite_autoindex_*), todos pertencentes às tabelas novas.
OBJETOS_NOVOS = TABELAS_NOVAS | {"ix_grupo_nivel", "ix_membro_municipio"}
NIVEIS = ("informativo", "atencao", "revisao_documental")

# Números auditados da execução 3 do banco real (auditoria da etapa 4, 14/11/2026).
ESPERADO = {
    3: {"total_grupos": 275, "total_registros": 726, "total_municipios": 269,
        "grupos_por_nivel": {"informativo": 142, "atencao": 102, "revisao_documental": 31},
        "registros_por_nivel": {"informativo": 441, "atencao": 221, "revisao_documental": 64},
        # Usados só na conferência depois da gravação (--conferir-gravados): um membro por registro.
        "total_membros": 726,
        "membros_por_nivel": {"informativo": 441, "atencao": 221, "revisao_documental": 64}},
}
LIMITE_DETALHES = 20                                                             # divergências listadas por conferência


class BackfillError(RuntimeError):
    """O backfill foi interrompido. A mensagem diz se nada foi gravado ou se o estado precisa ser conferido."""


class CommitError(BackfillError):
    """O COMMIT falhou. Guarda o erro original (também em __cause__) e, se houver, a falha do ROLLBACK."""

    def __init__(self, original: BaseException, transacao_ativa: bool, rollback_error: BaseException | None):
        self.original, self.transacao_ativa, self.rollback_error = original, transacao_ativa, rollback_error
        if not transacao_ativa:
            desfeito = "a transação já tinha sido encerrada pelo SQLite; ROLLBACK não executado"
        elif rollback_error is None:
            desfeito = "a transação estava ativa; ROLLBACK executado"
        else:
            desfeito = (f"a transação estava ativa; o ROLLBACK também falhou ({type(rollback_error).__name__}: "
                        f"{rollback_error})")
        super().__init__(
            f"falha no COMMIT: {type(original).__name__}: {original}. {desfeito}. O estado do arquivo pode estar "
            "indeterminado: não use o banco antes de conferir se há arquivo -journal ou -wal ao lado dele "
            "e de rodar --conferir-gravados (ou restaurar o backup)")


@dataclass
class Conferencia:
    codigo: str
    descricao: str
    ok: bool
    detalhe: str = ""


@dataclass
class Resultado:
    db: Path
    execucao: int
    localidades_execucao: int
    conferencias: list = field(default_factory=list)
    report: ValidationReport | None = None

    @property
    def ok(self) -> bool:
        return bool(self.conferencias) and all(c.ok for c in self.conferencias)

    @property
    def secao(self) -> dict | None:
        return None if self.report is None else self.report.secoes.get("pla05_grupos")

    def add(self, codigo: str, descricao: str, ok: bool, detalhe: str = "") -> bool:
        self.conferencias.append(Conferencia(codigo, descricao, bool(ok), detalhe))
        return bool(ok)


# --- Utilitários ----------------------------------------------------------------------------------
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _somente_leitura(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tabelas(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def _objetos(conn: sqlite3.Connection) -> dict:
    """Nome -> tabela a que pertence, para todos os objetos do esquema."""
    return {r[0]: r[1] for r in conn.execute("SELECT name, tbl_name FROM sqlite_master")}


def conteudo_historico(conn: sqlite3.Connection) -> dict:
    """Hash do conteúdo (todas as linhas, em ordem de rowid) de cada tabela histórica."""
    return {t: hashlib.sha256(repr([tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]).encode()).hexdigest()
            for t in TABELAS_HISTORICAS}


def _comandos(script: str) -> list[str]:
    """Divide o SCHEMA em comandos, para executá-los dentro da transação (executescript faria COMMIT)."""
    out, buf = [], ""
    for linha in script.splitlines(True):
        buf += linha
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise BackfillError("SCHEMA com comando incompleto")
    return out


def reconstruir(pam_path: Path, loc_path: Path) -> ValidationReport:
    """Mesmo caminho de run_pipeline.process(), em memória: bruto -> transformação -> validate_pam."""
    rows = validate_pam_payload(json.loads(Path(pam_path).read_text(encoding="utf-8")))
    sidra = pam_rows_to_frame(rows)
    sidra_names = sidra.drop_duplicates("cod_ibge").set_index("cod_ibge")["municipio"].to_dict()
    loc = localidades_to_frame(validate_localidades_payload(json.loads(Path(loc_path).read_text(encoding="utf-8"))))
    df = to_storage_frame(attach_localidades(sidra, loc))
    return validate_pam(df, localidades=loc, sidra_names=sidra_names, raw_rows=rows)


# --- Verificação (somente leitura) -----------------------------------------------------------------
def _execucao(conn, eid):
    return conn.execute("SELECT * FROM execucao WHERE id = ?", (eid,)).fetchone()


def _conferir_bruto(res: Resultado, codigo: str, ex, raw_dir: Path) -> Path | None:
    path = raw_dir / ex["arquivo_origem"]
    meta = path.with_suffix(".meta.json")
    if not path.exists() or not meta.exists():
        res.add(codigo, f"bruto da execução {ex['id']} disponível", False, f"não encontrado: {path.name} ou {meta.name} em {raw_dir}")
        return None
    h = sha256(path)
    m = json.loads(meta.read_text(encoding="utf-8")).get("sha256")
    ok = h == ex["hash_sha256"] == m
    res.add(codigo, f"bruto da execução {ex['id']} com o hash registrado", ok,
            f"{path.name}: {h}" + ("" if ok else f" (execucao: {ex['hash_sha256']}, .meta.json: {m})"))
    return path if ok else None


def verificar(db: Path, execucao: int, localidades_execucao: int, raw_dir: Path | None = None,
              esperado: dict | str | None = "auto") -> Resultado:
    """Reconstrói os grupos e roda todas as conferências sem escrever nada (banco aberto em mode=ro).

    `esperado="auto"` usa os números auditados (ESPERADO) quando houver para a execução; um dict usa
    esses números; None confere só contra o que está gravado no banco.
    """
    db = Path(db)
    if not db.exists():
        raise BackfillError(f"banco não encontrado: {db}")
    raw_dir = Path(raw_dir) if raw_dir else db.parent / "raw"
    esperado = ESPERADO.get(execucao) if esperado == "auto" else esperado
    res = Resultado(db, execucao, localidades_execucao)
    conn = _somente_leitura(db)
    try:
        ex, lo = _execucao(conn, execucao), _execucao(conn, localidades_execucao)
        if not res.add("C01", f"execução {execucao} é do IBGE/SIDRA e terminou com sucesso",
                       ex is not None and ex["fonte"].startswith(FONTE_SIDRA) and ex["status"] == "sucesso",
                       "inexistente" if ex is None else f"{ex['fonte']} | {ex['status']}"):
            return res
        if not res.add("C02", f"execução {localidades_execucao} é a de localidades da mesma chamada do pipeline",
                       lo is not None and lo["fonte"].startswith(FONTE_LOCALIDADES) and lo["status"] == "sucesso"
                       and lo["data_execucao"] == ex["data_execucao"],
                       "inexistente" if lo is None else f"{lo['fonte']} | {lo['status']} | data_execucao {lo['data_execucao']} "
                                                        f"(SIDRA: {ex['data_execucao']})"):
            return res
        pam_path = _conferir_bruto(res, "C03", ex, raw_dir)
        loc_path = _conferir_bruto(res, "C04", lo, raw_dir)
        regra = conn.execute("SELECT status, registros_afetados FROM validacao_regra WHERE execucao_id = ? AND regra = ?",
                             (execucao, REGRA)).fetchone()
        res.add("C05", "regra PLA-05 gravada na execução", regra is not None,
                "ausente" if regra is None else f"{regra['status']}, {regra['registros_afetados']} registros")
        tem_tabelas = {"validacao_grupo", "validacao_grupo_membro"} <= _tabelas(conn)
        ja = conn.execute("SELECT COUNT(*) FROM validacao_grupo WHERE execucao_id = ?", (execucao,)).fetchone()[0] if tem_tabelas else 0
        res.add("C06", "execução ainda sem grupos gravados", ja == 0,
                f"{ja} grupos já gravados" if ja else ("tabelas existem, sem grupos desta execução" if tem_tabelas
                                                        else "tabelas de grupos ainda não existem (serão criadas na gravação)"))
        if not (pam_path and loc_path and regra is not None and ja == 0):
            return res

        # Reconstrução em memória
        report = reconstruir(pam_path, loc_path)
        res.report = report
        sec = res.secao
        res.add("C07", "reconstrução aprovada pela validação, como a execução original", report.ok,
                "; ".join(report.errors)[:300])
        gravadas = {r["regra"]: (r["status"], r["registros_afetados"])
                    for r in conn.execute("SELECT regra, status, registros_afetados FROM validacao_regra WHERE execucao_id = ?", (execucao,))}
        hoje = {r.regra: (r.status, r.afetados) for r in report.regras}
        diferentes = sorted(k for k in set(hoje) & set(gravadas) if hoje[k] != gravadas[k])
        res.add("C08", "status e contagem das regras iguais aos gravados", not diferentes and bool(set(hoje) & set(gravadas)),
                f"{len(set(hoje) & set(gravadas))} regras comparadas" + (f"; diferentes: {diferentes}" if diferentes else ""))

        r = sec["resumo"]
        ocorr = sorted((c, a) for c, a in conn.execute(
            "SELECT codigo_ibge, ano FROM validacao_ocorrencia WHERE execucao_id = ? AND regra = ?", (execucao, REGRA)))
        totais_ok = (r["total_registros"] == regra["registros_afetados"] == len(ocorr)
                     and r["total_municipios"] == len({c for c, _ in ocorr}))
        if esperado:
            totais_ok = totais_ok and all(r[k] == esperado[k] for k in ("total_grupos", "total_registros", "total_municipios"))
        res.add("C09", "totais de grupos, registros e municípios", totais_ok,
                f"{r['total_grupos']} grupos, {r['total_registros']} registros, {r['total_municipios']} municípios"
                + (f" (esperado: {esperado['total_grupos']}, {esperado['total_registros']}, {esperado['total_municipios']})" if esperado else
                   f" (gravado: {regra['registros_afetados']} registros, {len({c for c, _ in ocorr})} municípios)"))
        niveis_ok = (sum(r["grupos_por_nivel"].values()) == r["total_grupos"]
                     and sum(r["registros_por_nivel"].values()) == r["total_registros"])
        if esperado:
            niveis_ok = niveis_ok and r["grupos_por_nivel"] == esperado["grupos_por_nivel"] \
                and r["registros_por_nivel"] == esperado["registros_por_nivel"]
        res.add("C10", "distribuição por nível", niveis_ok,
                "grupos " + "/".join(str(r["grupos_por_nivel"][n]) for n in NIVEIS)
                + ", registros " + "/".join(str(r["registros_por_nivel"][n]) for n in NIVEIS))

        pares = sorted((m["codigo_ibge"], g["ano"]) for g in sec["grupos"] for m in g["municipios"])
        res.add("C11", "pares (código IBGE, ano) idênticos às ocorrências PLA-05 gravadas", pares == ocorr,
                f"{len(pares)} reconstruídos, {len(ocorr)} gravados"
                + ("" if pares == ocorr else f"; só na reconstrução: {sorted(set(pares) - set(ocorr))[:5]}, "
                                             f"só no banco: {sorted(set(ocorr) - set(pares))[:5]}"))

        pam = {(c, a): (n, rd, ar, pr, e) for c, n, a, rd, ar, pr, e in conn.execute(
            "SELECT codigo_ibge, municipio, ano, rendimento_kg_ha, area_colhida_ha, producao_t, execucao_id FROM pam_municipio_ano")}
        membros = [(m, g) for g in sec["grupos"] for m in g["municipios"]]
        fora = [(m["codigo_ibge"], g["ano"]) for m, g in membros if pam.get((m["codigo_ibge"], g["ano"]), (None,) * 5)[4] != execucao]
        res.add("C12", "linhas da pam_municipio_ano ainda gravadas por esta execução (sem coleta posterior)", not fora,
                f"{len(fora)} registros já pertencem a outra execução ou não existem: {fora[:5]}" if fora else
                f"{len(membros)} registros conferidos")
        if fora:
            return res                                                           # D4: sem base histórica para conferir
        val = [(m["codigo_ibge"], g["ano"]) for m, g in membros
               if pam[(m["codigo_ibge"], g["ano"])][1:4] != (g["rendimento_kg_ha"], g["area_colhida_ha"], g["producao_t"])]
        res.add("C13", "rendimento, área e produção iguais aos gravados", not val, f"divergentes: {val[:5]}" if val else "")
        nom = [(m["codigo_ibge"], g["ano"], m["municipio"], pam[(m["codigo_ibge"], g["ano"])][0]) for m, g in membros
               if pam[(m["codigo_ibge"], g["ano"])][0] != m["municipio"]]
        res.add("C14", "nomes dos municípios iguais aos gravados", not nom, f"divergentes: {nom[:5]}" if nom else "")
        chaves = [(g["ano"], g["rendimento_kg_ha"], g["area_colhida_ha"], g["producao_t"]) for g in sec["grupos"]]
        numeros = [g["grupo"] for g in sec["grupos"]]
        res.add("C15", "sem grupos duplicados; numeração 1..n", len(chaves) == len(set(chaves)) and numeros == list(range(1, len(numeros) + 1)),
                f"{len(chaves)} grupos")

        # Ensaio da gravação numa cópia do banco na memória (nada é escrito em arquivo)
        mem = sqlite3.connect(":memory:", isolation_level=None)
        try:
            conn.backup(mem)
            try:
                info = _gravar_transacao(mem, execucao, report)
                res.add("C16", "ensaio da gravação numa cópia em memória (chaves, tabelas históricas, objetos novos)", True,
                        f"{info['grupos']} grupos e {info['membros']} membros; objetos criados: "
                        f"{sorted(n for n in info['objetos_criados'] if n in OBJETOS_NOVOS) or 'nenhum'}")
            except Exception as exc:                                            # qualquer erro reprova o ensaio
                res.add("C16", "ensaio da gravação numa cópia em memória", False, f"{type(exc).__name__}: {exc}")
        finally:
            mem.close()
        return res
    finally:
        conn.close()


# --- Conferência depois da gravação (somente leitura) -----------------------------------------------
# Colunas de validacao_grupo comparadas campo a campo, com o tipo com que o SQLite as guarda (typeof).
CAMPOS_GRUPO = (("ano", "integer"), ("nivel", "text"), ("rendimento_kg_ha", "real"), ("area_colhida_ha", "real"),
                ("producao_t", "real"), ("total_municipios", "integer"), ("total_registros", "integer"),
                ("faixa_acima_de_ha", "real"), ("faixa_ate_ha", "real"))


def _como_gravado(valor, tipo: str):
    """Valor reconstruído como a coluna o guarda (afinidade REAL: inteiro vira real). Nada é arredondado."""
    if valor is not None and tipo == "real" and isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    return valor


def _tipo(valor) -> str:
    return {type(None): "null", int: "integer", float: "real", str: "text", bytes: "blob"}.get(type(valor), type(valor).__name__)


def _lista(itens: list, total_rotulo: str = "divergências") -> str:
    if not itens:
        return ""
    extra = f" … (+{len(itens) - LIMITE_DETALHES})" if len(itens) > LIMITE_DETALHES else ""
    return f"{len(itens)} {total_rotulo}: " + "; ".join(itens[:LIMITE_DETALHES]) + extra


def _journal_pendente(db: Path) -> list[str]:
    return [p.name for p in (db.with_name(db.name + "-journal"), db.with_name(db.name + "-wal")) if p.exists()]


def conferir_gravados(db: Path, execucao: int, localidades_execucao: int, raw_dir: Path | None = None,
                      esperado: dict | str | None = "auto") -> Resultado:
    """Confere, sem gravar nada, os grupos e membros já gravados de uma execução contra a reconstrução dos brutos.

    Banco aberto só em modo somente leitura (mode=ro), e só depois de confirmar que não há -journal ou -wal ao
    lado dele (abrir poderia desfazer ou aplicar uma transação pendente). A comparação é exata: cada valor
    gravado (e o seu typeof no SQLite) contra o valor reconstruído como a coluna o guarda, sem tolerância.
    """
    db = Path(db)
    if not db.exists():
        raise BackfillError(f"banco não encontrado: {db}")
    raw_dir = Path(raw_dir) if raw_dir else db.parent / "raw"
    esperado = ESPERADO.get(execucao) if esperado == "auto" else esperado
    if esperado is not None and not isinstance(esperado, dict):
        raise BackfillError(f"esperado inválido: {esperado!r}")
    res = Resultado(db, execucao, localidades_execucao)
    pendentes = _journal_pendente(db)
    if not res.add("P00", "sem -journal ou -wal pendente ao lado do banco", not pendentes,
                   f"encontrado: {pendentes}; o banco não foi aberto" if pendentes else ""):
        return res
    conn = _somente_leitura(db)
    try:
        ex, lo = _execucao(conn, execucao), _execucao(conn, localidades_execucao)
        if not res.add("P01", f"execução {execucao} é do IBGE/SIDRA e terminou com sucesso",
                       ex is not None and ex["fonte"].startswith(FONTE_SIDRA) and ex["status"] == "sucesso",
                       "inexistente" if ex is None else f"{ex['fonte']} | {ex['status']}"):
            return res
        if not res.add("P02", f"execução {localidades_execucao} é a de localidades da mesma chamada do pipeline",
                       lo is not None and lo["fonte"].startswith(FONTE_LOCALIDADES) and lo["status"] == "sucesso"
                       and lo["data_execucao"] == ex["data_execucao"],
                       "inexistente" if lo is None else f"{lo['fonte']} | {lo['status']} | data_execucao {lo['data_execucao']} "
                                                        f"(SIDRA: {ex['data_execucao']})"):
            return res
        pam_path = _conferir_bruto(res, "P03", ex, raw_dir)
        loc_path = _conferir_bruto(res, "P04", lo, raw_dir)
        regra = conn.execute("SELECT status, registros_afetados FROM validacao_regra WHERE execucao_id = ? AND regra = ?",
                             (execucao, REGRA)).fetchone()
        res.add("P05", "regra PLA-05 gravada na execução", regra is not None,
                "ausente" if regra is None else f"{regra['status']}, {regra['registros_afetados']} registros")
        tem_tabelas = TABELAS_NOVAS <= _tabelas(conn)
        ja = conn.execute("SELECT COUNT(*) FROM validacao_grupo WHERE execucao_id = ?", (execucao,)).fetchone()[0] if tem_tabelas else 0
        res.add("P06", "execução com grupos PLA-05 gravados", ja > 0,
                f"{ja} grupos gravados" if tem_tabelas else "tabelas de grupos não existem neste banco")
        if not (pam_path and loc_path and regra is not None and ja > 0):
            return res

        report = reconstruir(pam_path, loc_path)
        res.report = report
        sec = res.secao
        if sec is None:
            raise BackfillError("a reconstrução não produziu a seção pla05_grupos")
        res.add("P07", "reconstrução aprovada pela validação, como a execução original", report.ok,
                "; ".join(report.errors)[:300])
        gravadas = {r["regra"]: (r["status"], r["registros_afetados"])
                    for r in conn.execute("SELECT regra, status, registros_afetados FROM validacao_regra WHERE execucao_id = ?", (execucao,))}
        hoje = {r.regra: (r.status, r.afetados) for r in report.regras}
        diferentes = sorted(k for k in set(hoje) & set(gravadas) if hoje[k] != gravadas[k])
        res.add("P08", "status e contagem das regras iguais aos gravados", not diferentes and bool(set(hoje) & set(gravadas)),
                f"{len(set(hoje) & set(gravadas))} regras comparadas" + (f"; diferentes: {diferentes}" if diferentes else ""))

        rec = {g["grupo"]: g for g in sec["grupos"]}
        limites = {n["nivel"]: (n["area_acima_de_ha"], n["area_ate_ha"]) for n in sec["classificacao"]["niveis"]}
        cols = ", ".join(f"{c}, typeof({c})" for c, _ in CAMPOS_GRUPO)
        grav = {r[0]: r for r in conn.execute(
            f"SELECT grupo, {cols}, produtividade_sc_ha, typeof(produtividade_sc_ha), regra "
            "FROM validacao_grupo WHERE execucao_id = ? ORDER BY grupo", (execucao,))}
        membros = conn.execute(
            "SELECT grupo, ano, codigo_ibge, typeof(codigo_ibge), municipio, typeof(municipio), regra "
            "FROM validacao_grupo_membro WHERE execucao_id = ? ORDER BY grupo, codigo_ibge", (execucao,)).fetchall()
        n_membros_rec = sum(len(g["municipios"]) for g in rec.values())

        # P09 e P10: quantidades
        ok = len(grav) == len(rec) and (not esperado or len(grav) == esperado["total_grupos"])
        res.add("P09", "quantidade de grupos gravados", ok,
                f"gravados {len(grav)}, reconstruídos {len(rec)}" + (f", esperados {esperado['total_grupos']}" if esperado else ""))
        alvo = (esperado or {}).get("total_membros")
        ok = len(membros) == n_membros_rec and (alvo is None or len(membros) == alvo)
        res.add("P10", "quantidade de membros gravados", ok,
                f"gravados {len(membros)}, reconstruídos {n_membros_rec}" + (f", esperados {alvo}" if alvo is not None else ""))

        # P11: números (identidade) dos grupos
        so_banco, so_rec = sorted(set(grav) - set(rec)), sorted(set(rec) - set(grav))
        res.add("P11", "números dos grupos iguais aos reconstruídos", not so_banco and not so_rec,
                f"só no banco: {so_banco[:LIMITE_DETALHES]}; só na reconstrução: {so_rec[:LIMITE_DETALHES]}" if so_banco or so_rec
                else f"grupos 1..{len(grav)}")

        # P12: cada campo de cada grupo, com o tipo gravado
        div = []
        for n in sorted(set(grav) & set(rec)):
            row, g = grav[n], rec[n]
            esperados = dict(g)
            esperados["faixa_acima_de_ha"], esperados["faixa_ate_ha"] = limites.get(g["nivel"], ("ausente", "ausente"))
            for i, (col, tipo) in enumerate(CAMPOS_GRUPO):
                v, t = row[1 + 2 * i], row[2 + 2 * i]
                e = _como_gravado(esperados.get(col), tipo)
                if v != e or t != _tipo(e) or type(v) is not type(e):
                    div.append(f"grupo {n}, campo {col}: gravado {v!r} ({t}), reconstruído {e!r} ({_tipo(e)})")
        res.add("P12", "ano, nível, faixa, valores e totais de cada grupo idênticos (valor e tipo)", not div,
                _lista(div) or f"{len(set(grav) & set(rec))} grupos × {len(CAMPOS_GRUPO)} campos")

        # P13: produtividade gerada pelo SQLite (rendimento_kg_ha / 60.0)
        i_prod = 1 + 2 * len(CAMPOS_GRUPO)
        div = []
        for n in sorted(set(grav) & set(rec)):
            row = grav[n]
            v, t, rend_db = row[i_prod], row[i_prod + 1], row[1 + 2 * 2]
            e = _como_gravado(rec[n]["rendimento_kg_ha"], "real") / 60.0
            if t != "real" or v != rend_db / 60.0 or v != e:
                div.append(f"grupo {n}, campo produtividade_sc_ha: gravado {v!r} ({t}), esperado {e!r} (rendimento / 60)")
        res.add("P13", "produtividade gerada pelo SQLite igual a rendimento / 60", not div, _lista(div))

        # P14: membros de cada grupo (código IBGE, nome, ano)
        por_grupo: dict = {}
        div = []
        for grupo, ano, cod, t_cod, nome, t_nome, _ in membros:
            por_grupo.setdefault(grupo, {})[cod] = nome
            if t_cod != "text" or len(cod) != 7:
                div.append(f"grupo {grupo}, membro {cod!r}: codigo_ibge gravado como {t_cod}")
            if grupo in rec and ano != rec[grupo]["ano"]:
                div.append(f"grupo {grupo}, membro {cod}: ano gravado {ano}, reconstruído {rec[grupo]['ano']}")
            if t_nome not in ("text", "null"):
                div.append(f"grupo {grupo}, membro {cod}: municipio gravado como {t_nome}")
        for n in sorted(set(rec) | set(por_grupo)):
            g_db = por_grupo.get(n, {})
            g_rec = {m["codigo_ibge"]: m["municipio"] for m in rec.get(n, {}).get("municipios", [])}
            for cod in sorted(set(g_rec) - set(g_db)):
                div.append(f"grupo {n}, membro {cod}: ausente no banco")
            for cod in sorted(set(g_db) - set(g_rec)):
                div.append(f"grupo {n}, membro {cod}: a mais no banco")
            for cod in sorted(set(g_db) & set(g_rec)):
                if g_db[cod] != g_rec[cod]:
                    div.append(f"grupo {n}, membro {cod}, campo municipio: gravado {g_db[cod]!r}, reconstruído {g_rec[cod]!r}")
        res.add("P14", "códigos IBGE, nomes e ano dos membros idênticos", not div, _lista(div) or f"{len(membros)} membros")

        # P15: totais gerais e distribuição por nível
        nivel_de = {n: grav[n][1 + 2 * 1] for n in grav}
        g_niv = {k: sum(1 for n in grav if nivel_de[n] == k) for k in NIVEIS}
        m_niv = {k: sum(1 for m in membros if nivel_de.get(m[0]) == k) for k in NIVEIS}
        m_niv_rec = {k: sum(len(g["municipios"]) for g in rec.values() if g["nivel"] == k) for k in NIVEIS}
        municipios = len({m[2] for m in membros})
        r = sec["resumo"]
        ok = g_niv == r["grupos_por_nivel"] and m_niv == m_niv_rec and municipios == r["total_municipios"]
        if esperado:
            ok = ok and g_niv == esperado["grupos_por_nivel"] and municipios == esperado["total_municipios"] \
                and m_niv == esperado.get("membros_por_nivel", m_niv)
        res.add("P15", "municípios distintos e distribuição por nível iguais aos reconstruídos", ok,
                f"{municipios} municípios (reconstruídos {r['total_municipios']}); grupos "
                + "/".join(str(g_niv[k]) for k in NIVEIS) + " (reconstruídos " + "/".join(str(r["grupos_por_nivel"][k]) for k in NIVEIS)
                + "); membros " + "/".join(str(m_niv[k]) for k in NIVEIS) + " (reconstruídos " + "/".join(str(m_niv_rec[k]) for k in NIVEIS) + ")")

        # P16: pares dos membros = ocorrências PLA-05 gravadas
        pares = sorted((m[2], m[1]) for m in membros)
        ocorr = sorted((c, a) for c, a in conn.execute(
            "SELECT codigo_ibge, ano FROM validacao_ocorrencia WHERE execucao_id = ? AND regra = ?", (execucao, REGRA)))
        res.add("P16", "pares (código IBGE, ano) dos membros idênticos às ocorrências PLA-05", pares == ocorr,
                f"{len(pares)} membros, {len(ocorr)} ocorrências"
                + ("" if pares == ocorr else f"; só nos membros: {sorted(set(pares) - set(ocorr))[:5]}, "
                                             f"só nas ocorrências: {sorted(set(ocorr) - set(pares))[:5]}"))

        # P17: vínculo com a execução e a regra
        regras = {row[-1] for row in grav.values()} | {m[6] for m in membros}
        res.add("P17", f"grupos e membros vinculados à execução {execucao} e à regra {REGRA}", regras == {REGRA},
                f"regras encontradas: {sorted(regras)}")

        # P18: chaves estrangeiras; P19: duplicidades
        fk = conn.execute("PRAGMA foreign_key_check(validacao_grupo)").fetchall() \
            + conn.execute("PRAGMA foreign_key_check(validacao_grupo_membro)").fetchall()
        res.add("P18", "chaves estrangeiras íntegras (foreign_key_check)", not fk, f"{[tuple(x) for x in fk][:5]}" if fk else "")
        dup = [f"grupo repetido {tuple(x)}" for x in conn.execute(
            "SELECT ano, rendimento_kg_ha, area_colhida_ha, producao_t, COUNT(*) FROM validacao_grupo WHERE execucao_id = ? "
            "GROUP BY regra, ano, rendimento_kg_ha, area_colhida_ha, producao_t HAVING COUNT(*) > 1", (execucao,))]
        dup += [f"município repetido no ano {tuple(x)}" for x in conn.execute(
            "SELECT ano, codigo_ibge, COUNT(*) FROM validacao_grupo_membro WHERE execucao_id = ? "
            "GROUP BY regra, ano, codigo_ibge HAVING COUNT(*) > 1", (execucao,))]
        res.add("P19", "sem grupos ou membros duplicados", not dup, _lista(dup))
        return res
    finally:
        conn.close()


# --- Gravação --------------------------------------------------------------------------------------
def _gravar_transacao(conn: sqlite3.Connection, execucao: int, report: ValidationReport) -> dict:
    """Cria as tabelas novas (se faltarem) e insere grupos e membros numa transação; confere antes do COMMIT."""
    conn.isolation_level = None                                                  # transação controlada aqui
    conn.execute("PRAGMA foreign_keys = ON")                                     # precisa ser fora da transação
    sec = report.secoes["pla05_grupos"]
    conn.execute("BEGIN IMMEDIATE")
    try:
        historico = conteudo_historico(conn)
        objetos = _objetos(conn)
        for comando in _comandos(load_sqlite.SCHEMA):                            # só CREATE ... IF NOT EXISTS
            conn.execute(comando)
        depois = _objetos(conn)
        criados = {n for n in depois if n not in objetos}
        estranhos = sorted(n for n in criados if depois[n] not in TABELAS_NOVAS)
        if estranhos or (criados and not OBJETOS_NOVOS <= set(depois)):
            raise BackfillError(f"objetos inesperados no esquema: {estranhos or sorted(criados)}")
        if any(objetos[n] != depois[n] for n in objetos):
            raise BackfillError("um objeto existente do esquema mudou")
        ja = conn.execute("SELECT COUNT(*) FROM validacao_grupo WHERE execucao_id = ?", (execucao,)).fetchone()[0]
        if ja:
            raise BackfillError(f"a execução {execucao} já tem {ja} grupos gravados; nada foi alterado")
        grupos = load_sqlite.record_pla05_groups(conn, execucao, report)         # só INSERT simples
        membros = conn.execute("SELECT COUNT(*) FROM validacao_grupo_membro WHERE execucao_id = ?", (execucao,)).fetchone()[0]
        esperado_membros = sum(len(g["municipios"]) for g in sec["grupos"])
        if grupos != len(sec["grupos"]) or membros != esperado_membros:
            raise BackfillError(f"gravados {grupos} grupos e {membros} membros; esperados {len(sec['grupos'])} e {esperado_membros}")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise BackfillError(f"chaves estrangeiras quebradas: {[tuple(r) for r in fk][:5]}")
        if conteudo_historico(conn) != historico:
            raise BackfillError("o conteúdo de uma tabela histórica mudou durante a gravação")
    except BaseException as exc:
        _desfazer(conn, exc)
        raise                                                                    # sempre o erro original
    try:
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        ativa = conn.in_transaction
        rb = _desfazer(conn, exc)
        raise CommitError(exc, ativa, rb) from exc
    except BaseException as exc:                                                 # ex.: interrupção durante o COMMIT
        _desfazer(conn, exc)
        raise
    return {"grupos": grupos, "membros": membros, "objetos_criados": criados}


def _desfazer(conn: sqlite3.Connection, original: BaseException) -> BaseException | None:
    """ROLLBACK só com transação ativa. Uma falha aqui é registrada e devolvida, sem substituir o erro original."""
    if not conn.in_transaction:
        return None
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error as rb:
        log.error("ROLLBACK falhou (%s: %s) depois de %s: %s; o erro original é mantido",
                  type(rb).__name__, rb, type(original).__name__, original)
        try:
            setattr(original, "rollback_error", rb)                              # disponível para quem tratar o erro
        except AttributeError:
            pass
        return rb
    return None


def gravar(db: Path, execucao: int, localidades_execucao: int, backup: Path, raw_dir: Path | None = None,
           esperado: dict | str | None = "auto") -> dict:
    """Roda a verificação completa e, só se tudo passar e o backup conferir, grava os grupos."""
    db, backup = Path(db), Path(backup)
    res = verificar(db, execucao, localidades_execucao, raw_dir, esperado)
    if not res.ok:
        falhas = [f"{c.codigo} {c.descricao}: {c.detalhe}" for c in res.conferencias if not c.ok] or ["conferências incompletas"]
        raise BackfillError("conferências falharam; nada foi gravado: " + " | ".join(falhas))
    if not backup.exists():
        raise BackfillError(f"backup não encontrado: {backup}; nada foi gravado")
    if backup.resolve() == db.resolve():
        raise BackfillError("o backup não pode ser o próprio banco; nada foi gravado")
    h_db, h_bk = sha256(db), sha256(backup)
    if h_db != h_bk:
        raise BackfillError(f"o backup não é cópia exata do banco (banco {h_db}, backup {h_bk}); nada foi gravado")
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        info = _gravar_transacao(conn, execucao, res.report)
        integridade = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {"execucao": execucao, "localidades_execucao": localidades_execucao, "grupos": info["grupos"],
            "membros": info["membros"], "objetos_criados": sorted(info["objetos_criados"]), "integridade": integridade,
            "hash_banco_antes": h_db, "hash_backup": h_bk}


# --- Linha de comando ------------------------------------------------------------------------------
def _imprimir(res: Resultado) -> None:
    for c in res.conferencias:
        print(f"[{'ok' if c.ok else 'FALHOU'}] {c.codigo} {c.descricao}" + (f" — {c.detalhe}" if c.detalhe else ""))
    print("Resultado: todas as conferências passaram" if res.ok else "Resultado: há conferências com falha; nada foi gravado")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, required=True, help="banco SQLite (obrigatório; não há valor padrão)")
    p.add_argument("--execucao", type=int, required=True, help="execução do IBGE/SIDRA a preencher")
    p.add_argument("--localidades-execucao", type=int, required=True, help="execução de localidades da mesma chamada")
    p.add_argument("--raw", type=Path, help="pasta dos brutos (padrão: pasta raw ao lado do banco)")
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--gravar", action="store_true", help="grava depois de verificar (sem isso, só verifica)")
    modo.add_argument("--conferir-gravados", action="store_true",
                      help="somente leitura: compara os grupos já gravados com a reconstrução dos brutos")
    p.add_argument("--backup", type=Path, help="cópia exata do banco (mesmo SHA-256); obrigatório com --gravar")
    a = p.parse_args(argv)
    if a.gravar and not a.backup:
        p.error("--gravar exige --backup com uma cópia exata do banco")
    try:
        if a.conferir_gravados:
            res = conferir_gravados(a.db, a.execucao, a.localidades_execucao, a.raw)
            _imprimir(res)
            print("(modo conferir gravados: somente leitura, nada foi gravado)")
            return 0 if res.ok else 1
        if not a.gravar:
            res = verificar(a.db, a.execucao, a.localidades_execucao, a.raw)
            _imprimir(res)
            print("(modo verificar: somente leitura, nada foi gravado)")
            return 0 if res.ok else 1
        info = gravar(a.db, a.execucao, a.localidades_execucao, a.backup, a.raw)
    except BackfillError as exc:
        print(f"Backfill interrompido: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
