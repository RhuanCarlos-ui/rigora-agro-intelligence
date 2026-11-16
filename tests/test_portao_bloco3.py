"""Testes do portão de qualidade no Bloco 3: simulação da publicação e suítes obrigatórias.

Mesmo princípio de tests/test_portao.py: o executor é simulado e só pastas temporárias são usadas.
Os testes antigos do portão continuam intactos em tests/test_portao.py.
"""
import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("portao", RAIZ / "tools" / "portao.py")
portao = importlib.util.module_from_spec(_spec)
sys.modules["portao"] = portao
_spec.loader.exec_module(portao)


def projeto(tmp_path):
    r = tmp_path / "proj"
    for d in ("data", "js", "tests/fixtures"):
        (r / d).mkdir(parents=True, exist_ok=True)
    (r / "data" / "rigora.db").write_bytes(b"banco")
    (r / "js" / "snapshots.js").write_text("window.RIGORA_SNAPSHOTS = {};", encoding="utf-8")
    (r / "tests" / "fixtures" / "ref.js").write_text("ref", encoding="utf-8")
    for a in portao.OBRIGATORIOS:
        (r / a).write_text("# teste", encoding="utf-8")
    return r, r / "tests" / "fixtures" / "ref.js"


def saida_pytest(arquivos=portao.OBRIGATORIOS, extra=""):
    linhas = [f"PASSED {a}::test_{i}" for i, a in enumerate(arquivos)]
    return "....\n=== short test summary info ===\n" + "\n".join(linhas) + extra + f"\n{300 + len(arquivos)} passed in 60s"


class Executor:
    """Responde como as suítes reais aprovadas; a simulação grava o snapshot na pasta --trabalho, fora do projeto."""

    def __init__(self, **trocas):
        self.trocas, self.chamadas = trocas, []

    @staticmethod
    def chave(cmd):
        if "pipeline.publicar" in cmd:
            return "publicar"
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
        self.chamadas.append((k, tz, list(cmd)))
        r = self.trocas.get(k)
        if k == "publicar" and r is None:
            trabalho = Path(cmd[cmd.index("--trabalho") + 1])
            (trabalho / "gerado").mkdir(parents=True)
            (trabalho / "gerado" / "snapshots.js").write_text("gerado na simulação", encoding="utf-8")
            return 0, "...\nResultado: SIMULAÇÃO APROVADA — nada foi publicado; js/snapshots.js sem alteração"
        if callable(r):
            return r(cmd)
        if r is not None:
            return r
        return {"pytest_versao": (0, "pytest 9.2.0"), "node_versao": (0, "v22.0.0"), "fuso": (0, tz),
                "js": (0, "111 testes aprovados, 0 reprovados."), "pytest": (0, saida_pytest()),
                "verificador": (0, "Resultado: APROVADO"), "autoteste": (0, "18 de 18 autotestes aprovados, 0 reprovados.")}[k]


def rodar(tmp_path, gerado=None, **trocas):
    r, ref = projeto(tmp_path)
    ex = Executor(**trocas)
    res = portao.rodar(r, gerado, ref, run=ex, python="python", node="node", simular=True, obrigatorios=portao.OBRIGATORIOS)
    return res, ex, r


def st(res, nome):
    return next(e.status for e in res.etapas if e.nome.startswith(nome))


def test_sem_gerado_o_verificador_usa_o_snapshot_da_simulacao_nos_tres_fusos(tmp_path):
    res, ex, r = rodar(tmp_path)
    assert res.veredito == "APROVADO", [(e.nome, e.status, e.detalhe) for e in res.etapas]
    assert st(res, "Simulação da publicação") == "APROVADO" and st(res, "Suítes obrigatórias") == "APROVADO"
    usados = [c[2][-2] for c in ex.chamadas if c[0] in ("verificador", "autoteste")]
    assert len(usados) == 6 and all(u.endswith(str(Path("publicar") / "gerado" / "snapshots.js")) for u in usados)
    assert not Path(usados[0]).exists(), "a pasta temporária da simulação é removida"
    assert not any(Path(u).resolve().is_relative_to(r) for u in usados)


def test_com_gerado_explicito_o_verificador_usa_esse_arquivo_e_a_simulacao_roda_igual(tmp_path):
    g = tmp_path / "escolhido.js"
    g.write_text("g", encoding="utf-8")
    res, ex, _ = rodar(tmp_path, gerado=g)
    assert res.veredito == "APROVADO"
    assert {c[2][-2] for c in ex.chamadas if c[0] == "verificador"} == {str(g)}
    assert sum(1 for c in ex.chamadas if c[0] == "publicar") == 1


def test_simulacao_reprovada_reprova_e_o_verificador_nao_roda_sem_snapshot(tmp_path):
    res, ex, _ = rodar(tmp_path, publicar=(1, "Resultado: REPROVADO — 1 problema(s) no snapshot gerado"))
    assert res.veredito == "REPROVADO" and st(res, "Simulação da publicação") == "REPROVADO"
    assert not [c for c in ex.chamadas if c[0] in ("verificador", "autoteste")]


def test_simulacao_incompleta_sem_node_nao_vira_aprovacao(tmp_path):
    res, _, _ = rodar(tmp_path, publicar=(2, "Resultado: INCOMPLETO — Node.js não encontrado"))
    assert res.veredito == "INCOMPLETO" and st(res, "Simulação da publicação") == "NÃO EXECUTADO"


def test_simulacao_aprovada_sem_arquivo_gerado_reprova(tmp_path):
    res, _, _ = rodar(tmp_path, publicar=(0, "Resultado: SIMULAÇÃO APROVADA"))
    assert res.veredito == "REPROVADO" and st(res, "Simulação da publicação") == "REPROVADO"


def test_suite_obrigatoria_ausente_ou_sem_testes_reprova(tmp_path):
    res, _, r = rodar(tmp_path, pytest=(0, saida_pytest(portao.OBRIGATORIOS[:-1])))
    assert res.veredito == "REPROVADO" and "test_publicar.py: nenhum teste aprovado" in next(
        e.detalhe for e in res.etapas if e.nome == "Suítes obrigatórias")
    r2, ref = projeto(tmp_path / "outro")
    (r2 / "tests" / "test_publicar.py").unlink()
    res2 = portao.rodar(r2, None, ref, run=Executor(), python="python", node="node", simular=True, obrigatorios=portao.OBRIGATORIOS)
    assert res2.veredito == "REPROVADO" and "tests/test_publicar.py ausente" in next(
        e.detalhe for e in res2.etapas if e.nome == "Suítes obrigatórias")


def test_suite_obrigatoria_com_falha_ou_teste_pulado_reprova(tmp_path):
    for extra in ("\nFAILED tests/test_publicar.py::test_x - AssertionError", "\nSKIPPED [1] tests/test_publicar.py:10: sem node"):
        res, _, _ = rodar(tmp_path / str(len(extra)), pytest=(0, saida_pytest(extra=extra)))
        assert st(res, "Suítes obrigatórias") == "REPROVADO", extra


def test_sem_pytest_as_suites_obrigatorias_nao_sao_dadas_como_aprovadas(tmp_path):
    res, _, _ = rodar(tmp_path, pytest_versao=(1, "No module named pytest"))
    assert res.veredito == "INCOMPLETO" and st(res, "Suítes obrigatórias") == "NÃO EXECUTADO"


def test_o_pytest_roda_com_resumo_por_teste(tmp_path):
    _, ex, _ = rodar(tmp_path)
    [cmd] = [c[2] for c in ex.chamadas if c[0] == "pytest"]
    assert cmd[1:] == ["-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider"]


def test_simulacao_que_grava_no_projeto_reprova_pelo_inventario(tmp_path):
    r, ref = projeto(tmp_path)

    def grava(cmd):
        (r / "js" / "snapshots.js").write_text("publicado por engano", encoding="utf-8")
        return 0, "Resultado: SIMULAÇÃO APROVADA"
    res = portao.rodar(r, tmp_path / "x.js", ref, run=Executor(publicar=grava), python="python", node="node",
                       simular=True, obrigatorios=portao.OBRIGATORIOS)
    assert res.codigo == 1 and res.protegidos_alterados == ["js/snapshots.js (alterado)"]


def test_linha_de_comando_liga_simulacao_e_suites_obrigatorias(tmp_path, monkeypatch):
    r, _ = projeto(tmp_path)
    visto = {}

    def falso(raiz, gerado, referencia, **kw):
        visto.update(kw, gerado=gerado)
        return portao.Resultado(etapas=[portao.Etapa("x", portao.APROVADO)])
    monkeypatch.setattr(portao, "rodar", falso)
    assert portao.main(["--raiz", str(r)]) == 0
    assert visto["simular"] is True and visto["obrigatorios"] == portao.OBRIGATORIOS and visto["gerado"] is None
    assert set(portao.OBRIGATORIOS) >= {"tests/test_publicar.py", "tests/test_portao_bloco3.py", "tests/test_portao.py"}


def test_analisadores_da_simulacao():
    assert portao.analisar_publicar(0, "x\nResultado: SIMULAÇÃO APROVADA — nada foi publicado")[0] == "APROVADO"
    assert portao.analisar_publicar(0, "Resultado: PUBLICADO — js/snapshots.js substituído")[0] == "REPROVADO"
    assert portao.analisar_publicar(1, "Resultado: REPROVADO")[0] == "REPROVADO"
    assert portao.analisar_publicar(2, "Resultado: INCOMPLETO")[0] == "NÃO EXECUTADO"
    assert portao.analisar_publicar(0, "sem resultado")[0] == "REPROVADO"
