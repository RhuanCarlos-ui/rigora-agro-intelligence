"""Testes da etapa 3d: INMET (extração simulada, tratamento, qualidade, SQLite, independência).

Sem internet: a resposta real de 14/11/2026 (tests/fixtures/inmet_previsao_sample.json)
é servida por um "opener" falso no lugar da rede. Alterações para provocar casos ficam
só em memória e estão comentadas.
"""
import copy
import json
import sqlite3
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from pipeline import config, extract_inmet, load_sqlite, query
from pipeline.extract_sidra import save_raw
from pipeline.http import SourceError
from pipeline.run_pipeline import process, process_inmet, run_all
from pipeline.transform_inmet import inmet_to_frame
from pipeline.validate import ACIONADA, APROVADA, NAO_VERIFICADA, ValidationError
from pipeline.validate_inmet import dedupe_identical, repeated_json_keys, validate_inmet

FIX = Path(__file__).parent / "fixtures"
COD = "3148103"
COLETA_REAL = "2026-11-14T16:13:28+00:00"
AGORA = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)


def payload():
    return json.loads((FIX / "inmet_previsao_sample.json").read_text(encoding="utf-8"))


def save(raw_dir, data=None, raw_bytes=None, collected_at=COLETA_REAL):
    """Salva como uma coleta real (bruto + .meta.json), fixando a data da coleta."""
    b = raw_bytes if raw_bytes is not None else json.dumps(data if data is not None else payload(), ensure_ascii=False).encode()
    meta = save_raw(b, f"inmet_previsao_{COD}", config.INMET_PREVISAO_URL.format(codigo_ibge=COD), 5, raw_dir)
    path = raw_dir / Path(meta.path).name
    m = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    m["collected_at"] = collected_at
    path.with_suffix(".meta.json").write_text(json.dumps(m), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    return {"db": tmp_path / "rigora.db", "out": tmp_path / "out", "raw": tmp_path / "raw"}


def run(env, data=None, sub="a", collected_at=COLETA_REAL, raw_bytes=None, now=AGORA):
    return process_inmet(save(env["raw"] / sub, data, raw_bytes, collected_at), env["db"], env["out"], now=now)


def rep_for(data=None, collected_at=COLETA_REAL, now=AGORA, raw=None):
    city = (data or payload())[COD]
    df, removed = dedupe_identical(inmet_to_frame(city))
    return validate_inmet(df, collected_at=collected_at, city=city, raw=raw, identical_removed=removed, now=now)


def rule(rep, rid):
    return next(r for r in rep.regras if r.regra == rid)


# --- Extração com respostas simuladas ----------------------------------------------------
def test_url_oficial():
    assert extract_inmet.inmet_url() == "https://apiprevmet3.inmet.gov.br/previsao/3148103"


def test_extracao_simulada_salva_bruto_e_metadados(tmp_path):
    body = (FIX / "inmet_previsao_sample.json").read_bytes()
    meta = extract_inmet.extract(tmp_path, opener=lambda url, t: body, sleep=lambda s: None)
    saved = tmp_path / Path(meta.path).name
    assert saved.read_bytes() == body and meta.records == 5 and len(meta.sha256) == 64
    assert meta.url.endswith("/previsao/3148103")


def test_extracao_recusa_municipio_errado_e_resposta_vazia(tmp_path):
    with pytest.raises(SourceError, match="município solicitado"):
        extract_inmet.extract(tmp_path, opener=lambda u, t: b'{"3106200": {"14/11/2026": {}}}', sleep=lambda s: None)
    with pytest.raises(SourceError, match="vazia"):
        extract_inmet.extract(tmp_path, opener=lambda u, t: b"{}", sleep=lambda s: None)


def test_falha_de_conexao_vira_erro_claro(tmp_path):
    def down(url, timeout):
        raise urllib.error.URLError("sem rede")
    with pytest.raises(SourceError, match="tentativas"):
        extract_inmet.extract(tmp_path, opener=down, sleep=lambda s: None)
    assert not list(tmp_path.glob("*.json"))  # nada salvo


# --- Transformação: datas, períodos, unidades ------------------------------------------------
def test_um_registro_por_data_e_periodo():
    df = inmet_to_frame(payload()[COD])
    assert len(df) == 9  # 2 dias × 3 períodos + 3 dias com registro diário
    assert list(df["periodo"][:3]) == ["manha", "tarde", "noite"] and set(df["periodo"][6:]) == {"dia"}
    assert list(df["data_previsao"].unique()) == ["2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"]
    assert list(df["horizonte_dias"].unique()) == [0, 1, 2, 3, 4]


def test_valores_reais_e_unidades():
    row = inmet_to_frame(payload()[COD]).query("data_previsao == '2026-11-14' and periodo == 'tarde'").iloc[0]
    assert (row.temp_min_c, row.temp_max_c, row.umidade_min_pct, row.umidade_max_pct) == (18.0, 31.0, 40.0, 100.0)
    assert row.vento_intensidade == "Fracos" and row.resumo.startswith("Muitas nuvens")


def test_ausente_vira_nan_e_nao_zero():
    data = payload()
    del data[COD]["17/11/2026"]["temp_max"]          # campo ausente
    data[COD]["18/11/2026"]["umidade_min"] = ""     # campo vazio
    df = inmet_to_frame(data[COD])
    assert df.query("data_previsao == '2026-11-17'")["temp_max_c"].isna().all()
    assert df.query("data_previsao == '2026-11-18'")["umidade_min_pct"].isna().all()


def test_data_invalida_nao_e_descartada_nem_corrigida():
    data = payload()
    data[COD]["31/02/2026"] = data[COD].pop("18/11/2026")
    df = inmet_to_frame(data[COD])
    assert len(df) == 9 and df["data_previsao"].isna().sum() == 1


# --- Regras de qualidade ----------------------------------------------------------------------
def test_dados_reais_aprovados():
    rep = rep_for()
    res = rep.resumo()
    assert res["resultado"] == "aprovado" and res["registros_analisados"] == 9 and res["registros_rejeitados"] == 0
    assert rule(rep, "MET-09").status == NAO_VERIFICADA     # a fonte não traz precipitação
    assert rule(rep, "MET-12").status == NAO_VERIFICADA     # fixture sem o campo 'entidade'
    assert rule(rep, "MET-10").status == APROVADA and rule(rep, "MET-11").status == APROVADA


def test_data_invalida_e_impeditiva():
    data = payload()
    data[COD]["31/02/2026"] = data[COD].pop("18/11/2026")
    rep = rep_for(data)
    assert rule(rep, "MET-01").status == ACIONADA and not rep.ok


def test_temperaturas_incoerentes_e_extremas():
    data = payload()
    data[COD]["16/11/2026"]["temp_min"] = 35         # mínima > máxima (28)
    data[COD]["17/11/2026"]["temp_max"] = 52         # fora da faixa plausível
    rep = rep_for(data)
    assert rule(rep, "MET-04").status == ACIONADA and rule(rep, "MET-05").status == ACIONADA
    assert not rep.ok


def test_umidade_fora_da_faixa_e_invertida():
    data = payload()
    data[COD]["16/11/2026"]["umidade_max"] = 120
    data[COD]["17/11/2026"]["umidade_min"] = 100
    data[COD]["17/11/2026"]["umidade_max"] = 70
    rep = rep_for(data)
    assert rule(rep, "MET-06").afetados == 1 and rule(rep, "MET-07").afetados == 1


def test_precipitacao_negativa_quando_a_fonte_informar():
    data = payload()
    data[COD]["16/11/2026"]["precipitacao_mm"] = -2.0   # campo hipotético, para testar a regra
    assert rule(rep_for(data), "MET-09").status == ACIONADA
    data[COD]["16/11/2026"]["precipitacao_mm"] = 3.0
    assert rule(rep_for(data), "MET-09").status == APROVADA


def test_data_repetida_no_json_e_detectada():
    raw = (FIX / "inmet_previsao_sample.json").read_text(encoding="utf-8")
    dup = raw.replace('"16/11/2026"', '"15/11/2026"', 1)  # duas chaves "15/11/2026"
    assert repeated_json_keys(dup.encode()) == ["15/11/2026"]
    data = json.loads(dup)
    rep = rep_for(data, raw=dup.encode())
    assert rule(rep, "MET-02").status == ACIONADA and not rep.ok


def test_registros_identicos_sao_mantidos_uma_vez_com_aviso():
    df = inmet_to_frame(payload()[COD])
    clean, removed = dedupe_identical(pd.concat([df, df.iloc[[8]]]))
    assert removed == 1 and len(clean) == 9


def test_previsao_desatualizada_e_aviso():
    rep = rep_for(now=AGORA + timedelta(days=3))
    r = rule(rep, "MET-10")
    assert r.status == ACIONADA and r.severidade == "aviso" and rep.ok


def test_cobertura_incompleta_e_aviso():
    data = payload()
    for d in ("17/11/2026", "18/11/2026"):
        del data[COD][d]
    r = rule(rep_for(data), "MET-11")
    assert r.status == ACIONADA and "3 de 5" in r.detalhe


def test_municipio_divergente_e_aviso():
    data = payload()
    data[COD]["16/11/2026"]["entidade"] = "Patrocínio"
    data[COD]["17/11/2026"]["entidade"] = "Uberaba"
    assert rule(rep_for(data), "MET-12").status == ACIONADA


# --- SQLite ---------------------------------------------------------------------------------
def test_grava_consulta_e_integridade(env):
    res = run(env)
    assert res["gravados"] == 9 and res["total_no_banco"] == 9
    conn = sqlite3.connect(env["db"])
    fk = conn.execute("PRAGMA foreign_key_list(inmet_previsao)").fetchall()
    assert [(f[2], f[3], f[4]) for f in fk] == [("execucao", "execucao_id", "id")]
    rows = query.run("previsao", (COD, COD), env["db"])
    assert len(rows) == 9 and rows[0]["data_coleta"] == COLETA_REAL
    assert conn.execute("SELECT status, quantidade_registros FROM execucao").fetchone() == ("sucesso", 9)
    with pytest.raises(sqlite3.IntegrityError):  # banco recusa umidade inválida mesmo fora do pipeline
        conn.execute("UPDATE inmet_previsao SET umidade_max_pct = 130")


def test_reprocessar_mesmo_bruto_nao_duplica(env):
    run(env)
    run(env, sub="b")  # mesmo conteúdo e mesma data de coleta
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM execucao WHERE status='sucesso'").fetchone()[0] == 2


def test_nova_coleta_preserva_historico(env):
    run(env)
    later = "2026-11-15T16:00:00+00:00"
    run(env, sub="b", collected_at=later, now=AGORA + timedelta(days=1))
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT COUNT(DISTINCT data_coleta) FROM inmet_previsao").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 18
    assert {r["data_coleta"] for r in query.run("previsao", (COD, COD), env["db"])} == {later}


def test_ausente_fica_null(env):
    data = payload()
    del data[COD]["17/11/2026"]["temp_max"]
    res = run(env, data)
    assert res["validacao"]["resumo"]["registros_com_aviso"] == 1
    v = sqlite3.connect(env["db"]).execute("SELECT temp_max_c FROM inmet_previsao WHERE data_previsao='2026-11-17'").fetchone()[0]
    assert v is None


def test_falha_registra_motivo_e_preserva_previsao_anterior(env):
    run(env)
    bad = payload()
    bad[COD]["16/11/2026"]["temp_min"] = 35
    with pytest.raises(ValidationError):
        run(env, bad, sub="b", collected_at="2026-11-15T10:00:00+00:00")
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 9
    eid, status = conn.execute("SELECT id, status FROM execucao ORDER BY id DESC").fetchone()
    assert status == "falha"
    assert conn.execute("SELECT status FROM validacao_regra WHERE execucao_id=? AND regra='MET-04'", (eid,)).fetchone()[0] == "acionada"
    doc = json.loads((env["db"].parent / "reports" / f"qualidade_execucao_{eid:04d}.json").read_text(encoding="utf-8"))
    assert doc["fonte"].startswith("INMET") and doc["status_execucao"] == "falha"


def test_bruto_adulterado_nao_grava(env):
    path = save(env["raw"])
    path.write_bytes(path.read_bytes().replace(b"31", b"39"))
    with pytest.raises(Exception, match="hash"):
        process_inmet(path, env["db"], env["out"], now=AGORA)
    assert sqlite3.connect(env["db"]).execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 0


# --- Independência entre fontes e compatibilidade --------------------------------------------
def _pam_files(raw_dir):
    out = []
    for name, f in [("sidra_pam_1613_mg_arabica", "sidra_pam_sample.json"), ("ibge_localidades_mg", "localidades_sample.json")]:
        m = save_raw((FIX / f).read_bytes(), name, "fixture", 1, raw_dir)
        out.append(raw_dir / Path(m.path).name)
    return out


def test_falha_no_inmet_nao_afeta_ibge(env):
    pam, loc = _pam_files(env["raw"] / "ibge")
    bad = payload()
    bad[COD]["16/11/2026"]["umidade_max"] = 150
    inm = save(env["raw"] / "inmet", bad)
    out = run_all(["sidra", "inmet"], True, env["db"], pam, loc, inm, processed_dir=env["out"])
    assert out["sidra"]["status"] == "sucesso" and out["inmet"]["status"] == "falha"
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 0


def test_falha_no_ibge_nao_afeta_inmet(env):
    pam, loc = _pam_files(env["raw"] / "ibge")
    pam.write_bytes(pam.read_bytes().replace(b"1110", b"9999"))  # bruto do IBGE adulterado
    inm = save(env["raw"] / "inmet")
    out = run_all(["sidra", "inmet"], True, env["db"], pam, loc, inm, processed_dir=env["out"])
    assert out["sidra"]["status"] == "falha" and out["inmet"]["status"] == "sucesso"
    assert sqlite3.connect(env["db"]).execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 9


def test_inmet_fora_do_ar_nao_afeta_dados_do_ibge(env, monkeypatch):
    pam, loc = _pam_files(env["raw"] / "ibge")
    process(pam, loc, env["db"], env["out"])

    def offline(*a, **k):
        raise SourceError("não foi possível obter a previsão após 3 tentativas: sem rede")
    monkeypatch.setattr(extract_inmet, "extract", offline)
    out = run_all(["inmet"], False, env["db"], processed_dir=env["out"])
    assert out["inmet"]["status"] == "falha" and "sem rede" in out["inmet"]["erro"]
    assert sqlite3.connect(env["db"]).execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 40


def test_banco_da_etapa_anterior_ganha_a_tabela_sem_perder_dados(env):
    pam, loc = _pam_files(env["raw"] / "ibge")
    process(pam, loc, env["db"], env["out"])
    conn = sqlite3.connect(env["db"])
    conn.execute("DROP TABLE inmet_previsao")  # simula um banco criado antes da 3d
    conn.commit()
    run(env)
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0] == 9


# --- Correção A5: os testes nunca gravam na pasta real de CSVs (config.PROCESSED_DIR) -------------
def _estado(pasta: Path) -> dict:
    """Conteúdo de uma pasta (nome -> sha256), ou None se ela não existir."""
    import hashlib
    if not pasta.exists():
        return None
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pasta.iterdir()) if p.is_file()}


def test_run_all_grava_csvs_so_na_pasta_informada(env):
    import inspect
    assert inspect.signature(run_all).parameters["processed_dir"].default == config.PROCESSED_DIR  # produção igual
    real_antes = _estado(config.PROCESSED_DIR)
    pam, loc = _pam_files(env["raw"] / "ibge")
    out = run_all(["sidra", "inmet"], True, env["db"], pam, loc, save(env["raw"] / "inmet"), processed_dir=env["out"])
    assert out["sidra"]["status"] == out["inmet"]["status"] == "sucesso"
    assert set(_estado(env["out"])) == {"pam_mg_arabica.csv", "pam_mg_totais.csv", "inmet_previsao.csv"}
    assert _estado(config.PROCESSED_DIR) == real_antes            # a pasta real não foi criada nem alterada


def test_nenhum_teste_chama_o_pipeline_sem_pasta_de_saida_temporaria():
    """Varre os testes: toda chamada a run_all, process ou process_inmet informa a pasta de saída.

    Argumentos desempacotados (*args) não contam: a chamada precisa deixar a pasta visível.
    """
    import ast
    minimo = {"run_all": None, "process": 4, "process_inmet": 3}   # posição do argumento processed_dir (1-based)
    falhas = []
    for arq in sorted(Path(__file__).parent.glob("test_*.py")):
        for node in ast.walk(ast.parse(arq.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            nome = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if nome not in minimo:
                continue
            por_nome = any(k.arg == "processed_dir" for k in node.keywords)
            explicitos = [a for a in node.args if not isinstance(a, ast.Starred)]
            por_posicao = minimo[nome] is not None and len(explicitos) >= minimo[nome]
            if not (por_nome or por_posicao):
                falhas.append(f"{arq.name}:{node.lineno} {nome}(...) sem processed_dir")
    assert falhas == [], falhas
