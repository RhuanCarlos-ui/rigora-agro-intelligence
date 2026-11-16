"""Tratamento da previsão do INMET com pandas.

Um registro por (data prevista, período). Período = "manha" | "tarde" | "noite" quando
a fonte divide o dia, ou "dia" quando traz um único registro diário.

- Datas: "dd/mm/aaaa" -> ISO "aaaa-mm-dd". Data inválida vira NaT e é apontada pela
  validação (MET-01); não é descartada em silêncio nem "consertada".
- Unidades: temperatura em °C e umidade relativa em %, como publicadas (sem conversão).
- Ausentes: campo ausente ou não numérico vira NaN (NULL no banco), nunca zero.
- Horizonte: dias entre a data prevista e a primeira data da previsão (0 = dia da emissão).
"""
from __future__ import annotations

import math

import pandas as pd

from . import config

COLUMNS = [
    "codigo_ibge", "municipio", "data_previsao", "periodo", "horizonte_dias",
    "temp_min_c", "temp_max_c", "umidade_min_pct", "umidade_max_pct",
    "vento_intensidade", "vento_direcao", "resumo",
]


def _num(v) -> float:
    if v is None or isinstance(v, bool):
        return math.nan
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return math.nan


def _text(v):
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def inmet_to_frame(city: dict, codigo_ibge: str = config.INMET_MUNICIPIO) -> pd.DataFrame:
    """Bloco do município (saída de validate_inmet_payload) -> DataFrame padronizado."""
    rows = []
    for raw_date, entry in city.items():
        parts = [(p, entry[p]) for p in config.INMET_PERIODOS if isinstance(entry.get(p), dict)]
        if not parts:
            parts = [("dia", entry)]
        for periodo, rec in parts:
            rows.append({
                "codigo_ibge": codigo_ibge,
                "municipio": _text(rec.get("entidade")),
                "data_bruta": raw_date,
                "periodo": periodo,
                "temp_min_c": _num(rec.get("temp_min")),
                "temp_max_c": _num(rec.get("temp_max")),
                "umidade_min_pct": _num(rec.get("umidade_min")),
                "umidade_max_pct": _num(rec.get("umidade_max")),
                "vento_intensidade": _text(rec.get("int_vento")),
                "vento_direcao": _text(rec.get("dir_vento")),
                "resumo": _text(rec.get("resumo")),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS + ["data_bruta"])
    parsed = pd.to_datetime(df["data_bruta"], format="%d/%m/%Y", errors="coerce")
    df["data_previsao"] = parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), None)
    first = parsed.min()
    df["horizonte_dias"] = (parsed - first).dt.days.astype("Int64") if pd.notna(first) else pd.NA
    order = {"manha": 0, "tarde": 1, "noite": 2, "dia": 3}
    df = df.assign(_o=df["periodo"].map(order), _d=parsed).sort_values(["_d", "_o"]).drop(columns=["_o", "_d"])
    return df[COLUMNS + ["data_bruta"]].reset_index(drop=True)
