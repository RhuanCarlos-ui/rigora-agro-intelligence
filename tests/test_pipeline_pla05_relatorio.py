"""Testes da etapa 2 da evolução da PLA-05: seção `pla05_grupos` no relatório JSON de qualidade.

A seção só existe no arquivo `qualidade_execucao_NNNN.json` dos relatórios do IBGE/SIDRA: não entra em
as_dict(), no retorno de process(), no terminal nem no banco. A detecção e os níveis da etapa 1 não mudam.
Sem internet. Fixtures reais (cópias alteradas só em memória) e dados sintéticos.
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from pipeline import config
from pipeline import validate as V
from pipeline.run_pipeline import PipelineError, process, write_quality_report
from pipeline.validate import NUMERIC, ValidationError, pla05_groups, pla05_level, pla05_section, validate_pam
from tests import test_pipeline_inmet as T_INMET
from tests.test_pipeline_quality import MANHUACU, PAT, base_rows, collect, report_for, setv

SECAO = "pla05_grupos"
CHAVES_ANTIGAS = ["execucao_id", "status_execucao", "fonte", "data_coleta", "url", "arquivo_bruto", "hash_sha256",
                  "resumo", "erros", "avisos", "estatisticas", "regras"]
PROIBIDO = re.compile(r"erro de origem|inconsist|esperad|\brar[oa]s?\b|suspeit|incorret|recomendad|com o IBGE", re.I)


# --- Auxiliares ---------------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    return {"db": tmp_path / "data" / "rigora.db", "out": tmp_path / "processed", "raw": tmp_path / "raw"}


def com_coincidencia(rows=None):
    """Manhuaçu recebe em 2024 os mesmos três valores de Patrocínio (coincidência injetada em memória)."""
    rows = rows or base_rows()
    for v in ("112", "216", "214"):
        val = next(r["V"] for r in rows[1:] if r["D1C"] == PAT and r["D3C"] == "2024" and r["D2C"] == v)
        setv(rows, MANHUACU, 2024, v, val)
    return rows


def run(env, rows=None, sub="a"):
    pam, loc = collect(env["raw"] / sub, rows)
    return process(pam, loc, env["db"], env["out"])


def report_file(env, exec_id):
    return Path(env["db"]).parent / "reports" / f"qualidade_execucao_{exec_id:04d}.json"


def frame(rows):
    """rows: (codigo, nome, ano, rendimento, área, produção)."""
    return pd.DataFrame([{"codigo_ibge": c, "municipio": n, "regiao": "Teste", "ano": a, "rendimento_kg_ha": r,
                          "area_colhida_ha": ar, "producao_t": p} for c, n, a, r, ar, p in rows])


def grupo(n, area=2.0, ano=2020, rend=1500.0, start=3100100):
    return [(str(start + i), f"Município {i:02d}", ano, rend, area, area * rend / 1000) for i in range(n)]


def secao(df):
    return validate_pam(df).secoes[SECAO]


def misto():
    return frame(grupo(3, 2.0) + grupo(2, 470.0, start=3101000) + grupo(2, 9000.0, ano=2016, rend=1200.0, start=3102000)
                 + [("3103000", "A", 2018, 1333.0, 3.0, 4.0), ("3103001", "B", 2018, 1333.0, 3.0, 4.0),
                    ("3104000", "C", 2019, 1999.0, 10.5, 20.99), ("3104001", "D", 2019, 1999.0, 10.5, 20.99),
                    ("3105000", "Sozinho", 2020, 1000.0, 7.0, 7.0)])


def pares(sec):
    """(codigo, ano) de cada registro da seção (um por registro, inclusive em chave repetida)."""
    out = []
    for g in sec["grupos"]:
        for m in g["municipios"]:
            out.append((m["codigo_ibge"], g["ano"]))
        out.extend([(g["municipios"][0]["codigo_ibge"], g["ano"])] * (g["total_registros"] - g["total_municipios"]))
    return out


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- 1 a 3: presença, serialização e preservação do relatório ----------------------------------
def test_01_process_grava_a_secao_no_json(env):
    res = run(env, com_coincidencia())
    doc = json.loads(report_file(env, res["execucao_pam"]).read_text(encoding="utf-8"))
    sec = doc[SECAO]
    assert sec["versao"] == 1 and sec["regra"] == "PLA-05" and sec["status_regra"] == "acionada"
    assert sec["resumo"]["total_grupos"] == 1 and sec["resumo"]["total_registros"] == 2
    g = sec["grupos"][0]
    assert g["ano"] == 2024 and g["nivel"] == "revisao_documental"
    assert [m["codigo_ibge"] for m in g["municipios"]] == sorted([PAT, MANHUACU])


def test_02_json_valido_e_secao_serializavel_sem_default_str(env):
    res = run(env, com_coincidencia())
    doc = json.loads(report_file(env, res["execucao_pam"]).read_text(encoding="utf-8"))
    texto = json.dumps(doc[SECAO], ensure_ascii=False)                    # sem default=str
    assert json.loads(texto) == doc[SECAO]
    sec = secao(misto())
    assert json.loads(json.dumps(sec, ensure_ascii=False)) == sec          # direto da validação
    g9000 = next(g for g in sec["grupos"] if g["ano"] == 2016)
    assert type(g9000["area_colhida_ha"]) is int and g9000["area_colhida_ha"] == 9000          # inteiro fica inteiro
    g105 = next(g for g in sec["grupos"] if g["ano"] == 2019)
    assert g105["area_colhida_ha"] == 10.5 and g105["producao_t"] == 20.99                      # decimal fica decimal
    assert all(type(m["codigo_ibge"]) is str for g in sec["grupos"] for m in g["municipios"])


def test_03_chaves_e_valores_antigos_do_relatorio_sao_preservados(env):
    res = run(env, com_coincidencia())
    doc = json.loads(report_file(env, res["execucao_pam"]).read_text(encoding="utf-8"))
    assert list(doc) == CHAVES_ANTIGAS + [SECAO]                           # aditiva, no fim, nada renomeado
    # o que a versão anterior gravava: cabeçalho + as_dict(), que não muda
    antigo = {k: doc[k] for k in CHAVES_ANTIGAS[:7]}
    antigo.update(json.loads(json.dumps(res["validacao"], ensure_ascii=False, default=str)))
    assert {k: doc[k] for k in CHAVES_ANTIGAS} == antigo


# --- 4 a 9: conteúdo da seção --------------------------------------------------------------------
def test_04_totais_de_grupos_registros_e_municipios():
    df = misto()
    sec, rule = secao(df), next(r for r in validate_pam(df).regras if r.regra == "PLA-05")
    r = sec["resumo"]
    assert r["total_grupos"] == len(sec["grupos"]) == 5
    assert r["total_registros"] == rule.afetados == 11
    assert r["total_municipios"] == len({c for c, *_ in rule.ocorrencias}) == 11
    assert r["total_registros"] == sum(r["registros_por_nivel"].values())
    assert r["total_grupos"] == sum(r["grupos_por_nivel"].values())


def test_05_total_municipios_igual_a_lista_de_cada_grupo():
    for g in secao(misto())["grupos"]:
        assert g["total_municipios"] == len(g["municipios"]) == len({m["codigo_ibge"] for m in g["municipios"]})


def test_06_nomes_e_codigos_vem_dos_dados_validados_e_nao_do_banco(env):
    res = run(env, com_coincidencia())
    path = report_file(env, res["execucao_pam"])
    antes = path.read_bytes()
    conn = sqlite3.connect(env["db"])
    with conn:  # o banco muda depois (como um UPSERT futuro com outro nome)
        conn.execute("UPDATE pam_municipio_ano SET municipio = 'Nome alterado depois' WHERE codigo_ibge = ?", (PAT,))
    assert path.read_bytes() == antes
    nomes = {m["codigo_ibge"]: m["municipio"] for m in json.loads(antes)[SECAO]["grupos"][0]["municipios"]}
    assert nomes == {PAT: "Patrocínio", MANHUACU: "Manhuaçu"}
    # a seção usa o nome do quadro validado, mesmo diferente do que estiver no banco
    df = frame([("3148103", "Nome desta coleta", 2020, 1500.0, 2.0, 3.0), ("3139409", "Outro", 2020, 1500.0, 2.0, 3.0)])
    assert [m["municipio"] for m in secao(df)["grupos"][0]["municipios"]] == ["Outro", "Nome desta coleta"]


def test_07_niveis_e_contagens_iguais_aos_da_etapa_1():
    df = misto()
    sec, rule = secao(df), next(r for r in validate_pam(df).regras if r.regra == "PLA-05")
    for g in sec["grupos"]:
        assert g["nivel"] == V.PLA05_CODIGO[pla05_level(g["area_colhida_ha"])]
    r = sec["resumo"]
    # 2 ha (3 mun.) e 3 ha (2) = informativo; 10,5 ha (2) e 470 ha (2) = atenção; 9.000 ha (2) = revisão documental
    assert r["grupos_por_nivel"] == {"informativo": 2, "atencao": 2, "revisao_documental": 1}
    assert r["registros_por_nivel"] == {"informativo": 5, "atencao": 4, "revisao_documental": 2}
    assert rule.detalhe.startswith("5 grupos de municípios com valores idênticos no mesmo ano (11 registros, 11 municípios): "
                                   "2 informativos (área até 10 ha), 2 de atenção (10–500 ha) e 1 de revisão documental")
    nivel_texto = {(c, a): d.split(" — ")[0] for c, a, d in rule.ocorrencias}
    for g in sec["grupos"]:
        for m in g["municipios"]:
            assert V.PLA05_CODIGO[nivel_texto[(m["codigo_ibge"], g["ano"])]] == g["nivel"]


def test_08_produtividade_igual_a_rendimento_dividido_por_60():
    for g in secao(misto())["grupos"]:
        assert g["produtividade_sc_ha"] == g["rendimento_kg_ha"] / 60                 # sem arredondar
        assert type(g["produtividade_sc_ha"]) is float
    g1333 = next(g for g in secao(misto())["grupos"] if g["rendimento_kg_ha"] == 1333)
    assert g1333["produtividade_sc_ha"] == 1333 / 60 == 22.216666666666665


def test_09_ordem_deterministica_com_entrada_embaralhada():
    df = misto()
    base = json.dumps(secao(df), ensure_ascii=False)
    for seed in (1, 2, 3):
        assert json.dumps(secao(df.sample(frac=1, random_state=seed).reset_index(drop=True)), ensure_ascii=False) == base
    grupos = json.loads(base)["grupos"]
    assert [g["grupo"] for g in grupos] == list(range(1, len(grupos) + 1))
    chaves = [(g["ano"], g["rendimento_kg_ha"], g["area_colhida_ha"], g["producao_t"]) for g in grupos]
    assert chaves == sorted(chaves)
    for g in grupos:
        codes = [m["codigo_ibge"] for m in g["municipios"]]
        assert codes == sorted(codes)


# --- 10 a 15: casos sem grupos, falhas e efeitos colaterais --------------------------------------
def test_10_sem_coincidencia_secao_vazia_e_aprovada(env):
    res = run(env)
    sec = json.loads(report_file(env, res["execucao_pam"]).read_text(encoding="utf-8"))[SECAO]
    assert sec["status_regra"] == "aprovada" and sec["grupos"] == []
    assert sec["resumo"] == {"total_grupos": 0, "total_registros": 0, "total_municipios": 0,
                             "grupos_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 0},
                             "registros_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 0}}
    assert sec["classificacao"]["faixas_ha"] == [10, 500]


def test_11_base_vazia_status_nao_verificada():
    rep = validate_pam(frame([]).iloc[0:0])
    sec = rep.secoes[SECAO]
    assert any(r.regra == "INT-00" for r in rep.regras) and not any(r.regra == "PLA-05" for r in rep.regras)
    assert sec["status_regra"] == "nao_verificada" and sec["grupos"] == [] and sec["resumo"]["total_grupos"] == 0


def test_12_bruto_adulterado_nao_gera_relatorio(env):
    pam, loc = collect(env["raw"] / "a", com_coincidencia())
    pam.write_bytes(pam.read_bytes().replace(b"Patroc", b"Patrok"))
    with pytest.raises(PipelineError, match="hash"):
        process(pam, loc, env["db"], env["out"])
    reports = Path(env["db"]).parent / "reports"
    assert not reports.exists() or list(reports.iterdir()) == []


def test_13_validacao_reprovada_grava_a_secao_com_os_grupos_avaliados(env):
    rows = setv(com_coincidencia(), PAT, 2025, "112", "18")      # erro impeditivo (CON-02) + coincidência
    esperado = report_for(rows).secoes[SECAO]
    with pytest.raises(ValidationError) as exc:
        run(env, rows)
    conn = sqlite3.connect(env["db"])
    eid = conn.execute("SELECT id FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%' ORDER BY id DESC").fetchone()[0]
    doc = json.loads(report_file(env, eid).read_text(encoding="utf-8"))
    assert doc["status_execucao"] == "falha" and doc["resumo"]["resultado"] == "reprovado"
    assert doc[SECAO] == esperado == exc.value.report.secoes[SECAO]
    assert doc[SECAO]["resumo"]["total_grupos"] == 1 and doc[SECAO]["status_regra"] == "acionada"
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 0   # nada gravado


def test_14_nome_ausente_vira_null_e_nada_e_inventado():
    df = frame([("3100001", None, 2020, 1500.0, 2.0, 3.0), ("3100002", "B", 2020, 1500.0, 2.0, 3.0),
                ("3100003", "C", 2020, None, 0.0, 0.0), ("3100004", "D", 2020, None, 0.0, 0.0),   # área zero
                ("3100005", "E", 2021, None, None, None), ("3100006", "F", 2021, None, None, None)])  # ausentes
    sec = secao(df)
    assert len(sec["grupos"]) == 1
    assert sec["grupos"][0]["municipios"] == [{"codigo_ibge": "3100001", "municipio": None},
                                              {"codigo_ibge": "3100002", "municipio": "B"}]
    assert '"municipio": null' in json.dumps(sec)


def test_15_gerar_o_relatorio_nao_altera_o_banco_nem_outros_arquivos(env, tmp_path):
    res = run(env, com_coincidencia())
    pasta = Path(env["db"]).parent
    antes = {p: sha(p) for p in sorted(pasta.rglob("*")) if p.is_file()}
    rep = report_for(com_coincidencia())
    raw = type("R", (), {"meta": {"collected_at": "x", "url": "fixture", "sha256": "0" * 64}, "path": Path("bruto.json")})()
    novo = write_quality_report(999, rep, raw, "sucesso", pasta / "reports")
    depois = {p: sha(p) for p in sorted(pasta.rglob("*")) if p.is_file()}
    assert {p: h for p, h in depois.items() if p != novo} == antes             # banco e demais arquivos intactos
    assert json.loads(novo.read_text(encoding="utf-8"))[SECAO]["resumo"]["total_grupos"] == 1


# --- 16 a 21: proteção ---------------------------------------------------------------------------
def test_16_relatorio_do_inmet_nao_tem_a_secao(tmp_path):
    env = {"db": tmp_path / "rigora.db", "out": tmp_path / "out", "raw": tmp_path / "raw"}
    res = T_INMET.run(env)
    doc = json.loads(Path(res["relatorio_qualidade"]).read_text(encoding="utf-8"))
    assert doc["fonte"].startswith("INMET") and SECAO not in doc
    assert list(doc) == CHAVES_ANTIGAS


def test_17_retorno_de_process_nao_tem_a_secao(env):
    res = run(env, com_coincidencia())
    assert SECAO not in res["validacao"] and SECAO not in json.dumps(res, ensure_ascii=False, default=str)
    assert list(res["validacao"]) == ["resumo", "erros", "avisos", "estatisticas", "regras"]


def test_18_legenda_e_niveis_acompanham_PLA05_FAIXAS_HA(monkeypatch):
    df = frame(grupo(2, 2.0) + grupo(2, 50.0, start=3101000) + grupo(2, 150.0, start=3102000))
    sec = secao(df)
    assert sec["classificacao"]["faixas_ha"] == [10, 500]
    assert [g["nivel"] for g in sec["grupos"]] == ["informativo", "atencao", "atencao"]
    monkeypatch.setattr(config, "PLA05_FAIXAS_HA", (2, 100))
    sec = secao(df)
    assert sec["classificacao"]["faixas_ha"] == [2, 100]
    n = sec["classificacao"]["niveis"]
    assert [(x["nivel"], x["area_acima_de_ha"], x["area_ate_ha"], x["faixa"]) for x in n] == [
        ("informativo", None, 2, "até 2 ha"), ("atencao", 2, 100, "2–100 ha"), ("revisao_documental", 100, None, "acima de 100 ha")]
    assert [g["nivel"] for g in sec["grupos"]] == ["informativo", "atencao", "revisao_documental"]


def test_19_linguagem_neutra_e_triagem_interna():
    sec = secao(misto())
    texto = json.dumps(sec, ensure_ascii=False)
    assert not PROIBIDO.search(texto), PROIBIDO.search(texto)
    d = sec["classificacao"]["descricao"]
    assert "Classificação interna de triagem do Rigora" in d and "Não é uma avaliação da qualidade dos dados do IBGE" in d
    assert d.endswith("A coincidência, isoladamente, não comprova erro nos dados.")
    assert [x["rotulo"] for x in sec["classificacao"]["niveis"]] == ["Informativo", "Atenção", "Revisão documental"]


def test_20_chave_repetida_do_mesmo_municipio_nao_quebra_a_estrutura():
    df = frame([("3100001", "A", 2020, 1500.0, 2.0, 3.0), ("3100001", "A", 2020, 1500.0, 2.0, 3.0),
                ("3100002", "B", 2021, 1500.0, 2.0, 3.0), ("3100002", "B", 2021, 1500.0, 2.0, 3.0),
                ("3100003", "C", 2021, 1500.0, 2.0, 3.0)])
    sec = secao(df)
    g20, g21 = sec["grupos"]
    assert (g20["total_municipios"], g20["total_registros"]) == (1, 2)
    assert (g21["total_municipios"], g21["total_registros"]) == (2, 3)
    assert sec["resumo"]["total_registros"] == 5 and sec["resumo"]["total_municipios"] == 3
    assert json.loads(json.dumps(sec)) == sec


def test_21_registros_da_secao_iguais_as_ocorrencias_da_pla05():
    for df in (misto(), frame(grupo(10) + grupo(5, 600.0, ano=2017, start=3109000))):
        rep = validate_pam(df)
        occ = [o[:2] for r in rep.regras if r.regra == "PLA-05" for o in r.ocorrencias]
        assert sorted(pares(rep.secoes[SECAO])) == sorted(occ)
    rep = report_for(com_coincidencia())
    occ = sorted(o[:2] for r in rep.regras if r.regra == "PLA-05" for o in r.ocorrencias)
    assert sorted(pares(rep.secoes[SECAO])) == occ == sorted([(PAT, 2024), (MANHUACU, 2024)])
