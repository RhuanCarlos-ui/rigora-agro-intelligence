"""Regras de qualidade da previsão do INMET (etapa 3d).

Mesma estrutura das regras da PAM (RuleResult/ValidationReport): erro impede a gravação
da previsão, aviso grava e anota, info só conta. Nenhum valor é corrigido.

Exceção justificada: registros EXATAMENTE iguais (mesma data, período e todos os campos)
são mantidos uma vez só (MET-03, aviso com a contagem): não há informação a perder.
Registros com a mesma data/período e valores DIFERENTES são erro (MET-02).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from . import config
from .validate import ACIONADA, AVISO, ERRO, INFO, NAO_VERIFICADA, RuleResult, ValidationReport, _norm

KEY = ["data_previsao", "periodo"]
VALUES = ["temp_min_c", "temp_max_c", "umidade_min_pct", "umidade_max_pct", "vento_intensidade", "vento_direcao", "resumo"]
PRECIP_HINTS = ("precip", "chuva", "mm")


def _k(df, detalhe=""):
    """Ocorrências por registro: (código IBGE, ano=NULL, "<data> <período>[: detalhe]")."""
    return [(config.INMET_MUNICIPIO, None, f"{d or '?'} {p}" + (f": {detalhe}" if detalhe else ""))
            for d, p in zip(df["data_bruta"], df["periodo"])]


class InmetReport(ValidationReport):
    """Mesmo relatório da PAM; registros identificados por data + período."""

    def resumo(self) -> dict:
        def recs(sev):
            return {o[2].split(":")[0] for r in self.regras if r.severidade == sev and r.status == ACIONADA
                    for o in r.ocorrencias if o[2]}
        n = self.stats.get("registros", 0)
        rej = recs(ERRO)
        return {
            "registros_analisados": n, "registros_rejeitados": len(rej), "registros_aprovados": n - len(rej),
            "registros_com_aviso": len(recs(AVISO)), "erros_impeditivos": len(self.errors), "avisos": len(self.warnings),
            "campos_ausentes": self.stats.get("ausentes", {}), "dias_previstos": self.stats.get("dias_previstos"),
            "idade_horas": self.stats.get("idade_horas"),
            "regras_executadas": sum(r.status != NAO_VERIFICADA for r in self.regras),
            "regras_nao_verificadas": [r.regra for r in self.regras if r.status == NAO_VERIFICADA],
            "regras_acionadas": [f"{r.regra} ({r.severidade})" for r in self.regras if r.status == ACIONADA],
            "resultado": "aprovado" if self.ok else "reprovado",
        }


def repeated_json_keys(raw: bytes) -> list[str]:
    """Datas repetidas no JSON bruto (json.loads guardaria só a última, em silêncio)."""
    found = []

    def hook(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                found.append(k)
            seen.add(k)
        return dict(pairs)

    json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    return found


def dedupe_identical(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    cols = ["data_bruta", "periodo"] + VALUES
    dup = df.duplicated(cols, keep="first")
    return df[~dup].reset_index(drop=True), int(dup.sum())


def validate_inmet(df: pd.DataFrame, *, collected_at: str, city: dict, raw: bytes | None = None,
                   identical_removed: int = 0, now: datetime | None = None,
                   expected_days: int = config.INMET_HORIZONTE_ESPERADO_DIAS) -> ValidationReport:
    rep = InmetReport()
    rep.stats["registros"] = int(len(df))
    now = now or datetime.now(timezone.utc)
    coleta = datetime.fromisoformat(collected_at)

    def add(rid, nome, dim, sev):
        return rep.add(RuleResult(rid, nome, dim, sev, origem="3d"))

    # --- Integridade --------------------------------------------------------------------
    r = add("MET-01", "Data de previsão válida (identificação temporal)", "integridade", ERRO)
    bad = df[df["data_previsao"].isna()]
    if len(bad):
        r.hit([], f"{len(bad)} registro(s) com data ausente ou inválida: {sorted(set(bad['data_bruta'].astype(str)))[:3]}")
        r.ocorrencias, r.afetados = _k(bad, "data inválida"), len(bad)

    r = add("MET-02", "Uma previsão por data e período", "integridade", ERRO)
    repeated = repeated_json_keys(raw) if raw is not None else []
    conflict = df[df["data_previsao"].notna() & df.duplicated(KEY, keep=False)]
    if repeated or len(conflict):
        occ = [(config.INMET_MUNICIPIO, None, f"{d} json: data repetida no JSON bruto") for d in repeated] + _k(conflict, "valores diferentes")
        r.hit([], f"{len(repeated)} data(s) repetida(s) no JSON bruto; {len(conflict)} registro(s) com mesma data/período e valores diferentes")
        r.ocorrencias, r.afetados = occ, len(occ)

    r = add("MET-03", "Registros idênticos repetidos (mantidos uma vez)", "integridade", AVISO)
    if identical_removed:
        r.status, r.afetados = ACIONADA, identical_removed
        r.detalhe = f"{identical_removed} registro(s) exatamente iguais a outro foram mantidos uma única vez"

    r = add("MET-12", f"Município da resposta é {config.INMET_MUNICIPIO_NOME}", "integridade", AVISO)
    names = {n for n in df["municipio"].dropna()} if "municipio" in df else set()
    if not names:
        r.status, r.detalhe = NAO_VERIFICADA, "a resposta não trouxe o nome do município (campo 'entidade')"
    elif any(_norm(n) != _norm(config.INMET_MUNICIPIO_NOME) for n in names):
        r.hit([], f"nome(s) na resposta: {sorted(names)}")
        r.afetados = len(names)

    # --- Consistência e plausibilidade -----------------------------------------------------
    r = add("MET-04", "Temperatura mínima ≤ máxima", "consistencia", ERRO)
    bad = df[df["temp_min_c"] > df["temp_max_c"]]
    if len(bad):
        r.hit([], f"{len(bad)} registro(s) com mínima acima da máxima")
        r.ocorrencias, r.afetados = _k(bad), len(bad)

    lo, hi = config.INMET_TEMP_PLAUSIVEL_C
    r = add("MET-05", f"Temperatura entre {lo:g} e {hi:g} °C", "plausibilidade", AVISO)
    t = df[["temp_min_c", "temp_max_c"]]
    bad = df[((t < lo) | (t > hi)).any(axis=1)]
    if len(bad):
        r.hit([], f"{len(bad)} registro(s) com temperatura fora de {lo:g}–{hi:g} °C")
        r.ocorrencias, r.afetados = _k(bad), len(bad)

    r = add("MET-06", "Umidade relativa entre 0 e 100%", "consistencia", ERRO)
    u = df[["umidade_min_pct", "umidade_max_pct"]]
    bad = df[((u < 0) | (u > 100)).any(axis=1)]
    if len(bad):
        r.hit([], f"{len(bad)} registro(s) com umidade fora de 0–100%")
        r.ocorrencias, r.afetados = _k(bad), len(bad)

    r = add("MET-07", "Umidade mínima ≤ máxima", "consistencia", ERRO)
    bad = df[df["umidade_min_pct"] > df["umidade_max_pct"]]
    if len(bad):
        r.hit([], f"{len(bad)} registro(s) com umidade mínima acima da máxima")
        r.ocorrencias, r.afetados = _k(bad), len(bad)

    # --- Completude, cobertura e atualidade -------------------------------------------------
    numeric = ["temp_min_c", "temp_max_c", "umidade_min_pct", "umidade_max_pct"]
    absent = {c: int(df[c].isna().sum()) for c in numeric}
    rep.stats["ausentes"] = absent
    r = add("MET-08", "Temperatura e umidade presentes", "completude", AVISO)
    bad = df[df[numeric].isna().any(axis=1)]
    if len(bad):
        r.hit([], "ausentes mantidos como NULL: " + ", ".join(f"{c}={n}" for c, n in absent.items() if n))
        r.ocorrencias, r.afetados = _k(bad), len(bad)

    r = add("MET-09", "Precipitação não negativa", "completude", ERRO)
    precip = []
    for date, entry in city.items():
        recs = [entry[p] for p in config.INMET_PERIODOS if isinstance(entry.get(p), dict)] or [entry]
        for rec in recs:
            precip += [(date, k, v) for k, v in rec.items() if any(h in k.lower() for h in PRECIP_HINTS)]
    if not precip:
        r.status, r.detalhe = NAO_VERIFICADA, "a fonte não fornece precipitação (nem volume nem probabilidade)"
    else:
        neg = [(d, k, v) for d, k, v in precip if isinstance(v, (int, float)) and v < 0]
        if neg:
            r.hit([], f"{len(neg)} valor(es) de precipitação negativos")
            r.ocorrencias, r.afetados = [(None, None, f"{d} {k}={v}") for d, k, v in neg], len(neg)

    r = add("MET-10", f"Previsão atual (coletada há até {config.INMET_MAX_IDADE_HORAS} h e iniciando no dia da coleta)", "completude", AVISO)
    idade_h = (now - coleta).total_seconds() / 3600
    first = pd.to_datetime(df["data_previsao"], errors="coerce").min()
    motivos = []
    if idade_h > config.INMET_MAX_IDADE_HORAS:
        motivos.append(f"coletada há {idade_h:.0f} h")
    if pd.notna(first) and (coleta.date() - first.date()).days > 1:
        motivos.append(f"a previsão começa em {first.date()}, antes da data da coleta ({coleta.date()})")
    rep.stats["idade_horas"] = round(idade_h, 1)
    if motivos:
        r.status, r.afetados, r.detalhe = ACIONADA, len(df), "; ".join(motivos) + " (dado histórico: gravado, mas não deve ser exibido como atual)"

    r = add("MET-11", f"Cobertura de {expected_days} dias consecutivos", "completude", AVISO)
    dates = sorted(pd.to_datetime(df["data_previsao"].dropna().unique()))
    gaps = [f"{a.date()}→{b.date()}" for a, b in zip(dates, dates[1:]) if (b - a).days != 1]
    rep.stats["dias_previstos"] = len(dates)
    if len(dates) < expected_days or gaps:
        msg = []
        if len(dates) < expected_days:
            msg.append(f"{len(dates)} de {expected_days} dias esperados")
        if gaps:
            msg.append(f"lacunas entre {gaps}")
        r.status, r.afetados, r.detalhe = ACIONADA, max(expected_days - len(dates), len(gaps)), "; ".join(msg)

    r = add("MET-13", "Campos que a fonte não fornece", "completude", INFO)
    r.status, r.detalhe = ACIONADA, "precipitação (mm e probabilidade) e velocidade do vento em número não existem na resposta; vento só como intensidade descrita"
    return rep


def post_load_rules_inmet(conn, exec_id: int) -> list[RuleResult]:
    r = RuleResult("MET-14", "Integridade no banco: chaves estrangeiras e chave única da previsão", "integridade", ERRO, origem="3d")
    fk = conn.execute("PRAGMA foreign_key_check(inmet_previsao)").fetchall()
    dup = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM inmet_previsao GROUP BY codigo_ibge, data_coleta, data_previsao, periodo HAVING COUNT(*) > 1)").fetchone()[0]
    if fk or dup:
        r.status, r.afetados, r.detalhe = ACIONADA, len(fk) + dup, f"chaves estrangeiras quebradas={len(fk)}, duplicados={dup}"
    return [r]
