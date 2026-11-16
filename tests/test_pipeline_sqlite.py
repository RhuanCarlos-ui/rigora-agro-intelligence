"""Testes da etapa 3b: validação + SQLite + orquestração (sem internet).

Usa as fixtures reais de tests/fixtures/, salvas como coletas com metadados e hash
pela mesma função usada na coleta ao vivo (save_raw).
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from pipeline import load_sqlite, query
from pipeline.extract_sidra import save_raw
from pipeline.run_pipeline import PipelineError, process
from pipeline.validate import ValidationError, validate_pam

FIX = Path(__file__).parent / "fixtures"


def _collect(raw_dir: Path, pam_payload=None, loc_payload=None):
    """Simula uma coleta: grava bruto + .meta.json com hash, como extract_sidra faz."""
    pam = pam_payload if pam_payload is not None else json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    loc = loc_payload if loc_payload is not None else json.loads((FIX / "localidades_sample.json").read_text(encoding="utf-8"))
    pm = save_raw(json.dumps(pam, ensure_ascii=False).encode("utf-8"), "sidra_pam_1613_mg_arabica", "fixture", len(pam) - 1, raw_dir)
    lm = save_raw(json.dumps(loc, ensure_ascii=False).encode("utf-8"), "ibge_localidades_mg", "fixture", len(loc), raw_dir)
    return Path(pm.path) if Path(pm.path).is_absolute() else raw_dir / Path(pm.path).name, \
        Path(lm.path) if Path(lm.path).is_absolute() else raw_dir / Path(lm.path).name


@pytest.fixture
def env(tmp_path):
    raw = tmp_path / "raw"
    pam, loc = _collect(raw)
    return {"db": tmp_path / "rigora.db", "out": tmp_path / "processed", "raw": raw, "pam": pam, "loc": loc}


def _run(env, pam=None, loc=None):
    return process(pam or env["pam"], loc or env["loc"], env["db"], env["out"])


def _db(env):
    conn = sqlite3.connect(env["db"])
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --- Criação ----------------------------------------------------------------------
def test_cria_banco_tabelas_e_indices(tmp_path):
    conn = load_sqlite.connect(tmp_path / "novo.db")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert (tmp_path / "novo.db").exists()
    assert {"execucao", "pam_municipio_ano", "ix_pam_ano", "ix_pam_execucao", "ix_execucao_fonte"} <= names
    fks = conn.execute("PRAGMA foreign_key_list(pam_municipio_ano)").fetchall()
    assert [(f["table"], f["from"], f["to"]) for f in fks] == [("execucao", "execucao_id", "id")]
    pk = [r["name"] for r in conn.execute("PRAGMA table_info(pam_municipio_ano)") if r["pk"]]
    assert pk == ["codigo_ibge", "ano"]


def test_schema_pode_ser_aplicado_de_novo(tmp_path):
    conn = load_sqlite.connect(tmp_path / "x.db")
    load_sqlite.init_schema(conn)  # idempotente


# --- Inserção e consulta -----------------------------------------------------------
def test_insere_e_consulta(env):
    res = _run(env)
    assert res["gravados"] == 40 and res["total_no_banco"] == 40
    row = _db(env).execute(
        "SELECT municipio, regiao, rendimento_kg_ha, produtividade_sc_ha, area_colhida_ha, producao_t "
        "FROM pam_municipio_ano WHERE codigo_ibge = '3148103' AND ano = 2025").fetchone()
    assert row == ("Patrocínio", "Triângulo Mineiro/Alto Paranaíba", 1110.0, 18.5, 43850.0, 48669.0)
    assert (env["out"] / "pam_mg_arabica.csv").exists()


def test_consultas_prontas(env):
    _run(env)
    serie = query.run("municipio", ("3148103",), env["db"])
    assert [r["ano"] for r in serie] == list(range(2016, 2026))
    top = query.run("ranking", (2025, 2), env["db"])
    assert len(top) == 2 and top[0]["sc_ha"] >= top[1]["sc_ha"]
    hist = query.run("historico", (), env["db"])
    assert {r["status"] for r in hist} == {"sucesso"}


# --- Integridade referencial ----------------------------------------------------------
def test_integridade_referencial(env):
    res = _run(env)
    assert res["integridade"] == {"chaves_estrangeiras_quebradas": 0, "duplicados": 0, "linhas_de_execucao_sem_sucesso": 0}
    conn = _db(env)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pam_municipio_ano (codigo_ibge, municipio, ano, execucao_id) VALUES ('3100000','X',2025,999)")
    with pytest.raises(sqlite3.IntegrityError):  # execução com dados não pode ser apagada
        conn.execute("DELETE FROM execucao WHERE id = ?", (res["execucao_pam"],))


def test_restricoes_de_dominio(env):
    _run(env)
    conn = _db(env)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE pam_municipio_ano SET area_colhida_ha = -1 WHERE ano = 2025")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE pam_municipio_ano SET rendimento_kg_ha = 0 WHERE ano = 2025")


# --- Reexecução ----------------------------------------------------------------------
def test_reexecucao_nao_duplica_e_registra_historico(env):
    first = _run(env)
    second = _run(env)
    conn = _db(env)
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(*) FROM execucao WHERE status = 'sucesso'").fetchone()[0] == 4  # 2 PAM + 2 localidades
    ids = {r[0] for r in conn.execute("SELECT DISTINCT execucao_id FROM pam_municipio_ano")}
    assert ids == {second["execucao_pam"]} != {first["execucao_pam"]}


def test_coleta_parcial_nao_apaga_anos_anteriores(env):
    _run(env)
    full = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    partial = [full[0]] + [r for r in full[1:] if r["D3C"] == "2025"]
    pam, loc = _collect(env["raw"] / "parcial", pam_payload=partial)
    _run(env, pam, loc)
    conn = _db(env)
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(DISTINCT execucao_id) FROM pam_municipio_ano").fetchone()[0] == 2


def test_revisao_do_ibge_atualiza_valor(env):
    _run(env)
    revised = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    for r in revised[1:]:  # simula revisão: produção de Patrocínio 2025 corrigida
        if r["D1C"] == "3148103" and r["D3C"] == "2025" and r["D2C"] == "214":
            r["V"] = "48700"
    pam, loc = _collect(env["raw"] / "revisao", pam_payload=revised)
    _run(env, pam, loc)
    assert _db(env).execute("SELECT producao_t FROM pam_municipio_ano WHERE codigo_ibge='3148103' AND ano=2025").fetchone()[0] == 48700


# --- Ausência preservada ---------------------------------------------------------------
def test_ausente_fica_null_e_nao_zero(env):
    data = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    for r in data[1:]:
        if r["D1C"] == "3139409" and r["D3C"] == "2016":
            r["V"] = "X"  # sigilo estatístico
    pam, loc = _collect(env["raw"] / "sigilo", pam_payload=data)
    res = _run(env, pam, loc)
    row = _db(env).execute("SELECT rendimento_kg_ha, produtividade_sc_ha, area_colhida_ha, producao_t "
                           "FROM pam_municipio_ano WHERE codigo_ibge='3139409' AND ano=2016").fetchone()
    assert row == (None, None, None, None)
    assert res["validacao"]["estatisticas"]["ausentes"]["rendimento_kg_ha"] == 1


# --- Tratamento de erros ----------------------------------------------------------------
def test_arquivo_alterado_depois_da_coleta_falha_sem_gravar(env):
    env["pam"].write_bytes(env["pam"].read_bytes().replace(b"1110", b"9999"))
    with pytest.raises(PipelineError, match="hash"):
        _run(env)
    conn = _db(env)
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 0
    assert {r[0] for r in conn.execute("SELECT status FROM execucao")} == {"falha"}


def test_validacao_bloqueia_e_preserva_dados_anteriores(env):
    _run(env)
    bad = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    for r in bad[1:]:
        if r["D1C"] == "3148103" and r["D3C"] == "2025" and r["D2C"] == "216":
            r["V"] = "-5"
    pam, loc = _collect(env["raw"] / "ruim", pam_payload=bad)
    with pytest.raises(ValidationError, match="negativos"):
        _run(env, pam, loc)
    conn = _db(env)
    assert conn.execute("SELECT area_colhida_ha FROM pam_municipio_ano WHERE codigo_ibge='3148103' AND ano=2025").fetchone()[0] == 43850
    last = conn.execute("SELECT status, mensagem FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%' ORDER BY id DESC").fetchone()
    assert last[0] == "falha" and "negativos" in last[1]


def test_falha_no_meio_da_gravacao_faz_rollback(env, monkeypatch):
    original = load_sqlite.UPSERT
    monkeypatch.setattr(load_sqlite, "UPSERT", original.replace("producao_t, execucao_id)", "producao_t, coluna_inexistente)"))
    with pytest.raises(sqlite3.OperationalError):
        _run(env)
    conn = _db(env)
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execucao WHERE status = 'falha'").fetchone()[0] == 2


def test_bruto_sem_metadados_e_recusado(env, tmp_path):
    orphan = tmp_path / "sem_meta.json"
    orphan.write_bytes(env["pam"].read_bytes())
    with pytest.raises(PipelineError, match="metadados"):
        _run(env, orphan)


def test_execucao_interrompida_e_marcada(env):
    conn = load_sqlite.connect(env["db"])
    load_sqlite.start_execution(conn, "teste", "2026-11-14T00:00:00+00:00", "x.json", "0" * 64)
    conn.close()
    _run(env)
    statuses = [r[0] for r in _db(env).execute("SELECT status FROM execucao ORDER BY id")]
    assert statuses[0] == "interrompida" and statuses[1:] == ["sucesso", "sucesso"]


# --- Validação ---------------------------------------------------------------------------
def _frame(**over):
    base = {"codigo_ibge": "3148103", "municipio": "Patrocínio", "regiao": "Triângulo", "ano": 2025,
            "rendimento_kg_ha": 1110.0, "area_colhida_ha": 43850.0, "producao_t": 48669.0}
    base.update(over)
    return pd.DataFrame([base])


def test_validacao_detecta_inconsistencia_producao_area():
    r = validate_pam(_frame(producao_t=60000.0))
    assert r.ok and any("produção ÷ área" in w for w in r.warnings)


def test_validacao_detecta_duplicidade_e_chave_invalida():
    df = pd.concat([_frame(), _frame(), _frame(codigo_ibge="3512345")])
    r = validate_pam(df)
    assert any("duplicados" in e for e in r.errors) and any("códigos IBGE" in e for e in r.errors)


def test_validacao_recusa_zero_no_lugar_de_ausencia():
    assert any("NULL" in e for e in validate_pam(_frame(rendimento_kg_ha=0.0)).errors)


def test_validacao_aprova_dados_oficiais_sem_aviso_de_consistencia(env):
    res = _run(env)
    assert res["validacao"]["erros"] == []
    assert res["validacao"]["estatisticas"]["consistencia"]["desvio_maximo_pct"] < 1
