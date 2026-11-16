"""Testes do portão de qualidade (tools/portao.py, Bloco 1).

O executor dos comandos é simulado: estes testes não rodam as suítes de verdade (isso é o portão
inteiro, executado à parte) e só usam pastas temporárias. Um único teste roda um comando real,
para conferir a captura do código de saída no sistema em uso.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("portao", RAIZ / "tools" / "portao.py")
portao = importlib.util.module_from_spec(_spec)
sys.modules["portao"] = portao                 # dataclasses precisam do módulo registrado
_spec.loader.exec_module(portao)


def projeto(tmp_path):
    r = tmp_path / "proj"
    (r / "data").mkdir(parents=True)
    (r / "js").mkdir()
    (r / "tests" / "fixtures").mkdir(parents=True)
    (r / "data" / "rigora.db").write_bytes(b"banco")
    (r / "js" / "snapshots.js").write_text("window.RIGORA_SNAPSHOTS = {};", encoding="utf-8")
    (r / "tests" / "fixtures" / "ref.js").write_text("ref", encoding="utf-8")
    (r / "README.md").write_text("x", encoding="utf-8")
    gerado = tmp_path / "gerado.js"
    gerado.write_text("g", encoding="utf-8")
    return r, gerado, r / "tests" / "fixtures" / "ref.js"


class Executor:
    """Responde como as suítes reais aprovadas; cada teste troca a resposta de uma etapa."""

    def __init__(self, **trocas):
        self.trocas, self.chamadas = trocas, []

    @staticmethod
    def chave(cmd):
        if "pytest" in cmd and "--version" in cmd:
            return "pytest_versao"
        if cmd[-1] == "--version":
            return "node_versao"
        if "-e" in cmd:
            return "fuso"
        if "tests/metrics.test.js" in cmd:
            return "js"
        if "pytest" in cmd:
            return "pytest"
        if "--autoteste" in cmd:
            return "autoteste"
        return "verificador"

    def __call__(self, cmd, raiz, env=None, timeout=0):
        tz = (env or {}).get("TZ")
        k = self.chave(cmd)
        self.chamadas.append((k, tz))
        r = self.trocas.get(k)
        if callable(r):
            return r(tz)
        if r is not None:
            return r
        return {"pytest_versao": (0, "pytest 9.2.0"), "node_versao": (0, "v22.0.0"), "fuso": (0, tz),
                "js": (0, "  ok - x\n88 testes aprovados, 0 reprovados."), "pytest": (0, "....\n296 passed in 57.42s"),
                "verificador": (0, "Resultado: APROVADO"),
                "autoteste": (0, "18 de 18 autotestes aprovados, 0 reprovados.")}[k]


def rodar(tmp_path, gerado=True, **trocas):
    r, g, ref = projeto(tmp_path)
    ex = Executor(**trocas)
    res = portao.rodar(r, g if gerado else None, ref, run=ex, python="python", node="node")
    return res, ex, r


def status(res, prefixo):
    return [e.status for e in res.etapas if e.nome.startswith(prefixo)]


def test_tudo_aprovado_executa_as_tres_suites_e_o_verificador_nos_tres_fusos(tmp_path):
    res, ex, _ = rodar(tmp_path)
    assert res.veredito == "APROVADO" and res.codigo == 0
    assert [tz for k, tz in ex.chamadas if k == "js"] == list(portao.FUSOS)
    assert [tz for k, tz in ex.chamadas if k == "verificador"] == list(portao.FUSOS)
    assert [tz for k, tz in ex.chamadas if k == "autoteste"] == list(portao.FUSOS)
    assert sum(1 for k, _ in ex.chamadas if k == "pytest") == 1
    assert status(res, "Inventário") == ["APROVADO", "APROVADO"]


@pytest.mark.parametrize("etapa,resposta", [
    ("js", (1, "  FALHOU - B7-02 x\n87 testes aprovados, 1 reprovado.")),
    ("pytest", (1, "2 failed, 294 passed in 50s")),
    ("verificador", (1, "Resultado: REPROVADO (3 problema(s))")),
    ("autoteste", (1, "17 de 18 autotestes aprovados, 1 reprovados.")),
])
def test_falha_em_qualquer_etapa_reprova(tmp_path, etapa, resposta):
    res, _, _ = rodar(tmp_path, **{etapa: resposta})
    assert res.veredito == "REPROVADO" and res.codigo == 1


def test_falha_em_um_so_fuso_reprova_e_aponta_o_fuso(tmp_path):
    def js(tz):
        return (1, "87 testes aprovados, 1 reprovado.") if tz == "Asia/Tokyo" else (0, "88 testes aprovados, 0 reprovados.")
    res, _, _ = rodar(tmp_path, js=js)
    assert res.codigo == 1
    assert [e.nome for e in res.etapas if e.status == "REPROVADO"] == ["JavaScript (Asia/Tokyo)"]


def test_pytest_ausente_nao_e_substituido_e_o_resultado_e_incompleto(tmp_path):
    res, ex, _ = rodar(tmp_path, pytest_versao=(1, "No module named pytest"))
    assert res.veredito == "INCOMPLETO" and res.codigo == 2
    py = next(e for e in res.etapas if e.nome == "Python (pytest)")
    assert py.status == "NÃO EXECUTADO" and "pytest não disponível" in py.detalhe
    assert not [k for k, _ in ex.chamadas if k == "pytest"], "a suíte não pode ser simulada nem rodada sem pytest"


def test_node_que_ignora_o_fuso_reprova(tmp_path):
    res, _, _ = rodar(tmp_path, fuso=(0, "America/Sao_Paulo"))
    assert res.codigo == 1
    assert status(res, "JavaScript") == ["APROVADO", "REPROVADO", "REPROVADO"]


def test_sem_snapshot_gerado_o_verificador_nao_roda_e_nao_ha_aprovacao(tmp_path):
    res, ex, _ = rodar(tmp_path, gerado=False)
    assert res.veredito == "INCOMPLETO" and res.codigo == 2
    assert not [k for k, _ in ex.chamadas if k in ("verificador", "autoteste")]


@pytest.mark.parametrize("etapa,resposta,esperado", [
    ("pytest", (0, "290 passed, 6 skipped in 50s"), "INCOMPLETO"),        # ignorados não contam como aprovados
    ("pytest", (5, "no tests ran in 0.01s"), "REPROVADO"),
    ("js", (0, "sem resumo"), "REPROVADO"),
    ("js", (0, "0 testes aprovados, 0 reprovados."), "REPROVADO"),
    ("verificador", (0, "Resultado: REPROVADO"), "REPROVADO"),
    ("autoteste", (0, "0 de 0 autotestes aprovados"), "REPROVADO"),
])
def test_aprovacao_parcial_nao_vira_aprovacao(tmp_path, etapa, resposta, esperado):
    res, _, _ = rodar(tmp_path, **{etapa: resposta})
    assert res.veredito == esperado


def test_alteracao_em_arquivo_protegido_durante_o_portao_reprova(tmp_path):
    r, g, ref = projeto(tmp_path)

    def js(tz):
        if tz == "UTC":
            (r / "data" / "rigora.db").write_bytes(b"banco alterado")       # simula uma suíte que grava no banco
        return 0, "88 testes aprovados, 0 reprovados."
    res = portao.rodar(r, g, ref, run=Executor(js=js), python="python", node="node")
    assert res.codigo == 1
    assert res.protegidos_alterados == ["data/rigora.db (alterado)"]
    assert res.etapas[-1].status == "REPROVADO" and "PROTEGIDO" in res.etapas[-1].detalhe


def test_qualquer_arquivo_criado_no_projeto_reprova(tmp_path):
    r, g, ref = projeto(tmp_path)

    def py(tz):
        (r / ".pytest_cache").mkdir()
        (r / ".pytest_cache" / "x").write_text("cache", encoding="utf-8")
        return 0, "296 passed in 1s"
    res = portao.rodar(r, g, ref, run=Executor(pytest=py), python="python", node="node")
    assert res.codigo == 1 and res.alterados == [".pytest_cache/x (criado)"] and not res.protegidos_alterados


def test_inventario_registra_os_hashes_e_marca_os_protegidos(tmp_path):
    r, _, _ = projeto(tmp_path)
    h = portao.inventario(r)
    assert sorted(h) == ["README.md", "data/rigora.db", "js/snapshots.js", "tests/fixtures/ref.js"]
    import hashlib
    assert h["data/rigora.db"] == hashlib.sha256(b"banco").hexdigest()
    assert portao.protegido("data/rigora.db") and portao.protegido("js/snapshots.js") and portao.protegido("tests/fixtures/ref.js")
    assert not portao.protegido("js/app.js") and not portao.protegido("database/x") and not portao.protegido("js/snapshots.json")


def test_o_portao_nao_grava_no_projeto(tmp_path):
    r, g, ref = projeto(tmp_path)
    antes = portao.inventario(r)
    portao.rodar(r, g, ref, run=Executor(), python="python", node="node")
    assert portao.inventario(r) == antes
    assert portao.main(["--raiz", str(r), "--json", str(r / "relatorio.json")]) == 3     # relatório dentro do projeto: recusado
    assert not (r / "relatorio.json").exists()


def test_relatorio_json_traz_veredito_e_etapas(tmp_path):
    res, _, _ = rodar(tmp_path, pytest=(1, "1 failed, 295 passed"))
    d = json.loads(portao.relatorio_json(res))
    assert d["veredito"] == "REPROVADO" and d["codigo"] == 1
    assert any(e["nome"] == "Python (pytest)" and e["status"] == "REPROVADO" for e in d["etapas"])


def test_executar_captura_o_codigo_de_saida_real_neste_sistema(tmp_path):
    rc, out = portao.executar([sys.executable, "-c", "import sys; print('olá, Rigora'); sys.exit(3)"], tmp_path)
    assert rc == 3 and "olá, Rigora" in out
    rc, out = portao.executar(["comando-que-nao-existe-rigora-xyz"], tmp_path)
    assert rc == 127 and "não encontrado" in out


def test_analisadores_com_as_saidas_reais():
    assert portao.analisar_js(0, "  ok - a\n\n88 testes aprovados, 0 reprovados.")[0] == "APROVADO"
    assert portao.analisar_js(1, "  FALHOU - B7-01 x\n54 testes aprovados, 1 reprovado.")[0] == "REPROVADO"
    assert portao.analisar_pytest(0, "296 passed in 57.42s") == ("APROVADO", "296 aprovados, 0 reprovados, 0 erros, 0 ignorados")
    assert portao.analisar_pytest(1, "1 error in 0.5s")[0] == "REPROVADO"
    assert portao.analisar_verificador(0, "...\nResultado: APROVADO\n")[0] == "APROVADO"
    assert portao.analisar_autoteste(0, "18 de 18 autotestes aprovados, 0 reprovados.") == ("APROVADO", "18 de 18")
