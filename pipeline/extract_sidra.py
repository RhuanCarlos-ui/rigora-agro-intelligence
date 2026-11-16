"""Extração da Produção Agrícola Municipal (IBGE/SIDRA, tabela 1613).

Uso:
    python -m pipeline.extract_sidra            # coleta ao vivo e salva em data/raw/
    python -m pipeline.extract_sidra --dry-run  # só mostra as URLs

Cada coleta grava o JSON exatamente como o IBGE respondeu, com data/hora e hash
SHA-256, para que qualquer número do painel possa ser auditado depois.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .http import SourceError, fetch_json

log = logging.getLogger(__name__)

# Dimensões esperadas na resposta do SIDRA para esta consulta
EXPECTED_DIMENSIONS = {"D1C": "Município", "D2C": "Variável", "D3C": "Ano", "D4C": "Produto"}


@dataclass
class RawCapture:
    """Metadados de uma coleta (vão para a tabela de execuções na etapa 3b)."""

    source: str
    url: str
    collected_at: str
    path: str
    sha256: str
    records: int


def pam_url(
    years: list[int] = config.YEARS,
    uf: str = config.UF_MG,
    product: str = config.PRODUCT_ARABICA,
) -> str:
    """URL da consulta: todos os municípios da UF, 3 variáveis, anos e produto."""
    variables = ",".join(config.VARIABLES)
    period = f"{min(years)}-{max(years)}"
    return (
        f"{config.SIDRA_BASE}/t/{config.SIDRA_TABLE}/n6/in%20n3%20{uf}/v/{variables}"
        f"/p/{period}/c{config.PRODUCT_CLASSIFICATION}/{product}"
    )


def validate_pam_payload(payload: Any) -> list[dict]:
    """Confere a estrutura da resposta e devolve só as linhas de dados.

    O SIDRA responde uma lista em que o primeiro item é o cabeçalho (nomes das
    colunas) e os demais são os valores.
    """
    if not isinstance(payload, list) or len(payload) < 2:
        raise SourceError("resposta do SIDRA vazia ou fora do formato de lista")
    header = payload[0]
    for key, label in EXPECTED_DIMENSIONS.items():
        if key not in header or label not in str(header[key]):
            raise SourceError(f"cabeçalho inesperado no SIDRA: {key} deveria ser '{label}', veio {header.get(key)!r}")
    if "V" not in header:
        raise SourceError("cabeçalho do SIDRA sem a coluna de valor 'V'")
    return payload[1:]


def validate_localidades_payload(payload: Any) -> list[dict]:
    if not isinstance(payload, list) or not payload or "id" not in payload[0]:
        raise SourceError("resposta de localidades do IBGE fora do formato esperado")
    return payload


def save_raw(raw: bytes, name: str, url: str, records: int, raw_dir: Path = config.RAW_DIR) -> RawCapture:
    """Salva a resposta bruta com carimbo de tempo e devolve os metadados."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = raw_dir / f"{name}_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_bytes(raw)
    meta = RawCapture(
        source=name,
        url=url,
        collected_at=now.isoformat(timespec="seconds"),
        path=str(path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path),
        sha256=hashlib.sha256(raw).hexdigest(),
        records=records,
    )
    path.with_suffix(".meta.json").write_text(json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def extract(raw_dir: Path = config.RAW_DIR, **fetch_kwargs) -> tuple[RawCapture, RawCapture]:
    """Coleta PAM e localidades ao vivo e salva as respostas brutas."""
    url = pam_url()
    log.info("consultando SIDRA: %s", url)
    payload, raw = fetch_json(url, **fetch_kwargs)
    rows = validate_pam_payload(payload)
    pam_meta = save_raw(raw, "sidra_pam_1613_mg_arabica", url, len(rows), raw_dir)
    log.info("SIDRA: %d linhas salvas em %s", len(rows), pam_meta.path)

    log.info("consultando localidades: %s", config.LOCALIDADES_URL)
    loc_payload, loc_raw = fetch_json(config.LOCALIDADES_URL, **fetch_kwargs)
    loc = validate_localidades_payload(loc_payload)
    loc_meta = save_raw(loc_raw, "ibge_localidades_mg", config.LOCALIDADES_URL, len(loc), raw_dir)
    log.info("localidades: %d municípios salvos em %s", len(loc), loc_meta.path)
    return pam_meta, loc_meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="só mostra as URLs, sem consultar")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.dry_run:
        print(pam_url())
        print(config.LOCALIDADES_URL)
        return 0
    try:
        pam_meta, loc_meta = extract()
    except SourceError as exc:
        log.error("%s", exc)
        return 1
    print(json.dumps({"pam": asdict(pam_meta), "localidades": asdict(loc_meta)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
