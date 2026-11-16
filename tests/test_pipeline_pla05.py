"""Testes da etapa 1 da evolução da PLA-05: grupos, níveis de triagem e mensagens.

A detecção não muda (mesmos registros da regra original); muda só o texto. Os níveis são uma
classificação interna de triagem do Rigora pela área colhida do grupo (config.PLA05_FAIXAS_HA):
até 10 ha = Informativo; acima de 10 até 500 ha = Atenção; acima de 500 ha = Revisão documental.
Sem internet. Dados sintéticos em memória e as fixtures reais (cópias alteradas só em memória).
"""
import re
import sqlite3

import pandas as pd
import pytest

from pipeline import config
from pipeline.validate import (AVISO, PLA05_ATENCAO, PLA05_INFORMATIVO, PLA05_REVISAO, pla05_groups, pla05_level,
                               validate_pam)
from tests.test_pipeline_quality import MANHUACU, PAT, base_rows, report_for, setv

NUM = ["rendimento_kg_ha", "area_colhida_ha", "producao_t"]
PROIBIDO = re.compile(r"erro de origem|inconsist|esperad|\brar[oa]s?\b|suspeit|confirmar .*IBGE|com o IBGE", re.I)


def frame(rows):
    """rows: (codigo, nome, ano, rendimento, área, produção)."""
    return pd.DataFrame([{"codigo_ibge": c, "municipio": n, "regiao": "Teste", "ano": a,
                          "rendimento_kg_ha": r, "area_colhida_ha": ar, "producao_t": p} for c, n, a, r, ar, p in rows])


def grupo(n, area=2.0, ano=2020, rend=1500.0, start=3100100):
    """n municípios com os mesmos valores; códigos crescentes a partir de start."""
    return [(str(start + i), f"Município {i:02d}", ano, rend, area, area * rend / 1000) for i in range(n)]


def pla05(df):
    return next(r for r in validate_pam(df).regras if r.regra == "PLA-05")


def msgs(rule):
    return {(c, a): d for c, a, d in rule.ocorrencias}


def parceiros(msg):
    return re.findall(r"\((\d{7})\)", msg)


def old_rule_keys(df):
    """Fórmula original da PLA-05 (antes da etapa 1), para comparar a detecção."""
    full = df[df[NUM].notna().all(axis=1) & (df["area_colhida_ha"] > 0)]
    same = full[full.duplicated(["ano"] + NUM, keep=False)]
    return list(zip(same["codigo_ibge"].astype(str), same["ano"].astype(int)))


# --- 1 e 2: regressão da detecção -------------------------------------------------------------
def test_01_caso_original_continua_com_2_afetados():
    rows = base_rows()
    for v in ("112", "216", "214"):
        val = next(r["V"] for r in rows[1:] if r["D1C"] == PAT and r["D3C"] == "2024" and r["D2C"] == v)
        setv(rows, MANHUACU, 2024, v, val)
    r = next(x for x in report_for(rows).regras if x.regra == "PLA-05")
    assert r.afetados == 2 and r.severidade == AVISO


def test_02_detecta_exatamente_os_mesmos_registros_da_regra_original():
    df = frame(grupo(3, 2.0) + grupo(2, 470.0, start=3101000) + grupo(2, 9000.0, ano=2016, rend=1200.0, start=3102000)
               + [("3103000", "Único", 2020, 1500.0, 3.0, 4.5),                     # sem par
                  ("3103001", "Zero A", 2020, None, 0.0, 0.0), ("3103002", "Zero B", 2020, None, 0.0, 0.0),
                  ("3103003", "Nulo A", 2021, None, None, None), ("3103004", "Nulo B", 2021, None, None, None)])
    r = pla05(df)
    assert [o[:2] for o in r.ocorrencias] == old_rule_keys(df)                # mesmos registros, mesma ordem
    assert r.afetados == 7
    real = report_for(base_rows())
    assert next(x for x in real.regras if x.regra == "PLA-05").afetados == len(old_rule_keys(real_df()))


def real_df():
    from pipeline.transform import attach_localidades, localidades_to_frame, pam_rows_to_frame
    from pipeline.validate import to_storage_frame
    from tests.test_pipeline_quality import base_loc
    return to_storage_frame(attach_localidades(pam_rows_to_frame(base_rows()[1:]), localidades_to_frame(base_loc())))


# --- 3 e 4: níveis e fronteiras ---------------------------------------------------------------
def test_03_tres_niveis_pela_area_do_grupo():
    df = frame(grupo(2, 2.0) + grupo(2, 470.0, start=3101000) + grupo(2, 9000.0, ano=2016, rend=1200.0, start=3102000))
    m = msgs(pla05(df))
    assert m[("3100100", 2020)].startswith("Informativo — 2020:")
    assert m[("3101000", 2020)].startswith("Atenção — 2020:")
    assert m[("3102000", 2016)].startswith("Revisão documental — 2016:")


@pytest.mark.parametrize("area, nivel", [(10, PLA05_INFORMATIVO), (11, PLA05_ATENCAO), (500, PLA05_ATENCAO),
                                         (501, PLA05_REVISAO), (1, PLA05_INFORMATIVO), (10.5, PLA05_ATENCAO)])
def test_04_fronteiras_das_faixas(area, nivel):
    assert config.PLA05_FAIXAS_HA == (10, 500)
    assert pla05_level(area) == nivel
    m = msgs(pla05(frame(grupo(2, float(area)))))
    assert all(v.startswith(f"{nivel} — 2020:") for v in m.values())


# --- 5 a 8: parceiros, resumo e linguagem -----------------------------------------------------
def test_05_grupo_de_3_cita_os_outros_2():
    m = msgs(pla05(frame(grupo(3))))
    for code, _ in m:
        assert sorted(parceiros(m[(code, 2020)])) == sorted({"3100100", "3100101", "3100102"} - {code})
        assert "mesmos valores de 2 outros municípios" in m[(code, 2020)]


def test_06_resumo_conta_grupos_registros_municipios_e_niveis():
    df = frame(grupo(3, 2.0) + grupo(2, 2.0, ano=2021, start=3100100) + grupo(2, 470.0, start=3101000)
               + grupo(2, 9000.0, ano=2016, rend=1200.0, start=3102000))
    d = pla05(df).detalhe
    assert d.startswith("4 grupos de municípios com valores idênticos no mesmo ano (9 registros, 7 municípios): "
                        "2 informativos (área até 10 ha), 1 de atenção (10–500 ha) e 1 de revisão documental (acima de 500 ha).")
    assert "classificação interna de triagem do Rigora" in d
    assert "não são uma conclusão sobre a qualidade dos dados do IBGE" in d
    assert d.endswith("A coincidência, isoladamente, não comprova erro nos dados.")


def test_07_nenhum_texto_usa_termos_proibidos():
    df = frame(grupo(10, 2.0) + grupo(2, 470.0, start=3101000) + grupo(2, 9000.0, ano=2016, rend=1200.0, start=3102000))
    r = pla05(df)
    for text in [r.detalhe, r.nome] + [d for *_, d in r.ocorrencias]:
        assert not PROIBIDO.search(text), text
        assert "Revisão recomendada" not in text


def test_08_todo_nivel_declara_a_triagem_e_a_revisao_traz_a_frase_aprovada():
    df = frame(grupo(2, 2.0) + grupo(2, 470.0, start=3101000) + grupo(2, 9000.0, ano=2016, rend=1200.0, start=3102000))
    m = msgs(pla05(df))
    assert m[("3100100", 2020)].endswith("Faixa até 10 ha, classificação interna de triagem do Rigora.")
    assert m[("3101000", 2020)].endswith("Faixa 10–500 ha, classificação interna de triagem do Rigora.")
    assert m[("3102000", 2016)].endswith(
        "Classificação de revisão documental, definida pela faixa de área adotada pelo Rigora. "
        "A coincidência, isoladamente, não comprova erro nos dados.")


# --- 9 a 12: exclusões, efeitos colaterais, formato, integração -------------------------------
def test_09_area_zero_e_nulos_identicos_nao_sao_marcados():
    df = frame([("3103001", "A", 2020, None, 0.0, 0.0), ("3103002", "B", 2020, None, 0.0, 0.0),
                ("3103003", "C", 2021, None, None, None), ("3103004", "D", 2021, None, None, None),
                ("3103005", "E", 2022, 1000.0, None, 5.0), ("3103006", "F", 2022, 1000.0, None, 5.0)])
    r = pla05(df)
    assert r.afetados == 0 and r.ocorrencias == []


def test_10_severidade_resumo_e_demais_regras_nao_mudam(monkeypatch):
    rows = base_rows()
    for v in ("112", "216", "214"):
        val = next(r["V"] for r in rows[1:] if r["D1C"] == PAT and r["D3C"] == "2024" and r["D2C"] == v)
        setv(rows, MANHUACU, 2024, v, val)
    new = report_for(rows)
    pla = next(r for r in new.regras if r.regra == "PLA-05")
    assert new.ok and (pla.severidade, pla.status, pla.dimensao, pla.afetados) == (AVISO, "acionada", "plausibilidade", 2)
    # Simula a regra ANTIGA (texto genérico, sem textos por registro) e compara todo o resto do relatório.
    from pipeline import validate as V
    monkeypatch.setattr(V, "pla05_summary", lambda groups: "2 registros repetem exatamente os valores de outro município")
    monkeypatch.setattr(V, "pla05_messages", lambda groups: None)
    old = report_for(rows)
    assert [r.summary() for r in new.regras if r.regra != "PLA-05"] == [r.summary() for r in old.regras if r.regra != "PLA-05"]
    assert new.resumo() == old.resumo() and new.stats == old.stats
    assert [o[:2] for r in new.regras if r.regra == "PLA-05" for o in r.ocorrencias] == \
           [o[:2] for r in old.regras if r.regra == "PLA-05" for o in r.ocorrencias]


def test_11_numeros_no_padrao_brasileiro_e_texto_repetivel():
    df = frame(grupo(2, 9000.0, ano=2016, rend=1200.0)
               + [("3101000", "A", 2018, 1333.0, 3.0, 4.0), ("3101001", "B", 2018, 1333.0, 3.0, 4.0),   # caso real: 4 t / 3 ha
                  ("3102000", "C", 2019, 1999.0, 10.5, 20.99), ("3102001", "D", 2019, 1999.0, 10.5, 20.99)])
    m = msgs(pla05(df))
    assert "(9.000 ha; 10.800 t; 1.200 kg/ha = 20,0 sc/ha)" in m[("3100100", 2016)]
    assert "(3 ha; 4 t; 1.333 kg/ha = 22,2 sc/ha)" in m[("3101000", 2018)]
    assert "(10,5 ha; 20,99 t; 1.999 kg/ha = 33,3 sc/ha)" in m[("3102000", 2019)]   # nada arredondado no texto
    assert msgs(pla05(df)) == m and pla05(df).detalhe == pla05(df).detalhe


def test_12_texto_novo_gravado_no_sqlite(tmp_path):
    from tests.test_pipeline_quality import run  # gravação com as fixtures reais, como nos testes da 3c
    rows = base_rows()
    for v in ("112", "216", "214"):
        val = next(r["V"] for r in rows[1:] if r["D1C"] == PAT and r["D3C"] == "2024" and r["D2C"] == v)
        setv(rows, MANHUACU, 2024, v, val)
    env = {"db": tmp_path / "rigora.db", "out": tmp_path / "processed", "raw": tmp_path / "raw"}
    exec_id = run(env, rows)["execucao_pam"]
    conn = sqlite3.connect(env["db"])
    det = conn.execute("SELECT detalhe FROM validacao_regra WHERE execucao_id=? AND regra='PLA-05'", (exec_id,)).fetchone()[0]
    occ = dict(((c, a), d) for c, a, d in conn.execute(
        "SELECT codigo_ibge, ano, detalhe FROM validacao_ocorrencia WHERE execucao_id=? AND regra='PLA-05'", (exec_id,)))
    assert det.startswith("1 grupo de municípios com valores idênticos no mesmo ano (2 registros, 2 municípios)")
    assert set(occ) == {(PAT, 2024), (MANHUACU, 2024)}
    assert occ[(PAT, 2024)].startswith("Revisão documental — 2024: mesmos valores de Manhuaçu (3139409)")


# --- 13 a 17: municípios parceiros ------------------------------------------------------------
def test_13_proprio_municipio_nunca_e_parceiro():
    for n in (2, 3, 4, 5, 10):
        for (code, ano), msg in msgs(pla05(frame(grupo(n)))).items():
            assert code not in parceiros(msg), (n, code)


def test_14_parceiros_sao_do_mesmo_grupo_e_do_mesmo_ano():
    # mesmos valores em 2020 (A, B) e em 2021 (A, C); outro grupo em 2020 com valores diferentes (D, E)
    df = frame([("3100001", "A", 2020, 1500.0, 2.0, 3.0), ("3100002", "B", 2020, 1500.0, 2.0, 3.0),
                ("3100001", "A", 2021, 1500.0, 2.0, 3.0), ("3100003", "C", 2021, 1500.0, 2.0, 3.0),
                ("3100004", "D", 2020, 1000.0, 2.0, 2.0), ("3100005", "E", 2020, 1000.0, 2.0, 2.0)])
    rule = pla05(df)
    m = msgs(rule)
    groups = {(g["ano"], c): {x for x, _ in g["membros"]} for g in pla05_groups(df.iloc[[0, 1, 2, 3, 4, 5]]) for c, _ in g["membros"]}
    for (code, ano), msg in m.items():
        assert set(parceiros(msg)) == groups[(ano, code)] - {code}
    assert parceiros(m[("3100001", 2020)]) == ["3100002"] and parceiros(m[("3100001", 2021)]) == ["3100003"]
    assert parceiros(m[("3100004", 2020)]) == ["3100005"]


@pytest.mark.parametrize("n", [2, 3, 4])
def test_15_grupos_de_ate_4_mostram_todos_os_parceiros(n):
    for (code, _), msg in msgs(pla05(frame(grupo(n)))).items():
        assert len(parceiros(msg)) == n - 1 and "e mais" not in msg


@pytest.mark.parametrize("n, omitidos", [(5, 1), (7, 3), (10, 6)])
def test_16_e_mais_n_mostra_a_quantidade_exata_omitida(n, omitidos):
    for (code, _), msg in msgs(pla05(frame(grupo(n)))).items():
        assert len(parceiros(msg)) == 3
        assert re.search(rf" e mais {omitidos}\. ", msg), msg
        assert f"mesmos valores de {n - 1} outros municípios" in msg
        assert len(parceiros(msg)) + omitidos == n - 1


def test_17_ordem_dos_parceiros_e_deterministica_por_codigo():
    rows = grupo(7)
    base = msgs(pla05(frame(rows)))
    for perm in (rows[::-1], rows[3:] + rows[:3], [rows[i] for i in (4, 0, 6, 2, 5, 1, 3)]):
        m = msgs(pla05(frame(perm)))
        assert m == base
    for (code, _), msg in base.items():
        p = parceiros(msg)
        assert p == sorted(p) and p == sorted({r[0] for r in rows} - {code})[:3]


def test_18_chave_repetida_do_mesmo_municipio_nao_quebra_o_texto():
    # o mesmo município duas vezes no mesmo ano (rejeitado pela INT-01): a PLA-05 marca os mesmos registros de antes
    df = frame([("3100001", "A", 2020, 1500.0, 2.0, 3.0), ("3100001", "A", 2020, 1500.0, 2.0, 3.0),
                ("3100002", "B", 2021, 1500.0, 2.0, 3.0), ("3100002", "B", 2021, 1500.0, 2.0, 3.0),
                ("3100003", "C", 2021, 1500.0, 2.0, 3.0)])
    r = pla05(df)
    assert [o[:2] for o in r.ocorrencias] == old_rule_keys(df)
    m = msgs(r)
    assert "registrados mais de uma vez para o próprio município" in m[("3100001", 2020)]
    assert parceiros(m[("3100002", 2021)]) == ["3100003"] and parceiros(m[("3100003", 2021)]) == ["3100002"]
    assert r.detalhe.startswith("2 grupos de municípios com valores idênticos no mesmo ano (5 registros, 3 municípios)")
