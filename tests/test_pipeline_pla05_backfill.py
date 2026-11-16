"""Testes da etapa 4 (fase 4a): backfill histórico dos grupos da PLA-05 (pipeline/backfill_pla05.py).

Cada teste monta, num banco temporário, uma execução do IBGE/SIDRA no estado da execução 3 real:
gravada pelo pipeline e sem grupos nas tabelas novas (que são apagadas). Nenhum teste usa
data/rigora.db, grava em data/processed ou acessa a rede.
"""
import hashlib
import inspect
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

from pipeline import backfill_pla05 as B
from pipeline import load_sqlite
from tests.test_pipeline_pla05_sqlite import com_coincidencias, grupos_do_banco, json_secao, run
from tests.test_pipeline_quality import MANHUACU, PAT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def montar(tmp_path, sem_tabelas=True):
    """Execução gravada pelo pipeline e devolvida ao estado histórico (sem grupos; opcionalmente sem as tabelas)."""
    env = {"db": tmp_path / "data" / "rigora.db", "out": tmp_path / "processed", "raw": tmp_path / "raw"}
    res = run(env, com_coincidencias())
    eid, lid = res["execucao_pam"], res["execucao_localidades"]
    c = sqlite3.connect(env["db"])
    c.execute("PRAGMA foreign_keys = ON")
    gravados = grupos_do_banco(c, eid)                                           # o que a etapa 3b gravaria
    with c:
        c.execute("DELETE FROM validacao_grupo_membro")
        c.execute("DELETE FROM validacao_grupo")
        if sem_tabelas:
            for sql in ("DROP INDEX ix_grupo_nivel", "DROP INDEX ix_membro_municipio",
                        "DROP TABLE validacao_grupo_membro", "DROP TABLE validacao_grupo"):
                c.execute(sql)
    c.close()
    return {"db": env["db"], "eid": eid, "lid": lid, "raw": env["raw"] / "a", "sec": json_secao(env, eid),
            "gravados": gravados, "tmp": tmp_path}


@pytest.fixture
def h(tmp_path):
    return montar(tmp_path)


def verificar(h, **kw):
    return B.verificar(h["db"], kw.pop("execucao", h["eid"]), kw.pop("loc", h["lid"]), h["raw"], **kw)


def backup(h, nome="bk.db"):
    dest = h["tmp"] / nome
    shutil.copy(h["db"], dest)
    return dest


def gravar(h, bk=None, **kw):
    return B.gravar(h["db"], h["eid"], h["lid"], bk or backup(h), h["raw"], **kw)


def falhas(res):
    return [c.codigo for c in res.conferencias if not c.ok]


def objetos(db):
    c = sqlite3.connect(db)
    try:
        return sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master"))
    finally:
        c.close()


def historico(db):
    c = sqlite3.connect(db)
    try:
        return B.conteudo_historico(c)
    finally:
        c.close()


def editar(db, *sqls):
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys = ON")
    with c:
        for sql, params in sqls:
            c.execute(sql, params)
    c.close()


# --- 1 a 3: reconstrução idêntica e verificação somente leitura ---------------------------------
def test_01_reconstrucao_identica_ao_que_o_pipeline_gravou(h):
    res = verificar(h)
    assert res.secao == h["sec"]                                                  # igual à seção JSON daquela execução
    assert grupos_do_banco_de(res) == h["gravados"]                               # e ao que a etapa 3b gravou


def grupos_do_banco_de(res):
    """Grupos da reconstrução no formato de grupos_do_banco (para comparar com o que a 3b gravaria)."""
    return [dict(g) for g in res.secao["grupos"]]


def test_02_verificacao_completa_e_correspondencia_dos_registros(h):
    res = verificar(h)
    assert res.ok, [(c.codigo, c.detalhe) for c in res.conferencias if not c.ok]
    assert [c.codigo for c in res.conferencias] == [f"C{i:02d}" for i in range(1, 17)]
    c = sqlite3.connect(h["db"])
    ocorr = sorted(c.execute("SELECT codigo_ibge, ano FROM validacao_ocorrencia WHERE execucao_id = ? AND regra = 'PLA-05'", (h["eid"],)))
    pares = sorted((m["codigo_ibge"], g["ano"]) for g in res.secao["grupos"] for m in g["municipios"])
    assert pares == ocorr == sorted([(PAT, 2024), (MANHUACU, 2024), ("3111507", 2020), ("3143104", 2020)])
    esperado = {"total_grupos": 2, "total_registros": 4, "total_municipios": 4,
                "grupos_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 2},
                "registros_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 4}}
    assert verificar(h, esperado=esperado).ok
    errado = dict(esperado, total_grupos=3)
    assert falhas(verificar(h, esperado=errado)) == ["C09"]
    errado = dict(esperado, grupos_por_nivel={"informativo": 1, "atencao": 0, "revisao_documental": 1})
    assert falhas(verificar(h, esperado=errado)) == ["C10"]
    assert B.ESPERADO[3]["total_grupos"] == 275 and B.ESPERADO[3]["registros_por_nivel"]["atencao"] == 221


def test_03_modo_verificar_e_somente_leitura(h, capsys):
    antes, pasta, objs = sha(h["db"]), sorted(p.name for p in h["db"].parent.iterdir()), objetos(h["db"])
    assert B.main(["--db", str(h["db"]), "--execucao", str(h["eid"]), "--localidades-execucao", str(h["lid"]), "--raw", str(h["raw"])]) == 0
    out = capsys.readouterr().out
    assert "Resultado: todas as conferências passaram" in out and "somente leitura, nada foi gravado" in out
    assert sha(h["db"]) == antes and objetos(h["db"]) == objs                     # nem um byte, nem uma tabela
    assert sorted(p.name for p in h["db"].parent.iterdir()) == pasta              # nenhum arquivo novo
    assert "validacao_grupo" not in objs


# --- 4: cada conferência que falha interrompe, sem gravar ---------------------------------------
def _bruto_adulterado(h):
    p = next(h["raw"].glob("sidra_pam_*[0-9]Z.json"))                            # o bruto, não o .meta.json
    novo = p.read_bytes().replace(b"Patroc", b"Patrok")
    assert novo != p.read_bytes()
    p.write_bytes(novo)


def _meta_ausente(h):
    next(h["raw"].glob("ibge_localidades_*.meta.json")).unlink()


CENARIOS = {
    "bruto_adulterado": (_bruto_adulterado, "C03"),
    "meta_ausente": (_meta_ausente, "C04"),
    "execucao_reprovada": (lambda h: editar(h["db"], ("UPDATE execucao SET status = 'falha' WHERE id = ?", (h["eid"],))), "C01"),
    "localidades_de_outra_chamada": (lambda h: editar(h["db"], ("UPDATE execucao SET data_execucao = '2020-01-01' WHERE id = ?", (h["lid"],))), "C02"),
    "regra_pla05_ausente": (lambda h: editar(h["db"], ("DELETE FROM validacao_regra WHERE execucao_id = ? AND regra = 'PLA-05'", (h["eid"],))), "C05"),
    "ocorrencia_a_mais": (lambda h: editar(h["db"], ("INSERT INTO validacao_ocorrencia (execucao_id, regra, codigo_ibge, ano, detalhe) "
                                                     "VALUES (?, 'PLA-05', '3148103', 2016, 'x')", (h["eid"],))), "C09"),
    "valor_alterado": (lambda h: editar(h["db"], ("UPDATE pam_municipio_ano SET producao_t = producao_t + 1 WHERE codigo_ibge = ? AND ano = 2024", (PAT,))), "C13"),
    "nome_alterado": (lambda h: editar(h["db"], ("UPDATE pam_municipio_ano SET municipio = 'Outro' WHERE codigo_ibge = ? AND ano = 2024", (PAT,))), "C14"),
    "regra_com_outra_contagem": (lambda h: editar(h["db"], ("UPDATE validacao_regra SET registros_afetados = registros_afetados + 1 "
                                                            "WHERE execucao_id = ? AND regra = 'CON-05'", (h["eid"],))), "C08"),
    "coleta_posterior_D4": (lambda h: editar(h["db"],
        ("INSERT INTO execucao (fonte, data_coleta, data_execucao, status, arquivo_origem, hash_sha256) "
         "VALUES ('IBGE/SIDRA PAM 1613 (café arábica, MG)', 't', 't', 'sucesso', 'novo.json', ?)", ("1" * 64,)),
        ("UPDATE pam_municipio_ano SET execucao_id = (SELECT max(id) FROM execucao) WHERE codigo_ibge = ? AND ano = 2024", (MANHUACU,))), "C12"),
}


@pytest.mark.parametrize("nome", sorted(CENARIOS))
def test_04_conferencia_que_falha_interrompe_sem_gravar(h, nome):
    mudar, codigo = CENARIOS[nome]
    mudar(h)
    res = verificar(h)
    assert not res.ok and codigo in falhas(res), (nome, falhas(res))
    bk = backup(h)
    antes, objs = sha(h["db"]), objetos(h["db"])
    with pytest.raises(B.BackfillError, match="nada foi gravado"):
        gravar(h, bk)
    assert sha(h["db"]) == antes and objetos(h["db"]) == objs


def test_05_execucao_que_ja_tem_grupos_e_recusada(tmp_path):
    h = montar(tmp_path, sem_tabelas=False)
    gravar(h)
    res = verificar(h)
    assert falhas(res) == ["C06"] and "2 grupos já gravados" in res.conferencias[5].detalhe


# --- 6 a 8: gravação, tabelas históricas e integridade -----------------------------------------
def test_06_gravacao_em_banco_temporario(h):
    hist = historico(h["db"])
    info = gravar(h)
    assert (info["grupos"], info["membros"], info["integridade"]) == (2, 4, "ok")
    assert {"validacao_grupo", "validacao_grupo_membro", "ix_grupo_nivel", "ix_membro_municipio"} <= set(info["objetos_criados"])
    c = sqlite3.connect(h["db"])
    assert grupos_do_banco(c, h["eid"]) == h["sec"]["grupos"] == h["gravados"]     # igual ao JSON e ao que a 3b gravaria
    assert historico(h["db"]) == hist                                              # tabelas históricas intactas
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("SELECT COUNT(*) FROM validacao_grupo_membro m LEFT JOIN validacao_grupo g "
                     "USING (execucao_id, regra, grupo, ano) WHERE g.grupo IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT detalhe FROM validacao_regra WHERE execucao_id = ? AND regra = 'PLA-05'", (h["eid"],)).fetchone()[0] \
        .startswith("2 grupos de municípios")                                     # texto da regra não foi regravado
    faixas = {tuple(r) for r in c.execute("SELECT nivel, faixa_acima_de_ha, faixa_ate_ha FROM validacao_grupo")}
    assert faixas == {("revisao_documental", 500.0, None)}                         # faixas atuais gravadas (D3)


def test_07_banco_que_ja_tem_as_tabelas_so_recebe_linhas(tmp_path):
    h = montar(tmp_path, sem_tabelas=False)
    objs = objetos(h["db"])
    info = gravar(h)
    assert info["objetos_criados"] == [] and objetos(h["db"]) == objs and info["grupos"] == 2


def test_08_somente_insercao_sem_replace_ou_upsert():
    fonte = Path(B.__file__).read_text(encoding="utf-8").upper()
    assert "INSERT OR REPLACE" not in fonte and "ON CONFLICT" not in fonte and "REPLACE INTO" not in fonte
    assert "RECORD_VALIDATION(" not in fonte                                       # D2: só record_pla05_groups
    assert "UPDATE " not in fonte.replace("UPDATE\\", "") and "DELETE FROM" not in fonte


# --- 9 e 10: rollback e prevenção de duplicação -------------------------------------------------
def test_09_falha_no_meio_desfaz_tudo_inclusive_as_tabelas(h, monkeypatch):
    original = load_sqlite.record_pla05_groups

    def grava_e_falha(conn, eid, report):
        original(conn, eid, report)                                               # grupos e membros inseridos...
        raise RuntimeError("falha simulada depois dos membros")                   # ...e a transação é desfeita
    bk = backup(h)
    antes, objs, hist = sha(h["db"]), objetos(h["db"]), historico(h["db"])
    monkeypatch.setattr(load_sqlite, "record_pla05_groups", grava_e_falha)
    with pytest.raises(B.BackfillError, match="C16"):                              # o ensaio em memória já falha
        gravar(h, bk)
    monkeypatch.setattr(B, "verificar", lambda *a, **k: _ok_sem_ensaio(h))       # pula a verificação e força a gravação
    with pytest.raises(RuntimeError, match="falha simulada"):
        gravar(h, bk)
    assert objetos(h["db"]) == objs and "validacao_grupo" not in objs             # criação das tabelas desfeita
    assert historico(h["db"]) == hist and sha(h["db"]) == antes                   # arquivo idêntico ao de antes


def _ok_sem_ensaio(h):
    res = B.Resultado(h["db"], h["eid"], h["lid"])
    res.add("X", "forçado no teste", True)
    res.report = B.reconstruir(next(h["raw"].glob("sidra_pam_*[0-9]Z.json")), next(h["raw"].glob("ibge_localidades_*[0-9]Z.json")))
    return res


def test_10_verificacao_interna_desfaz_se_tabela_historica_mudar(h, monkeypatch):
    bk = backup(h)
    antes = sha(h["db"])
    monkeypatch.setattr(B, "verificar", lambda *a, **k: _ok_sem_ensaio(h))
    chamadas = iter([{"execucao": "a"}, {"execucao": "b"}])                       # simula mudança durante a transação
    monkeypatch.setattr(B, "conteudo_historico", lambda conn: next(chamadas))
    with pytest.raises(B.BackfillError, match="tabela histórica mudou"):
        gravar(h, bk)
    assert sha(h["db"]) == antes


def test_11_nova_tentativa_nao_duplica(h, monkeypatch):
    gravar(h)
    c = sqlite3.connect(h["db"])
    contar = lambda: tuple(c.execute("SELECT (SELECT COUNT(*) FROM validacao_grupo), (SELECT COUNT(*) FROM validacao_grupo_membro)").fetchone())  # noqa: E731
    assert contar() == (2, 4)
    with pytest.raises(B.BackfillError, match="C06"):                              # recusada na verificação
        gravar(h, backup(h, "bk2.db"))
    monkeypatch.setattr(B, "verificar", lambda *a, **k: _ok_sem_ensaio(h))       # mesmo pulando a verificação...
    with pytest.raises(B.BackfillError, match="já tem 2 grupos"):                  # ...a transação recusa
        gravar(h, backup(h, "bk3.db"))
    conn = sqlite3.connect(h["db"])
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):                    # e o banco também recusa
        with conn:
            load_sqlite.record_pla05_groups(conn, h["eid"], _ok_sem_ensaio(h).report)
    assert contar() == (2, 4)


# --- 12 a 14: backup, rede e linha de comando ----------------------------------------------------
def test_12_backup_obrigatorio_e_identico(h):
    antes = sha(h["db"])
    with pytest.raises(B.BackfillError, match="backup não encontrado"):
        gravar(h, h["tmp"] / "nao_existe.db")
    with pytest.raises(B.BackfillError, match="próprio banco"):
        gravar(h, h["db"])
    bk = backup(h)
    editar(bk, ("UPDATE execucao SET mensagem = 'diferente' WHERE id = ?", (h["eid"],)))
    with pytest.raises(B.BackfillError, match="não é cópia exata"):
        gravar(h, bk)
    assert sha(h["db"]) == antes
    with pytest.raises(SystemExit) as e:
        B.main(["--db", str(h["db"]), "--execucao", str(h["eid"]), "--localidades-execucao", str(h["lid"]), "--gravar"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        B.main(["--execucao", "3", "--localidades-execucao", "2"])                 # --db é obrigatório
    assert e.value.code == 2


def test_13_sem_acesso_a_rede(h, monkeypatch):
    def sem_rede(*a, **k):
        raise OSError("rede bloqueada no teste")
    monkeypatch.setattr(socket, "create_connection", sem_rede)
    monkeypatch.setattr(socket.socket, "connect", sem_rede)
    assert verificar(h).ok
    assert gravar(h)["grupos"] == 2


def test_14_linha_de_comando(h, capsys):
    base = ["--db", str(h["db"]), "--execucao", str(h["eid"]), "--localidades-execucao", str(h["lid"]), "--raw", str(h["raw"])]
    assert B.main(base) == 0
    assert B.main(base + ["--gravar", "--backup", str(backup(h))]) == 0
    out = capsys.readouterr().out
    assert '"grupos": 2' in out and '"integridade": "ok"' in out
    assert B.main(base + ["--gravar", "--backup", str(backup(h, "bk2.db"))]) == 1    # segunda vez: recusada
    err = capsys.readouterr().err
    assert "Backfill interrompido" in err and "C06" in err
    assert B.main(base) == 1                                                       # verificar agora aponta grupos já gravados


# --- 15 a 18: falha no COMMIT e no ROLLBACK (correção 1) ----------------------------------------
class ConexaoComFalha:
    """Conexão real com falhas injetadas no COMMIT e/ou no ROLLBACK (sqlite3.Connection não aceita monkeypatch).

    commit="ativa": o COMMIT falha e a transação continua aberta. commit="encerrada": o SQLite encerra a
    transação ao falhar (como na etapa 4d, "cannot rollback - no transaction is active"). rollback_falha=True:
    o ROLLBACK também falha.
    """

    def __init__(self, real, commit=None, rollback_falha=False):
        self._real, self.commit, self.rollback_falha = real, commit, rollback_falha
        self.comandos, self.fechada = [], False

    @property
    def isolation_level(self):
        return self._real.isolation_level

    @isolation_level.setter
    def isolation_level(self, valor):
        self._real.isolation_level = valor

    @property
    def in_transaction(self):
        return self._real.in_transaction

    def execute(self, sql, *args):
        cmd = sql.strip().upper()
        self.comandos.append(cmd)
        if cmd == "COMMIT" and self.commit:
            if self.commit == "encerrada":
                self._real.execute("ROLLBACK")                                    # o SQLite já encerrou a transação
            raise sqlite3.OperationalError("disk I/O error")
        if cmd == "ROLLBACK" and self.rollback_falha:
            raise sqlite3.OperationalError("falha simulada no ROLLBACK")
        return self._real.execute(sql, *args)

    def executemany(self, *args):
        return self._real.executemany(*args)

    def close(self):
        self.fechada = True
        self._real.close()


def _estado(h):
    return sha(h["db"]), objetos(h["db"]), historico(h["db"]), sorted(p.name for p in h["db"].parent.iterdir())


def _transacao_com_falha(h, **kw):
    conn = ConexaoComFalha(sqlite3.connect(h["db"], isolation_level=None), **kw)
    try:
        with pytest.raises(B.CommitError) as e:
            B._gravar_transacao(conn, h["eid"], _ok_sem_ensaio(h).report)
        return conn, e.value
    finally:
        conn.close()


def test_15_commit_falha_com_transacao_ativa(h):
    antes = _estado(h)
    conn, erro = _transacao_com_falha(h, commit="ativa")
    msg = str(erro)
    assert "falha no COMMIT: OperationalError: disk I/O error" in msg and "ROLLBACK executado" in msg
    assert erro.transacao_ativa and erro.rollback_error is None
    assert isinstance(erro.__cause__, sqlite3.OperationalError) and erro.original is erro.__cause__
    assert conn.comandos.count("ROLLBACK") == 1 and "indeterminado" in msg
    assert _estado(h) == antes                                                     # nada ficou gravado


def test_16_commit_falha_com_transacao_ja_encerrada(h):
    antes = _estado(h)
    conn, erro = _transacao_com_falha(h, commit="encerrada")
    msg = str(erro)
    assert "disk I/O error" in msg and "ROLLBACK não executado" in msg
    assert "cannot rollback" not in msg and "no transaction" not in msg          # sem o erro secundário da etapa 4d
    assert not erro.transacao_ativa and erro.rollback_error is None
    assert conn.comandos.count("ROLLBACK") == 0                                  # ROLLBACK só com transação ativa
    assert _estado(h) == antes


def test_17_falha_secundaria_no_rollback_nao_esconde_o_erro_original(h, monkeypatch):
    antes = _estado(h)
    conn, erro = _transacao_com_falha(h, commit="ativa", rollback_falha=True)
    msg = str(erro)
    assert isinstance(erro.__cause__, sqlite3.OperationalError) and str(erro.__cause__) == "disk I/O error"
    assert str(erro.rollback_error) == "falha simulada no ROLLBACK"
    assert msg.index("disk I/O error") < msg.index("falha simulada no ROLLBACK")  # a causa vem primeiro
    assert conn.fechada and _estado(h) == antes                                   # fechar a conexão desfaz a transação

    def falha(conn, eid, report):                                                 # falha fora do COMMIT
        raise RuntimeError("falha original na inserção")
    monkeypatch.setattr(load_sqlite, "record_pla05_groups", falha)
    conn = ConexaoComFalha(sqlite3.connect(h["db"], isolation_level=None), rollback_falha=True)
    try:
        with pytest.raises(RuntimeError, match="falha original na inserção") as e:  # não o erro do ROLLBACK
            B._gravar_transacao(conn, h["eid"], _ok_sem_ensaio(h).report)
        assert str(e.value.rollback_error) == "falha simulada no ROLLBACK"
    finally:
        conn.close()
    assert _estado(h) == antes


@pytest.mark.parametrize("caso", ["ativa", "encerrada", "ativa_rollback_falha"])
def test_18_gravar_fecha_a_conexao_e_cli_mostra_o_erro_original(h, monkeypatch, capsys, caso):
    real_connect, criadas = sqlite3.connect, []

    def connect(*args, **kw):
        c = real_connect(*args, **kw)
        if args and args[0] == h["db"] and kw.get("isolation_level", "padrão") is None:  # só a conexão da gravação
            c = ConexaoComFalha(c, commit=caso.split("_")[0], rollback_falha=caso.endswith("falha"))
            criadas.append(c)
        return c
    bk = backup(h)
    antes = _estado(h)
    monkeypatch.setattr(sqlite3, "connect", connect)
    rc = B.main(["--db", str(h["db"]), "--execucao", str(h["eid"]), "--localidades-execucao", str(h["lid"]),
                 "--raw", str(h["raw"]), "--gravar", "--backup", str(bk)])
    err = capsys.readouterr().err
    assert rc == 1 and len(criadas) == 1 and criadas[0].fechada                   # conexão encerrada
    assert "Backfill interrompido: falha no COMMIT: OperationalError: disk I/O error" in err
    assert "Traceback" not in err and "cannot rollback" not in err
    assert _estado(h) == antes


# --- 19 a 23: conferência depois da gravação (correção 2) ---------------------------------------
ESPERADO_FIXTURE = {"total_grupos": 2, "total_registros": 4, "total_municipios": 4, "total_membros": 4,
                    "grupos_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 2},
                    "registros_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 4},
                    "membros_por_nivel": {"informativo": 0, "atencao": 0, "revisao_documental": 4}}


def conferir(h, **kw):
    return B.conferir_gravados(h["db"], h["eid"], h["lid"], h["raw"], **kw)


@pytest.fixture
def gravado(tmp_path):
    h = montar(tmp_path)
    gravar(h)
    return h


def test_19_conferir_gravados_aprova_o_que_foi_gravado(gravado):
    h = gravado
    antes = _estado(h)
    res = conferir(h)
    assert res.ok, [(c.codigo, c.detalhe) for c in res.conferencias if not c.ok]
    assert [c.codigo for c in res.conferencias] == [f"P{i:02d}" for i in range(20)]
    assert conferir(h, esperado=ESPERADO_FIXTURE).ok
    assert falhas(conferir(h, esperado=dict(ESPERADO_FIXTURE, total_membros=5))) == ["P10"]
    assert falhas(conferir(h, esperado=dict(ESPERADO_FIXTURE, total_grupos=3))) == ["P09"]
    assert falhas(conferir(h, esperado=dict(ESPERADO_FIXTURE, membros_por_nivel={"informativo": 4, "atencao": 0,
                                                                                 "revisao_documental": 0}))) == ["P15"]
    assert B.ESPERADO[3]["total_membros"] == 726 and B.ESPERADO[3]["membros_por_nivel"]["informativo"] == 441
    assert _estado(h) == antes                                                     # nem um byte alterado
    assert falhas(verificar(h)) == ["C06"]                                         # a prevenção de duplicidade continua


def test_20_conferir_gravados_abre_somente_leitura(gravado, monkeypatch, capsys):
    h = gravado
    real_connect, chamadas = sqlite3.connect, []

    def connect(*args, **kw):
        chamadas.append((str(args[0]), kw.get("uri", False)))
        return real_connect(*args, **kw)
    antes = _estado(h)
    monkeypatch.setattr(sqlite3, "connect", connect)
    base = ["--db", str(h["db"]), "--execucao", str(h["eid"]), "--localidades-execucao", str(h["lid"]), "--raw", str(h["raw"])]
    assert B.main(base + ["--conferir-gravados"]) == 0
    out = capsys.readouterr().out
    assert "Resultado: todas as conferências passaram" in out and "modo conferir gravados: somente leitura" in out
    assert chamadas and all(uri and alvo.endswith("?mode=ro") for alvo, uri in chamadas), chamadas
    assert _estado(h) == antes
    fonte = inspect.getsource(B.conferir_gravados).upper()
    for palavra in ("INSERT", "UPDATE ", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "COMMIT", "BEGIN", "EXECUTESCRIPT"):
        assert palavra not in fonte, palavra
    with pytest.raises(SystemExit) as e:                                           # modos exclusivos
        B.main(base + ["--conferir-gravados", "--gravar", "--backup", str(backup(h))])
    assert e.value.code == 2


def _sem_fk(db, *sqls):
    c = sqlite3.connect(db)
    with c:
        for sql in sqls:
            c.execute(sql)
    c.close()


OUTRO = "3100104"
DIVERGENCIAS = {
    "area_do_grupo": ("UPDATE validacao_grupo SET area_colhida_ha = area_colhida_ha + 0.5 WHERE grupo = 1",
                      "P12", "grupo 1, campo area_colhida_ha"),
    "rendimento_minimo": ("UPDATE validacao_grupo SET rendimento_kg_ha = rendimento_kg_ha + 1e-9 WHERE grupo = 2",
                          "P12", "grupo 2, campo rendimento_kg_ha"),
    "produtividade_com_rendimento": ("UPDATE validacao_grupo SET rendimento_kg_ha = rendimento_kg_ha + 1e-9 WHERE grupo = 2",
                                     "P13", "grupo 2, campo produtividade_sc_ha"),
    "nivel": ("UPDATE validacao_grupo SET nivel = 'atencao' WHERE grupo = 1", "P12", "grupo 1, campo nivel"),
    "nivel_na_distribuicao": ("UPDATE validacao_grupo SET nivel = 'atencao' WHERE grupo = 1", "P15", "grupos 0/1/1"),
    "faixa": ("UPDATE validacao_grupo SET faixa_acima_de_ha = 501 WHERE grupo = 2", "P12", "grupo 2, campo faixa_acima_de_ha"),
    "tipo_da_faixa": ("UPDATE validacao_grupo SET faixa_ate_ha = 'sem limite' WHERE grupo = 1", "P12",
                      "grupo 1, campo faixa_ate_ha: gravado 'sem limite' (text), reconstruído None (null)"),
    "total_do_grupo": ("UPDATE validacao_grupo SET total_registros = 3 WHERE grupo = 1", "P12", "grupo 1, campo total_registros"),
    "nome_do_membro": (f"UPDATE validacao_grupo_membro SET municipio = 'Outro' WHERE codigo_ibge = '{PAT}'", "P14",
                       f"membro {PAT}, campo municipio: gravado 'Outro', reconstruído 'Patrocínio'"),
    "membro_ausente": (f"DELETE FROM validacao_grupo_membro WHERE codigo_ibge = '{MANHUACU}'", "P14", f"membro {MANHUACU}: ausente no banco"),
    "membro_ausente_conta": (f"DELETE FROM validacao_grupo_membro WHERE codigo_ibge = '{MANHUACU}'", "P10", "gravados 3, reconstruídos 4"),
    "membro_a_mais": (f"INSERT INTO validacao_grupo_membro SELECT execucao_id, regra, grupo, ano, '{OUTRO}', 'Abadia' "
                      f"FROM validacao_grupo_membro WHERE codigo_ibge = '{PAT}'", "P14", f"membro {OUTRO}: a mais no banco"),
    "membro_a_mais_ocorrencias": (f"INSERT INTO validacao_grupo_membro SELECT execucao_id, regra, grupo, ano, '{OUTRO}', 'Abadia' "
                                  f"FROM validacao_grupo_membro WHERE codigo_ibge = '{PAT}'", "P16", "5 membros, 4 ocorrências"),
    "grupo_a_mais": ("INSERT INTO validacao_grupo (execucao_id, regra, grupo, ano, nivel, rendimento_kg_ha, area_colhida_ha, producao_t, "
                     "total_municipios, total_registros) SELECT execucao_id, regra, 3, 2016, nivel, 1, 1, 1, 2, 2 "
                     "FROM validacao_grupo WHERE grupo = 1", "P11", "só no banco: [3]"),
    "chave_estrangeira": ("INSERT INTO validacao_grupo_membro (execucao_id, regra, grupo, ano, codigo_ibge, municipio) "
                          f"SELECT execucao_id, regra, 99, ano, '{OUTRO}', 'Abadia' FROM validacao_grupo WHERE grupo = 1",
                          "P18", "validacao_grupo_membro"),
}


@pytest.mark.parametrize("nome", sorted(DIVERGENCIAS))
def test_21_divergencia_proposital_e_apontada(gravado, nome):
    h = gravado
    sql, codigo, trecho = DIVERGENCIAS[nome]
    if nome == "chave_estrangeira":                                               # só com as chaves desligadas
        _sem_fk(h["db"], sql)
    else:
        editar(h["db"], (sql, ()))
    antes = _estado(h)
    res = conferir(h)
    c = {x.codigo: x for x in res.conferencias}
    assert not res.ok and not c[codigo].ok, (nome, falhas(res))
    assert trecho in c[codigo].detalhe, (nome, c[codigo].detalhe)
    assert _estado(h) == antes                                                     # a conferência não corrige nada
    rc = B.main(["--db", str(h["db"]), "--execucao", str(h["eid"]), "--localidades-execucao", str(h["lid"]),
                 "--raw", str(h["raw"]), "--conferir-gravados"])
    assert rc == 1


def test_22_journal_pendente_impede_a_abertura(gravado, monkeypatch):
    h = gravado
    # Sem abrir o banco com SQLite depois de criar o journal: qualquer abertura com escrita trataria o journal
    # (e o apagaria ou aplicaria), que é exatamente o que a conferência P00 evita.
    pendente = h["db"].with_name(h["db"].name + "-journal")
    pendente.write_bytes(b"conteudo qualquer")
    abertos = []
    monkeypatch.setattr(B, "_somente_leitura", lambda db: abertos.append(db))
    antes = sha(h["db"]), sorted(p.name for p in h["db"].parent.iterdir())
    res = conferir(h)
    assert falhas(res) == ["P00"] and [c.codigo for c in res.conferencias] == ["P00"]
    assert "rigora.db-journal" in res.conferencias[0].detalhe and abertos == []    # o banco nem foi aberto
    assert (sha(h["db"]), sorted(p.name for p in h["db"].parent.iterdir())) == antes
    assert pendente.read_bytes() == b"conteudo qualquer"                           # journal intacto


def test_23_conferir_sem_grupos_ou_com_bruto_adulterado(h):
    res = conferir(h)                                                              # antes de gravar
    assert falhas(res) == ["P06"] and "tabelas de grupos não existem" in res.conferencias[6].detalhe
    gravar(h)
    _bruto_adulterado(h)
    assert "P03" in falhas(conferir(h))
    with pytest.raises(B.BackfillError, match="banco não encontrado"):
        B.conferir_gravados(h["tmp"] / "nao_existe.db", h["eid"], h["lid"], h["raw"])


# --- Correção C02: um único horário por chamada do pipeline -------------------------------------
VIRADA = ["2026-11-15T20:14:06+00:00", "2026-11-15T20:14:07+00:00", "2026-11-15T20:14:08+00:00"]


def test_24_virada_do_segundo_nao_separa_as_duas_execucoes_da_mesma_chamada(tmp_path, monkeypatch):
    """O relógio vira o segundo a cada leitura: as duas execuções da chamada recebem o mesmo horário."""
    lidos = []

    def relogio():                                   # cada leitura do relógio já é o segundo seguinte
        lidos.append(VIRADA[len(lidos)])
        return lidos[-1]
    monkeypatch.setattr(load_sqlite, "_now", relogio)
    h = montar(tmp_path)
    c = sqlite3.connect(h["db"])
    datas = dict(c.execute("SELECT id, data_execucao FROM execucao").fetchall())
    c.close()
    assert lidos == VIRADA[:1], lidos                # o relógio foi lido uma única vez na chamada
    assert datas[h["lid"]] == datas[h["eid"]] == VIRADA[0], datas
    res = verificar(h)                               # C02 inalterada aprova o par
    assert res.ok and "C02" not in falhas(res), [(x.codigo, x.detalhe) for x in res.conferencias if not x.ok]
    gravar(h)
    res = B.conferir_gravados(h["db"], h["eid"], h["lid"], h["raw"])
    assert res.ok and "P02" not in falhas(res), [(x.codigo, x.detalhe) for x in res.conferencias if not x.ok]


def test_25_c02_e_p02_continuam_recusando_horarios_diferentes(tmp_path):
    """A regra não foi afrouxada: um segundo de diferença ainda é recusado pela C02 e pela P02."""
    h = montar(tmp_path)
    gravar(h)                                        # par válido gravado (banco temporário do teste)
    c = sqlite3.connect(h["db"])
    ex = c.execute("SELECT data_execucao FROM execucao WHERE id = ?", (h["eid"],)).fetchone()[0]
    outro = ex[:17] + "%02d" % ((int(ex[17:19]) + 1) % 60) + ex[19:]
    with c:
        c.execute("UPDATE execucao SET data_execucao = ? WHERE id = ?", (outro, h["lid"]))
    c.close()
    assert "C02" in falhas(verificar(h))
    assert "P02" in falhas(B.conferir_gravados(h["db"], h["eid"], h["lid"], h["raw"]))
    with pytest.raises(B.BackfillError, match="C02"):
        gravar(h, bk=backup(h, "bk2.db"))


def test_26_start_execution_sem_o_parametro_continua_como_antes(tmp_path):
    """Compatibilidade: sem data_execucao, o horário vem de _now(), em UTC, com precisão de segundos."""
    import re
    from datetime import datetime, timedelta, timezone
    c = load_sqlite.connect(tmp_path / "x.db")
    load_sqlite.init_schema(c)
    a = load_sqlite.start_execution(c, "teste", "2026-11-14T00:00:00+00:00", "x.json", "0" * 64)
    b = load_sqlite.start_execution(c, "teste", "2026-11-14T00:00:00+00:00", "y.json", "1" * 64, data_execucao=VIRADA[0])
    va = c.execute("SELECT data_execucao FROM execucao WHERE id = ?", (a,)).fetchone()[0]
    vb = c.execute("SELECT data_execucao FROM execucao WHERE id = ?", (b,)).fetchone()[0]
    c.close()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", va), va
    assert abs(datetime.fromisoformat(va) - datetime.now(timezone.utc)) < timedelta(minutes=5)
    assert vb == VIRADA[0]
    assert load_sqlite._now()[-6:] == "+00:00" and len(load_sqlite._now()) == len(VIRADA[0])
