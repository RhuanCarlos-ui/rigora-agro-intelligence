"""Extração da previsão do tempo do INMET para um município (padrão: Patrocínio, MG).

Uso:
    python -m pipeline.extract_inmet            # coleta ao vivo e salva em data/raw/
    python -m pipeline.extract_inmet --dry-run  # só mostra a URL

Fonte: API pública de previsão do INMET (a mesma usada pelo portal https://portal.inmet.gov.br/).
Formato verificado em 14/11/2026: {"<codigo_ibge>": {"dd/mm/aaaa": {...}}}, com 5 datas;
as 2 primeiras divididas em "manha", "tarde" e "noite", as demais com um registro diário.

O que a fonte NÃO fornece (e por isso não existe no banco): precipitação (mm ou
probabilidade) e velocidade do vento em número (só a intensidade descrita, ex.: "Fracos").
É PREVISÃO, não observação medida em estação.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import config
from .extract_sidra import RawCapture, save_raw
from .http import SourceError, fetch_json

log = logging.getLogger(__name__)

RAW_NAME = "inmet_previsao"


def inmet_url(codigo_ibge: str = config.INMET_MUNICIPIO) -> str:
    return config.INMET_PREVISAO_URL.format(codigo_ibge=codigo_ibge)


def validate_inmet_payload(payload: Any, codigo_ibge: str = config.INMET_MUNICIPIO) -> dict:
    """Confere a estrutura e devolve o bloco do município: {data: registro}.

    Só verifica a FORMA da resposta. Valores (datas válidas, faixas, coerência) são
    responsabilidade das regras de qualidade, que registram o motivo em vez de abortar aqui.
    """
    if not isinstance(payload, dict) or not payload:
        raise SourceError("resposta do INMET vazia ou fora do formato de objeto")
    if codigo_ibge not in payload:
        raise SourceError(f"resposta do INMET não traz o município solicitado ({codigo_ibge}); veio {list(payload)[:3]}")
    city = payload[codigo_ibge]
    if not isinstance(city, dict) or not city:
        raise SourceError("resposta do INMET sem datas de previsão")
    for date, entry in city.items():
        if not isinstance(entry, dict):
            raise SourceError(f"previsão de {date!r} fora do formato esperado")
    return city


def extract(raw_dir: Path = config.RAW_DIR, codigo_ibge: str = config.INMET_MUNICIPIO, **fetch_kwargs) -> RawCapture:
    url = inmet_url(codigo_ibge)
    log.info("consultando INMET: %s", url)
    fetch_kwargs.setdefault("timeout", config.INMET_TIMEOUT_S)
    payload, raw = fetch_json(url, **fetch_kwargs)
    city = validate_inmet_payload(payload, codigo_ibge)
    meta = save_raw(raw, f"{RAW_NAME}_{codigo_ibge}", url, len(city), raw_dir)
    log.info("INMET: %d datas salvas em %s", len(city), meta.path)
    return meta


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.dry_run:
        print(inmet_url())
        return 0
    try:
        meta = extract()
    except SourceError as exc:
        log.error("%s", exc)
        return 1
    print(json.dumps(asdict(meta), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
