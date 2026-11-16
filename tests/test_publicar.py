"""Testes do Bloco 3: publicação protegida (pipeline/publicar.py) e --saida obrigatório no exportador.

Tudo em pastas temporárias: o banco é montado com as fixtures do pipeline, o "snapshot publicado" é
um arquivo temporário e o verificador B5 e a coleta são simulados. Os testes de integração (fim do
arquivo) rodam o verificador B5 real (Node). Nenhum teste acessa a rede, coleta dados, lê o banco
definitivo (regra do test_16 em test_pipeline_pla05_sqlite.py) ou altera o js/snapshots.js do projeto
ou a fixture: o módulo confere os hashes desses dois antes e depois. O caminho completo com o banco
real e o verificador aprovando é a etapa "Simulação da publicação" do portão (tools/portao.py).
"""
import errno
import hashlib
import json
import os
import shutil
import socket
import stat
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import config
from pipeline import export_snapshots as E
from pipeline import publicar as P
from pipeline.run_pipeline import process
from tests.test_pipeline_export import AGORA, COLETA_INMET, FIX, MANUAL, _save, load_inmet

REAL_DESTINO = config.ROOT / "js" / "snapshots.js"
COLETOR_ORIGINAL = P._coletor_real
COLETA_SIDRA = "2026-11-14T15:00:00+00:00"
C_INMET = datetime.fromisoformat(COLETA_INMET)


def h(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def producao_intocada():
    antes = {p: h(p) for p in (REAL_DESTINO, MANUAL)}
    yield
    assert {p: h(p) for p in antes} == antes, "um teste alterou o snapshot em uso ou a fixture"


@pytest.fixture(autouse=True)
def sem_rede_nem_coleta_real(monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("teste tentou acessar a rede")
    monkeypatch.setattr(socket, "create_connection", proibido)
    monkeypatch.setattr(socket.socket, "connect", proibido)

    def coleta_proibida(*a, **k):
        raise AssertionError("coleta real é proibida nos testes")
    monkeypatch.setattr(P, "_coletor_real", coleta_proibida)
    monkeypatch.setattr(P, "ESPERA_TENTATIVA_S", 0)


# --------------------------------------------------------------------------- montagem
def montar(tmp_path, nome="proj", com_inmet=True, publicado=True, backups="backups"):
    """Projeto temporário: banco das fixtures, brutos e um snapshot "publicado" gerado pelo pipeline."""
    raiz = tmp_path / nome
    env = {"db": raiz / "data" / "rigora.db", "raw": raiz / "data" / "raw", "out": tmp_path / "processed",
           "js": raiz / "js" / "snapshots.js"}
    env["js"].parent.mkdir(parents=True)
    pam = _save(env["raw"], "sidra_pam_1613_mg_arabica", (FIX / "sidra_pam_sample.json").read_bytes(), "fixture", COLETA_SIDRA)
    loc = _save(env["raw"], "ibge_localidades_mg", (FIX / "localidades_sample.json").read_bytes(), "fixture", COLETA_SIDRA)
    process(pam, loc, env["db"], env["out"])
    if com_inmet:
        load_inmet(env)
    if publicado:
        E.export(env["db"], env["js"], now=AGORA - timedelta(hours=1))
    return P.Caminhos.padrao(raiz, backups=tmp_path / backups, referencia=MANUAL)


class Verificador:
    """Verificador B5 simulado. `respostas`: lista de (status, detalhe) ou exceções, na ordem das chamadas."""

    def __init__(self, *respostas, depois=None):
        self.respostas, self.chamadas, self.depois = list(respostas), [], depois

    def __call__(self, arquivo, cam):
        self.chamadas.append(Path(arquivo))
        r = self.respostas.pop(0) if self.respostas else (P.OK, "APROVADO (simulado)")
        if self.depois:
            self.depois(len(self.chamadas))
        if isinstance(r, BaseException):
            raise r
        return r


def rodar(cam, agora=AGORA, verificador=None, coletor=None, **op):
    relogio = agora if callable(agora) else (lambda: agora)
    return P.executar(P.Opcoes(**op), cam, agora=relogio, verificador=verificador or Verificador(), coletor=coletor, saida=None)


def publicar(cam, agora=AGORA, verificador=None, **extra):
    return rodar(cam, agora, verificador, publicar=True, confirmar_atual=P.sha256_ou_none(cam.destino)[:12]
                 if cam.destino.exists() else "ausente", **extra)


def backups_de(cam, tipo="publicacao"):
    return sorted(cam.backups.glob(f"*_{tipo}*")) if cam.backups.exists() else []


def sem_residuo(cam):
    return not P.estagio_de(cam.destino).exists()


def gerar(cam, tmp_path, agora=AGORA) -> Path:
    alvo = tmp_path / "gerado" / "snapshots.js"
    alvo.parent.mkdir(parents=True, exist_ok=True)
    E.export(cam.banco, alvo, now=agora)
    return alvo


def mutado(arquivo: Path, mudar, destino: Path | None = None) -> Path:
    snap = E.parse_snapshots_js(arquivo.read_text(encoding="utf-8"))
    mudar(snap)
    alvo = destino or arquivo.with_name("mutado.js")
    alvo.write_text(E.render(snap), encoding="utf-8")
    return alvo


def registro(snap):
    """Primeiro registro da previsão no arquivo (período da tarde, ou o próprio dia)."""
    e = next(iter(snap["inmet"]["data"]["3148103"].values()))
    return e.get("tarde") or next((e[k] for k in ("manha", "noite") if k in e), e)


def validar(cam, arquivo, agora=AGORA, verificador=None):
    return P.validar_snapshot(arquivo, cam, cam.banco, agora, verificador or Verificador())[0]


# =========================================================================== argumentos e modos
def test_simulacao_padrao_nao_publica_nem_coleta_e_mostra_o_plano(tmp_path):
    cam = montar(tmp_path)
    antes, banco = h(cam.destino), h(cam.banco)
    rel = rodar(cam)
    assert rel.codigo == 0 and rel.resultado == "SIMULAÇÃO APROVADA"
    assert h(cam.destino) == antes and h(cam.banco) == banco
    assert not cam.backups.exists(), "a simulação não cria backup real"
    assert sem_residuo(cam) and not Path(rel.dados["trabalho"]).exists()
    plano = rel.dados["plano"]
    assert plano["hashAtual"] == antes and plano["hashNovo"] == rel.dados["gerado"]["sha256"]
    assert plano["destino"] == str(cam.destino) and plano["backupPrevisto"].startswith(str(cam.backups))
    assert f"--confirmar-atual {antes[:12]}" in plano["comando"]
    assert any(e["etapa"] == "Ensaio em cópia" and e["status"] == "ok" for e in rel.etapas)
    assert any(e["etapa"] == "Coleta" and "não solicitada" in e["detalhe"] for e in rel.etapas)


def test_linha_de_comando_sem_argumentos_e_simulacao(tmp_path, capsys):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    rc = P.main(["--raiz", str(cam.raiz), "--backups", str(cam.backups), "--referencia", str(MANUAL)],
                verificador=Verificador(), agora=lambda: AGORA)
    out = capsys.readouterr().out
    assert rc == 0 and "SIMULAÇÃO (padrão: sem coleta, sem publicação)" in out and "Resultado: SIMULAÇÃO APROVADA" in out
    assert h(cam.destino) == antes and not cam.backups.exists()


def test_coletar_sozinho_coleta_com_backup_do_banco_e_nao_publica(tmp_path):
    cam = montar(tmp_path)
    antes, banco = h(cam.destino), h(cam.banco)
    chamadas = []

    def coletor(fontes, banco_alvo, c):
        chamadas.append((fontes, banco_alvo))
        return {"sidra": {"status": "sucesso"}, "inmet": {"status": "sucesso"}}
    rel = rodar(cam, coletor=coletor, coletar=True)
    assert rel.codigo == 0 and rel.resultado == "COLETA CONCLUÍDA E SIMULAÇÃO APROVADA"
    assert chamadas == [(("sidra", "inmet"), cam.banco)]
    assert h(cam.destino) == antes and not backups_de(cam, "publicacao")
    [pasta] = backups_de(cam, "coleta")
    assert h(pasta / "data" / "rigora.db") == banco                # 1.0: backup integral de data/ (antes: só o banco)


def test_coleta_com_falha_para_antes_de_gerar(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    v = Verificador()
    rel = rodar(cam, verificador=v, coletor=lambda f, b, c: {"sidra": {"status": "sucesso"}, "inmet": {"status": "falha"}},
                coletar=True, publicar=True, confirmar_atual=antes[:12])
    assert rel.codigo == 1 and "inmet" in rel.motivo and not v.chamadas
    assert h(cam.destino) == antes and not backups_de(cam, "publicacao")


def test_coleta_real_recusa_projeto_que_nao_e_o_do_pipeline(tmp_path):
    cam = montar(tmp_path)
    with pytest.raises(P.Parada) as e:
        COLETOR_ORIGINAL(("sidra",), cam.banco, cam)
    assert e.value.codigo == P.USO


def test_coleta_nao_comeca_com_banco_em_transacao(tmp_path):
    cam = montar(tmp_path)
    Path(str(cam.banco) + "-journal").write_bytes(b"x")
    rel = rodar(cam, coletor=lambda *a: pytest.fail("não devia coletar"), coletar=True)
    assert rel.codigo == 2 and "-journal" in rel.motivo


def test_publicar_sem_confirmacao_prepara_mostra_o_plano_e_nao_altera(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    rel = rodar(cam, publicar=True)
    assert rel.codigo == 2 and "não confirmada" in rel.motivo
    assert h(cam.destino) == antes and not cam.backups.exists() and sem_residuo(cam)
    assert rel.dados["plano"]["hashAtual"] == antes


def test_confirmacao_que_nao_confere_nao_altera(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    rel = rodar(cam, publicar=True, confirmar_atual="0" * 12)
    assert rel.codigo == 2 and h(cam.destino) == antes and not cam.backups.exists()


@pytest.mark.parametrize("argv", [
    ["--restaurar", "X", "--publicar"], ["--restaurar", "X", "--coletar"], ["--confirmar-atual", "abcdef123456"],
    ["--aceitar-sem-inmet"], ["--coletar", "--aceitar-sem-inmet"], ["--fontes", "inmet"], ["--coletar", "--fontes", "cepea"],
    ["--publicar", "--confirmar-atual", "xyz"], ["--publicar", "--confirmar-atual", "abc"], ["--pub"], ["--desconhecida"],
    ["--restaurar", "X", "--trabalho", "Y"], ["--trabalho", "@raiz/tmp"], ["--relatorio", "@raiz/r.json"], ["--backups", "@raiz/b"],
])
def test_combinacoes_invalidas_sao_recusadas_sem_alterar_nada(tmp_path, argv, capsys):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    args = [a.replace("@raiz", str(cam.raiz)) if a.startswith("@raiz") else (str(tmp_path / a) if a in ("X", "Y") else a) for a in argv]
    rc = P.main(["--raiz", str(cam.raiz), "--backups", str(cam.backups), *args], verificador=Verificador(), agora=lambda: AGORA)
    assert rc == 3 and "erro de uso" in capsys.readouterr().err
    assert h(cam.destino) == antes and not cam.backups.exists()


def test_backups_padrao_ficam_fora_do_projeto():
    cam = P.Caminhos.padrao()
    assert not P.dentro(cam.backups, cam.raiz) and cam.backups.parent.name == "rigora_backups"
    assert cam.destino == REAL_DESTINO.resolve()


# =========================================================================== exportador
def test_exportador_sem_saida_falha_de_forma_clara(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        E.main(["--db", str(tmp_path / "rigora.db")])
    assert e.value.code == 2 and "--saida" in capsys.readouterr().err


def test_exportador_recusa_o_snapshot_em_uso(tmp_path, monkeypatch, caplog):
    cam = montar(tmp_path, publicado=False)
    assert E.main(["--db", str(cam.banco), "--saida", str(REAL_DESTINO)]) == 1
    monkeypatch.chdir(config.ROOT)
    assert E.main(["--db", str(cam.banco), "--saida", "js/snapshots.js"]) == 1
    assert "pipeline.publicar" in caplog.text
    with pytest.raises(E.ExportError, match="pipeline.publicar"):
        E.export(cam.banco, REAL_DESTINO, now=AGORA)


def test_exportador_funciona_com_saida_temporaria_com_espacos_e_acentos(tmp_path):
    cam = montar(tmp_path, publicado=False)
    alvo = tmp_path / "pasta com espaço" / "saída ação.js"
    alvo.parent.mkdir()
    assert E.main(["--db", str(cam.banco), "--saida", str(alvo)]) == 0
    assert E.validate_contract(E.parse_snapshots_js(alvo.read_text(encoding="utf-8"))) == []


# =========================================================================== exportação e verificação
def test_snapshot_valido_passa_em_todas_as_conferencias(tmp_path):
    cam = montar(tmp_path)
    assert validar(cam, gerar(cam, tmp_path)) == []


@pytest.mark.parametrize("estrago", [
    lambda t: t[: len(t) // 2], lambda t: t.replace("window.RIGORA_SNAPSHOTS = {", "window.X = {"),
    lambda t: t.replace("};\n", "}\n"), lambda t: t.replace('"sidra":', '"sidra":{', 1),
])
def test_snapshot_malformado_e_rejeitado(tmp_path, estrago):
    cam = montar(tmp_path)
    g = gerar(cam, tmp_path)
    ruim = g.with_name("ruim.js")
    ruim.write_text(estrago(g.read_text(encoding="utf-8")), encoding="utf-8")
    p = validar(cam, ruim)
    assert p and (p[0].startswith("snapshot ilegível") or p[0].startswith("contrato"))


@pytest.mark.parametrize("mudar,esperado", [
    (lambda s: s["sidra"].pop("municipiosMG"), "contrato: sidra.municipiosMG"),
    (lambda s: s["exportacao"].pop("geradoEm"), "contrato: exportacao sem geradoEm"),
    (lambda s: s["sidra"]["municipios"][0].pop("c"), "contrato: município inválido"),
    (lambda s: registro(s).pop("umidade_min"), "contrato: inmet"),
])
def test_campo_obrigatorio_ausente_e_detectado(tmp_path, mudar, esperado):
    cam = montar(tmp_path)
    p = validar(cam, mutado(gerar(cam, tmp_path), mudar))
    assert any(x.startswith(esperado) for x in p), p


@pytest.mark.parametrize("mudar,esperado", [
    (lambda s: s["sidra"].__setitem__("collectedAt", "ontem"), "contrato: sidra.collectedAt não é data ISO"),
    (lambda s: s["exportacao"].__setitem__("geradoEm", "2026-13-40"), "procedência: exportacao.geradoEm"),
    (lambda s: s["inmet"]["data"]["3148103"].__setitem__("31/02/2026", next(iter(s["inmet"]["data"]["3148103"].values()))),
     "INMET: previsão com data inválida: 31/02/2026"),
    (lambda s: s["inmet"].__setitem__("collectedAt", "sem data"), "INMET: previsão sem data de coleta válida"),
])
def test_data_invalida_e_detectada(tmp_path, mudar, esperado):
    cam = montar(tmp_path)
    p = validar(cam, mutado(gerar(cam, tmp_path), mudar))
    assert any(x.startswith(esperado) for x in p), p


def test_hash_incorreto_e_detectado_no_arquivo_e_no_bruto(tmp_path):
    cam = montar(tmp_path)
    g = gerar(cam, tmp_path)
    p = validar(cam, mutado(g, lambda s: s["exportacao"]["sidra"].__setitem__("hashSha256", "0" * 64)))
    assert "procedência: exportacao.sidra.hashSha256 diferente do registrado no banco" in p
    bruto = next(cam.bruto.glob("sidra_pam_1613_mg_arabica_*[0-9]Z.json"))
    bruto.write_bytes(bruto.read_bytes() + b" ")                   # bruto alterado depois da coleta
    assert any("hash do arquivo bruto" in x for x in validar(cam, g))
    bruto.unlink()
    assert any("arquivo bruto de sidra ausente" in x for x in validar(cam, g))


@pytest.mark.parametrize("mudar,esperado", [
    (lambda s: s["exportacao"]["sidra"].__setitem__("execucaoId", 999), "execucaoId 999 não existe no banco"),
    (lambda s: s["exportacao"]["sidra"].__setitem__("dataColeta", "2026-11-13T10:00:00+00:00"), "exportacao.sidra.dataColeta"),
    (lambda s: s["exportacao"]["sidra"].__setitem__("modo", "mantido_do_arquivo_anterior"), "seção sidra não veio do banco"),
    (lambda s: s["exportacao"]["sidra"].__setitem__("statusExecucao", "falha"), "com status 'sucesso' (arquivo diz 'falha')"),
    (lambda s: s["exportacao"].__setitem__("geradoPor", "editado à mão"), "geradoPor"),
    (lambda s: s["exportacao"].__setitem__("geradoEm", "2026-11-14T14:00:00+00:00"), "gerado antes da coleta"),
    (lambda s: s["exportacao"]["inmet"].__setitem__("modo", "indisponivel"), "diz indisponível, mas há previsão"),
    (lambda s: s["inmet"].__setitem__("collectedAt", "2026-11-14T17:00:00+00:00"), "inmet.collectedAt diferente"),
    (lambda s: s.__setitem__("inmet", None), "inmet null sem modo 'indisponivel'"),
])
def test_procedencia_inconsistente_e_detectada(tmp_path, mudar, esperado):
    cam = montar(tmp_path)
    p = validar(cam, mutado(gerar(cam, tmp_path), mudar))
    assert any(esperado in x for x in p), p


def test_reprovacao_do_verificador_b5_impede_a_publicacao(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    rel = publicar(cam, verificador=Verificador((P.FALHA, "REPROVADO (2 problema(s))")))
    assert rel.codigo == 1 and h(cam.destino) == antes and not cam.backups.exists()
    assert any("verificador B5: REPROVADO" in e["detalhe"] for e in rel.etapas)


def test_sem_node_o_resultado_e_incompleto_e_nada_muda(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    monkeypatch.setattr(P.shutil, "which", lambda nome: None)
    assert P.verificar_com_node(cam.destino, cam)[0] == P.INFO
    rel = P.executar(P.Opcoes(publicar=True, confirmar_atual=antes[:12]), cam, agora=lambda: AGORA, saida=None)
    assert rel.codigo == 2 and "Node.js não encontrado" in rel.motivo and h(cam.destino) == antes


def test_secao_exportacao_e_preservada_na_publicacao(tmp_path):
    cam = montar(tmp_path)
    rel = publicar(cam)
    assert rel.codigo == 0, rel.motivo
    pub = E.parse_snapshots_js(cam.destino.read_text(encoding="utf-8"))
    ex = pub["exportacao"]
    assert h(cam.destino) == rel.dados["gerado"]["sha256"]
    assert ex["geradoEm"] == AGORA.isoformat(timespec="seconds") and ex["geradoPor"] == "python -m pipeline.export_snapshots"
    assert ex["sidra"]["modo"] == "sqlite" and ex["sidra"]["dataColeta"] == COLETA_SIDRA
    assert ex["inmet"]["modo"] == "sqlite" and ex["inmet"]["dataColeta"] == COLETA_INMET
    assert list(pub) == ["sidra", "inmet", "exportacao"]


# =========================================================================== INMET
@pytest.mark.parametrize("horas,incluida", [(2, True), (23.99, True), (24, True), (24 + 1 / 3600, False), (30, False)])
def test_regra_de_24_horas_na_geracao_e_na_publicacao(tmp_path, horas, incluida):
    cam = montar(tmp_path)
    agora = C_INMET + timedelta(hours=horas)
    rel = rodar(cam, agora=agora)
    assert rel.codigo == 0, rel.motivo
    assert rel.dados["gerado"]["inmet"]["incluido"] is incluida
    snap = {"inmet": {"collectedAt": COLETA_INMET, "data": {"3148103": {"18/11/2026": {}}}}, "exportacao": {}}
    assert P.conferir_inmet(snap, agora)[0] is incluida            # 24 h exatas: ainda vale (mesma regra da M1)


def test_previsao_vencida_nao_e_apresentada_como_atual_e_o_motivo_vai_para_o_relatorio(tmp_path):
    cam = montar(tmp_path)
    agora = C_INMET + timedelta(hours=30)
    rel = rodar(cam, agora=agora)
    assert rel.codigo == 0
    assert "não é exibida como atual" in rel.dados["gerado"]["inmet"]["detalhe"]
    assert any(e["etapa"] == "INMET" and e["status"] == "aviso" and "--aceitar-sem-inmet" in e["detalhe"] for e in rel.etapas)
    vencida = {"inmet": {"collectedAt": COLETA_INMET, "data": {"3148103": {"18/11/2026": {}}}}, "exportacao": {}}
    ok, motivo = P.conferir_inmet(vencida, agora)
    assert not ok and "não pode ser apresentada como atual" in motivo


@pytest.mark.parametrize("inmet,esperado", [
    ({"data": {"3148103": {"15/11/2026": {}}}}, "sem data de coleta"), ({"collectedAt": "ontem", "data": {}}, "sem data de coleta"),
    ({"collectedAt": "2026-11-14T20:00:00+00:00", "data": {}}, "no futuro"),
    ({"collectedAt": COLETA_INMET, "data": {"3148103": {"13/11/2026": {}}}}, "já passou"),
    ({"collectedAt": COLETA_INMET, "data": {"3148103": {"2026-11-15": {}}}}, "data inválida"),
])
def test_previsao_sem_data_ou_com_data_invalida_e_recusada(inmet, esperado):
    ok, motivo = P.conferir_inmet({"inmet": inmet, "exportacao": {}}, AGORA)
    assert not ok and esperado in motivo


def test_sem_previsao_publicar_exige_decisao_explicita_e_registra_o_motivo(tmp_path):
    cam = montar(tmp_path, com_inmet=False)
    antes = h(cam.destino)
    rel = publicar(cam)
    assert rel.codigo == 2 and "--aceitar-sem-inmet" in rel.motivo and h(cam.destino) == antes and not cam.backups.exists()
    rel = publicar(cam, aceitar_sem_inmet=True)
    assert rel.codigo == 0 and rel.resultado == "PUBLICADO", rel.motivo
    pub = E.parse_snapshots_js(cam.destino.read_text(encoding="utf-8"))
    assert pub["inmet"] is None and pub["exportacao"]["inmet"]["motivo"].startswith("nenhuma execução do INMET")
    [pasta] = backups_de(cam)
    salvo = json.loads((pasta / "relatorio.json").read_text(encoding="utf-8"))
    assert any(e["etapa"] == "INMET" and "SEM previsão" in e["detalhe"] and "nenhuma execução" in e["detalhe"] for e in salvo["etapas"])


def test_previsao_que_vence_durante_a_publicacao_nao_e_publicada(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    relogio = {"t": C_INMET + timedelta(hours=23, minutes=59)}

    def depois(n):
        relogio["t"] = C_INMET + timedelta(hours=24, seconds=1)    # o tempo passa entre a validação e a troca
    rel = publicar(cam, agora=lambda: relogio["t"], verificador=Verificador(depois=depois))
    assert rel.codigo == 1 and "INMET deixou de valer" in rel.motivo
    assert h(cam.destino) == antes and sem_residuo(cam)
    [pasta] = backups_de(cam)
    assert h(pasta / "snapshots.js") == antes                      # backup preservado


# =========================================================================== backup, atomicidade e rollback
def test_publicacao_completa_backup_conferido_e_relatorio_gravado(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    rel = publicar(cam)
    assert rel.codigo == 0 and rel.resultado == "PUBLICADO"
    [pasta] = backups_de(cam)
    assert h(pasta / "snapshots.js") == antes
    assert (pasta / "snapshots.js.sha256").read_text(encoding="utf-8").split()[0] == antes
    assert h(cam.destino) == rel.dados["gerado"]["sha256"] != antes
    salvo = json.loads((pasta / "relatorio.json").read_text(encoding="utf-8"))
    assert salvo["codigo"] == 0 and salvo["resultado"] == "PUBLICADO" and (pasta / "relatorio.txt").is_file()
    assert sem_residuo(cam) and not Path(rel.dados["trabalho"]).exists()


def test_primeira_publicacao_sem_snapshot_anterior(tmp_path):
    cam = montar(tmp_path, publicado=False)
    rel = publicar(cam)
    assert rel.codigo == 0 and cam.destino.is_file() and h(cam.destino) == rel.dados["gerado"]["sha256"]


def test_falha_ao_gerar_snapshot(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)

    def falha(*a, **k):
        raise E.ExportError("banco ilegível (simulado)")
    monkeypatch.setattr(E, "export", falha)
    rel = publicar(cam)
    assert rel.codigo == 1 and "exportação falhou" in rel.motivo and h(cam.destino) == antes and not cam.backups.exists()


def test_falha_ao_criar_backup(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    real = P.copiar_com_hash

    def copiar(origem, destino, esperado):
        if P.dentro(destino, cam.backups):
            raise OSError(errno.EACCES, "sem permissão na pasta de backups (simulado)")
        return real(origem, destino, esperado)
    monkeypatch.setattr(P, "copiar_com_hash", copiar)
    rel = publicar(cam)
    assert rel.codigo == 1 and "backup falhou" in rel.motivo and h(cam.destino) == antes and sem_residuo(cam)


def test_backup_com_hash_divergente_para_e_o_backup_fica(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    real = shutil.copyfileobj

    def copia_corrompida(fi, fo, *a):
        real(fi, fo, *a)
        if P.dentro(Path(fo.name), cam.backups):
            fo.write(b"\n// corrompido")
    monkeypatch.setattr(P.shutil, "copyfileobj", copia_corrompida)
    rel = publicar(cam)
    assert rel.codigo == 1 and "hash divergente" in rel.motivo and h(cam.destino) == antes and sem_residuo(cam)
    [pasta] = backups_de(cam)
    assert (pasta / "snapshots.js").exists(), "backup não é apagado automaticamente"


def test_falha_de_permissao_no_destino(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    os.chmod(cam.destino, stat.S_IREAD)
    try:
        if os.access(cam.destino, os.W_OK):                        # administrador: o sistema não bloqueia
            def recusa(d):
                raise PermissionError(errno.EACCES, "arquivo somente leitura (simulado)")
            monkeypatch.setattr(P, "conferir_gravavel", recusa)
        rel = publicar(cam)
    finally:
        os.chmod(cam.destino, stat.S_IREAD | stat.S_IWRITE)
    assert rel.codigo == 1 and "não pode ser substituído" in rel.motivo
    assert h(cam.destino) == antes and sem_residuo(cam) and not any((p / "snapshots.js").exists() for p in backups_de(cam))


def test_falta_de_espaco_antes_de_comecar(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(P.shutil, "disk_usage", lambda p: Uso(10, 9, 1))
    rel = publicar(cam)
    assert rel.codigo == 1 and "espaço insuficiente" in rel.motivo and h(cam.destino) == antes and sem_residuo(cam)


def test_disco_cheio_ao_gravar_o_temporario(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)

    def cheio(destino, conteudo):
        P.estagio_de(destino).write_bytes(conteudo[:100])          # gravação interrompida no meio
        raise OSError(errno.ENOSPC, "No space left on device (simulado)")
    monkeypatch.setattr(P, "gravar_estagio", cheio)
    rel = publicar(cam)
    assert rel.codigo == 1 and "No space left" in rel.motivo
    assert h(cam.destino) == antes and sem_residuo(cam)
    [pasta] = backups_de(cam)
    assert h(pasta / "snapshots.js") == antes


def test_falha_durante_a_substituicao_mantem_o_anterior(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    tentativas = []

    def bloqueado(a, b):
        tentativas.append(1)
        raise PermissionError(errno.EACCES, "arquivo em uso por outro programa (simulado)")
    monkeypatch.setattr(P, "_trocar", bloqueado)
    rel = publicar(cam)
    assert rel.codigo == 1 and "substituição recusada" in rel.motivo and len(tentativas) == P.TENTATIVAS_SUBSTITUICAO
    assert h(cam.destino) == antes and sem_residuo(cam)
    [pasta] = backups_de(cam)
    assert h(pasta / "snapshots.js") == antes


def test_bloqueio_passageiro_no_windows_e_superado_com_novas_tentativas(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    real, n = os.replace, []

    def passageiro(a, b):
        n.append(1)
        if len(n) <= 2:
            raise PermissionError(errno.EACCES, "em uso (simulado)")
        return real(a, b)
    monkeypatch.setattr(P, "_trocar", passageiro)
    rel = publicar(cam)
    assert rel.codigo == 0 and rel.dados["publicacao"]["tentativasSubstituicao"] == 3


@pytest.mark.parametrize("falha_depois", [(P.FALHA, "REPROVADO (1 problema(s))"), KeyboardInterrupt(), RuntimeError("queda (simulada)")])
def test_falha_na_validacao_posterior_restaura_o_anterior(tmp_path, falha_depois):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    v = Verificador((P.OK, "APROVADO"), falha_depois)              # passa no gerado; falha no publicado
    rel = publicar(cam, verificador=v)
    assert rel.codigo == 1 and rel.resultado == "PUBLICAÇÃO DESFEITA" and "restaurado" in rel.motivo
    assert h(cam.destino) == antes and sem_residuo(cam)
    [pasta] = backups_de(cam)
    assert h(pasta / "snapshots.js") == antes and (pasta / "snapshots.js.rejeitado").is_file()
    assert json.loads((pasta / "relatorio.json").read_text(encoding="utf-8"))["resultado"] == "PUBLICAÇÃO DESFEITA"


def test_arquivo_publicado_diferente_do_validado_e_desfeito(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    real = P.substituir

    def corrompe(est, destino):
        r = real(est, destino)
        if destino == cam.destino and h(destino) != antes:
            with open(destino, "ab") as f:
                f.write(b" ")
        return r
    monkeypatch.setattr(P, "substituir", corrompe)
    rel = publicar(cam)
    assert rel.resultado == "PUBLICAÇÃO DESFEITA" and h(cam.destino) == antes


def test_restauracao_que_falha_e_falha_grave_com_instrucoes(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    real, n = os.replace, []

    def so_a_primeira(a, b):
        n.append(1)
        if len(n) == 1:
            return real(a, b)
        raise PermissionError(errno.EACCES, "bloqueado (simulado)")
    monkeypatch.setattr(P, "_trocar", so_a_primeira)
    rel = publicar(cam, verificador=Verificador((P.OK, "ok"), (P.FALHA, "REPROVADO")))
    assert rel.codigo == 4 and rel.resultado == "FALHA GRAVE"
    [pasta] = backups_de(cam)
    assert str(pasta / "snapshots.js") in rel.motivo and h(pasta / "snapshots.js") == antes
    assert json.loads((pasta / "relatorio.json").read_text(encoding="utf-8"))["codigo"] == 4
    assert sem_residuo(cam)


def test_destino_alterado_por_outro_programa_durante_a_publicacao(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    real = P.gravar_estagio

    def e_alguem_edita(destino, conteudo):
        r = real(destino, conteudo)
        if destino == cam.destino:
            destino.write_text(destino.read_text(encoding="utf-8") + "// editado", encoding="utf-8")
        return r
    monkeypatch.setattr(P, "gravar_estagio", e_alguem_edita)
    rel = publicar(cam)
    assert rel.codigo == 1 and "mudou durante a publicação" in rel.motivo
    assert cam.destino.read_text(encoding="utf-8").endswith("// editado"), "a edição do outro programa não é sobrescrita"
    assert sem_residuo(cam)
    [pasta] = backups_de(cam)
    assert h(pasta / "snapshots.js") == antes


def test_residuo_de_publicacao_interrompida_bloqueia_publicar_e_avisa_na_simulacao(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    est = P.estagio_de(cam.destino)
    est.write_bytes(b"sobra de uma queda de energia")
    rel = publicar(cam)
    assert rel.codigo == 2 and "interrompida" in rel.motivo and h(cam.destino) == antes
    assert est.read_bytes() == b"sobra de uma queda de energia", "o resíduo não é apagado sem conferência"
    rel = rodar(cam)
    assert rel.codigo == 0 and any(e["etapa"] == "Resíduo" and e["status"] == "aviso" for e in rel.etapas)


def test_restaurar_volta_ao_snapshot_anterior_com_hash_conferido(tmp_path):
    cam = montar(tmp_path)
    antes = h(cam.destino)
    assert publicar(cam).codigo == 0
    novo = h(cam.destino)
    [pasta] = backups_de(cam)
    rel = rodar(cam, restaurar=pasta)
    assert rel.codigo == 2 and h(cam.destino) == novo              # sem confirmação: nada muda
    rel = rodar(cam, restaurar=pasta, confirmar_atual=novo[:12])
    assert rel.codigo == 0 and rel.resultado == "RESTAURADO" and h(cam.destino) == antes
    [rest] = backups_de(cam, "restauracao")
    assert h(rest / "snapshots.js") == novo and h(pasta / "snapshots.js") == antes   # os dois backups ficam


def test_restaurar_recusa_backup_corrompido_ou_que_nao_e_backup(tmp_path):
    cam = montar(tmp_path)
    assert publicar(cam).codigo == 0
    atual = h(cam.destino)
    [pasta] = backups_de(cam)
    (pasta / "snapshots.js").write_bytes(b"window.RIGORA_SNAPSHOTS = {};")
    rel = rodar(cam, restaurar=pasta, confirmar_atual=atual[:12])
    assert rel.codigo == 1 and "corrompido" in rel.motivo and h(cam.destino) == atual
    rel = rodar(cam, restaurar=tmp_path, confirmar_atual=atual[:12])
    assert rel.codigo == 2 and h(cam.destino) == atual


# =========================================================================== compatibilidade
def test_caminhos_com_espacos_e_acentos(tmp_path):
    cam = montar(tmp_path, nome="Pasta com espaço/Ação Rigora", backups="Cópias de segurança/publicações")
    antes = h(cam.destino)
    assert rodar(cam).codigo == 0
    assert publicar(cam).codigo == 0
    [pasta] = backups_de(cam)
    assert rodar(cam, restaurar=pasta, confirmar_atual=h(cam.destino)[:12]).codigo == 0 and h(cam.destino) == antes


def test_reexecucao_cria_backups_novos_e_nunca_sobrescreve(tmp_path):
    cam = montar(tmp_path)
    assert rodar(cam).codigo == 0 and rodar(cam).codigo == 0 and not cam.backups.exists()
    h0 = h(cam.destino)
    assert publicar(cam).codigo == 0                               # mesmo relógio congelado: mesma hora
    h1 = h(cam.destino)
    assert publicar(cam).codigo == 0
    p1, p2 = backups_de(cam)
    assert p1.name.endswith("_publicacao") and p2.name.endswith("_publicacao_2")
    assert h(p1 / "snapshots.js") == h0 and h(p2 / "snapshots.js") == h1 and sem_residuo(cam)


def test_trabalho_explicito_fica_guardado_e_precisa_ser_pasta_nova(tmp_path):
    cam = montar(tmp_path)
    t = tmp_path / "trabalho com espaço"
    rel = rodar(cam, trabalho=t)
    assert rel.codigo == 0 and (t / "gerado" / "snapshots.js").is_file() and (t / "ensaio" / "js" / "snapshots.js").is_file()
    rel = rodar(cam, trabalho=t)
    assert rel.codigo == 3 and "pasta nova ou vazia" in rel.motivo


def test_sincronizacao_da_pasta_tolera_sistemas_sem_suporte(tmp_path, monkeypatch):
    def sem_suporte(*a, **k):
        raise OSError(errno.EINVAL, "não suportado (simulado)")
    monkeypatch.setattr(P.os, "open", sem_suporte)
    P.sincronizar_pasta(tmp_path)                                  # não lança


# =========================================================================== integração: verificador B5 real
def _node():
    if not shutil.which("node"):
        pytest.fail("Node.js é obrigatório para os testes de integração do Bloco 3 (verificador B5)")


def test_integracao_verificador_b5_real_reprova_e_nada_e_publicado(tmp_path):
    """O verificador real (Node, TZ de Brasília) exige 60 municípios: o banco reduzido das fixtures reprova."""
    _node()
    cam = montar(tmp_path, nome="integração com espaço")
    cam.verificador = config.ROOT / "tests" / "check_exported_snapshot.js"
    antes = h(cam.destino)
    st, det = P.verificar_com_node(gerar(cam, tmp_path), cam)
    assert st == P.FALHA and det.startswith("REPROVADO") and "esperados 60 municípios" in det
    rel = P.executar(P.Opcoes(publicar=True, confirmar_atual=antes[:12]), cam, agora=lambda: AGORA, saida=None)
    assert rel.codigo == 1 and h(cam.destino) == antes and not cam.backups.exists()
    assert any("verificador B5: REPROVADO" in e["detalhe"] for e in rel.etapas)


def test_integracao_verificador_b5_real_le_caminhos_acentuados_e_recusa_copia_manual(tmp_path):
    """Chamada real do Node com caminho com espaço e acento: o verificador lê o arquivo e recusa a cópia manual (sem a
    seção exportacao), que portanto nunca passaria como snapshot gerado pelo pipeline."""
    _node()
    import subprocess
    pasta = tmp_path / "pasta com espaço e ação"
    pasta.mkdir()
    ref = pasta / "referência.js"
    shutil.copyfile(MANUAL, ref)
    r = subprocess.run([shutil.which("node"), str(config.ROOT / "tests" / "check_exported_snapshot.js"), str(ref), str(ref)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, TZ=P.FUSO_VERIFICACAO))
    out = r.stdout + r.stderr
    assert "Verificador do snapshot exportado (B5)" in out and "Resultado:" in out    # o Node leu os caminhos acentuados
    assert r.returncode == 1 and "exportacao ausente" in out                         # a cópia manual não tem exportacao: nunca seria publicada


# =========================================================================== quebra de linha (Windows)
# Falha encontrada no Windows (16/11/2026): sem newline explícito, Path.write_text grava "\r\n" e o snapshot
# terminava em "};\r\n"; o verificador B5 exige "};\n" e reprovava o contrato. A emulação abaixo reproduz a
# escrita de texto do Windows (Path.write_text sem newline usa "\r\n", que é o os.linesep do Windows).
import codecs  # noqa: E402
import subprocess  # noqa: E402

_WRITE_TEXT = Path.write_text


def _escrita_windows(self, data, encoding=None, errors=None, newline=None):
    return _WRITE_TEXT(self, data, encoding=encoding, errors=errors, newline="\r\n" if newline is None else newline)


def _bytes_do_contrato(b: bytes) -> None:
    assert not b.startswith(codecs.BOM_UTF8), "snapshot com BOM"
    cr = b.count(b"\r")
    assert cr == 0, f"snapshot com {cr} byte(s) CR"
    assert b.endswith(b"};\n"), f"snapshot termina com {b[-6:]!r}"


def _contrato_b5(arquivo: Path) -> list:
    """verificarContrato do próprio verificador B5 (tests/check_exported_snapshot.js), chamado pelo Node."""
    _node()
    js = ("const v=require(process.argv[1]),fs=require('fs'),vm=require('vm');const t=fs.readFileSync(process.argv[2],'utf8');"
          "const s={window:{}};vm.runInNewContext(t,s);"
          "process.stdout.write(JSON.stringify(v.verificarContrato(t,JSON.parse(JSON.stringify(s.window.RIGORA_SNAPSHOTS)))))")
    r = subprocess.run([shutil.which("node"), "-e", js, str(config.ROOT / "tests" / "check_exported_snapshot.js"), str(arquivo)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_emulacao_reproduz_a_escrita_do_windows_e_o_defeito_anterior(tmp_path, monkeypatch):
    cam = montar(tmp_path, publicado=False)
    snap = E.parse_snapshots_js(E.render(E.build(cam.banco, None, AGORA)))
    monkeypatch.setattr(Path, "write_text", _escrita_windows)
    antigo = tmp_path / "como antes da correção.js"
    antigo.write_text(E.render(snap), encoding="utf-8")            # a escrita de antes: sem newline explícito
    b = antigo.read_bytes()
    assert b.count(b"\r\n") == b.count(b"\n") == 18 and b.endswith(b"};\r\n")
    assert E.validate_contract(E.parse_snapshots_js(antigo.read_text(encoding="utf-8"))) == []   # o Python não percebia
    assert 'arquivo não termina com "};"' in _contrato_b5(antigo)                                  # o B5 recusava (falha do Windows)


def test_exportador_grava_lf_sem_bom_e_termina_em_chave_ponto_e_virgula_tambem_no_windows(tmp_path, monkeypatch):
    cam = montar(tmp_path, publicado=False)
    normal = tmp_path / "normal" / "snapshots.js"
    windows = tmp_path / "windows" / "snapshots.js"
    normal.parent.mkdir()
    windows.parent.mkdir()
    E.export(cam.banco, normal, now=AGORA)
    monkeypatch.setattr(Path, "write_text", _escrita_windows)
    E.export(cam.banco, windows, now=AGORA)
    for arq in (normal, windows):
        _bytes_do_contrato(arq.read_bytes())
    assert windows.read_bytes() == normal.read_bytes()             # mesma geração, mesmos bytes nos dois sistemas


def test_estrutura_e_dados_iguais_entre_geracao_normal_e_windows(tmp_path, monkeypatch):
    cam = montar(tmp_path, publicado=False)
    normal = gerar(cam, tmp_path)
    monkeypatch.setattr(Path, "write_text", _escrita_windows)
    windows = tmp_path / "w" / "snapshots.js"
    windows.parent.mkdir()
    E.export(cam.banco, windows, now=AGORA)
    a = E.parse_snapshots_js(normal.read_text(encoding="utf-8"))
    b = E.parse_snapshots_js(windows.read_text(encoding="utf-8"))
    assert list(a) == list(b) == ["sidra", "inmet", "exportacao"]
    assert a == b and E.validate_contract(b) == []                 # nenhum dado muda (sidra, inmet, exportacao)
    linhas = windows.read_bytes().decode("utf-8").split("\n")
    assert [l.split(":")[0] for l in linhas if l.startswith("  ")] == ["  sidra", "  inmet", "  exportacao"]
    assert linhas[-2:] == ["};", ""]


def test_contrato_b5_aprovado_no_snapshot_gerado_com_a_escrita_do_windows(tmp_path, monkeypatch):
    cam = montar(tmp_path, publicado=False)
    monkeypatch.setattr(Path, "write_text", _escrita_windows)
    g = gerar(cam, tmp_path)
    assert _contrato_b5(g) == []


def test_simulacao_com_a_escrita_do_windows_passa_do_contrato_no_verificador_b5_real(tmp_path, monkeypatch):
    """Verificador real (Node): o snapshot das fixtures passa do contrato e só reprova pelos dados reduzidos
    (60 municípios exigidos), como no Linux. Antes da correção, reprovava no contrato ("};")."""
    _node()
    cam = montar(tmp_path)
    cam.verificador = config.ROOT / "tests" / "check_exported_snapshot.js"
    monkeypatch.setattr(Path, "write_text", _escrita_windows)
    st, det = P.verificar_com_node(gerar(cam, tmp_path), cam)
    assert st == P.FALHA and "esperados 60 municípios" in det and "não termina" not in det and "contrato inválido" not in det
    rel = P.executar(P.Opcoes(trabalho=tmp_path / "trabalho"), cam, agora=lambda: AGORA, saida=None)
    gerado = tmp_path / "trabalho" / "gerado" / "snapshots.js"
    _bytes_do_contrato(gerado.read_bytes())
    assert rel.codigo == 1 and any("esperados 60 municípios" in e["detalhe"] for e in rel.etapas)


def test_publicacao_em_copia_preserva_os_bytes_lf_com_a_escrita_do_windows(tmp_path, monkeypatch):
    cam = montar(tmp_path)
    monkeypatch.setattr(Path, "write_text", _escrita_windows)
    rel = publicar(cam)
    assert rel.codigo == 0, rel.motivo
    _bytes_do_contrato(cam.destino.read_bytes())
    assert h(cam.destino) == rel.dados["gerado"]["sha256"]
