"""Tratamento da PAM (IBGE/SIDRA) com pandas.

Entrada: respostas brutas salvas por extract_sidra.py.
Saída:   tabela limpa (um registro por município e ano) e totais de Minas Gerais.

Uso:
    python -m pipeline.transform                      # usa as coletas mais recentes de data/raw/
    python -m pipeline.transform --pam ARQ --localidades ARQ
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

import pandas as pd

from . import config
from .extract_sidra import validate_localidades_payload, validate_pam_payload

log = logging.getLogger(__name__)

# Sinais especiais do SIDRA (https://sidra.ibge.gov.br, notas da tabela):
#   "-"   zero absoluto, não resultante de arredondamento
#   ".."  não se aplica dado numérico
#   "..." dado numérico não disponível
#   "X"   dado omitido para não identificar o informante
SIDRA_ZERO = "-"

PAM_COLUMNS = [
    "cod_ibge", "municipio", "mesorregiao", "ano",
    "rendimento_kg_ha", "rendimento_sc_ha", "area_colhida_ha", "producao_t",
]


def parse_sidra_value(raw: object) -> float:
    """Converte o valor textual do SIDRA em número; sinais especiais viram NaN ou 0."""
    if raw is None:
        return math.nan
    text = str(raw).strip()
    if text == SIDRA_ZERO:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return math.nan


def _clean_name(name: str) -> str:
    """'Patrocínio - MG' -> 'Patrocínio'."""
    return name.rsplit(" - ", 1)[0].strip() if isinstance(name, str) else name


def pam_rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Linhas do SIDRA (formato longo) -> DataFrame largo: município × ano × variáveis."""
    long = pd.DataFrame(rows)
    missing = {"D1C", "D2C", "D3C", "V"} - set(long.columns)
    if missing:
        raise ValueError(f"colunas ausentes na resposta do SIDRA: {sorted(missing)}")
    long = long[long["D2C"].isin(config.VARIABLES)].copy()
    long["valor"] = long["V"].map(parse_sidra_value)
    long["variavel"] = long["D2C"].map(config.VARIABLES)
    long["municipio"] = long["D1N"].map(_clean_name) if "D1N" in long.columns else pd.NA

    dup = long.duplicated(subset=["D1C", "D3C", "variavel"], keep=False)
    if dup.any():
        raise ValueError(f"{int(dup.sum())} linhas duplicadas para o mesmo município, ano e variável")

    wide = (
        long.pivot_table(index=["D1C", "D3C"], columns="variavel", values="valor", aggfunc="first", dropna=False)
        .reset_index()
        .rename(columns={"D1C": "cod_ibge", "D3C": "ano"})
    )
    wide.columns.name = None
    names = long.drop_duplicates("D1C").set_index("D1C")["municipio"]
    wide["municipio"] = wide["cod_ibge"].map(names)
    for col in config.VARIABLES.values():
        if col not in wide.columns:
            wide[col] = math.nan

    wide["cod_ibge"] = wide["cod_ibge"].astype(str)
    wide["ano"] = wide["ano"].astype(int)
    # Rendimento sem área colhida não existe: um "zero" aqui é ausência de produção, não produtividade nula.
    no_area = wide["area_colhida_ha"].fillna(0) <= 0
    wide.loc[no_area | (wide["rendimento_kg_ha"] <= 0), "rendimento_kg_ha"] = math.nan
    wide["rendimento_sc_ha"] = wide["rendimento_kg_ha"] / config.SACA_KG
    wide["mesorregiao"] = pd.NA
    return wide[PAM_COLUMNS].sort_values(["cod_ibge", "ano"]).reset_index(drop=True)


def localidades_to_frame(payload: list[dict]) -> pd.DataFrame:
    """Municípios do IBGE -> código, nome e mesorregião."""
    records = []
    for m in payload:
        micro = m.get("microrregiao") or {}
        meso = micro.get("mesorregiao") or {}
        records.append({"cod_ibge": str(m["id"]), "nome_oficial": m.get("nome"), "mesorregiao": meso.get("nome")})
    return pd.DataFrame(records)


def attach_localidades(pam: pd.DataFrame, loc: pd.DataFrame) -> pd.DataFrame:
    out = pam.drop(columns=["mesorregiao"]).merge(loc, on="cod_ibge", how="left")
    out["municipio"] = out["nome_oficial"].fillna(out["municipio"])
    return out.drop(columns=["nome_oficial"])[PAM_COLUMNS]


def mg_totals(pam: pd.DataFrame) -> pd.DataFrame:
    """Totais estaduais por ano. Rendimento de MG = produção total ÷ área colhida total.

    Considera só municípios com área e produção maiores que zero no ano (mesmo
    critério da cópia de 14/11/2026).
    """
    valid = pam[(pam["area_colhida_ha"] > 0) & (pam["producao_t"] > 0)]
    tot = valid.groupby("ano").agg(
        area_colhida_ha=("area_colhida_ha", "sum"),
        producao_t=("producao_t", "sum"),
        municipios=("cod_ibge", "nunique"),
    ).reset_index()
    tot["rendimento_kg_ha"] = (tot["producao_t"] * 1000 / tot["area_colhida_ha"]).round()
    tot["rendimento_sc_ha"] = tot["rendimento_kg_ha"] / config.SACA_KG
    return tot


def select_top(pam: pd.DataFrame, year: int, n: int = config.TOP_N_MUNICIPIOS) -> list[str]:
    """Códigos dos n municípios com maior área colhida no ano de referência."""
    ref = pam[(pam["ano"] == year) & (pam["area_colhida_ha"] > 0)]
    return ref.sort_values("area_colhida_ha", ascending=False)["cod_ibge"].head(n).tolist()


def _latest(pattern: str) -> Path:
    files = sorted(p for p in config.RAW_DIR.glob(pattern) if not p.name.endswith(".meta.json"))
    if not files:
        raise FileNotFoundError(f"nenhuma coleta encontrada em {config.RAW_DIR} ({pattern}). Rode antes: python -m pipeline.extract_sidra")
    return files[-1]


def run(pam_path: Path, loc_path: Path, out_dir: Path = config.PROCESSED_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    pam = pam_rows_to_frame(validate_pam_payload(json.loads(pam_path.read_text(encoding="utf-8"))))
    loc = localidades_to_frame(validate_localidades_payload(json.loads(loc_path.read_text(encoding="utf-8"))))
    pam = attach_localidades(pam, loc)
    tot = mg_totals(pam)
    out_dir.mkdir(parents=True, exist_ok=True)
    pam.to_csv(out_dir / "pam_mg_arabica.csv", index=False, encoding="utf-8")
    tot.to_csv(out_dir / "pam_mg_totais.csv", index=False, encoding="utf-8")
    return pam, tot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pam", type=Path, help="JSON bruto da PAM (padrão: coleta mais recente)")
    parser.add_argument("--localidades", type=Path, help="JSON bruto de localidades (padrão: coleta mais recente)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pam_path = args.pam or _latest("sidra_pam_1613_mg_arabica_*.json")
    loc_path = args.localidades or _latest("ibge_localidades_mg_*.json")
    pam, tot = run(pam_path, loc_path)
    last = int(pam["ano"].max())
    pat = pam[(pam["cod_ibge"] == config.PATROCINIO) & (pam["ano"] == last)]
    log.info("%d municípios × %d anos; MG %d: %.1f sc/ha", pam["cod_ibge"].nunique(), pam["ano"].nunique(), last,
             float(tot.loc[tot["ano"] == last, "rendimento_sc_ha"].iloc[0]))
    if not pat.empty:
        log.info("Patrocínio %d: %.1f sc/ha", last, float(pat["rendimento_sc_ha"].iloc[0]))
    print(f"gravado em {config.PROCESSED_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
