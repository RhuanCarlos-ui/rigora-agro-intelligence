"""Backup integral e verificado da pasta data/ (banco, brutos, processados e relatórios).

Uso:
    python -m pipeline.backup_dados criar [--backups PASTA]         # padrão: ../rigora_backups/dados (fora do projeto)
    python -m pipeline.backup_dados verificar PASTA_DO_BACKUP
    python -m pipeline.backup_dados restaurar PASTA_DO_BACKUP --destino PASTA_NOVA

Cada backup é uma pasta nova com `data/` (cópia byte a byte, gravada no disco e conferida) e
`MANIFESTO.json` / `MANIFESTO.sha256` (caminho, tamanho e SHA-256 de cada arquivo). O backup só é
dado como válido se cada cópia conferir com o original e o original não tiver mudado durante a
cópia. O banco não pode ter arquivo -journal, -wal ou -shm (transação pendente ou banco em uso).

`restaurar` nunca sobrescreve nada: recupera o backup numa pasta NOVA, confere todos os hashes, e a
troca pela pasta data/ do projeto é feita à mão (procedimento no pipeline/README.md).

Códigos de saída: 0 ok; 1 falha ou backup inválido; 3 uso inválido.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config

PENDENTES = ("-journal", "-wal", "-shm")


class BackupError(RuntimeError):
    pass


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def inventario(pasta: Path) -> dict[str, tuple[int, str]]:
    return {p.relative_to(pasta).as_posix(): (p.stat().st_size, sha256(p))
            for p in sorted(Path(pasta).rglob("*")) if p.is_file() and not p.is_symlink()}


def _copiar(origem: Path, destino: Path, esperado: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(origem, "rb") as fi, open(destino, "xb") as fo:     # "x": nunca sobrescreve
        shutil.copyfileobj(fi, fo, 1 << 20)
        fo.flush()
        os.fsync(fo.fileno())
    if sha256(destino) != esperado:
        raise BackupError(f"cópia de {origem.name} com hash divergente")


def banco_em_uso(data_dir: Path, banco: str = "rigora.db") -> list[str]:
    return [banco + s for s in PENDENTES if (Path(data_dir) / (banco + s)).exists()]


def criar(data_dir: Path, base: Path, rotulo: str = "dados", agora: datetime | None = None) -> Path:
    """Cria e verifica o backup. Lança BackupError (e não deixa o backup como válido) em qualquer problema."""
    data_dir, base = Path(data_dir).resolve(), Path(base).resolve()
    if base == data_dir or data_dir in base.parents:
        raise BackupError("a pasta de backup não pode ficar dentro de data/")
    if not data_dir.is_dir():
        raise BackupError(f"pasta de dados não encontrada: {data_dir}")
    pend = banco_em_uso(data_dir)
    if pend:
        raise BackupError(f"o banco tem {', '.join(pend)}: transação pendente ou banco em uso; backup não feito")
    agora = agora or datetime.now(timezone.utc)
    base.mkdir(parents=True, exist_ok=True)
    nome = f"{agora.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}_{rotulo}"
    for n in range(1, 1000):
        pasta = base / (nome if n == 1 else f"{nome}_{n}")
        try:
            pasta.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise BackupError(f"não foi possível criar uma pasta nova em {base}")
    antes = inventario(data_dir)
    for rel, (_, h) in antes.items():
        _copiar(data_dir / rel, pasta / "data" / rel, h)
    if inventario(data_dir) != antes:
        raise BackupError("os dados mudaram durante o backup; repita com o pipeline parado")
    manifesto = {"criadoEm": agora.isoformat(timespec="seconds"), "origem": str(data_dir), "rotulo": rotulo,
                 "arquivos": [{"caminho": f"data/{rel}", "bytes": b, "sha256": h} for rel, (b, h) in antes.items()],
                 "total": len(antes), "bytesTotal": sum(b for b, _ in antes.values())}
    (pasta / "MANIFESTO.json").write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (pasta / "MANIFESTO.sha256").write_text("".join(f"{h}  data/{rel}\n" for rel, (_, h) in antes.items()),
                                             encoding="utf-8", newline="\n")
    problemas = verificar(pasta)
    if problemas:
        raise BackupError("backup criado mas não confere: " + "; ".join(problemas[:5]))
    return pasta


def verificar(pasta: Path) -> list[str]:
    """Confere cada arquivo do manifesto (existe, tamanho e SHA-256). Lista vazia = backup íntegro."""
    pasta = Path(pasta)
    try:
        m = json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifesto ilegível: {exc}"]
    p = []
    if m.get("total") != len(m.get("arquivos", [])) or not m.get("arquivos"):
        p.append("manifesto incompleto")
    for a in m.get("arquivos", []):
        f = pasta / a["caminho"]
        if not f.is_file():
            p.append(f"ausente: {a['caminho']}")
        elif f.stat().st_size != a["bytes"] or sha256(f) != a["sha256"]:
            p.append(f"diferente do manifesto: {a['caminho']}")
    extras = set(inventario(pasta / "data")) - {a["caminho"][5:] for a in m.get("arquivos", [])} if (pasta / "data").is_dir() else set()
    p += [f"arquivo fora do manifesto: data/{e}" for e in sorted(extras)]
    return p


def restaurar(pasta: Path, destino: Path) -> int:
    """Recupera o backup numa pasta NOVA (nunca sobrescreve) e confere todos os hashes. Devolve o número de arquivos."""
    pasta, destino = Path(pasta), Path(destino)
    problemas = verificar(pasta)
    if problemas:
        raise BackupError("backup inválido; nada foi restaurado: " + "; ".join(problemas[:5]))
    if destino.exists() and any(destino.iterdir()):
        raise BackupError(f"o destino precisa ser uma pasta nova ou vazia: {destino}")
    m = json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))
    for a in m["arquivos"]:
        _copiar(pasta / a["caminho"], destino / a["caminho"][5:], a["sha256"])
    esperado = {a["caminho"][5:]: (a["bytes"], a["sha256"]) for a in m["arquivos"]}
    if inventario(destino) != esperado:
        raise BackupError("a pasta restaurada não confere com o manifesto")
    return len(esperado)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.backup_dados", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("criar")
    c.add_argument("--backups", type=Path, default=config.ROOT.parent / "rigora_backups" / "dados")
    v = sub.add_parser("verificar")
    v.add_argument("pasta", type=Path)
    r = sub.add_parser("restaurar")
    r.add_argument("pasta", type=Path)
    r.add_argument("--destino", type=Path, required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 3 if e.code else 0
    raiz = config.ROOT.resolve()
    try:
        if a.cmd == "criar":
            alvo = a.backups.resolve()
            if alvo == raiz or raiz in alvo.parents:
                print("erro de uso: --backups precisa ficar fora do projeto", file=sys.stderr)
                return 3
            pasta = criar(config.DATA_DIR, alvo)
            m = json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))
            print(f"backup íntegro: {pasta} ({m['total']} arquivos, {m['bytesTotal']} bytes)")
        elif a.cmd == "verificar":
            p = verificar(a.pasta)
            print("backup íntegro" if not p else "BACKUP INVÁLIDO:\n  " + "\n  ".join(p))
            return 0 if not p else 1
        else:
            d = a.destino.resolve()
            if d == raiz or raiz in d.parents:
                print("erro de uso: restaure numa pasta fora do projeto e troque a pasta data/ à mão", file=sys.stderr)
                return 3
            n = restaurar(a.pasta, d)
            print(f"{n} arquivos restaurados e conferidos em {d}")
    except BackupError as exc:
        print(f"falha: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
