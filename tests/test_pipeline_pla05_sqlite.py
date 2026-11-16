"""Testes da etapa 3a da PLA-05: estrutura SQLite dos grupos (validacao_grupo e validacao_grupo_membro).

Só a estrutura: tabelas, restrições, índices e migração de um banco anterior. A gravação dos
grupos pelo pipeline é a etapa 3b. Todos os testes usam bancos temporários (tmp_path); nenhum
abre data/rigora.db nem grava em config.PROCESSED_DIR.
"""
import ast
import hashlib
import sqlite3
from pathlib import Path

import pytest

from pipeline import config, load_sqlite

TABELAS_NOVAS = {"validacao_grupo", "validacao_grupo_membro"}
INDICES_NOVOS = {"ix_grupo_nivel", "ix_membro_municipio"}
TABELAS_ANTIGAS = ["execucao", "pam_municipio_ano", "validacao_regra", "validacao_ocorrencia", "inmet_previsao"]


# --- Auxiliares ---------------------------------------------------------------------------------
@pytest.fixture
def conn(tmp_path):
    c = load_sqlite.connect(tmp_path / "teste.db")
    yield c
    c.close()


def execucao(c, fonte="IBGE/SIDRA PAM 1613 (café arábica, MG)"):
    with c:
        cur = c.execute("INSERT INTO execucao (fonte, data_coleta, data_execucao, status, arquivo_origem, hash_sha256) "
                        "VALUES (?, '2026-11-14T17:46:15+00:00', '2026-11-14T17:46:16+00:00', 'sucesso', 'bruto.json', ?)",
                        (fonte, "0" * 64))
        c.execute("INSERT INTO validacao_regra VALUES (?, 'PLA-05', 'Valores idênticos', 'plausibilidade', 'aviso', 'acionada', 2, 'x')",
                  (cur.lastrowid,))
    return cur.lastrowid


def grupo(c, eid, numero=1, ano=2016, rend=1200.0, area=9000.0, prod=10800.0, nivel="revisao_documental", regra="PLA-05",
          tm=2, tr=2):
    with c:
        c.execute("INSERT INTO validacao_grupo (execucao_id, regra, grupo, ano, nivel, rendimento_kg_ha, area_colhida_ha, "
                  "producao_t, total_municipios, total_registros, faixa_acima_de_ha, faixa_ate_ha) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 500, NULL)", (eid, regra, numero, ano, nivel, rend, area, prod, tm, tr))


def membro(c, eid, numero=1, ano=2016, codigo="3108404", nome="Botelhos", regra="PLA-05"):
    with c:
        c.execute("INSERT INTO validacao_grupo_membro VALUES (?, ?, ?, ?, ?, ?)", (eid, regra, numero, ano, codigo, nome))


def conteudo(c, tabelas=TABELAS_ANTIGAS) -> dict:
    """Hash do conteúdo (todas as linhas, em ordem) e da definição de cada tabela."""
    out = {}
    for t in tabelas:
        linhas = c.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3, 4").fetchall()
        ddl = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
        out[t] = (len(linhas), hashlib.sha256(repr([tuple(r) for r in linhas]).encode()).hexdigest(), ddl)
    return out


def banco_anterior(path: Path) -> sqlite3.Connection:
    """Banco no formato anterior à etapa 3a (sem as tabelas novas), com dados nas tabelas antigas."""
    c = load_sqlite.connect(path)
    with c:
        c.execute("DROP INDEX ix_grupo_nivel")
        c.execute("DROP INDEX ix_membro_municipio")
        c.execute("DROP TABLE validacao_grupo_membro")
        c.execute("DROP TABLE validacao_grupo")
    eid = execucao(c)
    with c:
        c.execute("INSERT INTO pam_municipio_ano (codigo_ibge, municipio, regiao, ano, rendimento_kg_ha, area_colhida_ha, "
                  "producao_t, execucao_id) VALUES ('3148103', 'Patrocínio', 'Triângulo', 2025, 1110, 43850, 48669, ?)", (eid,))
        c.execute("INSERT INTO validacao_ocorrencia (execucao_id, regra, codigo_ibge, ano, detalhe) VALUES (?, 'PLA-05', '3148103', 2025, 't')", (eid,))
        ie = execucao(c, fonte="INMET/Previsão do tempo (Patrocínio, 3148103)")
        c.execute("INSERT INTO inmet_previsao (codigo_ibge, data_coleta, data_previsao, periodo, horizonte_dias, temp_min_c, "
                  "temp_max_c, execucao_id) VALUES ('3148103', 't', '2026-11-14', 'tarde', 0, 18, 31, ?)", (ie,))
    return c


def nomes(c, tipo):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type=?", (tipo,))}


# --- 1 a 4: criação, idempotência e migração ----------------------------------------------------
def test_01_cria_as_duas_tabelas(conn):
    assert TABELAS_NOVAS <= nomes(conn, "table")
    cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(validacao_grupo)")]
    assert cols == ["execucao_id", "regra", "grupo", "ano", "nivel", "rendimento_kg_ha", "produtividade_sc_ha",
                    "area_colhida_ha", "producao_t", "total_municipios", "total_registros", "faixa_acima_de_ha", "faixa_ate_ha"]
    assert [r[1] for r in conn.execute("PRAGMA table_info(validacao_grupo_membro)")] == [
        "execucao_id", "regra", "grupo", "ano", "codigo_ibge", "municipio"]
    pk = lambda t: [r[1] for r in sorted(conn.execute(f"PRAGMA table_info({t})"), key=lambda r: r[5]) if r[5]]  # noqa: E731
    assert pk("validacao_grupo") == ["execucao_id", "regra", "grupo"]
    assert pk("validacao_grupo_membro") == ["execucao_id", "regra", "grupo", "codigo_ibge"]
    tipos = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(validacao_grupo_membro)")}
    assert tipos["codigo_ibge"] == "TEXT" and tipos["municipio"] == "TEXT"


def test_02_criacao_do_esquema_e_idempotente(tmp_path):
    c = load_sqlite.connect(tmp_path / "x.db")
    eid = execucao(c)
    grupo(c, eid)
    membro(c, eid)
    antes = conteudo(c, TABELAS_ANTIGAS + sorted(TABELAS_NOVAS))
    ddl = sorted(tuple(r) for r in c.execute("SELECT type, name, sql FROM sqlite_master"))
    for _ in range(3):
        load_sqlite.init_schema(c)
    c.close()
    c = load_sqlite.connect(tmp_path / "x.db")                       # reabrir também reaplica o esquema
    assert conteudo(c, TABELAS_ANTIGAS + sorted(TABELAS_NOVAS)) == antes
    assert sorted(tuple(r) for r in c.execute("SELECT type, name, sql FROM sqlite_master")) == ddl
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0       # sem controle de versão nesta etapa


def test_03_banco_anterior_sem_as_tabelas_novas_recebe_as_tabelas(tmp_path):
    c = banco_anterior(tmp_path / "antigo.db")
    assert not TABELAS_NOVAS & nomes(c, "table")
    c.close()
    c = load_sqlite.connect(tmp_path / "antigo.db")
    assert TABELAS_NOVAS <= nomes(c, "table") and INDICES_NOVOS <= nomes(c, "index")
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo_membro").fetchone()[0] == 0


def test_04_tabelas_e_registros_antigos_preservados(tmp_path):
    c = banco_anterior(tmp_path / "antigo.db")
    antes = conteudo(c)
    indices_antes = sorted(tuple(r) for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
    c.close()
    c = load_sqlite.connect(tmp_path / "antigo.db")
    assert conteudo(c) == antes                                       # linhas e definições iguais
    depois = sorted(tuple(r) for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
    assert [i for i in depois if i[0] not in INDICES_NOVOS] == indices_antes
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert load_sqlite.integrity_check(c) == {"chaves_estrangeiras_quebradas": 0, "duplicados": 0,
                                              "linhas_de_execucao_sem_sucesso": 0}


# --- 5 a 8: coluna gerada e restrições ----------------------------------------------------------
def test_05_produtividade_e_coluna_gerada(conn):
    eid = execucao(conn)
    grupo(conn, eid, numero=1, rend=1333.0, area=3.0, prod=4.0, nivel="informativo")
    grupo(conn, eid, numero=2, rend=1200.0)
    vals = dict(conn.execute("SELECT grupo, produtividade_sc_ha FROM validacao_grupo").fetchall())
    assert vals == {1: 1333 / 60, 2: 20.0} and vals[1] == 22.216666666666665    # igual ao JSON da etapa 2
    gerada = {r[1]: r[6] for r in conn.execute("PRAGMA table_xinfo(validacao_grupo)")}
    assert gerada["produtividade_sc_ha"] == 3                                     # 3 = coluna gerada armazenada
    with pytest.raises(sqlite3.OperationalError):
        with conn:
            conn.execute("INSERT INTO validacao_grupo (execucao_id, regra, grupo, ano, nivel, rendimento_kg_ha, produtividade_sc_ha, "
                         "area_colhida_ha, producao_t, total_municipios, total_registros) "
                         "VALUES (?, 'PLA-05', 3, 2016, 'informativo', 1000, 99, 1, 1, 2, 2)", (eid,))


def test_06_regra_diferente_de_pla05_e_recusada(conn):
    eid = execucao(conn)
    with conn:
        conn.execute("INSERT INTO validacao_regra VALUES (?, 'PLA-04', 'x', 'plausibilidade', 'aviso', 'acionada', 1, 'x')", (eid,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        grupo(conn, eid, regra="PLA-04")


@pytest.mark.parametrize("codigo", ["314810", "31481030", "", "3148103 "])
def test_07_codigo_ibge_com_tamanho_invalido_e_recusado(conn, codigo):
    eid = execucao(conn)
    grupo(conn, eid)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        membro(conn, eid, codigo=codigo)
    membro(conn, eid, codigo="3108404")                                           # 7 caracteres: aceito


def test_08_chaves_estrangeiras(conn):
    eid = execucao(conn)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        grupo(conn, 999)                                                          # execução inexistente
    eid2 = execucao(conn)
    with conn:
        conn.execute("DELETE FROM validacao_regra WHERE execucao_id = ?", (eid2,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        grupo(conn, eid2)                                                         # sem a linha da PLA-05 em validacao_regra
    grupo(conn, eid)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        membro(conn, eid, numero=2)                                               # grupo inexistente
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        membro(conn, eid, ano=2017)                                               # ano diferente do grupo
    eid3 = execucao(conn)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        membro(conn, eid3, numero=1)                                              # grupo 1 de outra execução
    fks = {}
    for r in conn.execute("PRAGMA foreign_key_list(validacao_grupo)"):          # id, seq, table, from, to, on_update, on_delete
        fks.setdefault((r[0], r[2], r[6]), []).append(r[3])
    assert sorted((t, tuple(cols), d) for (_, t, d), cols in fks.items()) == [
        ("execucao", ("execucao_id",), "RESTRICT"), ("validacao_regra", ("execucao_id", "regra"), "RESTRICT")]
    fkm = [(r[2], r[3], r[4], r[6]) for r in conn.execute("PRAGMA foreign_key_list(validacao_grupo_membro)")]
    assert sorted(fkm) == sorted([("validacao_grupo", c, c, "RESTRICT") for c in ("execucao_id", "regra", "grupo", "ano")])
    assert not any(r[2] == "pam_municipio_ano" for t in TABELAS_NOVAS for r in conn.execute(f"PRAGMA foreign_key_list({t})"))
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# --- 9 a 14: exclusões e associações ------------------------------------------------------------
def test_09_exclusao_de_execucao_com_grupos_e_bloqueada(conn):
    eid = execucao(conn)
    grupo(conn, eid)
    for sql in ("DELETE FROM execucao WHERE id = ?", "DELETE FROM validacao_regra WHERE execucao_id = ?"):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with conn:
                conn.execute(sql, (eid,))
    assert conn.execute("SELECT COUNT(*) FROM validacao_grupo").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM validacao_regra WHERE execucao_id = ?", (eid,)).fetchone()[0] == 1


def test_10_exclusao_de_grupo_com_membros_e_bloqueada(conn):
    eid = execucao(conn)
    grupo(conn, eid)
    membro(conn, eid)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with conn:
            conn.execute("DELETE FROM validacao_grupo WHERE execucao_id = ?", (eid,))
    assert conn.execute("SELECT COUNT(*) FROM validacao_grupo_membro").fetchone()[0] == 1
    with conn:  # exclusão explícita, na ordem: membros -> grupos -> regras
        conn.execute("DELETE FROM validacao_grupo_membro WHERE execucao_id = ?", (eid,))
        conn.execute("DELETE FROM validacao_grupo WHERE execucao_id = ?", (eid,))
        conn.execute("DELETE FROM validacao_regra WHERE execucao_id = ?", (eid,))
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_11_mesmo_municipio_em_dois_grupos_no_mesmo_ano_e_recusado(conn):
    eid = execucao(conn)
    grupo(conn, eid, numero=1)
    grupo(conn, eid, numero=2, rend=1000.0, area=2.0, prod=2.0, nivel="informativo")
    membro(conn, eid, numero=1)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        membro(conn, eid, numero=2)                                               # mesmo código, mesmo ano, outro grupo
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        membro(conn, eid, numero=1)                                               # repetido no mesmo grupo
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        grupo(conn, eid, numero=3)                                                # mesmos valores no mesmo ano


def test_12_mesmo_municipio_em_anos_diferentes_e_aceito(conn):
    eid = execucao(conn)
    grupo(conn, eid, numero=1, ano=2016)
    grupo(conn, eid, numero=2, ano=2021, rend=1260.0, area=7050.0, prod=8883.0)
    membro(conn, eid, numero=1, ano=2016)
    membro(conn, eid, numero=2, ano=2021)
    assert conn.execute("SELECT COUNT(*) FROM validacao_grupo_membro WHERE codigo_ibge = '3108404'").fetchone()[0] == 2


def test_13_mesmo_municipio_em_execucoes_diferentes_e_aceito(conn):
    e1, e2 = execucao(conn), execucao(conn)
    for eid in (e1, e2):
        grupo(conn, eid)                                                          # mesmos valores e mesmo número: históricos independentes
        membro(conn, eid)
    rows = conn.execute("SELECT execucao_id, grupo, ano FROM validacao_grupo_membro ORDER BY 1").fetchall()
    assert [tuple(r) for r in rows] == [(e1, 1, 2016), (e2, 1, 2016)]


def test_14_nome_do_municipio_pode_ser_null(conn):
    eid = execucao(conn)
    grupo(conn, eid)
    membro(conn, eid, nome=None)
    assert conn.execute("SELECT municipio FROM validacao_grupo_membro").fetchone()[0] is None


# --- 15: índices; 16: proteção dos dados reais --------------------------------------------------
def test_15_indices_criados(conn):
    assert INDICES_NOVOS <= nomes(conn, "index")
    cols = lambda i: [r[2] for r in conn.execute(f"PRAGMA index_info({i})")]  # noqa: E731
    assert cols("ix_grupo_nivel") == ["execucao_id", "nivel"]
    assert cols("ix_membro_municipio") == ["codigo_ibge", "ano"]
    tabela = dict(conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index'").fetchall())
    assert tabela["ix_grupo_nivel"] == "validacao_grupo" and tabela["ix_membro_municipio"] == "validacao_grupo_membro"
    plano = " ".join(r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM validacao_grupo_membro WHERE codigo_ibge = '3108404' AND ano = 2016"))
    assert "ix_membro_municipio" in plano or "autoindex" in plano


def test_16_nenhum_teste_abre_o_banco_real():
    """Varre os testes: load_sqlite.connect() sempre recebe um caminho, e nada referencia config.DB_PATH."""
    falhas = []
    for arq in sorted(Path(__file__).parent.glob("test_*.py")):
        for node in ast.walk(ast.parse(arq.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                f = node.func
                nome = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                dono = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
                if nome == "connect" and dono in ("load_sqlite", None) and not node.args and not node.keywords:
                    falhas.append(f"{arq.name}:{node.lineno} connect() sem caminho")
            if isinstance(node, ast.Attribute) and node.attr == "DB_PATH":
                falhas.append(f"{arq.name}:{node.lineno} usa config.DB_PATH")
    assert falhas == [], falhas


# =================================================================================================
# Etapa 3b: gravação dos grupos a partir de report.secoes["pla05_grupos"] (sem recalcular nada)
# =================================================================================================
import json  # noqa: E402

import pandas as pd  # noqa: E402

from pipeline import validate as V  # noqa: E402
from pipeline.run_pipeline import process  # noqa: E402
from pipeline.validate import ValidationError, validate_pam  # noqa: E402
from tests.test_pipeline_quality import (CAMPOS_ALTOS, MANHUACU, MONTE_CARMELO, PAT, base_loc, base_rows,  # noqa: E402
                                         collect, report_for, setv)

SECAO = "pla05_grupos"


@pytest.fixture
def env(tmp_path):
    return {"db": tmp_path / "data" / "rigora.db", "out": tmp_path / "processed", "raw": tmp_path / "raw"}


def copiar_valores(rows, origem, destino, ano):
    """O município `destino` recebe, em `ano`, os três valores de `origem` (coincidência injetada em memória)."""
    for v in ("112", "216", "214"):
        val = next(r["V"] for r in rows[1:] if r["D1C"] == origem and r["D3C"] == str(ano) and r["D2C"] == v)
        setv(rows, destino, ano, v, val)
    return rows


def com_coincidencias():
    rows = copiar_valores(base_rows(), PAT, MANHUACU, 2024)
    return copiar_valores(rows, CAMPOS_ALTOS, MONTE_CARMELO, 2020)


def run(env, rows=None, loc=None, sub="a"):
    pam, lc = collect(env["raw"] / sub, rows, loc)
    return process(pam, lc, env["db"], env["out"])               # pasta de saída temporária (correção A5)


def json_secao(env, eid):
    path = Path(env["db"]).parent / "reports" / f"qualidade_execucao_{eid:04d}.json"
    return json.loads(path.read_text(encoding="utf-8"))[SECAO]


def db(env):
    c = sqlite3.connect(env["db"])
    c.execute("PRAGMA foreign_keys = ON")
    return c


def grupos_do_banco(c, eid):
    """Grupos e membros gravados, no mesmo formato da seção JSON (só as tabelas de grupos, sem pam_municipio_ano)."""
    out = []
    for g in c.execute("SELECT grupo, ano, nivel, rendimento_kg_ha, produtividade_sc_ha, area_colhida_ha, producao_t, "
                       "total_municipios, total_registros FROM validacao_grupo WHERE execucao_id = ? ORDER BY grupo", (eid,)):
        municipios = [{"codigo_ibge": cod, "municipio": nome} for cod, nome in c.execute(
            "SELECT codigo_ibge, municipio FROM validacao_grupo_membro WHERE execucao_id = ? AND grupo = ? ORDER BY codigo_ibge",
            (eid, g[0]))]
        out.append(dict(zip(["grupo", "ano", "nivel", "rendimento_kg_ha", "produtividade_sc_ha", "area_colhida_ha",
                             "producao_t", "total_municipios", "total_registros"], g), municipios=municipios))
    return out


def contagens(c, eid):
    q = lambda t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE execucao_id = ?", (eid,)).fetchone()[0]  # noqa: E731
    return {t: q(t) for t in ("pam_municipio_ano", "validacao_regra", "validacao_ocorrencia", "validacao_grupo",
                              "validacao_grupo_membro")}


def frame(rows):
    """rows: (codigo, nome, ano, rendimento, área, produção)."""
    return pd.DataFrame([{"codigo_ibge": c, "municipio": n, "regiao": "Teste", "ano": a, "rendimento_kg_ha": r,
                          "area_colhida_ha": ar, "producao_t": p} for c, n, a, r, ar, p in rows])


def tres_niveis():
    return frame([("3100001", "A", 2020, 1500.0, 2.0, 3.0), ("3100002", "B", 2020, 1500.0, 2.0, 3.0),
                  ("3100003", "C", 2020, 1800.0, 470.0, 846.0), ("3100004", "D", 2020, 1800.0, 470.0, 846.0),
                  ("3100005", None, 2016, 1200.0, 9000.0, 10800.0), ("3100006", "F", 2016, 1200.0, 9000.0, 10800.0),
                  ("3100007", "G", 2018, 1333.0, 3.0, 4.0), ("3100008", "H", 2018, 1333.0, 3.0, 4.0)])


def gravar(c, report):
    """Cria uma execução e grava a validação numa transação (como fazem load_pam e fail_execution)."""
    eid = load_sqlite.start_execution(c, "IBGE/SIDRA PAM 1613 (café arábica, MG)", "t", "bruto.json", "0" * 64)
    with c:
        load_sqlite.record_validation(c, eid, report)
    return eid


# --- 17 a 20: gravação, correspondência com o JSON, produtividade, níveis e faixas ---------------
def test_17_persiste_grupos_e_membros(env):
    res = run(env, com_coincidencias())
    c = db(env)
    eid = res["execucao_pam"]
    assert contagens(c, eid)["validacao_grupo"] == 2 and contagens(c, eid)["validacao_grupo_membro"] == 4
    g = grupos_do_banco(c, eid)
    assert [(x["grupo"], x["ano"]) for x in g] == [(1, 2020), (2, 2024)]
    assert [m["codigo_ibge"] for m in g[1]["municipios"]] == sorted([PAT, MANHUACU])


def test_18_registros_sqlite_iguais_a_secao_json(env):
    res = run(env, com_coincidencias())
    eid = res["execucao_pam"]
    sec = json_secao(env, eid)
    assert grupos_do_banco(db(env), eid) == sec["grupos"]                       # campo a campo, na mesma ordem
    c = db(env)
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo WHERE execucao_id = ?", (eid,)).fetchone()[0] == sec["resumo"]["total_grupos"]
    assert c.execute("SELECT SUM(total_registros) FROM validacao_grupo WHERE execucao_id = ?", (eid,)).fetchone()[0] \
        == sec["resumo"]["total_registros"]


def test_19_produtividade_gerada_igual_a_rendimento_dividido_por_60(conn):
    eid = gravar(conn, validate_pam(tres_niveis()))
    for rend, prod in conn.execute("SELECT rendimento_kg_ha, produtividade_sc_ha FROM validacao_grupo WHERE execucao_id = ?", (eid,)):
        assert prod == rend / 60
    sec = validate_pam(tres_niveis()).secoes[SECAO]
    assert sorted(r[0] for r in conn.execute("SELECT produtividade_sc_ha FROM validacao_grupo")) == \
        sorted(g["produtividade_sc_ha"] for g in sec["grupos"])                  # igual ao valor do JSON


def test_20_niveis_e_faixas_preservados(conn, monkeypatch):
    sec = validate_pam(tres_niveis()).secoes[SECAO]
    eid = gravar(conn, validate_pam(tres_niveis()))
    limites = {n["nivel"]: (n["area_acima_de_ha"], n["area_ate_ha"]) for n in sec["classificacao"]["niveis"]}
    rows = conn.execute("SELECT grupo, nivel, faixa_acima_de_ha, faixa_ate_ha FROM validacao_grupo WHERE execucao_id = ? "
                        "ORDER BY grupo", (eid,)).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(g["grupo"], g["nivel"]) for g in sec["grupos"]]
    assert {r[1] for r in rows} == {"informativo", "atencao", "revisao_documental"}
    assert all((r[2], r[3]) == limites[r[1]] for r in rows)
    assert limites == {"informativo": (None, 10), "atencao": (10, 500), "revisao_documental": (500, None)}
    # limites de outra execução ficam gravados com os valores daquela execução
    monkeypatch.setattr(config, "PLA05_FAIXAS_HA", (2, 100))
    e2 = gravar(conn, validate_pam(tres_niveis()))
    faixas = {tuple(r) for r in conn.execute("SELECT nivel, faixa_acima_de_ha, faixa_ate_ha FROM validacao_grupo WHERE execucao_id = ?", (e2,))}
    assert faixas == {("informativo", None, 2), ("atencao", 2, 100), ("revisao_documental", 100, None)}
    assert {tuple(r) for r in conn.execute("SELECT nivel, faixa_acima_de_ha, faixa_ate_ha FROM validacao_grupo WHERE execucao_id = ?", (eid,))} \
        == {(k, *v) for k, v in limites.items()}


# --- 21 a 23: sem grupos, não verificada, execução reprovada --------------------------------------
def test_21_pla05_aprovada_sem_grupos_nao_grava_linhas(env):
    res = run(env)
    c = db(env)
    eid = res["execucao_pam"]
    assert c.execute("SELECT status FROM validacao_regra WHERE execucao_id = ? AND regra = 'PLA-05'", (eid,)).fetchone()[0] == "aprovada"
    assert contagens(c, eid)["validacao_grupo"] == contagens(c, eid)["validacao_grupo_membro"] == 0
    assert json_secao(env, eid)["grupos"] == []


def test_22_pla05_nao_verificada_nao_inventa_grupos(conn):
    report = validate_pam(frame([]).iloc[0:0])                                   # base vazia: INT-00, PLA-05 não roda
    assert report.secoes[SECAO]["status_regra"] == "nao_verificada"
    eid = load_sqlite.start_execution(conn, "IBGE/SIDRA PAM 1613 (café arábica, MG)", "t", "b.json", "0" * 64)
    load_sqlite.fail_execution(conn, eid, "ValidationError: nenhum registro para gravar", report)
    c = contagens(conn, eid)
    assert c["validacao_regra"] == 1 and c["validacao_grupo"] == c["validacao_grupo_membro"] == 0
    assert conn.execute("SELECT COUNT(*) FROM validacao_regra WHERE execucao_id = ? AND regra = 'PLA-05'", (eid,)).fetchone()[0] == 0


def test_23_execucao_reprovada_grava_os_grupos_do_relatorio(env):
    rows = setv(com_coincidencias(), PAT, 2025, "112", "18")                      # erro impeditivo (CON-02) + coincidências
    with pytest.raises(ValidationError):
        run(env, rows)
    c = db(env)
    eid, status = c.execute("SELECT id, status FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%' ORDER BY id DESC").fetchone()
    assert status == "falha"
    assert grupos_do_banco(c, eid) == json_secao(env, eid)["grupos"]            # coerente com o JSON de falha
    assert contagens(c, eid)["validacao_grupo"] == 2 and contagens(c, eid)["pam_municipio_ano"] == 0


# --- 24 a 26: histórico, regravação idempotente e ordem de exclusão --------------------------------
def test_24_duas_execucoes_tem_historicos_independentes(env):
    e1 = run(env, com_coincidencias(), sub="a")["execucao_pam"]
    e2 = run(env, com_coincidencias(), sub="b")["execucao_pam"]
    c = db(env)
    assert e1 != e2 and grupos_do_banco(c, e1) == grupos_do_banco(c, e2)
    assert [g["grupo"] for g in grupos_do_banco(c, e2)] == [1, 2]               # numeração por execução
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo").fetchone()[0] == 4


def test_25_regravacao_da_mesma_execucao_e_idempotente(env):
    eid = run(env, com_coincidencias())["execucao_pam"]
    c = db(env)
    original = grupos_do_banco(c, eid)
    report = report_for(com_coincidencias())
    estados = []
    for _ in range(3):
        with c:
            load_sqlite.record_validation(c, eid, report)
        estados.append((contagens(c, eid), grupos_do_banco(c, eid)))
    assert estados[0] == estados[1] == estados[2]
    assert estados[0][1] == original and estados[0][0]["validacao_grupo_membro"] == 4


def test_26_exclusao_na_ordem_membros_grupos_regras(env):
    eid = run(env, com_coincidencias())["execucao_pam"]
    c = db(env)
    sqls = []
    c.set_trace_callback(sqls.append)
    with c:
        load_sqlite.record_validation(c, eid, report_for(com_coincidencias()))
    c.set_trace_callback(None)
    # o rastreamento repete o comando a cada verificação de chave/cascata; vale a ordem da primeira aparição
    deletes = list(dict.fromkeys(s.split(" WHERE")[0] for s in sqls if s.lstrip().upper().startswith("DELETE")))
    assert deletes == ["DELETE FROM validacao_grupo_membro", "DELETE FROM validacao_grupo", "DELETE FROM validacao_regra"]
    inserts = list(dict.fromkeys(s.split(" (")[0] for s in sqls if s.lstrip().upper().startswith("INSERT")))
    assert inserts.index("INSERT INTO validacao_grupo") < inserts.index("INSERT INTO validacao_grupo_membro")
    assert inserts.index("INSERT INTO validacao_regra") < inserts.index("INSERT INTO validacao_grupo")


# --- 27 e 28: rollback --------------------------------------------------------------------------
def test_27_falha_na_insercao_dos_membros_desfaz_tudo(env):
    from pipeline.transform import attach_localidades, localidades_to_frame, pam_rows_to_frame
    rows = com_coincidencias()
    report = report_for(rows)
    report.secoes[SECAO]["grupos"][-1]["municipios"][-1]["codigo_ibge"] = "123"  # membro inválido no fim da lista
    df = V.to_storage_frame(attach_localidades(pam_rows_to_frame(rows[1:]), localidades_to_frame(base_loc())))
    c = load_sqlite.connect(env["db"])
    eid = load_sqlite.start_execution(c, "IBGE/SIDRA PAM 1613 (café arábica, MG)", "t", "b.json", "0" * 64)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        load_sqlite.load_pam(c, df, eid, report)                                 # caminho de sucesso
    assert contagens(c, eid) == dict.fromkeys(contagens(c, eid), 0)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        load_sqlite.fail_execution(c, eid, "falha", report)                      # caminho de falha
    assert contagens(c, eid) == dict.fromkeys(contagens(c, eid), 0)
    assert c.execute("SELECT status FROM execucao WHERE id = ?", (eid,)).fetchone()[0] == "em_andamento"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_28_falha_simulada_no_pipeline_nao_deixa_nada_parcial(env, monkeypatch):
    original = load_sqlite.record_pla05_groups

    def grava_e_falha(conn, exec_id, report):
        original(conn, exec_id, report)                                          # grupos e membros já inseridos...
        raise RuntimeError("falha simulada depois dos membros")                  # ...e a transação é desfeita
    monkeypatch.setattr(load_sqlite, "record_pla05_groups", grava_e_falha)
    with pytest.raises(RuntimeError, match="falha simulada"):
        run(env, com_coincidencias())
    c = db(env)
    eid, status, msg = c.execute("SELECT id, status, mensagem FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%'").fetchone()
    assert status == "falha" and "falha simulada" in msg
    assert contagens(c, eid) == dict.fromkeys(contagens(c, eid), 0)
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo").fetchone()[0] == 0


# --- 29 e 30: histórico de nomes e valores --------------------------------------------------------
def coleta_futura(env):
    """1ª coleta com coincidências; 2ª coleta com Patrocínio renomeado e Manhuaçu 2024 revisado (UPSERT)."""
    e1 = run(env, com_coincidencias(), sub="a")["execucao_pam"]
    loc = base_loc()
    next(x for x in loc if str(x["id"]) == PAT)["nome"] = "Patrocínio (nome revisado)"
    rows = copiar_valores(base_rows(), CAMPOS_ALTOS, MONTE_CARMELO, 2020)        # Manhuaçu 2024 volta ao valor original
    e2 = run(env, rows, loc, sub="b")["execucao_pam"]
    return e1, e2, json_secao(env, e1)


def test_29_nomes_historicos_preservados_apos_atualizacao(env):
    e1, e2, sec1 = coleta_futura(env)
    c = db(env)
    assert c.execute("SELECT municipio FROM pam_municipio_ano WHERE codigo_ibge = ? AND ano = 2024", (PAT,)).fetchone()[0] \
        == "Patrocínio (nome revisado)"
    nome = c.execute("SELECT municipio FROM validacao_grupo_membro WHERE execucao_id = ? AND codigo_ibge = ?", (e1, PAT)).fetchone()[0]
    assert nome == "Patrocínio" and grupos_do_banco(c, e1) == sec1["grupos"]


def test_30_valores_historicos_preservados_apos_atualizacao(env):
    e1, e2, sec1 = coleta_futura(env)
    c = db(env)
    g2024 = next(g for g in grupos_do_banco(c, e1) if g["ano"] == 2024)
    atual = c.execute("SELECT rendimento_kg_ha, area_colhida_ha, producao_t FROM pam_municipio_ano WHERE codigo_ibge = ? AND ano = 2024",
                      (MANHUACU,)).fetchone()
    assert (g2024["rendimento_kg_ha"], g2024["area_colhida_ha"], g2024["producao_t"]) != tuple(atual)   # pam mudou
    assert g2024 == next(g for g in sec1["grupos"] if g["ano"] == 2024)                                 # o grupo não
    assert [g["ano"] for g in grupos_do_banco(c, e2)] == [2020]                  # a 2ª coleta tem o seu próprio histórico


# --- 31 a 35: órfãos, compatibilidade, NULL, fonte única, INMET e formato -------------------------
def test_31_sem_registros_orfaos(env):
    coleta_futura(env)
    c = db(env)
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo_membro m LEFT JOIN validacao_grupo g "
                     "USING (execucao_id, regra, grupo, ano) WHERE g.grupo IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo g WHERE total_municipios <> (SELECT COUNT(*) FROM "
                     "validacao_grupo_membro m WHERE m.execucao_id = g.execucao_id AND m.grupo = g.grupo)").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo g LEFT JOIN validacao_regra r USING (execucao_id, regra) "
                     "WHERE r.status IS NULL").fetchone()[0] == 0


def test_32_compativel_com_as_regras_de_validacao_existentes(env):
    res = run(env, com_coincidencias())
    eid = res["execucao_pam"]
    c = db(env)
    assert res["integridade"] == {"chaves_estrangeiras_quebradas": 0, "duplicados": 0, "linhas_de_execucao_sem_sucesso": 0}
    regras = {r["regra"] for r in res["validacao"]["regras"]}
    assert contagens(c, eid)["validacao_regra"] == len(regras) == 26 and {"INT-07", "CON-04"} <= regras   # inclui pós-gravação
    pla = c.execute("SELECT status, registros_afetados FROM validacao_regra WHERE execucao_id = ? AND regra = 'PLA-05'", (eid,)).fetchone()
    assert tuple(pla) == ("acionada", 4)
    ocorr = sorted(c.execute("SELECT codigo_ibge, ano FROM validacao_ocorrencia WHERE execucao_id = ? AND regra = 'PLA-05'", (eid,)))
    membros = sorted(c.execute("SELECT codigo_ibge, ano FROM validacao_grupo_membro WHERE execucao_id = ?", (eid,)))
    assert ocorr == membros
    assert SECAO not in res["validacao"]                                         # retorno de process() sem a seção


def test_33_nome_de_municipio_null_gravado_como_null(conn):
    eid = gravar(conn, validate_pam(tres_niveis()))
    assert conn.execute("SELECT municipio FROM validacao_grupo_membro WHERE execucao_id = ? AND codigo_ibge = '3100005'",
                        (eid,)).fetchone()[0] is None


def test_34_fonte_e_a_secao_do_relatorio_sem_recalculo(conn, monkeypatch):
    report = validate_pam(tres_niveis())
    report.secoes[SECAO]["grupos"][0]["municipios"][0]["municipio"] = "Nome que só existe na seção"
    for nome in ("pla05_groups", "pla05_section", "pla05_level", "validate_pam"):
        monkeypatch.setattr(V, nome, lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria recalcular")))
    eid = gravar(conn, report)
    nomes = [r[0] for r in conn.execute("SELECT municipio FROM validacao_grupo_membro WHERE execucao_id = ?", (eid,))]
    assert "Nome que só existe na seção" in nomes
    assert len(nomes) == sum(len(g["municipios"]) for g in report.secoes[SECAO]["grupos"])


def test_35_inmet_e_formato_desconhecido(env, conn):
    from tests import test_pipeline_inmet as T_INMET
    T_INMET.run({"db": env["db"], "out": env["out"], "raw": env["raw"]})          # relatório do INMET: sem seção
    c = db(env)
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo").fetchone()[0] == 0
    report = validate_pam(tres_niveis())
    report.secoes[SECAO]["versao"] = 99
    eid = load_sqlite.start_execution(conn, "IBGE/SIDRA PAM 1613 (café arábica, MG)", "t", "b.json", "0" * 64)
    with pytest.raises(ValueError, match="versao=99"):
        with conn:
            load_sqlite.record_validation(conn, eid, report)
    assert contagens(conn, eid) == dict.fromkeys(contagens(conn, eid), 0)
