"""Testes da camada HTTP: tratamento de Content-Encoding (correção da falha de 14/11/2026).

Contexto real: a API de localidades do IBGE respondeu HTTP 200 com Content-Encoding: gzip e
Transfer-Encoding: chunked (bytes 1f 8b 08 00), inclusive pedindo Accept-Encoding: identity.
O urllib não descomprime sozinho, e a leitura direta como UTF-8 falhava no byte 0x8b.

Sem internet: os testes de HTTP usam um servidor local (http.server, biblioteca padrão) e o
urllib de verdade, com proxy desligado só para esse endereço local.
"""
import gzip
import http.server
import json
import threading
import urllib.request
import zlib
from pathlib import Path

import pytest

from pipeline import config, extract_sidra
from pipeline.http import ContentEncodingError, SourceError, decode_content, fetch_json
from pipeline.run_pipeline import process

FIX = Path(__file__).parent / "fixtures"
LOC = (FIX / "localidades_sample.json").read_bytes()      # JSON real (reduzido) da API de localidades
PAM = (FIX / "sidra_pam_sample.json").read_bytes()


def deflate_zlib(b):
    return zlib.compress(b)                              # RFC 1950: com cabeçalho zlib


def deflate_raw(b):
    c = zlib.compressobj(wbits=-zlib.MAX_WBITS)          # RFC 1951: deflate puro, sem cabeçalho
    return c.compress(b) + c.flush()


# --- decode_content ---------------------------------------------------------------------------
def test_sem_compressao_fica_intacto():
    assert decode_content(LOC, None) is LOC
    assert decode_content(LOC, "") is LOC
    assert decode_content(LOC, "identity") == LOC


@pytest.mark.parametrize("header", ["gzip", "x-gzip", "GZIP", " gzip "])
def test_gzip_e_x_gzip(header):
    assert decode_content(gzip.compress(LOC), header) == LOC


@pytest.mark.parametrize("compress", [deflate_zlib, deflate_raw])
def test_deflate_nos_dois_formatos(compress):
    assert decode_content(compress(LOC), "deflate") == LOC


def test_codificacoes_em_sequencia_sao_desfeitas_na_ordem_inversa():
    body = gzip.compress(deflate_zlib(LOC))              # deflate aplicado primeiro, depois gzip
    assert decode_content(body, "deflate, gzip") == LOC


@pytest.mark.parametrize("header", ["br", "compress", "zstd"])
def test_codificacao_desconhecida_e_erro_explicito(header):
    with pytest.raises(ContentEncodingError, match=f"'{header}' não suportado"):
        decode_content(b"qualquer coisa", header, "https://exemplo")


def test_gzip_corrompido_e_erro_explicito():
    body = bytearray(gzip.compress(LOC))
    body[20:40] = b"\x00" * 20                            # miolo corrompido
    with pytest.raises(ContentEncodingError, match="'gzip' está inválido ou truncado"):
        decode_content(bytes(body), "gzip")
    with pytest.raises(ContentEncodingError, match="inválido ou truncado"):
        decode_content(b"isto nao e gzip", "gzip")       # cabeçalho declarado, corpo sem gzip


def test_gzip_truncado_e_erro_explicito():
    body = gzip.compress(LOC)
    with pytest.raises(ContentEncodingError, match="EOFError"):
        decode_content(body[:-8], "gzip")                # perdeu o final (CRC e tamanho)
    with pytest.raises(ContentEncodingError):
        decode_content(body[: len(body) // 2], "gzip")


@pytest.mark.parametrize("compress", [deflate_zlib, deflate_raw])
def test_deflate_truncado_e_erro_explicito(compress):
    body = compress(LOC)
    with pytest.raises(ContentEncodingError, match="'deflate' está inválido ou truncado"):
        decode_content(body[: len(body) // 2], "deflate")


def test_gzip_sem_header_nao_e_descomprimido_por_adivinhacao():
    with pytest.raises(ContentEncodingError, match="não declarou Content-Encoding") as e:
        decode_content(gzip.compress(LOC), None, "https://exemplo")
    assert "1f 8b" in str(e.value)


def test_erro_de_compressao_nao_gera_novas_tentativas():
    calls = []

    def opener(url, timeout):
        calls.append(url)
        return decode_content(b"zzz", "br", url)
    with pytest.raises(ContentEncodingError):
        fetch_json("http://exemplo", opener=opener, sleep=lambda s: None, retries=3)
    assert len(calls) == 1 and issubclass(ContentEncodingError, SourceError)


# --- urllib real contra um servidor HTTP local ------------------------------------------------
ROUTES = {
    "/plano": ({}, LOC),
    "/gzip": ({"Content-Encoding": "gzip"}, gzip.compress(LOC)),
    "/x-gzip": ({"Content-Encoding": "x-gzip"}, gzip.compress(LOC)),
    "/deflate-zlib": ({"Content-Encoding": "deflate"}, deflate_zlib(LOC)),
    "/deflate-raw": ({"Content-Encoding": "deflate"}, deflate_raw(LOC)),
    "/br": ({"Content-Encoding": "br"}, b"\x0b\x02\x80{}\x03"),
    "/gzip-sem-header": ({}, gzip.compress(LOC)),
    "/gzip-truncado": ({"Content-Encoding": "gzip"}, gzip.compress(LOC)[:-8]),
    "/localidades": ({"Content-Encoding": "gzip"}, gzip.compress(LOC)),   # como o IBGE em 14/11/2026
    "/sidra": ({}, PAM),                                                    # SIDRA veio sem compressão
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        headers, body = ROUTES[self.path]
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")  # igual à resposta real do IBGE
        self.end_headers()
        for i in range(0, len(body), 64):
            part = body[i:i + 64]
            self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    # urllib real, sem proxy (o proxy do sistema não alcança o servidor local)
    monkeypatch.setattr(urllib.request, "_opener", urllib.request.build_opener(urllib.request.ProxyHandler({})))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("path", ["/plano", "/gzip", "/x-gzip", "/deflate-zlib", "/deflate-raw"])
def test_http_local_entrega_o_json(server, path):
    obj, raw = fetch_json(server + path, retries=1, timeout=10)
    assert raw == LOC and obj == json.loads(LOC)


@pytest.mark.parametrize("path, msg", [("/br", "'br' não suportado"),
                                       ("/gzip-sem-header", "não declarou Content-Encoding"),
                                       ("/gzip-truncado", "inválido ou truncado")])
def test_http_local_erros_explicitos(server, path, msg):
    with pytest.raises(ContentEncodingError, match=msg):
        fetch_json(server + path, retries=3, timeout=10, sleep=lambda s: pytest.fail("não deveria tentar de novo"))


def test_coleta_do_ibge_com_localidades_em_gzip_ate_o_sqlite(server, monkeypatch, tmp_path):
    # Reproduz o cenário real: SIDRA sem compressão, localidades em gzip + chunked.
    monkeypatch.setattr(extract_sidra, "pam_url", lambda: server + "/sidra")
    monkeypatch.setattr(config, "LOCALIDADES_URL", server + "/localidades")
    pam_meta, loc_meta = extract_sidra.extract(tmp_path / "raw", retries=1, timeout=10)
    loc_raw = tmp_path / "raw" / Path(loc_meta.path).name
    assert loc_raw.read_bytes() == LOC                         # salvo já descomprimido: o JSON da fonte
    assert json.loads(loc_raw.with_suffix(".meta.json").read_text(encoding="utf-8"))["sha256"] == loc_meta.sha256
    out = process(tmp_path / "raw" / Path(pam_meta.path).name, loc_raw, tmp_path / "rigora.db", tmp_path / "processed")
    assert out["validacao"]["erros"] == [] and out["gravados"] == 40
    assert out["validacao"]["resumo"]["registros_rejeitados"] == 0
