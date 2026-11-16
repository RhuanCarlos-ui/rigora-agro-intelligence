"""Testes da etapa 3a: extração e tratamento da PAM (IBGE/SIDRA).

Rodar:  python -m pytest tests/ -q
Não acessam a internet: usam fixtures no formato real do SIDRA (ver tests/fixtures/README.md).
"""
import json
import math
import re
import urllib.error
from pathlib import Path

import pytest

from pipeline import config
from pipeline.extract_sidra import pam_url, save_raw, validate_pam_payload
from pipeline.http import SourceError, fetch_json
from pipeline.transform import (
    attach_localidades, localidades_to_frame, mg_totals, pam_rows_to_frame, parse_sidra_value, select_top,
)

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def payload():
    return json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pam(payload):
    loc = localidades_to_frame(json.loads((FIX / "localidades_sample.json").read_text(encoding="utf-8")))
    return attach_localidades(pam_rows_to_frame(validate_pam_payload(payload)), loc)


@pytest.fixture(scope="module")
def snapshot():
    """Cópia oficial atual usada pelo protótipo (referência de regressão)."""
    src = (ROOT / "js" / "snapshots.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"sidra: (\{.*?\}),\n  inmet:", src, re.S).group(1))


# --- URL e validação ---------------------------------------------------------
def test_url_reproduz_a_consulta_da_copia_oficial(snapshot):
    assert pam_url() == snapshot["query"]


def test_rejeita_resposta_vazia():
    with pytest.raises(SourceError):
        validate_pam_payload([])


def test_rejeita_cabecalho_inesperado(payload):
    broken = [dict(payload[0], D3C="Mês (Código)")] + payload[1:]
    with pytest.raises(SourceError, match="D3C"):
        validate_pam_payload(broken)


# --- Sinais especiais do SIDRA -----------------------------------------------
@pytest.mark.parametrize("raw, expected", [("1110", 1110.0), ("-", 0.0), ("12.5", 12.5)])
def test_valores_numericos(raw, expected):
    assert parse_sidra_value(raw) == expected


@pytest.mark.parametrize("raw", ["..", "...", "X", "", None])
def test_sinais_sem_valor_viram_nan(raw):
    assert math.isnan(parse_sidra_value(raw))


def test_rendimento_sem_area_vira_ausencia_e_nao_zero():
    header = {"V": "Valor", "D1C": "Município (Código)", "D2C": "Variável (Código)", "D3C": "Ano (Código)", "D4C": "Produto"}
    rows = [  # linhas sintéticas, só para o caso de borda
        {"D1C": "9999999", "D1N": "Teste - MG", "D2C": "112", "D3C": "2025", "V": "-"},
        {"D1C": "9999999", "D1N": "Teste - MG", "D2C": "216", "D3C": "2025", "V": "-"},
        {"D1C": "9999999", "D1N": "Teste - MG", "D2C": "214", "D3C": "2025", "V": "-"},
    ]
    df = pam_rows_to_frame(validate_pam_payload([header] + rows))
    assert math.isnan(df.loc[0, "rendimento_kg_ha"])
    assert df.loc[0, "area_colhida_ha"] == 0


def test_duplicidade_e_recusada(payload):
    with pytest.raises(ValueError, match="duplicadas"):
        pam_rows_to_frame(validate_pam_payload(payload + [payload[1]]))


# --- Tratamento ----------------------------------------------------------------
def test_formato_um_registro_por_municipio_e_ano(pam):
    assert len(pam) == 4 * 10
    assert not pam.duplicated(["cod_ibge", "ano"]).any()
    assert sorted(pam["ano"].unique()) == list(range(2016, 2026))


def test_nome_e_mesorregiao(pam):
    row = pam[pam["cod_ibge"] == config.PATROCINIO].iloc[0]
    assert row["municipio"] == "Patrocínio"
    assert row["mesorregiao"] == "Triângulo Mineiro/Alto Paranaíba"


def test_patrocinio_2025_regressao():
    """Valor publicado pelo IBGE e exibido no protótipo: 1.110 kg/ha = 18,5 sc/ha."""
    loc = localidades_to_frame(json.loads((FIX / "localidades_sample.json").read_text(encoding="utf-8")))
    df = attach_localidades(pam_rows_to_frame(validate_pam_payload(json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8")))), loc)
    row = df[(df["cod_ibge"] == config.PATROCINIO) & (df["ano"] == 2025)].iloc[0]
    assert row["rendimento_kg_ha"] == 1110
    assert row["rendimento_sc_ha"] == pytest.approx(18.5)
    assert row["area_colhida_ha"] == 43850
    assert row["producao_t"] == 48669


def test_todos_os_valores_batem_com_a_copia_oficial(pam, snapshot):
    by_code = {m["c"]: m for m in snapshot["municipios"]}
    for _, r in pam.iterrows():
        ref = by_code[r["cod_ibge"]]["y"][str(r["ano"])]
        assert [r["rendimento_kg_ha"], r["area_colhida_ha"], r["producao_t"]] == ref


def test_totais_de_mg(pam):
    tot = mg_totals(pam).set_index("ano")
    sub = pam[pam["ano"] == 2025]
    expected = round(sub["producao_t"].sum() * 1000 / sub["area_colhida_ha"].sum())
    assert tot.loc[2025, "rendimento_kg_ha"] == expected
    assert tot.loc[2025, "municipios"] == 4


def test_ranking_por_area_colhida(pam):
    assert select_top(pam, 2025, 2) == ["3148103", "3139409"]  # Patrocínio e Manhuaçu lideram em área em 2025


# --- HTTP: novas tentativas sem rede real ----------------------------------------
def test_tenta_de_novo_e_depois_sucede():
    calls = []

    def flaky(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.URLError("timeout simulado")
        return b'[{"ok": 1}]'

    data, raw = fetch_json("http://exemplo", opener=flaky, sleep=lambda s: None, retries=3)
    assert data == [{"ok": 1}] and len(calls) == 3 and raw.startswith(b"[")


def test_desiste_com_erro_claro():
    def down(url, timeout):
        raise urllib.error.URLError("sem rede")

    with pytest.raises(SourceError, match="3 tentativas"):
        fetch_json("http://exemplo", opener=down, sleep=lambda s: None, retries=3)


def test_salva_bruto_com_hash(tmp_path):
    meta = save_raw(b'[{"a":1}]', "teste", "http://exemplo", 1, raw_dir=tmp_path)
    saved = Path(meta.path) if Path(meta.path).is_absolute() else tmp_path / Path(meta.path).name
    assert saved.read_bytes() == b'[{"a":1}]'
    assert len(meta.sha256) == 64
    assert (tmp_path / (saved.stem + ".meta.json")).exists()
