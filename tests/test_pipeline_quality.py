"""Testes da etapa 3c: regras de qualidade, relatório e rastreabilidade (sem internet).

Base: fixtures reais (tests/fixtures/). Cada caso altera uma cópia em memória para
provocar uma situação; alterações estão comentadas e nunca são gravadas nas fixtures.
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from pipeline import load_sqlite, query, validate
from pipeline.extract_sidra import save_raw
from pipeline.run_pipeline import process
from pipeline.transform import attach_localidades, localidades_to_frame, pam_rows_to_frame
from pipeline.validate import ACIONADA, APROVADA, NAO_VERIFICADA, ValidationError, to_storage_frame, validate_pam

FIX = Path(__file__).parent / "fixtures"
PAT, MANHUACU, MONTE_CARMELO, CAMPOS_ALTOS = "3148103", "3139409", "3143104", "3111507"
UNITS = {"112": "Quilogramas por Hectare", "216": "Hectares", "214": "Toneladas"}


def base_rows():
    return json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))


def base_loc():
    return json.loads((FIX / "localidades_sample.json").read_text(encoding="utf-8"))


def setv(rows, code, year, var, value):
    for r in rows[1:]:
        if r["D1C"] == code and r["D3C"] == str(year) and r["D2C"] == var:
            r["V"] = value
    return rows


def report_for(rows=None, loc=None):
    rows = rows or base_rows()
    loc_payload = loc if loc is not None else base_loc()
    sidra = pam_rows_to_frame(rows[1:])
    names = sidra.drop_duplicates("cod_ibge").set_index("cod_ibge")["municipio"].to_dict()
    lf = localidades_to_frame(loc_payload)
    df = to_storage_frame(attach_localidades(sidra, lf))
    return validate_pam(df, localidades=lf, sidra_names=names, raw_rows=rows[1:])


def rule(rep, rid):
    return next(r for r in rep.regras if r.regra == rid)


def collect(raw_dir, rows=None, loc=None):
    rows = rows or base_rows()
    loc = loc if loc is not None else base_loc()
    pm = save_raw(json.dumps(rows, ensure_ascii=False).encode(), "sidra_pam_1613_mg_arabica", "fixture", len(rows) - 1, raw_dir)
    lm = save_raw(json.dumps(loc, ensure_ascii=False).encode(), "ibge_localidades_mg", "fixture", len(loc), raw_dir)
    return raw_dir / Path(pm.path).name, raw_dir / Path(lm.path).name


@pytest.fixture
def env(tmp_path):
    return {"db": tmp_path / "rigora.db", "out": tmp_path / "processed", "raw": tmp_path / "raw"}


def run(env, rows=None, loc=None, sub="a"):
    pam, lc = collect(env["raw"] / sub, rows, loc)
    return process(pam, lc, env["db"], env["out"])


# --- Catálogo -------------------------------------------------------------------------
def test_catalogo_completo_de_regras():
    ids = {r.regra for r in report_for().regras}
    assert ids == {"INT-01", "INT-02", "INT-03", "INT-04", "INT-05", "INT-06",
                   "COM-01", "COM-02", "COM-03", "COM-04", "COM-05", "COM-06", "COM-07", "COM-08", "COM-09",
                   "CON-01", "CON-02", "CON-03", "CON-05",
                   "PLA-01", "PLA-02", "PLA-03", "PLA-04", "PLA-05"}


# --- Dados válidos ------------------------------------------------------------------------
def test_dados_oficiais_validos_passam_sem_avisos():
    rep = report_for()
    res = rep.resumo()
    assert res["resultado"] == "aprovado"
    assert (res["registros_analisados"], res["registros_rejeitados"], res["registros_aprovados"]) == (40, 0, 40)
    assert res["avisos"] == 0 and res["registros_com_aviso"] == 0
    assert rule(rep, "CON-03").status == NAO_VERIFICADA  # fixture não traz o campo de unidade
    assert rule(rep, "PLA-04").status == APROVADA          # bienalidade de Patrocínio (2,58×) não é suspeita


# --- Ausentes, zeros e cobertura ----------------------------------------------------------------
def test_registro_incompleto_e_apontado_com_o_campo():
    rep = report_for(setv(base_rows(), PAT, 2020, "214", "X"))
    r = rule(rep, "COM-02")
    assert r.status == ACIONADA and r.afetados == 1
    assert r.ocorrencias[0][:2] == (PAT, 2020) and "producao_t" in r.ocorrencias[0][2]
    assert rep.ok  # aviso não vira erro


def test_zero_real_e_ausencia_sao_contados_separadamente():
    rows = base_rows()
    for v in ("112", "216", "214"):
        setv(rows, MANHUACU, 2016, v, "-")   # zero absoluto ('-')
    setv(rows, MANHUACU, 2017, "112", "...")  # não disponível
    rep = report_for(rows)
    assert rep.stats["zeros"]["area_colhida_ha"] == 1
    assert rep.stats["ausentes"]["rendimento_kg_ha"] == 2  # 2016 (sem área => sem rendimento) e 2017


def test_municipios_sem_cobertura():
    rows = base_rows()
    for y in range(2016, 2026):
        for v in ("112", "216", "214"):
            setv(rows, CAMPOS_ALTOS, y, v, "-")
    loc = base_loc() + [{"id": 3106200, "nome": "Belo Horizonte", "microrregiao": {"mesorregiao": {"nome": "Metropolitana de Belo Horizonte"}}}]
    rep = report_for(rows, loc)
    assert rule(rep, "COM-04").afetados == 1  # Campos Altos sem produção no período
    assert rule(rep, "COM-05").afetados == 1  # Belo Horizonte sem registro no SIDRA
    assert rep.resumo()["municipios_sem_cobertura"] == {"sem_producao_no_periodo": 1, "ausentes_no_sidra": 1}


def test_lacuna_de_anos_por_municipio():
    rows = [r for r in base_rows() if not (r.get("D1C") == MONTE_CARMELO and r.get("D3C") == "2019")]
    r = rule(report_for(rows), "COM-03")
    assert r.status == ACIONADA and (MONTE_CARMELO, 2019) in [o[:2] for o in r.ocorrencias]


# --- Duplicidade e integridade ------------------------------------------------------------
def test_duplicidade_rejeita_e_conta_registros():
    df = to_storage_frame(attach_localidades(pam_rows_to_frame(base_rows()[1:]), localidades_to_frame(base_loc())))
    rep = validate_pam(pd.concat([df, df.iloc[[0]]]))
    assert rule(rep, "INT-01").status == ACIONADA and not rep.ok
    assert rep.resumo()["registros_rejeitados"] == 1


def test_codigo_fora_da_lista_oficial_e_nome_divergente():
    loc = [m for m in base_loc() if m["id"] != int(CAMPOS_ALTOS)]
    rows = base_rows()
    for r in rows[1:]:
        if r["D1C"] == PAT:
            r["D1N"] = "Patrocínio do Muriaé - MG"  # nome trocado na origem
    rep = report_for(rows, loc)
    assert rule(rep, "INT-05").status == ACIONADA
    assert rule(rep, "INT-06").status == ACIONADA
    assert rep.ok  # avisos


def test_nome_sem_acento_nao_e_divergencia():
    rows = base_rows()
    for r in rows[1:]:
        if r["D1C"] == PAT:
            r["D1N"] = "Patrocinio - MG"
    assert rule(report_for(rows), "INT-06").status == APROVADA


# --- Valores extremos e variações -----------------------------------------------------------
def test_produtividade_extrema_e_area_extrema():
    rows = setv(setv(base_rows(), PAT, 2021, "112", "6000"), PAT, 2021, "214", str(6000 * 42092 // 1000))
    rows = setv(rows, MANHUACU, 2020, "216", "150000")
    setv(rows, MANHUACU, 2020, "214", str(round(150000 * float(next(r["V"] for r in rows[1:] if r["D1C"] == MANHUACU and r["D3C"] == "2020" and r["D2C"] == "112")) / 1000)))
    rep = report_for(rows)
    assert (PAT, 2021) in [o[:2] for o in rule(rep, "PLA-02").ocorrencias]
    assert (MANHUACU, 2020) in [o[:2] for o in rule(rep, "PLA-03").ocorrencias]
    assert (PAT, 2021) in [o[:2] for o in rule(rep, "CON-05").ocorrencias]  # 100 sc/ha ≫ média estadual
    assert rep.ok


def test_variacao_anual_suspeita():
    rows = setv(setv(base_rows(), PAT, 2025, "112", "300"), PAT, 2025, "214", str(300 * 43850 // 1000))
    r = rule(report_for(rows), "PLA-04")
    assert r.status == ACIONADA and (PAT, 2025) in [o[:2] for o in r.ocorrencias]
    assert "0.20×" in dict(((c, a), d) for c, a, d in r.ocorrencias)[(PAT, 2025)]


def test_valores_identicos_em_municipios_diferentes():
    rows = base_rows()
    for v in ("112", "216", "214"):
        val = next(r["V"] for r in rows[1:] if r["D1C"] == PAT and r["D3C"] == "2024" and r["D2C"] == v)
        setv(rows, MANHUACU, 2024, v, val)  # cópia dos valores de Patrocínio
    assert rule(report_for(rows), "PLA-05").afetados == 2


# --- Unidades -------------------------------------------------------------------------------
def test_unidades_declaradas_corretas():
    rows = base_rows()
    for r in rows[1:]:
        r["MN"] = UNITS[r["D2C"]]
    assert rule(report_for(rows), "CON-03").status == APROVADA


def test_unidade_declarada_errada_e_impeditiva():
    rows = base_rows()
    for r in rows[1:]:
        r["MN"] = "Toneladas" if r["D2C"] == "112" else UNITS[r["D2C"]]
    rep = report_for(rows)
    assert rule(rep, "CON-03").severidade == "erro" and not rep.ok


def test_valor_em_unidade_trocada_e_detectado():
    rows = setv(base_rows(), PAT, 2025, "112", "18")  # 18,5 sc/ha informado como se fosse kg/ha
    rep = report_for(rows)
    assert (PAT, 2025) in [o[:2] for o in rule(rep, "CON-02").ocorrencias]
    assert rule(rep, "CON-01").status == APROVADA  # o mesmo registro não é contado duas vezes
    assert not rep.ok


# --- Falhas, preservação e rastreabilidade no banco ---------------------------------------------
def test_falha_registra_regras_ocorrencias_e_preserva_dados(env):
    first = run(env)
    bad = setv(base_rows(), PAT, 2025, "112", "18")
    with pytest.raises(ValidationError):
        run(env, bad, sub="b")
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT rendimento_kg_ha FROM pam_municipio_ano WHERE codigo_ibge=? AND ano=2025", (PAT,)).fetchone()[0] == 1110
    last = conn.execute("SELECT id, status FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%' ORDER BY id DESC").fetchone()
    assert last[1] == "falha"
    assert conn.execute("SELECT status FROM validacao_regra WHERE execucao_id=? AND regra='CON-02'", (last[0],)).fetchone()[0] == "acionada"
    assert conn.execute("SELECT count(*) FROM validacao_ocorrencia WHERE execucao_id=? AND codigo_ibge=? AND ano=2025", (last[0], PAT)).fetchone()[0] >= 1
    rep_file = Path(env["db"]).parent / "reports" / f"qualidade_execucao_{last[0]:04d}.json"
    doc = json.loads(rep_file.read_text(encoding="utf-8"))
    assert doc["status_execucao"] == "falha" and doc["resumo"]["resultado"] == "reprovado"
    assert first["execucao_pam"] != last[0]


def test_avisos_nao_impedem_gravacao_e_ficam_rastreaveis(env):
    rows = setv(base_rows(), PAT, 2020, "214", "X")
    res = run(env, rows)
    assert res["validacao"]["resumo"]["registros_com_aviso"] == 1
    trace = query.run("rastrear", (PAT, 2020), env["db"])[0]
    assert trace["fonte"].startswith("IBGE/SIDRA") and trace["arquivo_origem"].startswith("sidra_pam")
    assert trace["data_coleta"] and len(trace["hash"]) == 12
    assert trace["regras_executadas"] >= 20 and "COM-02" in trace["ocorrencias"]


def test_relatorio_por_execucao_no_banco_e_em_arquivo(env):
    res = run(env)
    rules = query.run("qualidade", (res["execucao_pam"],), env["db"])
    assert {r["regra"] for r in rules} >= {"INT-07", "CON-04"}  # regras pós-gravação também registradas
    assert all(r["status"] != "acionada" or r["severidade"] != "erro" for r in rules)
    doc = json.loads(Path(res["relatorio_qualidade"]).read_text(encoding="utf-8"))
    assert doc["hash_sha256"] and doc["data_coleta"] and doc["resumo"]["registros_analisados"] == 40


def test_falha_na_regra_pos_gravacao_desfaz_tudo(env, monkeypatch):
    def broken(conn, exec_id):
        r = validate.RuleResult("INT-07", "Integridade no banco", "integridade", "erro")
        r.status, r.afetados, r.detalhe = ACIONADA, 1, "falha simulada"
        return [r]
    monkeypatch.setattr(validate, "post_load_rules", broken)
    with pytest.raises(ValidationError):
        run(env)
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT count(*) FROM pam_municipio_ano").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM validacao_regra WHERE regra='INT-07'").fetchone()[0] == "acionada"


def test_historico_de_execucoes_preservado(env):
    run(env)
    run(env, sub="b")
    conn = sqlite3.connect(env["db"])
    execs = [r[0] for r in conn.execute("SELECT id FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%' ORDER BY id")]
    assert len(execs) == 2
    assert all(conn.execute("SELECT count(*) FROM validacao_regra WHERE execucao_id=?", (e,)).fetchone()[0] > 20 for e in execs)
