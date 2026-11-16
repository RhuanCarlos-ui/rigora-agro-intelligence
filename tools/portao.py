#!/usr/bin/env python3
"""Portão de qualidade do Rigora: valida o projeto inteiro com um comando (Windows e Linux).

Uso, na raiz do projeto:
    python tools/portao.py                                              # snapshot gerado pela própria simulação
    python tools/portao.py --gerado CAMINHO/snapshots_gerado.js         # verificador sobre um arquivo escolhido
    python tools/portao.py --json C:/temp/portao.json                   # relatório em JSON, fora do projeto

Etapas, nesta ordem:
  1. Inventário: SHA-256 de todos os arquivos do projeto (dados protegidos marcados).
  2. Ambiente: Python, pytest, Node e se o Node respeita o fuso pedido (TZ).
  3. JavaScript: node tests/metrics.test.js em America/Sao_Paulo, UTC e Asia/Tokyo.
  4. Python: a suíte completa com o pytest real (python -m pytest). Sem pytest, a etapa não roda
     e o resultado é INCOMPLETO: nada é simulado no lugar.
  5. Suítes obrigatórias (Bloco 3): cada arquivo de OBRIGATORIOS precisa existir e ter testes
     aprovados na etapa 4 (arquivo apagado, renomeado ou sem testes reprova).
  6. Simulação da publicação (Bloco 3): python -m pipeline.publicar, sem coleta e sem publicação, numa
     pasta temporária fora do projeto. Sem Node, INCOMPLETO.
  7. Verificador do snapshot exportado (B5) e seus autotestes, nos três fusos, sobre o --gerado ou,
     sem ele, sobre o snapshot gerado na etapa 6. Sem nenhum dos dois, INCOMPLETO.
  8. Inventário de novo: qualquer arquivo criado, alterado ou removido reprova.

Resultado: APROVADO (código 0) só se todas as etapas rodaram e passaram; REPROVADO (1) se alguma
falhou; INCOMPLETO (2) se alguma não pôde rodar. Erro de uso: 3.

O portão não grava nada no projeto: o pytest roda sem cache (-p no:cacheprovider) e sem .pyc
(PYTHONDONTWRITEBYTECODE=1); não há coleta, exportação, pipeline nem acesso ao banco além da leitura
dos bytes para o hash. Pode ser executado quantas vezes for preciso.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUSOS = ("America/Sao_Paulo", "UTC", "Asia/Tokyo")
ALIASES_UTC = {"UTC", "Etc/UTC", "Etc/Universal", "Universal", "Zulu", "Etc/Zulu"}
# Dados que nenhuma validação pode tocar: banco, backups, brutos, processados, relatórios, snapshot e fixtures.
PROTEGIDOS = ("data/", "js/snapshots.js", "tests/fixtures/")
REFERENCIA_PADRAO = "tests/fixtures/snapshots_manual_20261114.js"
TIMEOUT_S = {"js": 300, "pytest": 1800, "verificador": 300, "versao": 60, "publicar": 600}
# Suítes que precisam ter rodado e passado (Bloco 3): exportador, portão e publicação protegida.
OBRIGATORIOS = ("tests/test_pipeline_export.py", "tests/test_portao.py", "tests/test_portao_bloco3.py",
                "tests/test_estabilidade_1_0.py", "tests/test_publicar.py")
REGISTRO_REFERENCIAS = "tests/fixtures/referencias_b5/registro.json"


def referencia_ativa(raiz: Path) -> Path:
    """Referência B5 ativa (registro versionado); sem registro, a fixture histórica. Hash conferido."""
    reg_path = raiz / REGISTRO_REFERENCIAS
    if not reg_path.exists():
        return raiz / REFERENCIA_PADRAO
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    e = next((x for x in reg.get("referencias", []) if x.get("id") == reg.get("ativa")), None)
    if e is None or e.get("status") != "aprovada":
        raise ValueError("registro de referências sem referência ativa aprovada")
    arq = raiz / e["arquivo"]
    if not arq.is_file() or hashlib.sha256(arq.read_bytes()).hexdigest() != e["sha256"]:
        raise ValueError(f"referência ativa {e['id']} ausente ou com hash diferente do registrado")
    return arq

APROVADO, REPROVADO, NAO_EXECUTADO = "APROVADO", "REPROVADO", "NÃO EXECUTADO"


@dataclass
class Etapa:
    nome: str
    status: str
    detalhe: str = ""
    duracao_s: float = 0.0
    saida: str = ""                       # final da saída, para diagnóstico


@dataclass
class Resultado:
    etapas: list = field(default_factory=list)
    alterados: list = field(default_factory=list)
    protegidos_alterados: list = field(default_factory=list)

    @property
    def veredito(self) -> str:
        st = [e.status for e in self.etapas]
        if REPROVADO in st:
            return REPROVADO
        if NAO_EXECUTADO in st or not st:
            return "INCOMPLETO"
        return APROVADO

    @property
    def codigo(self) -> int:
        return {APROVADO: 0, REPROVADO: 1}.get(self.veredito, 2)


# --------------------------------------------------------------------------- inventário
def inventario(raiz: Path) -> dict[str, str]:
    """SHA-256 de cada arquivo sob a raiz (caminho relativo com '/')."""
    hashes = {}
    for p in sorted(raiz.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for bloco in iter(lambda: f.read(1 << 20), b""):
                    h.update(bloco)
            hashes[p.relative_to(raiz).as_posix()] = h.hexdigest()
    return hashes


def protegido(caminho: str) -> bool:
    return any(caminho == p or (p.endswith("/") and caminho.startswith(p)) for p in PROTEGIDOS)


def comparar(antes: dict[str, str], depois: dict[str, str]) -> list[tuple[str, str]]:
    """Lista (caminho, 'criado' | 'removido' | 'alterado')."""
    mud = []
    for c in sorted(set(antes) | set(depois)):
        if c not in antes:
            mud.append((c, "criado"))
        elif c not in depois:
            mud.append((c, "removido"))
        elif antes[c] != depois[c]:
            mud.append((c, "alterado"))
    return mud


# --------------------------------------------------------------------------- execução
def executar(cmd: list[str], raiz: Path, env_extra: dict | None = None, timeout: int = 600) -> tuple[int, str]:
    """Roda um comando e devolve (código de saída, stdout+stderr). Nunca esconde erro de ambiente."""
    env = dict(os.environ)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(env_extra or {})
    try:
        p = subprocess.run(cmd, cwd=str(raiz), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError as e:
        return 127, f"comando não encontrado: {cmd[0]} ({e})"
    except subprocess.TimeoutExpired as e:
        return 124, f"tempo limite de {timeout} s excedido: {' '.join(cmd)}\n{(e.stdout or '')}"


def _final(texto: str, linhas: int = 25) -> str:
    return "\n".join(texto.strip().splitlines()[-linhas:])


# --------------------------------------------------------------------------- leitura das saídas
def analisar_js(rc: int, saida: str) -> tuple[str, str]:
    m = re.search(r"(\d+) testes aprovados, (\d+) reprovados?\.", saida)
    if not m:
        return REPROVADO, f"saída sem o resumo da suíte (código {rc})"
    ok, falhas = int(m.group(1)), int(m.group(2))
    if rc != 0 or falhas or ok == 0:
        falhou = re.findall(r"FALHOU - (.+)", saida)
        return REPROVADO, f"{ok} aprovados, {falhas} reprovados (código {rc})" + (f"; falhou: {falhou[0]}" if falhou else "")
    return APROVADO, f"{ok} aprovados, 0 reprovados"


def analisar_pytest(rc: int, saida: str) -> tuple[str, str]:
    resumo = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)", saida)}
    passed, failed = resumo.get("passed", 0), resumo.get("failed", 0)
    errors = resumo.get("error", 0) + resumo.get("errors", 0)
    skipped = resumo.get("skipped", 0)
    txt = f"{passed} aprovados, {failed} reprovados, {errors} erros, {skipped} ignorados"
    if rc == 5 or passed == 0 and not failed and not errors:
        return REPROVADO, f"nenhum teste coletado (código {rc})"
    if rc != 0 or failed or errors:
        return REPROVADO, f"{txt} (código {rc})"
    if skipped:
        return NAO_EXECUTADO, f"{txt}: testes ignorados não contam como aprovados"
    return APROVADO, txt


def analisar_verificador(rc: int, saida: str) -> tuple[str, str]:
    if rc == 0 and "Resultado: APROVADO" in saida:
        return APROVADO, "APROVADO"
    m = re.search(r"Resultado: (.+)", saida)
    return REPROVADO, f"{m.group(1) if m else 'sem resultado'} (código {rc})"


def analisar_autoteste(rc: int, saida: str) -> tuple[str, str]:
    m = re.search(r"(\d+) de (\d+) autotestes aprovados", saida)
    if rc == 0 and m and m.group(1) == m.group(2) and int(m.group(2)) > 0:
        return APROVADO, f"{m.group(1)} de {m.group(2)}"
    return REPROVADO, (f"{m.group(1)} de {m.group(2)}" if m else "sem resumo") + f" (código {rc})"


def analisar_publicar(rc: int, saida: str) -> tuple[str, str]:
    m = re.search(r"Resultado: (.+)", saida)
    resultado = m.group(1).strip() if m else "sem resultado"
    if rc == 0 and resultado.startswith("SIMULAÇÃO APROVADA"):
        return APROVADO, "SIMULAÇÃO APROVADA (nada coletado, nada publicado)"
    if rc == 2:
        return NAO_EXECUTADO, f"{resultado} (código {rc})"
    return REPROVADO, f"{resultado} (código {rc})"


def analisar_obrigatorios(raiz: Path, saida: str, arquivos) -> tuple[str, str]:
    """Cada arquivo obrigatório existe e tem testes aprovados (linhas PASSED do pytest -rA), sem falha nem pulo."""
    faltas, contagem = [], []
    for a in arquivos:
        if not (raiz / a).is_file():
            faltas.append(f"{a} ausente")
            continue
        ok = len(re.findall(rf"^PASSED {re.escape(a)}::", saida, re.M))
        ruins = len(re.findall(rf"^(?:FAILED|ERROR|XFAIL|XPASS) {re.escape(a)}|^SKIPPED \[\d+\] {re.escape(a)}", saida, re.M))
        if not ok:
            faltas.append(f"{a}: nenhum teste aprovado")
        if ruins:
            faltas.append(f"{a}: {ruins} teste(s) não aprovado(s)")
        contagem.append(f"{Path(a).name} {ok}")
    if faltas:
        return REPROVADO, "; ".join(faltas)
    return APROVADO, " · ".join(contagem)


def fuso_do_node(node: str, fuso: str, raiz: Path, run=executar) -> tuple[bool, str]:
    rc, out = run([node, "-e", "process.stdout.write(Intl.DateTimeFormat().resolvedOptions().timeZone)"],
                  raiz, {"TZ": fuso}, TIMEOUT_S["versao"])
    visto = out.strip()
    ok = rc == 0 and (visto == fuso or (fuso == "UTC" and visto in ALIASES_UTC))
    return ok, visto


# --------------------------------------------------------------------------- portão
def rodar(raiz: Path, gerado: Path | None, referencia: Path, run=executar, python: str = sys.executable,
          node: str | None = None, avisar=lambda msg: None, simular: bool = False, obrigatorios=()) -> Resultado:
    """`simular` e `obrigatorios` (Bloco 3) são sempre ligados pela linha de comando (main)."""
    res = Resultado()
    t0 = time.time()
    antes = inventario(raiz)
    res.etapas.append(Etapa("Inventário inicial", APROVADO,
                            f"{len(antes)} arquivos; {sum(protegido(c) for c in antes)} protegidos", time.time() - t0))
    avisar(res.etapas[-1])

    # Ambiente
    node = node if node is not None else shutil.which("node")
    rc, out = run([python, "-m", "pytest", "--version"], raiz, None, TIMEOUT_S["versao"])
    tem_pytest = rc == 0 and "pytest" in out
    versao_py = f"Python {sys.version.split()[0]}" if python == sys.executable else python
    amb = [versao_py, out.strip().splitlines()[-1] if tem_pytest and out.strip() else "pytest AUSENTE"]
    if node:
        rcn, outn = run([node, "--version"], raiz, None, TIMEOUT_S["versao"])
        amb.append(f"Node {outn.strip()}" if rcn == 0 else f"Node com erro: {_final(outn, 3)}")
    else:
        amb.append("Node AUSENTE")
    res.etapas.append(Etapa("Ambiente", APROVADO if tem_pytest and node else NAO_EXECUTADO, " · ".join(amb)))
    avisar(res.etapas[-1])

    # JavaScript nos três fusos
    for fuso in FUSOS:
        nome = f"JavaScript ({fuso})"
        if not node:
            res.etapas.append(Etapa(nome, NAO_EXECUTADO, "Node não encontrado no PATH")); avisar(res.etapas[-1]); continue
        ok, visto = fuso_do_node(node, fuso, raiz, run)
        if not ok:
            res.etapas.append(Etapa(nome, REPROVADO, f"o Node não respeitou TZ={fuso} (resolveu '{visto}'): a validação de fuso não seria real"))
            avisar(res.etapas[-1]); continue
        t = time.time()
        rc, out = run([node, "tests/metrics.test.js"], raiz, {"TZ": fuso}, TIMEOUT_S["js"])
        st, det = analisar_js(rc, out)
        res.etapas.append(Etapa(nome, st, det, time.time() - t, _final(out) if st != APROVADO else ""))
        avisar(res.etapas[-1])

    # Python, com o pytest real
    saida_pytest = None
    if not tem_pytest:
        res.etapas.append(Etapa("Python (pytest)", NAO_EXECUTADO,
                                f"pytest não disponível para {python}: instale com 'python -m pip install -r requirements-dev.txt'. "
                                "Nenhum substituto foi usado.", 0, _final(out, 5)))
    else:
        t = time.time()
        rc, out = run([python, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider"], raiz, None, TIMEOUT_S["pytest"])
        saida_pytest = out
        st, det = analisar_pytest(rc, out)
        res.etapas.append(Etapa("Python (pytest)", st, det, time.time() - t, _final(out) if st != APROVADO else ""))
    avisar(res.etapas[-1])

    # Suítes obrigatórias (Bloco 3)
    if obrigatorios:
        if saida_pytest is None:
            res.etapas.append(Etapa("Suítes obrigatórias", NAO_EXECUTADO, "depende da suíte Python, que não rodou"))
        else:
            st, det = analisar_obrigatorios(raiz, saida_pytest, obrigatorios)
            res.etapas.append(Etapa("Suítes obrigatórias", st, det))
        avisar(res.etapas[-1])

    # Simulação da publicação (Bloco 3): nada é coletado nem publicado; arquivos só numa pasta temporária
    pasta_sim = None
    if simular:
        pasta_sim = Path(tempfile.mkdtemp(prefix="rigora_portao_"))
        trabalho = pasta_sim / "publicar"
        t = time.time()
        rc, out = run([python, "-m", "pipeline.publicar", "--trabalho", str(trabalho)], raiz, None, TIMEOUT_S["publicar"])
        st, det = analisar_publicar(rc, out)
        gerado_sim = trabalho / "gerado" / "snapshots.js"
        if st == APROVADO and gerado is None:
            if gerado_sim.is_file():
                gerado, det = gerado_sim, det + "; o verificador abaixo usa o snapshot gerado nesta simulação"
            else:
                st, det = REPROVADO, "simulação aprovada, mas o snapshot gerado não foi encontrado"
        res.etapas.append(Etapa("Simulação da publicação", st, det, time.time() - t, _final(out) if st != APROVADO else ""))
        avisar(res.etapas[-1])

    # Verificador do snapshot exportado
    if gerado is None:
        res.etapas.append(Etapa("Verificador do snapshot", NAO_EXECUTADO,
                                "informe --gerado com um snapshot gerado pelo exportador (fora do projeto)"))
        avisar(res.etapas[-1])
    elif not gerado.is_file() or not referencia.is_file():
        res.etapas.append(Etapa("Verificador do snapshot", REPROVADO,
                                f"arquivo não encontrado: {gerado if not gerado.is_file() else referencia}"))
        avisar(res.etapas[-1])
    elif not node:
        res.etapas.append(Etapa("Verificador do snapshot", NAO_EXECUTADO, "Node não encontrado no PATH")); avisar(res.etapas[-1])
    else:
        for fuso in FUSOS:
            ok, visto = fuso_do_node(node, fuso, raiz, run)
            if not ok:
                res.etapas.append(Etapa(f"Verificador ({fuso})", REPROVADO, f"o Node não respeitou TZ={fuso} (resolveu '{visto}')"))
                avisar(res.etapas[-1]); continue
            for nome, extra, analisar in (("Verificador", [], analisar_verificador), ("Autoteste do verificador", ["--autoteste"], analisar_autoteste)):
                t = time.time()
                rc, out = run([node, "tests/check_exported_snapshot.js", *extra, str(gerado), str(referencia)],
                              raiz, {"TZ": fuso}, TIMEOUT_S["verificador"])
                st, det = analisar(rc, out)
                res.etapas.append(Etapa(f"{nome} ({fuso})", st, det, time.time() - t, _final(out) if st != APROVADO else ""))
                avisar(res.etapas[-1])
    if pasta_sim is not None:
        shutil.rmtree(pasta_sim, ignore_errors=True)           # pasta temporária do próprio portão, fora do projeto

    # Inventário final
    depois = inventario(raiz)
    mud = comparar(antes, depois)
    res.alterados = [f"{c} ({tipo})" for c, tipo in mud]
    res.protegidos_alterados = [f"{c} ({tipo})" for c, tipo in mud if protegido(c)]
    if mud:
        det = (f"{len(mud)} arquivo(s) mudaram durante o portão"
               + (f", {len(res.protegidos_alterados)} PROTEGIDO(S)" if res.protegidos_alterados else "") + ": "
               + "; ".join(res.alterados[:10]) + (" …" if len(mud) > 10 else ""))
        res.etapas.append(Etapa("Inventário final", REPROVADO, det))
    else:
        res.etapas.append(Etapa("Inventário final", APROVADO, f"{len(depois)} arquivos idênticos ao início, inclusive os protegidos"))
    avisar(res.etapas[-1])
    return res


# --------------------------------------------------------------------------- relatório
def linha(e: Etapa) -> str:
    marca = {APROVADO: "ok   ", REPROVADO: "FALHA", NAO_EXECUTADO: "N/EXE"}[e.status]
    dur = f" [{e.duracao_s:.0f} s]" if e.duracao_s >= 1 else ""
    return f"  {marca} {e.nome}: {e.detalhe}{dur}"


def imprimir(res: Resultado, raiz: Path) -> None:
    print(f"\nResumo do portão de qualidade · {raiz}")
    for e in res.etapas:
        print(linha(e))
        if e.saida:
            print("        " + e.saida.replace("\n", "\n        "))
    print(f"\nResultado: {res.veredito}"
          + ("" if res.veredito == APROVADO else
             " — " + "; ".join(e.nome for e in res.etapas if e.status != APROVADO)))


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gerado", type=Path, help="snapshot gerado pelo exportador, fora do projeto (padrão: o da simulação)")
    ap.add_argument("--referencia", type=Path, help="cópia de referência (padrão: a referência B5 ativa)")
    ap.add_argument("--json", type=Path, help="grava o relatório em JSON neste arquivo (fora do projeto)")
    ap.add_argument("--raiz", type=Path, default=RAIZ, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    raiz = a.raiz.resolve()
    if a.json is not None:
        alvo = a.json.resolve()
        if alvo == raiz or raiz in alvo.parents:
            print(f"erro: --json precisa apontar para fora do projeto ({alvo})", file=sys.stderr)
            return 3
    try:
        referencia = a.referencia.resolve() if a.referencia else referencia_ativa(raiz)
    except (ValueError, OSError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 3
    print(f"Portão de qualidade do Rigora · {raiz} · referência B5: {referencia.name}", flush=True)
    res = rodar(raiz, a.gerado.resolve() if a.gerado else None, referencia,
                avisar=lambda e: print(linha(e), flush=True), simular=True, obrigatorios=OBRIGATORIOS)
    imprimir(res, raiz)
    if a.json is not None:
        a.json.write_text(relatorio_json(res), encoding="utf-8")
    return res.codigo


def relatorio_json(res: Resultado) -> str:
    return json.dumps({"veredito": res.veredito, "codigo": res.codigo, "etapas": [asdict(e) for e in res.etapas],
                       "alterados": res.alterados, "protegidos_alterados": res.protegidos_alterados},
                      ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
