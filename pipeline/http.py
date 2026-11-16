"""Requisições HTTP com timeout, novas tentativas e erro explícito.

Usa só a biblioteca padrão (urllib) para não acrescentar dependências.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Callable

from . import config

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Falha ao obter dados de uma fonte oficial (rede, HTTP ou conteúdo)."""


class ContentEncodingError(SourceError):
    """Resposta com compressão não suportada, inválida, truncada ou não declarada.

    Não gera nova tentativa: o problema está no conteúdo recebido, e a mensagem diz qual é.
    """


GZIP_MAGIC = b"\x1f\x8b"


def _inflate(body: bytes) -> bytes:
    """deflate com cabeçalho zlib (RFC 1950) ou, se não houver, deflate puro (RFC 1951)."""
    try:
        return zlib.decompress(body)
    except zlib.error as first:
        try:
            return zlib.decompress(body, -zlib.MAX_WBITS)
        except zlib.error:
            raise first from None  # informa o erro do formato padrão (com cabeçalho)


def decode_content(body: bytes, content_encoding: str | None, url: str = "") -> bytes:
    """Desfaz o Content-Encoding declarado pelo servidor. Sem cabeçalho ou 'identity': bytes intactos.

    O urllib não descomprime sozinho, e a API de localidades do IBGE responde em gzip mesmo
    quando o cliente não pede (confirmado em 14/11/2026: Content-Encoding: gzip, bytes 1f 8b 08 00).
    """
    codings = [c.strip().lower() for c in (content_encoding or "").split(",") if c.strip()]
    if not codings:
        if body[:2] == GZIP_MAGIC:
            raise ContentEncodingError(
                f"{url}: corpo começa com 1f 8b (assinatura gzip), mas o servidor não declarou Content-Encoding; "
                "a resposta não foi descomprimida por adivinhação")
        return body
    for coding in reversed(codings):  # aplicadas na ordem listada; desfaz da última para a primeira
        try:
            if coding == "identity":
                continue
            if coding in ("gzip", "x-gzip"):
                body = gzip.decompress(body)
            elif coding == "deflate":
                body = _inflate(body)
            else:
                raise ContentEncodingError(
                    f"{url}: Content-Encoding '{coding}' não suportado (aceitos: gzip, x-gzip, deflate, identity)")
        except (OSError, EOFError, zlib.error) as exc:  # gzip.BadGzipFile é OSError; truncado vira EOFError
            raise ContentEncodingError(
                f"{url}: conteúdo declarado como '{coding}' está inválido ou truncado ({type(exc).__name__}: {exc})") from exc
    return body


def _default_opener(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URLs fixas da config)
        return decode_content(resp.read(), resp.headers.get("Content-Encoding"), url)


def fetch_json(
    url: str,
    *,
    timeout: float = config.HTTP_TIMEOUT_S,
    retries: int = config.HTTP_RETRIES,
    backoff: float = config.HTTP_BACKOFF_S,
    opener: Callable[[str, float], bytes] = _default_opener,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Any, bytes]:
    """Baixa e decodifica JSON. Retorna (objeto, bytes brutos).

    Os bytes brutos são devolvidos para que o chamador salve exatamente o que
    a fonte respondeu (rastreabilidade) e calcule o hash. Se a resposta veio comprimida,
    são os bytes já descomprimidos (o JSON entregue pela fonte).
    """
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = opener(url, timeout)
            return json.loads(raw.decode("utf-8")), raw
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = exc
            log.warning("tentativa %d/%d falhou para %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                sleep(backoff * (2 ** (attempt - 1)))
    raise SourceError(f"não foi possível obter {url} após {retries} tentativas: {last_error}")
