"""Testes da estabilidade 1.0: preservação dos dados do SIDRA (INT-08), backup integral de data/ e
referências B5 versionadas. Tudo em pastas temporárias, sem rede; o banco real não é lido (test_16)."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import backup_dados as B
from pipeline import config
from pipeline import export_snapshots as E
from pipeline import publicar as P
from pipeline import referencias as R
from pipeline.validate import ValidationError
from tests.test_pipeline_export import FIX, MANUAL, pam_files
from pipeline.run_pipeline import process

PAT = "3148103"


def h(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# =========================================================================== A. preservação do SIDRA
def payload(mudar=None) -> bytes:
    rows = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    if mudar:
        rows = [rows[0]] + mudar(rows[1:])
    return json.dumps(rows, ensure_ascii=False).encode("utf-8")


def carregar(tmp, pam_bytes=None):
    raw, db, out = tmp / "raw", tmp / "rigora.db", tmp / "out"
    pam, loc = pam_files(raw, pam_bytes)
    return process(pam, loc, db, out)


def pam(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT codigo_ibge, ano, rendimento_kg_ha, area_colhida_ha, producao_t, execucao_id "
                         "FROM pam_municipio_ano ORDER BY 1, 2").fetchall()
    finally:
        c.close()


def regra(db, eid, nome="INT-08"):
    c = sqlite3.connect(db)
    try:
        r = c.execute("SELECT status, registros_afetados FROM validacao_regra WHERE execucao_id = ? AND regra = ?", (eid, nome)).fetchone()
        oc = c.execute("SELECT codigo_ibge, ano, detalhe FROM validacao_ocorrencia WHERE execucao_id = ? AND regra = ?", (eid, nome)).fetchall()
        st = c.execute("SELECT status FROM execucao WHERE id = ?", (eid,)).fetchone()
        return r, oc, st[0] if st else None
    finally:
        c.close()


def test_primeira_carga_nao_tem_o_que_comparar_e_mantem_as_26_regras(tmp_path):
    res = carregar(tmp_path)
    assert regra(tmp_path / "rigora.db", res["execucao_pam"])[0] is None
    assert len(res["validacao"]["regras"]) == 26


def test_coleta_identica_ou_revisada_e_aceita(tmp_path):
    carregar(tmp_path)

    def revisa(rows):
        for r in rows:
            if r["D1C"] == PAT and r["D3C"] == "2024" and r["D2C"] == "214":
                r["V"] = str(int(float(r["V"])) + 10)
        return rows
    for b in (payload(), payload(revisa)):
        res = carregar(tmp_path, b)
        st, oc, ex = regra(tmp_path / "rigora.db", res["execucao_pam"])
        assert st == ("aprovada", 0) and oc == [] and ex == "sucesso"


@pytest.mark.parametrize("mudar,esperado", [
    (lambda rows: [r for r in rows if not (r["D1C"] == PAT and r["D3C"] == "2016")], "viria ausente"),   # a grade completa o ano com ausentes
    (lambda rows: [dict(r, V="..") if (r["D1C"] == PAT and r["D3C"] == "2016") else r for r in rows], "viria ausente"),
])
def test_resposta_parcial_nao_apaga_dado_valido_e_registra_a_ocorrencia(tmp_path, mudar, esperado):
    primeira = carregar(tmp_path)
    antes = pam(tmp_path / "rigora.db")
    with pytest.raises(ValidationError):
        carregar(tmp_path, payload(mudar))
    depois = pam(tmp_path / "rigora.db")
    assert depois == antes, "nenhum valor pode mudar quando a carga é recusada"
    assert {r[5] for r in depois} == {primeira["execucao_pam"]}
    c = sqlite3.connect(tmp_path / "rigora.db")
    eid = c.execute("SELECT MAX(id) FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%'").fetchone()[0]
    c.close()
    st, oc, ex = regra(tmp_path / "rigora.db", eid)
    assert ex == "falha" and st[0] == "acionada" and st[1] == len(oc) >= 1
    assert any(o[0] == PAT and esperado in o[2] for o in oc), oc
    rel = json.loads((tmp_path / "reports" / f"qualidade_execucao_{eid:04d}.json").read_text(encoding="utf-8"))
    assert rel["status_execucao"] == "falha" and any(r["regra"] == "INT-08" for r in rel["regras"])


def test_registros_fora_da_resposta_sao_preservados_sem_bloquear(tmp_path):
    """Município inteiro ausente ou só alguns anos na resposta: o UPSERT não toca o que não veio (3b)."""
    primeira = carregar(tmp_path)
    antes = {r[:2]: r for r in pam(tmp_path / "rigora.db")}
    res = carregar(tmp_path, payload(lambda rows: [r for r in rows if r["D1C"] != PAT]))
    depois = {r[:2]: r for r in pam(tmp_path / "rigora.db")}
    assert all(depois[k] == antes[k] for k in antes if k[0] == PAT)                  # valores e execução antigos mantidos
    assert regra(tmp_path / "rigora.db", res["execucao_pam"])[0] == ("aprovada", 0)
    assert {v[5] for k, v in depois.items() if k[0] != PAT} == {res["execucao_pam"]} != {primeira["execucao_pam"]}


# =========================================================================== B. backup integral
def dados(tmp):
    d = tmp / "proj" / "data"
    for rel, conteudo in {"rigora.db": b"banco", "raw/a.json": b"bruto", "raw/a.meta.json": b"{}",
                          "processed/pam.csv": b"csv", "reports/q.json": b"relatorio", "rigora_teste.db-journal": b"historico"}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(conteudo)
    return d


def test_backup_integral_inclui_tudo_com_manifesto_e_verificacao(tmp_path):
    d = dados(tmp_path)
    pasta = B.criar(d, tmp_path / "backups com espaço", "teste")
    m = json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))
    assert {a["caminho"] for a in m["arquivos"]} == {f"data/{p}" for p in B.inventario(d)}
    assert all(a["sha256"] == h(d / a["caminho"][5:]) for a in m["arquivos"])
    assert B.verificar(pasta) == []
    linhas = (pasta / "MANIFESTO.sha256").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == m["total"] == 6


def test_backup_recusa_destino_dentro_de_data_e_banco_com_transacao(tmp_path):
    d = dados(tmp_path)
    with pytest.raises(B.BackupError, match="dentro de data"):
        B.criar(d, d / "bk")
    (d / "rigora.db-journal").write_bytes(b"j")
    with pytest.raises(B.BackupError, match="transação pendente"):
        B.criar(d, tmp_path / "bk")


def test_backup_falha_se_os_dados_mudarem_durante_a_copia(tmp_path, monkeypatch):
    d = dados(tmp_path)
    real = B._copiar

    def e_muda(o, dst, esp):
        real(o, dst, esp)
        (d / "processed" / "pam.csv").write_bytes(b"mudou")
    monkeypatch.setattr(B, "_copiar", e_muda)
    with pytest.raises(B.BackupError, match="mudaram durante o backup"):
        B.criar(d, tmp_path / "bk")


def test_verificar_detecta_arquivo_alterado_ausente_ou_extra(tmp_path):
    pasta = B.criar(dados(tmp_path), tmp_path / "bk")
    (pasta / "data" / "raw" / "a.json").write_bytes(b"adulterado")
    (pasta / "data" / "reports" / "q.json").unlink()
    (pasta / "data" / "intruso.txt").write_bytes(b"x")
    p = B.verificar(pasta)
    assert any("diferente do manifesto: data/raw/a.json" in x for x in p)
    assert any("ausente: data/reports/q.json" in x for x in p)
    assert any("fora do manifesto: data/intruso.txt" in x for x in p)
    with pytest.raises(B.BackupError, match="nada foi restaurado"):
        B.restaurar(pasta, tmp_path / "novo")


def test_recuperacao_comprovada_em_copia_temporaria(tmp_path):
    d = dados(tmp_path)
    original = B.inventario(d)
    pasta = B.criar(d, tmp_path / "bk")
    (d / "rigora.db").write_bytes(b"banco corrompido")            # o desastre
    (d / "processed" / "pam.csv").unlink()
    with pytest.raises(B.BackupError, match="pasta nova ou vazia"):
        B.restaurar(pasta, d)                                      # nunca sobrescreve
    novo = tmp_path / "proj" / "data_restaurado"
    assert B.restaurar(pasta, novo) == 6
    d.rename(tmp_path / "proj" / "data_danificado")               # troca manual documentada
    novo.rename(d)
    assert B.inventario(d) == original


def test_cli_do_backup_recusa_pastas_dentro_do_projeto(tmp_path):
    assert B.main(["criar", "--backups", str(config.ROOT / "bk")]) == 3
    assert B.main(["restaurar", str(tmp_path), "--destino", str(config.ROOT / "x")]) == 3
    assert not (config.ROOT / "bk").exists() and not (config.ROOT / "x").exists()


def test_coletar_faz_backup_integral_verificado_antes_e_bloqueia_se_falhar(tmp_path, monkeypatch):
    from tests.test_publicar import montar, Verificador, AGORA
    cam = montar(tmp_path)
    vistos = []

    def coletor(fontes, banco, c):
        [pasta] = sorted(cam.backups.glob("*_coleta"))
        vistos.append(B.verificar(pasta))                          # o backup já existe e confere quando a coleta começa
        return {"sidra": {"status": "sucesso"}, "inmet": {"status": "sucesso"}}
    rel = P.executar(P.Opcoes(coletar=True), cam, agora=lambda: AGORA, verificador=Verificador(), coletor=coletor, saida=None)
    assert rel.codigo == 0 and vistos == [[]]
    assert B.inventario(Path(rel.dados["backupDados"]) / "data") == B.inventario(cam.banco.parent)

    def quebra(*a, **k):
        raise B.BackupError("disco cheio (simulado)")
    monkeypatch.setattr(B, "criar", quebra)
    rel = P.executar(P.Opcoes(coletar=True), cam, agora=lambda: AGORA, verificador=Verificador(),
                     coletor=lambda *a: pytest.fail("a coleta não pode começar sem backup"), saida=None)
    assert rel.codigo == 1 and "coleta não iniciada" in rel.motivo


# =========================================================================== C. referências B5 versionadas
def projeto_ref(tmp):
    raiz = tmp / "ref proj"
    for rel in ("tests/check_exported_snapshot.js", R.HISTORICA, "js/metrics.js", "js/data.js", "js/charts.js", "js/app.js"):
        (raiz / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.ROOT / rel, raiz / rel)
    (raiz / R.REGISTRO).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.ROOT / R.REGISTRO, raiz / R.REGISTRO)
    return raiz


def gerado_valido(tmp, mudar=None, nome="gerado.js", inmet=True) -> Path:
    """Snapshot no formato do exportador, com os dados aprovados da fixture e exportacao coerente."""
    s = E.parse_snapshots_js(MANUAL.read_text(encoding="utf-8"))
    if mudar:
        mudar(s)
    if not inmet:
        s["inmet"] = None
    s["exportacao"] = {"geradoEm": "2026-11-14T18:00:00+00:00", "geradoPor": "python -m pipeline.export_snapshots", "banco": "rigora.db",
                       "aviso": "teste", "sidra": {"modo": "sqlite", "fonte": "IBGE/SIDRA PAM 1613 (café arábica, MG)", "execucaoId": 7,
                       "execucoesNosDadosExportados": [7], "dataColeta": s["sidra"]["collectedAt"], "hashSha256": "a" * 64,
                       "statusExecucao": "sucesso", "validacao": {"resultado": "aprovado"}, "municipiosExportados": 60,
                       "registrosExportados": 600, "municipiosNaMediaMG": s["sidra"]["municipiosMG"]},
                       "inmet": {"modo": "sqlite", "execucaoId": 8, "dataColeta": "2026-11-14T16:13:28.004Z"} if inmet else
                       {"modo": "indisponivel", "motivo": "teste"}}
    p = tmp / nome
    p.write_text(E.render(s), encoding="utf-8", newline="\n")
    return p


def revisar_patrocinio(s):
    t = next(m for m in s["sidra"]["municipios"] if m["c"] == PAT)["y"]["2016"]
    t[2] += 100
    t[0] = round(t[2] * 1000 / t[1])


def test_registro_do_projeto_aponta_a_referencia_historica_intacta():
    reg = R.carregar()
    assert reg["ativa"] == "historica-20261114"
    assert R.ativa() == config.ROOT / R.HISTORICA and h(R.ativa()) == "410fa9cb428b440f4eb71f2de29998cb72922595d8ec04e4657cffcc2d61b0d5"
    assert P.Caminhos.padrao().referencia == (config.ROOT / R.HISTORICA).resolve()


def test_propor_registra_diferencas_e_nao_ativa_nada(tmp_path):
    raiz = projeto_ref(tmp_path)
    historica = h(raiz / R.HISTORICA)
    e = R.propor(gerado_valido(tmp_path, revisar_patrocinio), raiz)
    assert e["status"] == "proposta" and R.carregar(raiz)["ativa"] == "historica-20261114"
    assert R.ativa(raiz) == raiz / R.HISTORICA and h(raiz / R.HISTORICA) == historica
    assert {d["onde"] for d in e["diferencas"]} == {f"município {PAT} 2016"}
    assert e["execucaoSidra"] == 7 and e["hashBrutoSidra"] == "a" * 64 and (raiz / e["arquivo"]).is_file()
    assert h(raiz / e["arquivo"]) == e["sha256"]


def test_propor_recusa_snapshot_que_nao_serve_de_referencia(tmp_path):
    raiz = projeto_ref(tmp_path)
    with pytest.raises(R.ReferenciaError, match="previsão do INMET"):
        R.propor(gerado_valido(tmp_path, inmet=False), raiz)
    with pytest.raises(R.ReferenciaError, match="modo sqlite"):
        g = gerado_valido(tmp_path, nome="m.js")
        s = E.parse_snapshots_js(g.read_text(encoding="utf-8"))
        s["exportacao"]["sidra"]["modo"] = "mantido_do_arquivo_anterior"
        g.write_text(E.render(s), encoding="utf-8", newline="\n")
        R.propor(g, raiz)
    with pytest.raises(R.ReferenciaError, match="além das diferenças"):
        R.propor(gerado_valido(tmp_path, lambda s: s["sidra"]["municipios"].pop(), nome="59.js"), raiz)
    assert len(R.carregar(raiz)["referencias"]) == 1


def test_aprovar_exige_hash_responsavel_e_justificativa_e_preserva_a_anterior(tmp_path):
    raiz = projeto_ref(tmp_path)
    e = R.propor(gerado_valido(tmp_path, revisar_patrocinio), raiz)
    for args, msg in [(("0" * 12, "Fulano", "revisão do IBGE"), "não é o do arquivo"), ((e["sha256"][:12], "", "x"), "obrigatórios"),
                      ((e["sha256"][:12], "Fulano", " "), "obrigatórios"), (("abc", "Fulano", "x"), "12 a 64")]:
        with pytest.raises(R.ReferenciaError, match=msg):
            R.aprovar(e["id"], *args, raiz=raiz)
    assert R.carregar(raiz)["ativa"] == "historica-20261114"
    a = R.aprovar(e["id"], e["sha256"][:12], "Fulano", "revisão anual do IBGE conferida", raiz=raiz)
    reg = R.carregar(raiz)
    assert reg["ativa"] == e["id"] and a["substituiu"] == "historica-20261114"
    assert R.ativa(raiz) == raiz / e["arquivo"]
    assert R._entrada(reg, "historica-20261114")["status"] == "aprovada" and (raiz / R.HISTORICA).is_file()
    with pytest.raises(R.ReferenciaError, match="não está proposta"):
        R.aprovar(e["id"], e["sha256"][:12], "Fulano", "de novo", raiz=raiz)
    assert P.Caminhos.padrao(raiz).referencia == (raiz / e["arquivo"]).resolve()


def test_referencia_adulterada_e_recusada(tmp_path):
    raiz = projeto_ref(tmp_path)
    e = R.propor(gerado_valido(tmp_path), raiz)
    (raiz / e["arquivo"]).write_bytes((raiz / e["arquivo"]).read_bytes() + b"\n")
    with pytest.raises(R.ReferenciaError, match="alterado desde a proposta"):
        R.aprovar(e["id"], e["sha256"][:12], "Fulano", "x", raiz=raiz)
    (raiz / R.HISTORICA).write_bytes(b"adulterada")
    with pytest.raises(R.ReferenciaError, match="hash diferente"):
        R.ativa(raiz)
    with pytest.raises(R.ReferenciaError):
        P.Caminhos.padrao(raiz)


def test_portao_usa_a_referencia_ativa_do_registro(tmp_path):
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("portao_1_0", config.ROOT / "tools" / "portao.py")
    portao = importlib.util.module_from_spec(spec)
    sys.modules["portao_1_0"] = portao
    spec.loader.exec_module(portao)
    assert portao.referencia_ativa(config.ROOT) == config.ROOT / R.HISTORICA
    raiz = projeto_ref(tmp_path)
    e = R.propor(gerado_valido(tmp_path, revisar_patrocinio), raiz)
    assert portao.referencia_ativa(raiz) == raiz / R.HISTORICA            # proposta não vale
    R.aprovar(e["id"], e["sha256"][:12], "Fulano", "revisão", raiz=raiz)
    assert portao.referencia_ativa(raiz) == raiz / e["arquivo"]


def test_verificador_aprova_contra_a_nova_referencia_aprovada(tmp_path):
    """Depois de aprovada, a revisão deixa de ser diferença: o B5 (real, Node) aprova o mesmo snapshot."""
    if not shutil.which("node"):
        pytest.fail("Node.js é obrigatório")
    raiz = projeto_ref(tmp_path)
    g = gerado_valido(tmp_path, revisar_patrocinio)
    cmd = lambda ref: subprocess.run([shutil.which("node"), str(raiz / "tests" / "check_exported_snapshot.js"), str(g), str(ref)],  # noqa: E731
                                     capture_output=True, text=True, encoding="utf-8", errors="replace").returncode
    assert cmd(R.ativa(raiz)) == 1                                          # contra a histórica: reprova
    e = R.propor(g, raiz)
    R.aprovar(e["id"], e["sha256"][:12], "Fulano", "revisão", raiz=raiz)
    assert cmd(R.ativa(raiz)) == 0
