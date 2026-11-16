"""Testes da etapa 3e: geração do js/snapshots.js a partir do SQLite.

Sem internet. Dados: fixtures reais (IBGE e INMET de 14/11/2026, reduzidas) carregadas pelo
pipeline das etapas 3a–3d. O contrato de referência é a cópia manual do js/snapshots.js da v2
(tests/fixtures/snapshots_manual_20261114.js), lida pela interface em js/app.js.
Alterações para provocar casos ficam em memória ou em arquivos temporários e estão comentadas.
"""
import json
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import config
from pipeline import export_snapshots as E
from pipeline.extract_sidra import save_raw
from pipeline.run_pipeline import process, process_inmet, run_all

FIX = Path(__file__).parent / "fixtures"
MANUAL = FIX / "snapshots_manual_20261114.js"
COD = "3148103"
COLETA_INMET = "2026-11-14T16:13:28+00:00"
AGORA = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)   # 2 h depois da coleta real


# --- Montagem do banco com as fixtures (mesmo caminho das etapas 3a–3d) -------------------------
def _save(raw_dir, name, data: bytes, url, collected_at=None):
    m = save_raw(data, name, url, 1, raw_dir)
    path = raw_dir / Path(m.path).name
    if collected_at:
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        meta["collected_at"] = collected_at
        path.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def inmet_payload():
    return json.loads((FIX / "inmet_previsao_sample.json").read_text(encoding="utf-8"))


def pam_files(raw_dir, pam_bytes=None):
    pam = _save(raw_dir, "sidra_pam_1613_mg_arabica", pam_bytes or (FIX / "sidra_pam_sample.json").read_bytes(), "fixture")
    loc = _save(raw_dir, "ibge_localidades_mg", (FIX / "localidades_sample.json").read_bytes(), "fixture")
    return pam, loc


def inmet_file(raw_dir, data=None, collected_at=COLETA_INMET):
    b = json.dumps(data or inmet_payload(), ensure_ascii=False).encode()
    return _save(raw_dir, f"inmet_previsao_{COD}", b, "fixture", collected_at)


@pytest.fixture
def env(tmp_path):
    d = {"db": tmp_path / "data" / "rigora.db", "raw": tmp_path / "data" / "raw", "out": tmp_path / "processed",
         "js": tmp_path / "js" / "snapshots.js"}
    d["js"].parent.mkdir(parents=True)
    return d


def load_ibge(env, pam_bytes=None):
    pam, loc = pam_files(env["raw"], pam_bytes)
    process(pam, loc, env["db"], env["out"])


def load_inmet(env, data=None, collected_at=COLETA_INMET):
    process_inmet(inmet_file(env["raw"], data, collected_at), env["db"], env["out"], now=AGORA)


def full(env):
    load_ibge(env)
    load_inmet(env)


def read(path):
    return E.parse_snapshots_js(Path(path).read_text(encoding="utf-8"))


def with_previous(env, src=MANUAL):
    shutil.copy(src, env["js"])
    return env["js"].read_bytes()


# --- Contrato ---------------------------------------------------------------------------------
def test_arquivo_manual_da_v2_e_lido_pelo_parser_e_segue_o_contrato():
    snap = read(MANUAL)
    assert E.validate_contract(snap) == ["exportacao sem geradoEm"]   # única diferença: chave nova da 3e


def test_gerado_tem_a_mesma_estrutura_do_arquivo_manual(env):
    full(env)
    E.export(env["db"], env["js"], now=AGORA)
    new, old = read(env["js"]), read(MANUAL)
    assert E.validate_contract(new) == []
    assert list(new) == ["sidra", "inmet", "exportacao"]
    assert list(new["sidra"]) == list(old["sidra"])
    assert list(new["sidra"]["municipios"][0]) == list(old["sidra"]["municipios"][0]) == ["c", "n", "meso", "y"]
    assert new["sidra"]["source"] == old["sidra"]["source"] and new["sidra"]["fields"] == old["sidra"]["fields"]
    assert new["sidra"]["query"] == old["sidra"]["query"]
    assert list(new["inmet"]) == list(old["inmet"]) == ["collectedAt", "data"]
    # todo campo que js/app.js lê do INMET existe com o mesmo nome e tipo
    read_by_app = ["resumo", "temp_max", "temp_min", "umidade_max", "umidade_min", "int_vento", "dir_vento", "dia_semana"]
    for date, entry in new["inmet"]["data"][COD].items():
        o = old["inmet"]["data"][COD][date]
        assert set(entry) == ({"manha", "tarde", "noite"} if "manha" in o else set(entry))
        for n, ref in ([(entry[p], o[p]) for p in ("manha", "tarde", "noite")] if "manha" in o else [(entry, o)]):
            for k in read_by_app:
                assert type(n[k]) is type(ref[k]) and n[k] == ref[k], (date, k)


def test_js_valido_para_o_navegador(env):
    full(env)
    E.export(env["db"], env["js"], now=AGORA)
    text = env["js"].read_text(encoding="utf-8")
    assert text.startswith("/**") and "window.RIGORA_SNAPSHOTS = {" in text and text.endswith("};\n")
    assert "Patrocínio" in text                                        # UTF-8, sem escapes \u
    if not shutil.which("node"):
        pytest.skip("node não instalado: checagem de sintaxe JS feita só pelo parser")
    out = subprocess.run(["node", "-e", f"global.window={{}};require({json.dumps(str(env['js']))});"
                          "const s=window.RIGORA_SNAPSHOTS;console.log(s.sidra.municipios.length, Object.keys(s.inmet.data['3148103']).length)"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["4", "5"]



# --- Cabeçalho (B4): descreve o comportamento real e não mexe no conteúdo -----------------------
CABECALHO_ANTES_B4 = "Usadas somente quando a consulta ao vivo falha (CORS, timeout, offline)."


def test_cabecalho_descreve_ibge_como_base_conferida_e_inmet_como_reserva_da_m1():
    h = E.HEADER
    assert CABECALHO_ANTES_B4 not in h                       # valia só para o INMET; o IBGE é sempre a base exibida
    ibge = h[h.index("IBGE/SIDRA (sidra)"):h.index("INMET (inmet)")]
    assert "base oficial exibida na aplicação" in ibge
    assert "só confere o rendimento de Patrocínio" in ibge and "sem trocar valores" in ibge
    assert "este arquivo não muda" in ibge
    inmet = h[h.index("INMET (inmet)"):h.index("Nunca são exibidas")]
    assert "a previsão vem da consulta ao vivo" in inmet and "reserva para quando a consulta" in inmet
    assert "até 24 h" in inmet and "hoje em diante" in inmet and "M1" in inmet
    assert str(config.INMET_MAX_IDADE_HORAS) + " h" in inmet  # mesmo limite que o exportador aplica
    assert "null" in inmet and "indisponível" in inmet
    assert 'Nunca são exibidas como "ao vivo"' in h
    assert "GERADO AUTOMATICAMENTE" in h and "`exportacao`" in h


def test_cabecalho_e_um_unico_comentario_js_valido():
    h = E.HEADER
    assert h.startswith("/**\n") and h.endswith(" */\n")
    assert h.count("*/") == 1 and h.count("/*") == 1         # nada fecha o comentário antes da hora
    linhas = h.splitlines()
    assert all(l == " *" or l.startswith(" * ") for l in linhas[1:-1]), linhas
    assert "window.RIGORA_SNAPSHOTS" not in h
    h.encode("utf-8")


def test_saida_com_os_mesmos_dados_difere_so_no_cabecalho(env, monkeypatch):
    full(env)
    novo = E.HEADER
    E.export(env["db"], env["js"], now=AGORA)
    atual = env["js"].read_text(encoding="utf-8")
    antigo = ("/**\n * Rigora - Cópias locais (snapshots) de dados OFICIAIS, com data de coleta.\n * "
              + CABECALHO_ANTES_B4 + "\n */\n")
    outro = env["js"].with_name("snapshots_cabecalho_antigo.js")
    monkeypatch.setattr(E, "HEADER", antigo)                # mesmo banco, mesmo instante; só o cabeçalho muda
    E.export(env["db"], outro, now=AGORA)
    anterior = outro.read_text(encoding="utf-8")
    assert atual.startswith(novo) and anterior.startswith(antigo) and atual != anterior
    assert atual[len(novo):] == anterior[len(antigo):]       # sidra, inmet e exportacao idênticos byte a byte
    assert read(env["js"]) == read(outro)
    assert E.validate_contract(read(env["js"])) == []
    # corpo montado aqui, de forma independente do render(): o formato de sempre, igual ao de antes do B4
    snap = read(env["js"])
    j = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    esperado = ("window.RIGORA_SNAPSHOTS = {\n" f"  sidra: {j(snap['sidra'])},\n" f"  inmet: {j(snap['inmet'])},\n"
                f"  exportacao: {j(snap['exportacao'])}\n" "};\n")
    assert atual[len(novo):] == esperado

# --- IBGE -------------------------------------------------------------------------------------
def test_exporta_ibge_com_valores_oficiais(env):
    load_ibge(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    s = read(env["js"])["sidra"]
    pat = next(m for m in s["municipios"] if m["c"] == COD)
    manual = next(m for m in read(MANUAL)["sidra"]["municipios"] if m["c"] == COD)
    assert pat == manual                                               # mesmos números da cópia oficial
    assert pat["y"]["2025"] == [1110, 43850, 48669] and all(isinstance(v, int) for v in pat["y"]["2025"])
    areas = [m["y"]["2025"][1] for m in s["municipios"]]
    assert areas == sorted(areas, reverse=True)                        # ordenado por área colhida em 2025
    assert s["municipiosMG"] == 4 and set(s["mg"]) == {str(y) for y in range(2016, 2026)}
    a, p = sum(m["y"]["2025"][1] for m in s["municipios"]), sum(m["y"]["2025"][2] for m in s["municipios"])
    assert s["mg"]["2025"] == [round(p * 1000 / a), a, p]
    assert info["sidra"]["modo"] == "sqlite" and info["sidra"]["registrosExportados"] == 40
    assert info["sidra"]["validacao"]["erros_impeditivos"] == 0


def test_ordem_estavel_em_empate_de_area(env):
    # Monte Carmelo passa a ter a mesma área de Manhuaçu em 2025 (empate); a ordem segue o código IBGE
    pam = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    area = {r["D1C"]: r for r in pam[1:] if r["D2C"] == "216" and r["D3C"] == "2025"}
    codes = sorted(area, key=lambda c: -float(area[c]["V"]))
    a, b = codes[1], codes[2]
    area[b]["V"] = area[a]["V"]
    for r in pam[1:]:   # mantém produção/área coerentes para a validação aprovar
        if r["D1C"] == b and r["D3C"] == "2025" and r["D2C"] in ("214", "112"):
            r["V"] = next(x["V"] for x in pam[1:] if x["D1C"] == a and x["D3C"] == "2025" and x["D2C"] == r["D2C"])
    load_ibge(env, json.dumps(pam, ensure_ascii=False).encode())
    E.export(env["db"], env["js"], now=AGORA)
    order = [m["c"] for m in read(env["js"])["sidra"]["municipios"]]
    assert order.index(min(a, b)) < order.index(max(a, b))


def test_ibge_ausente_vira_null_e_nunca_zero(env):
    pam = json.loads((FIX / "sidra_pam_sample.json").read_text(encoding="utf-8"))
    for r in pam[1:]:   # rendimento de Patrocínio em 2020 não divulgado pelo IBGE ('...')
        if r["D1C"] == COD and r["D3C"] == "2020" and r["D2C"] == "112":
            r["V"] = "..."
    load_ibge(env, json.dumps(pam, ensure_ascii=False).encode())
    E.export(env["db"], env["js"], now=AGORA)
    y = next(m for m in read(env["js"])["sidra"]["municipios"] if m["c"] == COD)["y"]["2020"]
    assert y[0] is None and y[1] > 0 and y[2] > 0


def test_cobertura_parcial_nao_troca_a_media_de_mg(env):
    # o banco tem 4 municípios; o arquivo anterior tem a média dos 533 -> mantém a seção anterior
    load_ibge(env)
    with_previous(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    s = read(env["js"])["sidra"]
    assert s == read(MANUAL)["sidra"]
    assert info["sidra"]["modo"] == "mantido_do_arquivo_anterior" and "cobertura parcial" in info["sidra"]["motivo"]
    assert info["sidra"]["candidatoDoBanco"]["municipiosNaMediaMG"] == 4


# --- INMET ------------------------------------------------------------------------------------
def test_exporta_inmet_no_formato_da_api(env):
    load_inmet(env)
    with_previous(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    i = read(env["js"])["inmet"]
    assert i["collectedAt"] == "2026-11-14T16:13:28Z"
    days = i["data"][COD]
    assert list(days) == ["14/11/2026", "15/11/2026", "16/11/2026", "17/11/2026", "18/11/2026"]
    assert set(days["14/11/2026"]) == {"manha", "tarde", "noite"} and "temp_max" in days["16/11/2026"]
    t = days["14/11/2026"]["tarde"]
    assert (t["temp_min"], t["temp_max"], t["umidade_min"], t["umidade_max"]) == (18, 31, 40, 100)
    assert t["dia_semana"] == "Sábado" and days["16/11/2026"]["dia_semana"] == "Segunda-Feira"
    flat = json.dumps(i, ensure_ascii=False).lower()
    assert "precip" not in flat and "chuva_mm" not in flat and "vel_vento" not in flat   # nada inventado
    assert info["inmet"]["modo"] == "sqlite" and info["inmet"]["registrosExportados"] == 9


def test_previsao_desatualizada_nao_e_exibida_como_atual(env):
    full(env)
    info = E.export(env["db"], env["js"], now=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))   # 44 h depois
    assert read(env["js"])["inmet"] is None              # a interface mostra "Previsão indisponível"
    assert info["inmet"]["modo"] == "indisponivel" and "44 h" in info["inmet"]["motivo"]


def test_datas_que_ja_passaram_sao_retiradas(env):
    full(env)
    info = E.export(env["db"], env["js"], now=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc))   # 20 h depois
    assert list(read(env["js"])["inmet"]["data"][COD])[0] == "15/11/2026"
    assert info["inmet"]["datasOmitidas"][0]["data"] == "14/11/2026"


def test_dia_com_valor_ausente_e_omitido_e_nao_vira_zero(env):
    data = inmet_payload()
    del data[COD]["17/11/2026"]["temp_max"]              # a fonte não informou a máxima de 17/11
    load_ibge(env)
    load_inmet(env, data)
    info = E.export(env["db"], env["js"], now=AGORA)
    days = read(env["js"])["inmet"]["data"][COD]
    assert "17/11/2026" not in days and len(days) == 4
    assert all(v != 0 for d in days.values() for r in ([d[p] for p in d] if "manha" in d else [d])
               for k, v in r.items() if k.startswith(("temp", "umidade")))
    om = info["inmet"]["datasOmitidas"]
    assert om == [{"data": "17/11/2026", "motivo": "valores ausentes (temp_max_c); a interface exige esses campos"}]


# --- Uma fonte ausente ou com falha não apaga a outra -----------------------------------------
def test_banco_so_com_inmet_mantem_ibge_do_arquivo_anterior(env):
    load_inmet(env)
    with_previous(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    snap = read(env["js"])
    assert snap["sidra"] == read(MANUAL)["sidra"] and snap["inmet"]["data"][COD]
    assert info["sidra"]["modo"] == "mantido_do_arquivo_anterior"


def test_banco_so_com_ibge_e_sem_arquivo_anterior_deixa_inmet_null(env):
    load_ibge(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    assert read(env["js"])["inmet"] is None and info["inmet"]["modo"] == "indisponivel"


def test_banco_so_com_ibge_mantem_previsao_anterior_somente_se_ainda_atual(env):
    load_ibge(env)
    with_previous(env)
    info = E.export(env["db"], env["js"], now=AGORA)          # anterior coletada há 2 h: mantida
    assert read(env["js"])["inmet"]["collectedAt"] == read(MANUAL)["inmet"]["collectedAt"]
    assert info["inmet"]["modo"] == "mantido_do_arquivo_anterior"
    with_previous(env)
    E.export(env["db"], env["js"], now=datetime(2026, 11, 17, tzinfo=timezone.utc))   # 2,3 dias depois
    assert read(env["js"])["inmet"] is None


def test_falha_do_inmet_no_pipeline_nao_apaga_ibge(env):
    pam, loc = pam_files(env["raw"])
    bad = inmet_payload()
    bad[COD]["16/11/2026"]["umidade_max"] = 150          # erro impeditivo na 3d
    out = run_all(["sidra", "inmet"], True, env["db"], pam, loc, inmet_file(env["raw"], bad), processed_dir=env["out"])
    assert out["sidra"]["status"] == "sucesso" and out["inmet"]["status"] == "falha"
    info = E.export(env["db"], env["js"], now=AGORA)
    snap = read(env["js"])
    assert len(snap["sidra"]["municipios"]) == 4 and snap["inmet"] is None
    assert "nenhuma execução do INMET com sucesso" in info["inmet"]["motivo"]


def test_falha_do_ibge_no_pipeline_nao_apaga_inmet_nem_o_ibge_anterior(env):
    pam, loc = pam_files(env["raw"])
    pam.write_bytes(pam.read_bytes().replace(b"1110", b"9999"))   # bruto adulterado: hash não confere
    out = run_all(["sidra", "inmet"], True, env["db"], pam, loc, inmet_file(env["raw"]), processed_dir=env["out"])
    assert out["sidra"]["status"] == "falha" and out["inmet"]["status"] == "sucesso"
    with_previous(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    snap = read(env["js"])
    assert snap["sidra"] == read(MANUAL)["sidra"] and len(snap["inmet"]["data"][COD]) == 5
    assert info["sidra"]["modo"] == "mantido_do_arquivo_anterior" and info["inmet"]["modo"] == "sqlite"


def test_execucao_com_falha_nunca_e_exportada(env):
    full(env)
    bad = inmet_payload()
    bad[COD]["16/11/2026"]["umidade_max"] = 150
    with pytest.raises(Exception):
        load_inmet(env, bad, collected_at="2026-11-14T17:30:00+00:00")   # coleta mais nova, reprovada
    info = E.export(env["db"], env["js"], now=AGORA)
    assert info["inmet"]["dataColeta"] == COLETA_INMET           # continua a última válida


def test_sem_ibge_no_banco_e_sem_arquivo_anterior_falha_sem_criar_arquivo(env):
    load_inmet(env)
    with pytest.raises(E.ExportError, match="IBGE/SIDRA sem dados"):
        E.export(env["db"], env["js"], now=AGORA)
    assert not env["js"].exists()


# --- Escrita atômica, reexecução e preservação do arquivo anterior ----------------------------
def test_reexecucao_nao_duplica_e_so_muda_a_data_de_geracao(env):
    full(env)
    E.export(env["db"], env["js"], now=AGORA)
    first = read(env["js"])
    load_inmet(env)                     # reprocessa o mesmo bruto (UPSERT, sem duplicar)
    E.export(env["db"], env["js"], now=datetime(2026, 11, 14, 18, 30, tzinfo=timezone.utc))
    second = read(env["js"])
    assert first["sidra"] == second["sidra"] and first["inmet"] == second["inmet"]
    assert first["exportacao"]["geradoEm"] != second["exportacao"]["geradoEm"]
    text = env["js"].read_text(encoding="utf-8")
    assert text.count("  sidra: ") == text.count("  inmet: ") == text.count("window.RIGORA_SNAPSHOTS") == 1


def test_geracao_e_coleta_nao_se_confundem(env):
    full(env)
    info = E.export(env["db"], env["js"], now=AGORA)
    assert info["geradoEm"] == "2026-11-14T18:00:00+00:00"
    assert info["inmet"]["dataColeta"] == COLETA_INMET and info["sidra"]["dataColeta"] != info["geradoEm"]
    assert "não indica coleta ao vivo" in info["aviso"]


def test_erro_na_validacao_preserva_arquivo_anterior(env, monkeypatch):
    full(env)
    before = with_previous(env)
    monkeypatch.setattr(E, "validate_contract", lambda snap: ["falha simulada"])
    with pytest.raises(E.ExportError, match="falha simulada"):
        E.export(env["db"], env["js"], now=AGORA)
    assert env["js"].read_bytes() == before
    assert list(env["js"].parent.iterdir()) == [env["js"]]    # sem .tmp esquecido


def test_erro_ao_substituir_preserva_arquivo_anterior(env, monkeypatch):
    full(env)
    before = with_previous(env)

    def boom(src, dst):
        raise OSError("disco cheio (simulado)")
    monkeypatch.setattr(E.os, "replace", boom)
    with pytest.raises(OSError):
        E.export(env["db"], env["js"], now=AGORA)
    assert env["js"].read_bytes() == before and list(env["js"].parent.iterdir()) == [env["js"]]


def test_banco_ausente_ou_corrompido_preserva_arquivo(env):
    before = with_previous(env)
    assert E.main(["--db", str(env["db"]), "--saida", str(env["js"])]) == 1
    env["db"].parent.mkdir(parents=True, exist_ok=True)
    env["db"].write_bytes(b"isto nao e um banco sqlite")
    assert E.main(["--db", str(env["db"]), "--saida", str(env["js"])]) == 1
    assert env["js"].read_bytes() == before


def test_arquivo_anterior_ilegivel_nao_impede_a_geracao(env):
    full(env)
    env["js"].write_text("window.ALGO = 1;", encoding="utf-8")
    E.export(env["db"], env["js"], now=AGORA)
    assert E.validate_contract(read(env["js"])) == []


def test_exportacao_abre_o_banco_somente_leitura(env):
    full(env)
    conn = sqlite3.connect(env["db"])
    before = conn.execute("SELECT COUNT(*), MAX(id) FROM execucao").fetchone()
    E.export(env["db"], env["js"], now=AGORA)
    assert conn.execute("SELECT COUNT(*), MAX(id) FROM execucao").fetchone() == before


def test_funciona_sem_internet(env, monkeypatch):
    full(env)

    def no_net(*a, **k):
        raise OSError("sem rede (teste)")
    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    info = E.export(env["db"], env["js"], now=AGORA)
    assert info["sidra"]["modo"] == "sqlite" and info["inmet"]["modo"] == "sqlite"
