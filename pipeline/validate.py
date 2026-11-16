"""Camada de qualidade dos dados da PAM (etapa 3c).

Cada regra tem identificador, dimensão (integridade, completude, consistência,
plausibilidade), severidade e resultado. Severidades:

- ERRO  (impeditivo): a execução inteira é recusada; nada é gravado e o motivo fica registrado.
- AVISO (não impeditivo): os dados são gravados e cada registro afetado fica anotado.
- INFO: contagem informativa (ex.: municípios sem produção de café).

Nenhuma regra corrige valores. Dado ausente continua ausente (NULL), nunca zero.
Regras de 3b preservadas com os mesmos identificadores de texto; as novas estão marcadas como origem "3c".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

import pandas as pd

from . import config

KEY = ["codigo_ibge", "ano"]
NUMERIC = ["rendimento_kg_ha", "area_colhida_ha", "producao_t"]

ERRO, AVISO, INFO = "erro", "aviso", "info"
APROVADA, ACIONADA, NAO_VERIFICADA = "aprovada", "acionada", "nao_verificada"

# Unidade esperada por variável do SIDRA (campo MN da resposta); comparação sem acento/maiúsculas
EXPECTED_UNITS = {
    config.VAR_RENDIMENTO: ("quilograma", "hectare"),
    config.VAR_AREA: ("hectare",),
    config.VAR_PRODUCAO: ("tonelada",),
}


@dataclass
class RuleResult:
    regra: str
    nome: str
    dimensao: str
    severidade: str
    status: str = APROVADA
    afetados: int = 0
    detalhe: str = ""
    origem: str = "3c"
    ocorrencias: list = field(default_factory=list)  # [(codigo_ibge|None, ano|None, detalhe)]

    def hit(self, keys: Iterable[tuple], detalhe: str, per_record: dict | None = None) -> None:
        keys = list(keys)
        self.status = ACIONADA
        self.afetados = len(keys)
        self.detalhe = detalhe
        self.ocorrencias = [(k[0], k[1], (per_record or {}).get(k, detalhe)) for k in keys]

    def summary(self) -> dict:
        d = asdict(self)
        d["amostra"] = [{"codigo_ibge": c, "ano": a} for c, a, _ in self.ocorrencias[:5]]
        del d["ocorrencias"]
        return d


@dataclass
class ValidationReport:
    regras: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    # Seções extras gravadas só no arquivo JSON de qualidade (ex.: "pla05_grupos"); não entram em
    # as_dict(), no retorno de process() nem no banco. Preenchida apenas por validate_pam (IBGE/SIDRA).
    secoes: dict = field(default_factory=dict)

    # --- compatibilidade com a etapa 3b ---
    @property
    def errors(self) -> list[str]:
        return [f"[{r.regra}] {r.detalhe}" for r in self.regras if r.status == ACIONADA and r.severidade == ERRO]

    @property
    def warnings(self) -> list[str]:
        return [f"[{r.regra}] {r.detalhe}" for r in self.regras if r.status == ACIONADA and r.severidade == AVISO]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, rule: RuleResult) -> RuleResult:
        self.regras.append(rule)
        return rule

    def resumo(self) -> dict:
        n = self.stats.get("registros", 0)
        rec_err = {(c, a) for r in self.regras if r.severidade == ERRO for c, a, _ in r.ocorrencias if c is not None}
        rec_warn = {(c, a) for r in self.regras if r.severidade == AVISO for c, a, _ in r.ocorrencias if c is not None and a is not None}
        return {
            "registros_analisados": n,
            "registros_rejeitados": len(rec_err),
            "registros_aprovados": n - len(rec_err),
            "registros_com_aviso": len(rec_warn),
            "erros_impeditivos": len(self.errors),
            "avisos": len(self.warnings),
            "campos_ausentes": self.stats.get("ausentes", {}),
            "zeros_reais": self.stats.get("zeros", {}),
            "municipios_sem_cobertura": self.stats.get("sem_cobertura", {}),
            "regras_executadas": sum(r.status != NAO_VERIFICADA for r in self.regras),
            "regras_nao_verificadas": [r.regra for r in self.regras if r.status == NAO_VERIFICADA],
            "regras_acionadas": [f"{r.regra} ({r.severidade})" for r in self.regras if r.status == ACIONADA],
            "resultado": "aprovado" if self.ok else "reprovado",
        }

    def as_dict(self) -> dict:
        return {"resumo": self.resumo(), "erros": self.errors, "avisos": self.warnings,
                "estatisticas": self.stats, "regras": [r.summary() for r in self.regras]}


class ValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        super().__init__("; ".join(report.errors))
        self.report = report


def to_storage_frame(pam: pd.DataFrame) -> pd.DataFrame:
    """Saída de transform.py -> colunas do banco (produtividade é calculada pelo próprio banco)."""
    return pam.rename(columns={"cod_ibge": "codigo_ibge", "mesorregiao": "regiao"})[
        ["codigo_ibge", "municipio", "regiao", "ano", "rendimento_kg_ha", "area_colhida_ha", "producao_t"]
    ].copy()


def _keys(df: pd.DataFrame) -> list[tuple]:
    return list(zip(df["codigo_ibge"].astype(str), df["ano"].astype(int)))


# =============================================================================
# PLA-05: grupos de coincidência e níveis de triagem (etapa 1 da evolução da regra)
# Só muda a apresentação: os registros marcados são os mesmos da regra original.
# =============================================================================
PLA05_INFORMATIVO, PLA05_ATENCAO, PLA05_REVISAO = "Informativo", "Atenção", "Revisão documental"
PLA05_MAX_PARCEIROS = 3  # grupos de até 4 municípios mostram todos os parceiros
PLA05_TRIAGEM = "classificação interna de triagem do Rigora"
PLA05_SEM_ERRO = "A coincidência, isoladamente, não comprova erro nos dados."


def _br(value: float, decimals: int | None = None) -> str:
    """Número no padrão brasileiro: 10800 -> '10.800'; 25.0 com 1 casa -> '25,0'; 3.999 -> '3,999'.

    Sem casas fixas, mostra o valor como está (até 3 casas, sem zeros à direita), para não arredondar
    um dado da fonte no texto.
    """
    v = float(value)
    if decimals is None:
        text = f"{v:,.3f}".rstrip("0").rstrip(".")
    else:
        text = f"{v:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pla05_level(area_ha: float, faixas: tuple | None = None) -> str:
    """Nível de triagem pela área colhida do grupo (limites inclusivos à esquerda: até 10, até 500)."""
    lo, hi = faixas or config.PLA05_FAIXAS_HA
    if area_ha <= lo:
        return PLA05_INFORMATIVO
    if area_ha <= hi:
        return PLA05_ATENCAO
    return PLA05_REVISAO


def _faixa(level: str, faixas: tuple | None = None) -> str:
    lo, hi = (_br(x) for x in (faixas or config.PLA05_FAIXAS_HA))
    return {PLA05_INFORMATIVO: f"até {lo} ha", PLA05_ATENCAO: f"{lo}–{hi} ha", PLA05_REVISAO: f"acima de {hi} ha"}[level]


def pla05_groups(same: pd.DataFrame) -> list[dict]:
    """Agrupa os registros da PLA-05 por (ano, rendimento, área, produção). Membros em ordem de código IBGE."""
    names = same["municipio"] if "municipio" in same.columns else pd.Series(None, index=same.index)
    data = same.assign(_nome=names)
    groups = []
    for (ano, rend, area, prod), g in data.groupby(["ano"] + NUMERIC, sort=True):
        members = sorted((str(c), None if pd.isna(n) else str(n)) for c, n in zip(g["codigo_ibge"], g["_nome"]))
        groups.append({"ano": int(ano), "rendimento_kg_ha": float(rend), "area_colhida_ha": float(area),
                       "producao_t": float(prod), "membros": members, "nivel": pla05_level(float(area))})
    return groups


def _municipio(code: str, name: str | None) -> str:
    return f"{name} ({code})" if name else code


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1]


def pla05_messages(groups: list[dict]) -> dict:
    """Texto de cada ocorrência: nível, valores, tamanho do grupo e municípios parceiros."""
    out = {}
    for g in groups:
        valores = (f"({_br(g['area_colhida_ha'])} ha; {_br(g['producao_t'])} t; {_br(g['rendimento_kg_ha'])} kg/ha = "
                   f"{_br(g['rendimento_kg_ha'] / config.SACA_KG, 1)} sc/ha)")
        if g["nivel"] == PLA05_REVISAO:
            fim = (f"Classificação de revisão documental, definida pela faixa de área adotada pelo Rigora. {PLA05_SEM_ERRO}")
        else:
            fim = f"Faixa {_faixa(g['nivel'])}, {PLA05_TRIAGEM}."
        unique = sorted(dict(g["membros"]).items())  # um parceiro por código, mesmo com chave repetida
        for code, _ in unique:
            partners = [m for m in unique if m[0] != code]
            shown = [_municipio(c, n) for c, n in partners[:PLA05_MAX_PARCEIROS]]
            rest = len(partners) - len(shown)
            if not partners:  # o mesmo município duas vezes no ano: chave duplicada, tratada pela INT-01
                quem = f"mesmos valores registrados mais de uma vez para o próprio município {valores}"
            elif len(partners) == 1:
                quem = f"mesmos valores de {shown[0]} {valores}"
            else:
                lista = ", ".join(shown) + f" e mais {rest}" if rest else _join(shown)
                quem = f"mesmos valores de {len(partners)} outros municípios {valores}: {lista}"
            out[(code, g["ano"])] = f"{g['nivel']} — {g['ano']}: {quem}. {fim}"
    return out


def pla05_summary(groups: list[dict]) -> str:
    """Texto da regra: grupos, registros e municípios, com a contagem por nível de triagem."""
    count = {lvl: sum(g["nivel"] == lvl for g in groups) for lvl in (PLA05_INFORMATIVO, PLA05_ATENCAO, PLA05_REVISAO)}
    regs = sum(len(g["membros"]) for g in groups)
    muns = len({c for g in groups for c, _ in g["membros"]})
    pl = lambda n, s, p: f"{_br(n)} {s if n == 1 else p}"  # noqa: E731
    return (f"{pl(len(groups), 'grupo', 'grupos')} de municípios com valores idênticos no mesmo ano "
            f"({pl(regs, 'registro', 'registros')}, {pl(muns, 'município', 'municípios')}): "
            f"{pl(count[PLA05_INFORMATIVO], 'informativo', 'informativos')} (área {_faixa(PLA05_INFORMATIVO)}), "
            f"{_br(count[PLA05_ATENCAO])} de atenção ({_faixa(PLA05_ATENCAO)}) e "
            f"{_br(count[PLA05_REVISAO])} de revisão documental ({_faixa(PLA05_REVISAO)}). "
            f"Os níveis são uma {PLA05_TRIAGEM}, definida pela faixa de área; não são uma conclusão sobre a "
            f"qualidade dos dados do IBGE. {PLA05_SEM_ERRO}")


# --- Etapa 2: grupos estruturados no relatório JSON ---------------------------------------
PLA05_SECAO_VERSAO = 1
PLA05_CODIGO = {PLA05_INFORMATIVO: "informativo", PLA05_ATENCAO: "atencao", PLA05_REVISAO: "revisao_documental"}
PLA05_DESCRICAO = ("Classificação interna de triagem do Rigora, definida pela faixa de área colhida do grupo. "
                   "Não é uma avaliação da qualidade dos dados do IBGE. " + PLA05_SEM_ERRO)


def _num(value: float) -> int | float:
    """Inteiro quando o valor é inteiro (como o SIDRA publica); decimais legítimos ficam como estão."""
    v = float(value)
    return int(v) if v.is_integer() else v


def pla05_section(groups: list[dict], status: str) -> dict:
    """Seção `pla05_grupos` do relatório JSON: legenda dos níveis, resumo e todos os grupos.

    Usa os grupos já montados por pla05_groups (mesma ordem: ano, rendimento, área e produção),
    com os nomes dos dados validados nesta execução. Só tipos nativos do JSON (sem default=str).
    """
    lo, hi = config.PLA05_FAIXAS_HA
    niveis = [
        {"nivel": PLA05_CODIGO[PLA05_INFORMATIVO], "rotulo": PLA05_INFORMATIVO, "faixa": _faixa(PLA05_INFORMATIVO),
         "area_acima_de_ha": None, "area_ate_ha": _num(lo)},
        {"nivel": PLA05_CODIGO[PLA05_ATENCAO], "rotulo": PLA05_ATENCAO, "faixa": _faixa(PLA05_ATENCAO),
         "area_acima_de_ha": _num(lo), "area_ate_ha": _num(hi)},
        {"nivel": PLA05_CODIGO[PLA05_REVISAO], "rotulo": PLA05_REVISAO, "faixa": _faixa(PLA05_REVISAO),
         "area_acima_de_ha": _num(hi), "area_ate_ha": None},
    ]
    grupos = []
    for number, g in enumerate(groups, start=1):
        municipios = sorted(dict(g["membros"]).items())  # um item por código, em ordem de código IBGE
        grupos.append({
            "grupo": number,
            "ano": int(g["ano"]),
            "nivel": PLA05_CODIGO[g["nivel"]],
            "rendimento_kg_ha": _num(g["rendimento_kg_ha"]),
            "produtividade_sc_ha": float(g["rendimento_kg_ha"]) / config.SACA_KG,
            "area_colhida_ha": _num(g["area_colhida_ha"]),
            "producao_t": _num(g["producao_t"]),
            "total_municipios": len(municipios),
            "total_registros": len(g["membros"]),
            "municipios": [{"codigo_ibge": str(c), "municipio": None if n is None else str(n)} for c, n in municipios],
        })
    zeros = {codigo: 0 for codigo in PLA05_CODIGO.values()}
    por_grupo, por_registro = dict(zeros), dict(zeros)
    for g in grupos:
        por_grupo[g["nivel"]] += 1
        por_registro[g["nivel"]] += g["total_registros"]
    return {
        "versao": PLA05_SECAO_VERSAO,
        "regra": "PLA-05",
        "status_regra": status,
        "classificacao": {"descricao": PLA05_DESCRICAO, "faixas_ha": [_num(lo), _num(hi)], "niveis": niveis},
        "resumo": {
            "total_grupos": len(grupos),
            "total_registros": sum(g["total_registros"] for g in grupos),
            "total_municipios": len({m["codigo_ibge"] for g in grupos for m in g["municipios"]}),
            "grupos_por_nivel": por_grupo,
            "registros_por_nivel": por_registro,
        },
        "grupos": grupos,
    }


def _norm(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)).lower()


# =============================================================================
# Regras
# =============================================================================
def _integridade(df, rep, uf, years, localidades, sidra_names):
    r = rep.add(RuleResult("INT-01", "Chave única por município e ano", "integridade", ERRO, origem="3b"))
    dup = df[df.duplicated(KEY, keep=False)]
    if len(dup):
        r.hit(_keys(dup), f"{len(dup)} registros duplicados em (codigo_ibge, ano)")

    r = rep.add(RuleResult("INT-02", f"Código IBGE com 7 dígitos da UF {uf}", "integridade", ERRO, origem="3b"))
    bad = df[~df["codigo_ibge"].astype(str).str.fullmatch(rf"{uf}\d{{5}}")]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} códigos IBGE fora do padrão de 7 dígitos da UF {uf}")

    r = rep.add(RuleResult("INT-03", "Município com nome", "integridade", ERRO, origem="3b"))
    bad = df[df["municipio"].isna()]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} registros sem nome de município")

    r = rep.add(RuleResult("INT-04", f"Ano dentro de {min(years)}–{max(years)}", "integridade", ERRO, origem="3b"))
    bad = df[~df["ano"].isin(years)]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} registros com ano fora de {min(years)}–{max(years)}")

    r = rep.add(RuleResult("INT-05", "Código existe na lista oficial de municípios (IBGE/Localidades)", "integridade", AVISO))
    if localidades is None:
        r.status, r.detalhe = NAO_VERIFICADA, "lista de localidades não fornecida"
    else:
        bad = df[~df["codigo_ibge"].astype(str).isin(set(localidades["cod_ibge"].astype(str)))]
        if len(bad):
            r.hit(_keys(bad), f"{bad['codigo_ibge'].nunique()} código(s) do SIDRA ausentes da lista oficial de municípios")

    r = rep.add(RuleResult("INT-06", "Nome do município no SIDRA igual ao nome oficial", "integridade", AVISO))
    if localidades is None or sidra_names is None:
        r.status, r.detalhe = NAO_VERIFICADA, "nomes do SIDRA ou lista de localidades não fornecidos"
    else:
        off = {k: v for k, v in sidra_names.items()
               if k in set(localidades["cod_ibge"]) and _norm(v) != _norm(localidades.set_index("cod_ibge").at[k, "nome_oficial"])}
        if off:
            bad = df[df["codigo_ibge"].isin(off)]
            r.hit(_keys(bad), f"{len(off)} município(s) com nome diferente entre SIDRA e lista oficial: {sorted(off.values())[:3]}")


def _completude(df, rep, years, localidades):
    absent = {c: int(df[c].isna().sum()) for c in NUMERIC + ["regiao"]}
    zeros = {c: int((df[c] == 0).sum()) for c in ["area_colhida_ha", "producao_t"]}
    rep.stats["ausentes"] = absent
    rep.stats["zeros"] = zeros
    r = rep.add(RuleResult("COM-01", "Valores ausentes mantidos como NULL (contagem)", "completude", INFO, origem="3b"))
    if any(absent.values()):
        r.status = ACIONADA
        r.afetados = int(df[NUMERIC].isna().any(axis=1).sum())
        r.detalhe = "ausentes: " + ", ".join(f"{c}={n}" for c, n in absent.items() if n) + \
                    f"; zeros reais ('-' no SIDRA): " + ", ".join(f"{c}={n}" for c, n in zeros.items())

    r = rep.add(RuleResult("COM-02", "Registro incompleto (parte dos valores ausente)", "completude", AVISO))
    present = df[NUMERIC].notna().sum(axis=1)
    bad = df[(present > 0) & (present < len(NUMERIC))]
    if len(bad):
        per = {k: "ausente: " + ", ".join(c for c in NUMERIC if pd.isna(row[c])) for k, (_, row) in zip(_keys(bad), bad.iterrows())}
        r.hit(_keys(bad), f"{len(bad)} registros com apenas parte dos valores", per)

    r = rep.add(RuleResult("COM-03", f"Cobertura dos anos {min(years)}–{max(years)} por município", "completude", AVISO))
    has_data = df[df[NUMERIC].notna().any(axis=1)]
    got_years = set(df["ano"].unique())
    missing_years = sorted(set(years) - got_years)
    gaps = []
    for code, g in has_data.groupby("codigo_ibge"):
        lack = sorted(set(years) - set(g["ano"]))
        if lack and set(lack) != set(missing_years):
            gaps.extend((str(code), int(y)) for y in lack if y not in missing_years)
    rep.stats["anos_ausentes_na_coleta"] = missing_years
    if missing_years or gaps:
        msg = []
        if missing_years:
            msg.append(f"anos ausentes na coleta inteira: {missing_years} (coleta parcial; dados anteriores do banco são preservados)")
        if gaps:
            msg.append(f"{len({c for c, _ in gaps})} município(s) sem dado em algum ano")
        r.hit(gaps, "; ".join(msg), {k: "ano sem dado na coleta" for k in gaps})
        if missing_years and not gaps:
            r.ocorrencias = [(None, int(y), "ano ausente na coleta") for y in missing_years]
            r.afetados = len(missing_years)

    r = rep.add(RuleResult("COM-04", "Municípios sem produção de café em todo o período", "completude", INFO))
    produced = df[(df["area_colhida_ha"] > 0) | (df["producao_t"] > 0)]["codigo_ibge"].unique()
    none = sorted(set(df["codigo_ibge"]) - set(produced))
    if none:
        r.status, r.afetados = ACIONADA, len(none)
        r.detalhe = f"{len(none)} município(s) aparecem no SIDRA sem área colhida nem produção em nenhum ano"
        r.ocorrencias = [(c, None, "sem produção no período") for c in none]

    r = rep.add(RuleResult("COM-05", "Municípios da UF ausentes da resposta do SIDRA", "completude", INFO))
    if localidades is None:
        r.status, r.detalhe = NAO_VERIFICADA, "lista de localidades não fornecida"
        out = []
    else:
        out = sorted(set(localidades["cod_ibge"]) - set(df["codigo_ibge"]))
        if out:
            r.status, r.afetados = ACIONADA, len(out)
            r.detalhe = f"{len(out)} município(s) da UF sem nenhum registro de café arábica no SIDRA"
            r.ocorrencias = [(c, None, "sem registro no SIDRA") for c in out]
    rep.stats["sem_cobertura"] = {"sem_producao_no_periodo": len(none), "ausentes_no_sidra": len(out)}

    r = rep.add(RuleResult("COM-06", "Rendimento igual a zero (ausência deveria ser NULL)", "completude", ERRO, origem="3b"))
    bad = df[df["rendimento_kg_ha"] == 0]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} rendimentos iguais a zero: ausência deveria ser NULL")

    r = rep.add(RuleResult("COM-07", "Rendimento informado sem área colhida", "completude", ERRO, origem="3b"))
    bad = df[df["rendimento_kg_ha"].notna() & ~(df["area_colhida_ha"] > 0)]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} rendimentos informados sem área colhida")

    r = rep.add(RuleResult("COM-08", "Produção informada com área colhida zero", "completude", ERRO))
    bad = df[(df["producao_t"] > 0) & (df["area_colhida_ha"] == 0)]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} registros com produção e área colhida zero (impossível)")

    r = rep.add(RuleResult("COM-09", "Área colhida sem produção", "completude", AVISO))
    bad = df[(df["area_colhida_ha"] > 0) & (df["producao_t"] == 0)]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} registros com área colhida e produção zero (perda total ou erro de origem)")


def _consistencia(df, rep, tolerance, raw_rows):
    full = df[df[NUMERIC].notna().all(axis=1) & (df["area_colhida_ha"] > 0) & (df["rendimento_kg_ha"] > 0)].copy()
    full["calc"] = full["producao_t"] * 1000 / full["area_colhida_ha"]
    full["ratio"] = full["rendimento_kg_ha"] / full["calc"]
    dev = (full["calc"] - full["rendimento_kg_ha"]).abs() / full["rendimento_kg_ha"]
    rep.stats["consistencia"] = {"verificados": int(len(full)),
                                 "desvio_maximo_pct": round(float(dev.max() * 100), 4) if len(dev) else None}

    unit_mask = pd.Series(False, index=full.index)
    for f in config.UNIT_FACTORS:
        for ratio in (f, 1 / f):
            unit_mask |= (full["ratio"] / ratio - 1).abs() <= config.UNIT_FACTOR_TOLERANCE

    r = rep.add(RuleResult("CON-01", "Rendimento ≈ produção ÷ área colhida", "consistencia", AVISO, origem="3b"))
    off = full[(dev > tolerance) & ~unit_mask]
    if len(off):
        per = {k: f"publicado {row.rendimento_kg_ha:.0f} kg/ha; calculado {row.calc:.0f} kg/ha"
               for k, row in zip(_keys(off), off.itertuples())}
        r.hit(_keys(off), f"{len(off)} registros com rendimento ≠ produção ÷ área (> {tolerance:.0%})", per)

    r = rep.add(RuleResult("CON-02", "Possível erro de unidade entre rendimento, produção e área", "consistencia", ERRO))
    bad = full[unit_mask]
    if len(bad):
        per = {k: f"rendimento/calculado = {row.ratio:.3g} (fator típico de troca de unidade)"
               for k, row in zip(_keys(bad), bad.itertuples())}
        r.hit(_keys(bad), f"{len(bad)} registros com diferença próxima de ×60 ou ×1000 (troca de unidade)", per)

    r = rep.add(RuleResult("CON-03", "Unidade declarada pela fonte é a esperada", "consistencia", ERRO))
    if raw_rows is None or not any("MN" in row for row in raw_rows):
        r.status, r.detalhe = NAO_VERIFICADA, "a resposta não trouxe o campo de unidade (MN)"
    else:
        wrong = {}
        for row in raw_rows:
            want = EXPECTED_UNITS.get(row.get("D2C"))
            unit = row.get("MN")
            if want and unit is not None and not all(w in _norm(unit) for w in want):
                wrong[(str(row["D1C"]), int(row["D3C"]))] = f"variável {row['D2C']}: unidade '{unit}'"
        if wrong:
            r.hit(list(wrong), f"{len(wrong)} registros com unidade diferente da esperada", wrong)

    r = rep.add(RuleResult("CON-05", "Município muito acima/abaixo da média dos demais municípios da UF no ano", "consistencia", AVISO))
    valid = df[(df["area_colhida_ha"] > 0) & (df["producao_t"] > 0)]
    if valid.empty:
        r.status, r.detalhe = NAO_VERIFICADA, "sem registros com área e produção para calcular a média estadual"
    else:
        # Média dos DEMAIS municípios no ano (deixa o próprio registro de fora, para que
        # um valor extremo não puxe a média de comparação e se esconda).
        sums = valid.groupby("ano")[["producao_t", "area_colhida_ha"]].sum()
        sub = df[df["rendimento_kg_ha"].notna()].copy()
        own = sub["area_colhida_ha"].gt(0) & sub["producao_t"].gt(0)
        prod_others = sub["ano"].map(sums["producao_t"]) - sub["producao_t"].where(own, 0)
        area_others = sub["ano"].map(sums["area_colhida_ha"]) - sub["area_colhida_ha"].where(own, 0)
        sub["ratio_uf"] = sub["rendimento_kg_ha"] / (prod_others * 1000 / area_others.where(area_others > 0))
        lo, hi = config.STATE_RATIO_RANGE
        bad = sub[(sub["ratio_uf"] < lo) | (sub["ratio_uf"] > hi)]
        if len(bad):
            per = {k: f"{row.ratio_uf:.2f}× a média dos demais municípios" for k, row in zip(_keys(bad), bad.itertuples())}
            r.hit(_keys(bad), f"{len(bad)} registros fora de {lo}×–{hi}× a média dos demais municípios no ano", per)


def _plausibilidade(df, rep):
    r = rep.add(RuleResult("PLA-01", "Valores negativos", "plausibilidade", ERRO, origem="3b"))
    neg = df[(df[NUMERIC] < 0).any(axis=1)]
    if len(neg):
        cols = [c for c in NUMERIC if (df[c] < 0).any()]
        r.hit(_keys(neg), f"{len(neg)} registros com valores negativos em {', '.join(cols)}")

    lo, hi = config.PLAUSIBLE_SC_HA
    r = rep.add(RuleResult("PLA-02", f"Produtividade entre {lo:.0f} e {hi:.0f} sc/ha", "plausibilidade", AVISO))
    sc = df["rendimento_kg_ha"] / config.SACA_KG
    bad = df[sc.notna() & ((sc < lo) | (sc > hi))]
    if len(bad):
        per = {k: f"{v / config.SACA_KG:.1f} sc/ha" for k, v in zip(_keys(bad), bad["rendimento_kg_ha"])}
        r.hit(_keys(bad), f"{len(bad)} registros com produtividade fora de {lo:.0f}–{hi:.0f} sc/ha", per)

    r = rep.add(RuleResult("PLA-03", f"Área colhida até {config.MAX_AREA_HA_MUNICIPIO:,} ha por município".replace(",", "."), "plausibilidade", AVISO))
    bad = df[df["area_colhida_ha"] > config.MAX_AREA_HA_MUNICIPIO]
    if len(bad):
        r.hit(_keys(bad), f"{len(bad)} registros com área colhida acima do limite plausível")

    r = rep.add(RuleResult("PLA-04", f"Variação anual de rendimento até {config.MAX_YOY_RATIO:g}×", "plausibilidade", AVISO))
    s = df[df["rendimento_kg_ha"].notna()].sort_values(KEY)
    s = s.assign(prev=s.groupby("codigo_ibge")["rendimento_kg_ha"].shift(), prev_ano=s.groupby("codigo_ibge")["ano"].shift())
    s = s[s["prev"].notna() & (s["ano"] - s["prev_ano"] == 1)]
    ratio = s["rendimento_kg_ha"] / s["prev"]
    bad = s[(ratio > config.MAX_YOY_RATIO) | (ratio < 1 / config.MAX_YOY_RATIO)]
    if len(bad):
        per = {k: f"{p:.0f} → {c:.0f} kg/ha ({c / p:.2f}×)" for k, p, c in zip(_keys(bad), bad["prev"], bad["rendimento_kg_ha"])}
        r.hit(_keys(bad), f"{len(bad)} variações anuais acima de {config.MAX_YOY_RATIO:g}× (a bienalidade do café explica até ~2,6× nos dados oficiais)", per)

    r = rep.add(RuleResult("PLA-05", "Valores idênticos em municípios diferentes no mesmo ano", "plausibilidade", AVISO))
    full = df[df[NUMERIC].notna().all(axis=1) & (df["area_colhida_ha"] > 0)]
    same = full[full.duplicated(["ano"] + NUMERIC, keep=False)]
    groups = []
    if len(same):
        groups = pla05_groups(same)
        r.hit(_keys(same), pla05_summary(groups), pla05_messages(groups))
    rep.secoes["pla05_grupos"] = pla05_section(groups, r.status)  # etapa 2: só no arquivo JSON


def validate_pam(df: pd.DataFrame, *, years: list[int] = config.YEARS, uf: str = config.UF_MG,
                 tolerance: float = config.CONSISTENCY_TOLERANCE, localidades: pd.DataFrame | None = None,
                 sidra_names: dict | None = None, raw_rows: list[dict] | None = None) -> ValidationReport:
    """Executa todas as regras sobre o DataFrame no formato do banco (to_storage_frame)."""
    rep = ValidationReport()
    rep.stats["registros"] = int(len(df))
    if df.empty:
        r = rep.add(RuleResult("INT-00", "Há registros para gravar", "integridade", ERRO, origem="3b"))
        r.status, r.detalhe = ACIONADA, "nenhum registro para gravar"
        rep.secoes["pla05_grupos"] = pla05_section([], NAO_VERIFICADA)  # a PLA-05 não chegou a ser executada
        return rep
    df = df.copy()
    df["codigo_ibge"] = df["codigo_ibge"].astype(str)
    if localidades is not None:
        localidades = localidades.assign(cod_ibge=localidades["cod_ibge"].astype(str))
    _integridade(df, rep, uf, years, localidades, sidra_names)
    _completude(df, rep, years, localidades)
    _consistencia(df, rep, tolerance, raw_rows)
    _plausibilidade(df, rep)
    return rep


# --- Regras executadas depois da gravação, dentro da mesma transação ---------------
CAMPOS_PAM = ("rendimento_kg_ha", "area_colhida_ha", "producao_t")


def preservation_rule(conn, df) -> RuleResult | None:
    """INT-08 (1.0): a nova coleta não pode deixar ausente um valor válido já gravado.

    O SIDRA responde em grade (município x ano) e o tratamento completa a grade com ausentes; uma
    resposta parcial apagaria valores válidos pelo UPSERT. Esta regra compara, ANTES da gravação e na
    mesma transação, cada registro do banco com o que a nova coleta traz: valor gravado que viria
    ausente aciona a regra (erro) e nada é gravado. Registro que não vem na resposta (outro período,
    município fora da resposta) não é tocado pelo UPSERT e continua como estava (comportamento da 3b).
    Valores revisados (número -> outro número) passam: a revisão é conferida depois contra a
    referência B5. Na primeira carga (banco sem registros) não há o que comparar: a regra não entra.
    """
    import pandas as pd
    existentes = conn.execute("SELECT codigo_ibge, ano, " + ", ".join(CAMPOS_PAM) + " FROM pam_municipio_ano").fetchall()
    if not existentes:
        return None
    r = RuleResult("INT-08", "Nova coleta não apaga valor válido já gravado", "integridade", ERRO, origem="1.0")
    novos = {(str(x.codigo_ibge), int(x.ano)): x for x in df.itertuples(index=False)}
    chaves, detalhes = [], {}
    for cod, ano, *vals in existentes:
        k = (str(cod), int(ano))
        if all(v is None for v in vals):
            continue
        n = novos.get(k)
        if n is None:                                   # fora da resposta: preservado como está
            continue
        sumiram = [f"{c}={v:g}" for c, v in zip(CAMPOS_PAM, vals) if v is not None and pd.isna(getattr(n, c))]
        if sumiram:
            chaves.append(k)
            detalhes[k] = "valor gravado viria ausente: " + ", ".join(sumiram)
    if chaves:
        r.hit(chaves, f"{len(chaves)} registro(s) com valor válido que a nova coleta deixaria ausente "
                      "(resposta parcial ou incompleta): nada foi gravado; os dados anteriores foram mantidos", detalhes)
    return r


def post_load_rules(conn, exec_id: int) -> list[RuleResult]:
    out = []
    r = RuleResult("INT-07", "Integridade no banco: chaves estrangeiras, duplicidade e execução responsável", "integridade", ERRO, origem="3b")
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    dup = conn.execute("SELECT COUNT(*) FROM (SELECT codigo_ibge, ano FROM pam_municipio_ano GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0]
    orphan = conn.execute("SELECT COUNT(*) FROM pam_municipio_ano p LEFT JOIN execucao e ON e.id = p.execucao_id "
                          "WHERE e.id IS NULL OR (e.status NOT IN ('sucesso') AND p.execucao_id <> ?)", (exec_id,)).fetchone()[0]
    if fk or dup or orphan:
        r.status, r.afetados = ACIONADA, len(fk) + dup + orphan
        r.detalhe = f"chaves estrangeiras quebradas={len(fk)}, duplicados={dup}, linhas sem execução válida={orphan}"
    out.append(r)

    r = RuleResult("CON-04", "Produtividade (sc/ha) = rendimento (kg/ha) ÷ 60 no banco", "consistencia", ERRO)
    bad = conn.execute("SELECT codigo_ibge, ano FROM pam_municipio_ano WHERE rendimento_kg_ha IS NOT NULL "
                       "AND abs(produtividade_sc_ha - rendimento_kg_ha / 60.0) > 1e-9").fetchall()
    if bad:
        r.hit([(b[0], b[1]) for b in bad], f"{len(bad)} registros com produtividade incoerente")
    out.append(r)
    return out
