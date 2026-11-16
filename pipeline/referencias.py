"""Referências versionadas do verificador B5 (tests/fixtures/referencias_b5/registro.json).

Uso:
    python -m pipeline.referencias listar
    python -m pipeline.referencias propor --gerado ARQ_GERADO.js
    python -m pipeline.referencias aprovar ID --confirmar SHA256_12+ --responsavel NOME --justificativa TEXTO

A referência histórica (tests/fixtures/snapshots_manual_20261114.js) nunca é alterada nem removida.
Uma referência nova é sempre um ARQUIVO NOVO, copiado de um snapshot gerado pelo pipeline, com hash,
execuções, datas de coleta, hash do bruto e a lista de diferenças contra a referência ativa (pelo
próprio verificador B5). Ela entra como "proposta" e só passa a valer com `aprovar`, que exige o hash
do arquivo, o nome do responsável e a justificativa. Nada é aprovado automaticamente.

Quem usa a referência ativa: python -m pipeline.publicar e tools/portao.py (sem --referencia).
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
from datetime import datetime, timezone
from pathlib import Path

from . import config

HISTORICA = "tests/fixtures/snapshots_manual_20261114.js"
PASTA = "tests/fixtures/referencias_b5"
REGISTRO = f"{PASTA}/registro.json"


class ReferenciaError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def registro_padrao() -> dict:
    return {"versao": 1, "ativa": "historica-20261114", "referencias": [{
        "id": "historica-20261114", "arquivo": HISTORICA,
        "sha256": "410fa9cb428b440f4eb71f2de29998cb72922595d8ec04e4657cffcc2d61b0d5",
        "status": "aprovada", "criadaEm": "2026-11-14", "fonte": "cópia manual de 14/11/2026 (IBGE/SIDRA e INMET)",
        "aprovadaEm": "2026-11-15", "responsavel": "aprovação da B5", "justificativa": "referência histórica do T0 e da B5",
        "diferencas": []}]}


def carregar(raiz: Path = config.ROOT) -> dict:
    p = Path(raiz) / REGISTRO
    if not p.exists():
        return registro_padrao()
    try:
        reg = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReferenciaError(f"registro de referências ilegível: {exc}")
    if reg.get("versao") != 1 or not isinstance(reg.get("referencias"), list):
        raise ReferenciaError("registro de referências em formato desconhecido")
    return reg


def _salvar(raiz: Path, reg: dict) -> None:
    p = Path(raiz) / REGISTRO
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def _entrada(reg: dict, rid: str) -> dict:
    for e in reg["referencias"]:
        if e["id"] == rid:
            return e
    raise ReferenciaError(f"referência {rid!r} não existe no registro")


def ativa(raiz: Path = config.ROOT) -> Path:
    """Arquivo da referência ativa, com o hash conferido contra o registro."""
    reg = carregar(raiz)
    e = _entrada(reg, reg.get("ativa"))
    if e.get("status") != "aprovada":
        raise ReferenciaError(f"a referência ativa {e['id']} não está aprovada")
    arq = Path(raiz) / e["arquivo"]
    if not arq.is_file() or _sha(arq) != e["sha256"]:
        raise ReferenciaError(f"referência ativa {e['id']} ausente ou com hash diferente do registrado ({arq})")
    return arq


def _diferencas(raiz: Path, gerado: Path, referencia: Path) -> dict:
    node = shutil.which("node")
    if not node:
        raise ReferenciaError("Node.js é necessário para comparar com o verificador B5")
    with tempfile.TemporaryDirectory() as t:
        j = Path(t) / "v.json"
        r = subprocess.run([node, str(Path(raiz) / "tests" / "check_exported_snapshot.js"), str(gerado), str(referencia), "--json", str(j)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, TZ="America/Sao_Paulo"))
        if not j.exists():
            raise ReferenciaError(f"verificador B5 não rodou: {(r.stdout + r.stderr)[-300:]}")
        v = json.loads(j.read_text(encoding="utf-8"))
    problemas = [x for x in v["reprovacoes"] if not x.startswith("diferença de dado")]
    return {"diferencasDeDados": v["diferencasDeDados"], "diferencasEsperadas": v["diferencasEsperadas"], "outrosProblemas": problemas}


def propor(gerado: Path, raiz: Path = config.ROOT, agora: datetime | None = None) -> dict:
    """Registra um snapshot gerado como referência PROPOSTA (não ativa). Recusa arquivo que não serviria de referência."""
    from .export_snapshots import ExportError, parse_snapshots_js, validate_contract
    raiz, gerado = Path(raiz), Path(gerado)
    agora = agora or datetime.now(timezone.utc)
    try:
        snap = parse_snapshots_js(gerado.read_text(encoding="utf-8"))
    except (OSError, ValueError, ExportError) as exc:
        raise ReferenciaError(f"snapshot ilegível: {exc}")
    if validate_contract(snap):
        raise ReferenciaError("snapshot fora do contrato: " + "; ".join(validate_contract(snap)[:3]))
    ex = snap["exportacao"]
    if (ex.get("sidra") or {}).get("modo") != "sqlite":
        raise ReferenciaError("a seção sidra precisa vir do banco (modo sqlite)")
    if snap.get("inmet") is None:
        raise ReferenciaError("a referência precisa ter previsão do INMET (o verificador deriva dela os cenários da M1); "
                              "gere o snapshot logo após uma coleta do INMET")
    reg = carregar(raiz)
    dif = _diferencas(raiz, gerado, ativa(raiz))
    if dif["outrosProblemas"]:
        raise ReferenciaError("o verificador B5 aponta problemas além das diferenças de dados: " + "; ".join(dif["outrosProblemas"][:3]))
    es = ex["sidra"]
    rid = f"ref-{agora.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-exec{es.get('execucaoId')}"
    destino = raiz / PASTA / f"{rid}.js"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(gerado, "rb") as fi, open(destino, "xb") as fo:      # arquivo novo; nunca sobrescreve
        shutil.copyfileobj(fi, fo)
    entrada = {"id": rid, "arquivo": destino.relative_to(raiz).as_posix(), "sha256": _sha(destino), "status": "proposta",
               "criadaEm": agora.isoformat(timespec="seconds"), "fonte": es.get("fonte"),
               "execucaoSidra": es.get("execucaoId"), "dataColetaSidra": es.get("dataColeta"), "hashBrutoSidra": es.get("hashSha256"),
               "execucaoInmet": (ex.get("inmet") or {}).get("execucaoId"), "dataColetaInmet": (ex.get("inmet") or {}).get("dataColeta"),
               "comparadaCom": reg["ativa"], "diferencas": dif["diferencasDeDados"], "diferencasEsperadas": dif["diferencasEsperadas"]}
    reg["referencias"].append(entrada)
    _salvar(raiz, reg)
    return entrada


def aprovar(rid: str, confirmar: str, responsavel: str, justificativa: str, raiz: Path = config.ROOT,
            agora: datetime | None = None) -> dict:
    """Promove uma referência proposta a ativa. A anterior continua no registro e no disco."""
    if not re.fullmatch(r"[0-9a-fA-F]{12,64}", confirmar or ""):
        raise ReferenciaError("--confirmar: informe 12 a 64 caracteres do SHA-256 do arquivo proposto")
    if not (responsavel or "").strip() or not (justificativa or "").strip():
        raise ReferenciaError("responsável e justificativa são obrigatórios")
    reg = carregar(raiz)
    e = _entrada(reg, rid)
    if e["status"] != "proposta":
        raise ReferenciaError(f"{rid} não está proposta (status {e['status']})")
    arq = Path(raiz) / e["arquivo"]
    if not arq.is_file() or _sha(arq) != e["sha256"]:
        raise ReferenciaError(f"arquivo de {rid} ausente ou alterado desde a proposta")
    if not e["sha256"].startswith(confirmar.lower()):
        raise ReferenciaError("o hash confirmado não é o do arquivo proposto")
    agora = agora or datetime.now(timezone.utc)
    e.update(status="aprovada", aprovadaEm=agora.isoformat(timespec="seconds"), responsavel=responsavel.strip(),
             justificativa=justificativa.strip(), substituiu=reg["ativa"])
    reg["ativa"] = rid
    _salvar(raiz, reg)
    return e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.referencias", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    p = sub.add_parser("propor")
    p.add_argument("--gerado", type=Path, required=True)
    a_ = sub.add_parser("aprovar")
    a_.add_argument("id")
    a_.add_argument("--confirmar", required=True)
    a_.add_argument("--responsavel", required=True)
    a_.add_argument("--justificativa", required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 3 if e.code else 0
    try:
        if a.cmd == "listar":
            reg = carregar()
            for e in reg["referencias"]:
                marca = "ATIVA " if e["id"] == reg["ativa"] else "      "
                print(f"{marca}{e['id']}  {e['status']:<9} {e['sha256'][:12]}  {e['arquivo']}  diferenças: {len(e.get('diferencas', []))}")
        elif a.cmd == "propor":
            e = propor(a.gerado)
            print(f"referência PROPOSTA (não ativa): {e['id']} · sha256 {e['sha256'][:12]} · {len(e['diferencas'])} diferença(s) de dados")
            for d in e["diferencas"][:50]:
                print(f"  {d['onde']}: referência {json.dumps(d['referencia'])} → gerado {json.dumps(d['gerado'])}")
            print(f"Para aprovar: python -m pipeline.referencias aprovar {e['id']} --confirmar {e['sha256'][:12]} "
                  "--responsavel \"NOME\" --justificativa \"MOTIVO\"")
        else:
            e = aprovar(a.id, a.confirmar, a.responsavel, a.justificativa)
            print(f"referência ATIVA: {e['id']} (substituiu {e['substituiu']}, que continua no registro)")
    except ReferenciaError as exc:
        print(f"falha: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
