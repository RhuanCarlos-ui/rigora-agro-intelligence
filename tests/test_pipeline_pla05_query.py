"""Testes da etapa 3c da PLA-05: consultas dos grupos gravados (query.py).

As consultas são somente leitura e mostram o que está gravado em validacao_grupo e
validacao_grupo_membro, sem recalcular e sem consultar pam_municipio_ano. Todos os bancos são
temporários (tmp_path); o pipeline, quando usado para montar o banco, grava CSVs em pasta temporária.
"""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from pipeline import load_sqlite, query
from pipeline.validate import ValidationError, validate_pam
from tests.test_pipeline_pla05_sqlite import (banco_anterior, com_coincidencias, coleta_futura, frame, json_secao, run,
                                              tres_niveis)
from tests.test_pipeline_quality import PAT, setv

FONTE = "IBGE/SIDRA PAM 1613 (café arábica, MG)"


@pytest.fixture
def env(tmp_path):
    return {"db": tmp_path / "data" / "rigora.db", "out": tmp_path / "processed", "raw": tmp_path / "raw"}


def gravar(db, df, status="sucesso", fonte=FONTE):
    """Execução sintética com os grupos de validate_pam(df), gravados pelo caminho normal (record_validation)."""
    c = load_sqlite.connect(db)
    report = validate_pam(df)
    eid = load_sqlite.start_execution(c, fonte, "2026-11-14T17:46:15+00:00", "bruto.json", "0" * 64)
    if status == "sucesso":
        with c:
            load_sqlite.record_validation(c, eid, report)
        load_sqlite.finish_execution(c, eid, len(df))
    else:
        load_sqlite.fail_execution(c, eid, "ValidationError: simulada", report)
    c.close()
    return eid


def cli(capsys, *args, db):
    rc = query.main([*map(str, args), "--db", str(db)])
    out = capsys.readouterr()
    return rc, out.out, out.err


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- 1 a 3: listagem, filtro e execução padrão ---------------------------------------------------
def test_01_lista_os_grupos_de_uma_execucao(tmp_path):
    db = tmp_path / "t.db"
    eid = gravar(db, tres_niveis())
    rows = query.run("grupos", (eid, None, None), db)
    assert list(rows[0].keys()) == ["execucao_id", "status_execucao", "regra", "grupo", "ano", "nivel", "faixa",
                                    "rendimento_kg_ha", "sc_ha", "area_colhida_ha", "producao_t", "total_municipios",
                                    "total_registros"]
    assert [(r["grupo"], r["ano"], r["nivel"], r["faixa"]) for r in rows] == [
        (1, 2016, "revisao_documental", "acima de 500 ha"), (2, 2018, "informativo", "até 10 ha"),
        (3, 2020, "informativo", "até 10 ha"), (4, 2020, "atencao", "10–500 ha")]
    c = sqlite3.connect(db)
    gravados = c.execute("SELECT grupo, rendimento_kg_ha, round(produtividade_sc_ha, 2), area_colhida_ha, producao_t, "
                         "total_municipios, total_registros FROM validacao_grupo WHERE execucao_id = ? ORDER BY grupo", (eid,)).fetchall()
    assert [(r["grupo"], r["rendimento_kg_ha"], r["sc_ha"], r["area_colhida_ha"], r["producao_t"], r["total_municipios"],
             r["total_registros"]) for r in rows] == gravados                    # valores direto do SQLite
    assert all(r["execucao_id"] == eid and r["regra"] == "PLA-05" and r["status_execucao"] == "sucesso" for r in rows)


def test_02_filtro_por_nivel(tmp_path, capsys):
    db = tmp_path / "t.db"
    eid = gravar(db, tres_niveis())
    assert [r["grupo"] for r in query.run("grupos", (eid, "atencao", "atencao"), db)] == [4]
    assert [r["grupo"] for r in query.run("grupos", (eid, "informativo", "informativo"), db)] == [2, 3]
    rc, out, _ = cli(capsys, "grupos", eid, "--nivel", "revisao_documental", db=db)
    assert rc == 0 and len(out.strip().splitlines()) == 2 and "revisao_documental" in out
    with pytest.raises(SystemExit) as e:
        query.main(["grupos", str(eid), "--nivel", "suspeito", "--db", str(db)])
    assert e.value.code == 2


def test_03_execucao_padrao_e_a_mais_recente_do_sidra_com_sucesso_e_grupos(tmp_path, capsys):
    db = tmp_path / "t.db"
    a = gravar(db, tres_niveis())                                                 # sucesso, com grupos
    gravar(db, frame([("3100001", "A", 2020, 1500.0, 2.0, 3.0)]))                  # sucesso, sem grupos
    gravar(db, tres_niveis(), status="falha")                                     # mais nova, mas reprovada
    gravar(db, tres_niveis(), fonte="INMET/Previsão do tempo (Patrocínio, 3148103)")  # outra fonte
    assert {r["execucao_id"] for r in query.run("grupos", (None, None, None), db)} == {a}
    d = gravar(db, tres_niveis())                                                 # nova execução válida
    assert {r["execucao_id"] for r in query.run("grupos", (None, None, None), db)} == {d}
    rc, out, _ = cli(capsys, "grupos", db=db)
    assert rc == 0 and out.splitlines()[1].startswith(f"{d} | sucesso | PLA-05 | 1 |")


# --- 4 e 5: detalhe de um grupo --------------------------------------------------------------------
def test_04_detalhe_de_grupo_existente(env, capsys):
    eid = run(env, com_coincidencias())["execucao_pam"]
    sec = {g["grupo"]: g for g in json_secao(env, eid)["grupos"]}
    resumo = query.run("grupo", (eid, 2), env["db"])[0]
    membros = query.run("grupo_membros", (eid, 2), env["db"])
    g = sec[2]
    assert (resumo["ano"], resumo["nivel"], resumo["faixa"], resumo["rendimento_kg_ha"], resumo["area_colhida_ha"],
            resumo["producao_t"], resumo["total_municipios"], resumo["total_registros"]) == (
        g["ano"], g["nivel"], "acima de 500 ha", g["rendimento_kg_ha"], g["area_colhida_ha"], g["producao_t"],
        g["total_municipios"], g["total_registros"])
    assert resumo["sc_ha"] == round(g["produtividade_sc_ha"], 2) and resumo["data_coleta"]
    assert [dict(m) for m in membros] == g["municipios"]
    rc, out, _ = cli(capsys, "grupo", eid, 2, db=env["db"])
    linhas = out.splitlines()
    assert rc == 0 and linhas[0] == "Grupo" and linhas[1].startswith("execucao_id | data_coleta |")
    assert linhas[4] == "Municípios (2)" and linhas[5] == "codigo_ibge | municipio"
    assert linhas[6:] == ["3139409 | Manhuaçu", "3148103 | Patrocínio"]


def test_05_grupo_ou_execucao_inexistente(tmp_path, capsys):
    db = tmp_path / "t.db"
    eid = gravar(db, tres_niveis())
    assert cli(capsys, "grupo", eid, 99, db=db)[:2] == (1, f"Grupo 99 não encontrado na execução {eid}.\n")
    assert cli(capsys, "grupo", 999, 1, db=db)[:2] == (1, "Grupo 1 não encontrado na execução 999.\n")
    assert cli(capsys, "grupos", 999, db=db)[:2] == (1, "Nenhum grupo PLA-05 na execução 999.\n")
    for args in (["grupo", str(eid)], ["grupo", "0", "1"], ["grupo", "x", "1"], ["grupo", str(eid), "1.5"], ["grupos", "-1"]):
        with pytest.raises(SystemExit) as e:
            query.main(args + ["--db", str(db)])
        assert e.value.code == 2, args


# --- 6 a 8: consulta por município e histórico -----------------------------------------------------
def test_06_grupos_de_um_municipio(env, capsys):
    eid = run(env, com_coincidencias())["execucao_pam"]
    rows = query.run("grupos_municipio", (PAT,), env["db"])
    assert [(r["execucao_id"], r["status_execucao"], r["grupo"], r["ano"], r["nivel"], r["municipio"]) for r in rows] == [
        (eid, "sucesso", 2, 2024, "revisao_documental", "Patrocínio")]
    rc, out, _ = cli(capsys, "grupos_municipio", PAT, db=env["db"])
    assert rc == 0 and "Patrocínio" in out and len(out.strip().splitlines()) == 2


def test_07_municipio_sem_grupos_e_codigo_invalido(env, capsys):
    run(env, com_coincidencias())
    rc, out, _ = cli(capsys, "grupos_municipio", "3100000", db=env["db"])
    assert (rc, out) == (1, "O município 3100000 não aparece em nenhum grupo PLA-05 gravado neste banco.\n")
    for codigo in ("31481", "31481030", "abcdefg", "3148 03", None):
        with pytest.raises(SystemExit) as e:
            query.main(["grupos_municipio"] + ([codigo] if codigo else []) + ["--db", str(env["db"])])
        assert e.value.code == 2, codigo


def test_08_historico_em_varias_execucoes_inclusive_reprovada(env):
    e1, e2, sec1 = coleta_futura(env)                                             # 2ª coleta renomeia Patrocínio
    rows_reprovada = setv(com_coincidencias(), PAT, 2025, "112", "18")             # reprovada (CON-02), com grupos
    with pytest.raises(ValidationError):
        run(env, rows_reprovada, sub="c")
    c = sqlite3.connect(env["db"])
    e3 = c.execute("SELECT max(id) FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%'").fetchone()[0]
    assert c.execute("SELECT municipio FROM pam_municipio_ano WHERE codigo_ibge = ? AND ano = 2024", (PAT,)).fetchone()[0] \
        == "Patrocínio (nome revisado)"
    rows = query.run("grupos_municipio", (PAT,), env["db"])
    assert [(r["execucao_id"], r["status_execucao"], r["ano"]) for r in rows] == [(e3, "falha", 2024), (e1, "sucesso", 2024)]
    g1 = next(g for g in sec1["grupos"] if g["ano"] == 2024)
    r1 = rows[1]
    assert r1["municipio"] == "Patrocínio"                                        # nome daquela execução
    assert (r1["rendimento_kg_ha"], r1["area_colhida_ha"], r1["producao_t"]) == (
        g1["rendimento_kg_ha"], g1["area_colhida_ha"], g1["producao_t"])          # valores daquela execução
    assert e2 not in {r["execucao_id"] for r in rows}                             # a 2ª coleta não tem Patrocínio em grupo


# --- 9 a 13: banco sem tabelas, somente leitura, consultas antigas, NULL e SQL --------------------
def test_09_banco_sem_tabelas_pla05_tem_mensagem_clara(tmp_path, capsys):
    db = tmp_path / "antigo.db"
    banco_anterior(db).close()
    for nome, params in (("grupos", (None, None, None)), ("grupo", (1, 1)), ("grupo_membros", (1, 1)), ("grupos_municipio", (PAT,))):
        with pytest.raises(query.ConsultaError, match="ainda não tem as tabelas de grupos da PLA-05"):
            query.run(nome, params, db)
    for args in (["grupos"], ["grupo", "1", "1"], ["grupos_municipio", PAT]):
        rc, out, err = cli(capsys, *args, db=db)
        assert rc == 1 and out == "" and "ainda não tem as tabelas de grupos da PLA-05" in err
        assert "no such table" not in err and "faça um backup" in err
    tabelas = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"validacao_grupo", "validacao_grupo_membro"} & tabelas           # a consulta não cria tabelas
    assert len(query.run("historico", (), db)) == 2                               # consultas antigas seguem funcionando


def test_10_consultas_sao_somente_leitura(env, capsys):
    eid = run(env, com_coincidencias())["execucao_pam"]
    db = Path(env["db"])
    antes = sha(db)
    pasta = sorted(p.name for p in db.parent.iterdir())
    for args in (["grupos"], ["grupos", eid], ["grupos", eid, "--nivel", "atencao"], ["grupo", eid, 1], ["grupo", eid, 99],
                 ["grupos_municipio", PAT], ["grupos_municipio", "3100000"], ["historico"], ["qualidade", eid]):
        cli(capsys, *args, db=db)
    assert sha(db) == antes                                                       # nem um byte mudou
    assert sorted(p.name for p in db.parent.iterdir()) == pasta                   # nenhum -journal/-wal criado
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)               # mesma forma de abrir de query.run
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.execute("DELETE FROM validacao_grupo_membro")


def test_11_consultas_antigas_preservadas(env, capsys):
    from tests import test_pipeline_inmet as T_INMET
    res = run(env, com_coincidencias())
    T_INMET.run({"db": env["db"], "out": env["out"], "raw": env["raw"]})
    db = env["db"]
    assert len(query.run("historico", (), db)) == 3
    assert [r["ano"] for r in query.run("municipio", (PAT,), db)] == list(range(2016, 2026))
    assert len(query.run("ranking", (2025, 2), db)) == 2
    assert len(query.run("qualidade", (res["execucao_pam"],), db)) == 26
    assert query.run("rastrear", (PAT, 2024), db)[0]["execucao_id"] == res["execucao_pam"]
    assert len(query.run("previsao", (PAT, PAT), db)) == 9
    for args in (["historico"], ["municipio", PAT], ["ranking", "2025", "--top", "3"], ["qualidade"], ["rastrear", PAT],
                 ["previsao"]):
        rc, out, _ = cli(capsys, *args, db=db)
        assert rc == 0 and out.strip(), args


def test_12_nome_null_aparece_vazio_no_detalhe(tmp_path, capsys):
    db = tmp_path / "t.db"
    eid = gravar(db, tres_niveis())                                               # 3100005 sem nome (grupo 1, 2016)
    assert [dict(m) for m in query.run("grupo_membros", (eid, 1), db)] == [
        {"codigo_ibge": "3100005", "municipio": None}, {"codigo_ibge": "3100006", "municipio": "F"}]
    rc, out, _ = cli(capsys, "grupo", eid, 1, db=db)
    assert rc == 0 and "3100005 | \n" in out


def test_13_sql_das_consultas_pla05_so_le_tabelas_de_grupos():
    for nome in query.PLA05_QUERIES:
        sql = query.QUERIES[nome].lower()
        assert "pam_municipio_ano" not in sql, nome                               # nada de dados mutáveis
        assert "validacao_grupo" in sql, nome
    for nome, sql in query.QUERIES.items():
        assert not any(w in sql.upper().split() for w in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER")), nome
