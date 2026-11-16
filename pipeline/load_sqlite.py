"""Armazenamento no SQLite (data/rigora.db).

Garantias:
- Reexecução não duplica: chave primária (codigo_ibge, ano) + UPSERT.
- Dado anterior não some: valor válido já gravado que uma nova coleta traria ausente (resposta parcial)
  faz a carga inteira ser recusada (regra INT-08), e os dados anteriores ficam como estavam.
- Falha não deixa estado parcial: os dados de uma execução entram numa única transação.
- Toda linha aponta para a execução que a gravou (execucao_id, com chave estrangeira).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS execucao (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    fonte                TEXT    NOT NULL,
    data_coleta          TEXT    NOT NULL,              -- quando a fonte respondeu (UTC, ISO 8601)
    data_execucao        TEXT    NOT NULL,              -- quando o pipeline processou (um mesmo bruto pode ser reprocessado)
    status               TEXT    NOT NULL CHECK (status IN ('em_andamento', 'sucesso', 'falha', 'interrompida')),
    quantidade_registros INTEGER NOT NULL DEFAULT 0 CHECK (quantidade_registros >= 0),
    arquivo_origem       TEXT    NOT NULL,
    hash_sha256          TEXT    NOT NULL CHECK (length(hash_sha256) = 64),
    mensagem             TEXT                           -- erro (falha) ou relatório de validação (JSON)
);

CREATE TABLE IF NOT EXISTS pam_municipio_ano (
    codigo_ibge          TEXT    NOT NULL CHECK (length(codigo_ibge) = 7),
    municipio            TEXT    NOT NULL,
    regiao               TEXT,                          -- mesorregião IBGE; NULL se a API de localidades não informar
    ano                  INTEGER NOT NULL,
    rendimento_kg_ha     REAL    CHECK (rendimento_kg_ha IS NULL OR rendimento_kg_ha > 0),
    produtividade_sc_ha  REAL    GENERATED ALWAYS AS (rendimento_kg_ha / 60.0) STORED,
    area_colhida_ha      REAL    CHECK (area_colhida_ha IS NULL OR area_colhida_ha >= 0),
    producao_t           REAL    CHECK (producao_t IS NULL OR producao_t >= 0),
    execucao_id          INTEGER NOT NULL REFERENCES execucao(id) ON DELETE RESTRICT,
    PRIMARY KEY (codigo_ibge, ano)
);

-- Etapa 3c: resultado de cada regra de qualidade por execução (inclusive as que falharam)
CREATE TABLE IF NOT EXISTS validacao_regra (
    execucao_id          INTEGER NOT NULL REFERENCES execucao(id) ON DELETE CASCADE,
    regra                TEXT    NOT NULL,              -- ex.: CON-01
    nome                 TEXT    NOT NULL,
    dimensao             TEXT    NOT NULL CHECK (dimensao IN ('integridade', 'completude', 'consistencia', 'plausibilidade')),
    severidade           TEXT    NOT NULL CHECK (severidade IN ('erro', 'aviso', 'info')),
    status               TEXT    NOT NULL CHECK (status IN ('aprovada', 'acionada', 'nao_verificada')),
    registros_afetados   INTEGER NOT NULL DEFAULT 0 CHECK (registros_afetados >= 0),
    detalhe              TEXT,
    PRIMARY KEY (execucao_id, regra)
);

-- Etapa 3c: cada registro apontado por uma regra (o motivo fica guardado, nada é descartado em silêncio)
CREATE TABLE IF NOT EXISTS validacao_ocorrencia (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    execucao_id          INTEGER NOT NULL,
    regra                TEXT    NOT NULL,
    codigo_ibge          TEXT,                          -- NULL quando a ocorrência é de um ano inteiro
    ano                  INTEGER,                       -- NULL quando a ocorrência é do município inteiro
    detalhe              TEXT,
    FOREIGN KEY (execucao_id, regra) REFERENCES validacao_regra (execucao_id, regra) ON DELETE CASCADE
);

-- Etapa 3a da PLA-05: grupos de coincidência por execução (estrutura; a gravação vem na etapa 3b).
-- Guardam o estado daquela execução: valores, nível, limites da faixa e nomes, sem depender de
-- pam_municipio_ano (atualizada por UPSERT) e sem chave estrangeira para ela, de propósito.
-- Exclusões são bloqueadas (RESTRICT): apagar exige remover membros, depois grupos, explicitamente.
CREATE TABLE IF NOT EXISTS validacao_grupo (
    execucao_id          INTEGER NOT NULL,
    regra                TEXT    NOT NULL CHECK (regra = 'PLA-05'),
    grupo                INTEGER NOT NULL CHECK (grupo >= 1),   -- 1..n dentro da execução; não é identificador global
    ano                  INTEGER NOT NULL,
    nivel                TEXT    NOT NULL CHECK (nivel IN ('informativo', 'atencao', 'revisao_documental')),
    rendimento_kg_ha     REAL    NOT NULL CHECK (rendimento_kg_ha > 0),
    produtividade_sc_ha  REAL    GENERATED ALWAYS AS (rendimento_kg_ha / 60.0) STORED,
    area_colhida_ha      REAL    NOT NULL CHECK (area_colhida_ha > 0),
    producao_t           REAL    NOT NULL CHECK (producao_t >= 0),
    total_municipios     INTEGER NOT NULL CHECK (total_municipios >= 1),
    total_registros      INTEGER NOT NULL CHECK (total_registros >= 2 AND total_registros >= total_municipios),
    faixa_acima_de_ha    REAL,                                  -- limites da faixa usados nesta execução (NULL = sem limite)
    faixa_ate_ha         REAL,
    PRIMARY KEY (execucao_id, regra, grupo),
    UNIQUE (execucao_id, regra, grupo, ano),                    -- alvo da chave do membro (garante o mesmo ano)
    UNIQUE (execucao_id, regra, ano, rendimento_kg_ha, area_colhida_ha, producao_t),
    FOREIGN KEY (execucao_id) REFERENCES execucao (id) ON DELETE RESTRICT,
    FOREIGN KEY (execucao_id, regra) REFERENCES validacao_regra (execucao_id, regra) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS validacao_grupo_membro (
    execucao_id          INTEGER NOT NULL,
    regra                TEXT    NOT NULL,
    grupo                INTEGER NOT NULL,
    ano                  INTEGER NOT NULL,                      -- igual ao do grupo (garantido pela chave estrangeira)
    codigo_ibge          TEXT    NOT NULL CHECK (length(codigo_ibge) = 7),
    municipio            TEXT,                                  -- nome nos dados validados daquela execução; NULL se ausente
    PRIMARY KEY (execucao_id, regra, grupo, codigo_ibge),
    UNIQUE (execucao_id, regra, ano, codigo_ibge),              -- um município em um único grupo por ano
    FOREIGN KEY (execucao_id, regra, grupo, ano)
        REFERENCES validacao_grupo (execucao_id, regra, grupo, ano) ON DELETE RESTRICT
);

-- Etapa 3d: PREVISÃO do tempo do INMET (não é observação medida em estação).
-- Cada coleta guarda sua própria previsão: a previsão para 18/11 feita em 14/11 e a feita
-- em 16/11 são registros diferentes (histórico preservado). Reprocessar o mesmo bruto
-- (mesma data_coleta) atualiza as mesmas linhas, sem duplicar.
CREATE TABLE IF NOT EXISTS inmet_previsao (
    codigo_ibge          TEXT    NOT NULL CHECK (length(codigo_ibge) = 7),
    data_coleta          TEXT    NOT NULL,              -- quando a previsão foi obtida (UTC); identifica a "edição" da previsão
    data_previsao        TEXT    NOT NULL CHECK (date(data_previsao) = data_previsao),
    periodo              TEXT    NOT NULL CHECK (periodo IN ('manha', 'tarde', 'noite', 'dia')),
    horizonte_dias       INTEGER NOT NULL CHECK (horizonte_dias >= 0),
    temp_min_c           REAL,
    temp_max_c           REAL,
    umidade_min_pct      REAL    CHECK (umidade_min_pct IS NULL OR umidade_min_pct BETWEEN 0 AND 100),
    umidade_max_pct      REAL    CHECK (umidade_max_pct IS NULL OR umidade_max_pct BETWEEN 0 AND 100),
    vento_intensidade    TEXT,                          -- descrição da fonte (ex.: "Fracos"); a fonte não dá km/h
    vento_direcao        TEXT,
    resumo               TEXT,
    execucao_id          INTEGER NOT NULL REFERENCES execucao(id) ON DELETE RESTRICT,
    PRIMARY KEY (codigo_ibge, data_coleta, data_previsao, periodo),
    CHECK (temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c <= temp_max_c),
    CHECK (umidade_min_pct IS NULL OR umidade_max_pct IS NULL OR umidade_min_pct <= umidade_max_pct)
);

CREATE INDEX IF NOT EXISTS ix_inmet_data_previsao ON inmet_previsao (codigo_ibge, data_previsao);
CREATE INDEX IF NOT EXISTS ix_inmet_execucao      ON inmet_previsao (execucao_id);
CREATE INDEX IF NOT EXISTS ix_ocorrencia_registro ON validacao_ocorrencia (codigo_ibge, ano);
CREATE INDEX IF NOT EXISTS ix_ocorrencia_execucao ON validacao_ocorrencia (execucao_id, regra);
CREATE INDEX IF NOT EXISTS ix_pam_ano       ON pam_municipio_ano (ano);
CREATE INDEX IF NOT EXISTS ix_pam_execucao  ON pam_municipio_ano (execucao_id);
CREATE INDEX IF NOT EXISTS ix_execucao_fonte ON execucao (fonte, data_execucao);
CREATE INDEX IF NOT EXISTS ix_grupo_nivel        ON validacao_grupo (execucao_id, nivel);
CREATE INDEX IF NOT EXISTS ix_membro_municipio   ON validacao_grupo_membro (codigo_ibge, ano);
"""

UPSERT = """
INSERT INTO pam_municipio_ano
    (codigo_ibge, municipio, regiao, ano, rendimento_kg_ha, area_colhida_ha, producao_t, execucao_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (codigo_ibge, ano) DO UPDATE SET
    municipio = excluded.municipio,
    regiao = excluded.regiao,
    rendimento_kg_ha = excluded.rendimento_kg_ha,
    area_colhida_ha = excluded.area_colhida_ha,
    producao_t = excluded.producao_t,
    execucao_id = excluded.execucao_id
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path = config.DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def mark_interrupted(conn: sqlite3.Connection) -> int:
    """Execuções que ficaram 'em_andamento' (processo morto) passam a 'interrompida'."""
    with conn:
        cur = conn.execute("UPDATE execucao SET status = 'interrompida', mensagem = 'processo encerrado antes de concluir' "
                           "WHERE status = 'em_andamento'")
    return cur.rowcount


def start_execution(conn: sqlite3.Connection, fonte: str, data_coleta: str, arquivo: str, sha256: str,
                    data_execucao: str | None = None) -> int:
    """Abre uma execução. Sem `data_execucao`, usa o horário atual (_now), como sempre.

    Uma mesma chamada do pipeline que abre mais de uma execução passa o mesmo `data_execucao`
    (obtido uma única vez com _now) a todas elas: é o que a conferência C02/P02 do backfill exige.
    """
    with conn:
        cur = conn.execute(
            "INSERT INTO execucao (fonte, data_coleta, data_execucao, status, arquivo_origem, hash_sha256) "
            "VALUES (?, ?, ?, 'em_andamento', ?, ?)",
            (fonte, data_coleta, data_execucao if data_execucao is not None else _now(), arquivo, sha256),
        )
    return int(cur.lastrowid)


PLA05_SECAO_VERSOES = (1,)  # versões de report.secoes["pla05_grupos"] que esta gravação entende


def record_pla05_groups(conn: sqlite3.Connection, exec_id: int, report) -> int:
    """Grava os grupos da PLA-05 a partir de report.secoes["pla05_grupos"] (etapa 3b), sem recalcular nada.

    Usa exatamente os grupos, números, níveis, valores, totais e nomes da seção (a mesma do relatório JSON);
    os limites da faixa vêm de classificacao.niveis daquela execução. A produtividade em sc/ha é a coluna
    gerada do banco. Sem a seção (ex.: INMET) ou sem grupos (aprovada ou não verificada), não grava nada.
    Não faz commit (roda na transação do chamador). Devolve o número de grupos gravados.
    """
    secao = (getattr(report, "secoes", None) or {}).get("pla05_grupos")
    if not secao or not secao.get("grupos"):
        return 0
    if secao.get("versao") not in PLA05_SECAO_VERSOES:
        raise ValueError(f"formato de pla05_grupos não suportado: versao={secao.get('versao')!r}")
    regra = secao["regra"]
    limites = {n["nivel"]: (n["area_acima_de_ha"], n["area_ate_ha"]) for n in secao["classificacao"]["niveis"]}
    grupos = [(exec_id, regra, g["grupo"], g["ano"], g["nivel"], g["rendimento_kg_ha"], g["area_colhida_ha"],
               g["producao_t"], g["total_municipios"], g["total_registros"], *limites[g["nivel"]])
              for g in secao["grupos"]]
    membros = [(exec_id, regra, g["grupo"], g["ano"], m["codigo_ibge"], m["municipio"])
               for g in secao["grupos"] for m in g["municipios"]]
    conn.executemany(
        "INSERT INTO validacao_grupo (execucao_id, regra, grupo, ano, nivel, rendimento_kg_ha, area_colhida_ha, producao_t, "
        "total_municipios, total_registros, faixa_acima_de_ha, faixa_ate_ha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        grupos,
    )
    conn.executemany(
        "INSERT INTO validacao_grupo_membro (execucao_id, regra, grupo, ano, codigo_ibge, municipio) VALUES (?, ?, ?, ?, ?, ?)",
        membros,
    )
    return len(grupos)


def record_validation(conn: sqlite3.Connection, exec_id: int, report) -> None:
    """Grava o resultado de cada regra, cada registro apontado e os grupos da PLA-05.

    Não faz commit (roda na transação do chamador): ou entra tudo, ou nada. A regravação da mesma
    execução apaga primeiro membros, depois grupos, depois regras (as chaves usam ON DELETE RESTRICT;
    as ocorrências saem em cascata com as regras, como antes) e grava de novo, sem duplicar.
    """
    conn.execute("DELETE FROM validacao_grupo_membro WHERE execucao_id = ?", (exec_id,))
    conn.execute("DELETE FROM validacao_grupo WHERE execucao_id = ?", (exec_id,))
    conn.execute("DELETE FROM validacao_regra WHERE execucao_id = ?", (exec_id,))
    conn.executemany(
        "INSERT INTO validacao_regra (execucao_id, regra, nome, dimensao, severidade, status, registros_afetados, detalhe) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(exec_id, r.regra, r.nome, r.dimensao, r.severidade, r.status, r.afetados, r.detalhe or None) for r in report.regras],
    )
    conn.executemany(
        "INSERT INTO validacao_ocorrencia (execucao_id, regra, codigo_ibge, ano, detalhe) VALUES (?, ?, ?, ?, ?)",
        [(exec_id, r.regra, c, a, d) for r in report.regras for c, a, d in r.ocorrencias],
    )
    record_pla05_groups(conn, exec_id, report)


def fail_execution(conn: sqlite3.Connection, exec_id: int, message: str, report=None) -> None:
    """Marca a execução como falha. Se houver relatório de validação, grava as regras e ocorrências junto."""
    with conn:
        if report is not None:
            record_validation(conn, exec_id, report)
        conn.execute("UPDATE execucao SET status = 'falha', quantidade_registros = 0, mensagem = ? WHERE id = ?",
                     (message, exec_id))


def finish_execution(conn: sqlite3.Connection, exec_id: int, records: int, report: dict | None = None) -> None:
    """Fecha uma execução sem tabela de dados (ex.: localidades, que só enriquece a PAM)."""
    with conn:
        conn.execute("UPDATE execucao SET status = 'sucesso', quantidade_registros = ?, mensagem = ? WHERE id = ?",
                     (records, json.dumps(report, ensure_ascii=False) if report else None, exec_id))


def _none(v):
    return None if pd.isna(v) else v


def load_pam(conn: sqlite3.Connection, df: pd.DataFrame, exec_id: int, report) -> int:
    """Grava a PAM, roda as regras pós-gravação, registra a validação e conclui a execução
    na MESMA transação: ou entra tudo, ou nada. Se uma regra pós-gravação falhar, a transação
    é desfeita e ValidationError é lançada com o relatório completo."""
    from .validate import ValidationError, post_load_rules, preservation_rule
    rows = [
        (str(r.codigo_ibge), r.municipio, _none(r.regiao), int(r.ano),
         _none(r.rendimento_kg_ha), _none(r.area_colhida_ha), _none(r.producao_t), exec_id)
        for r in df.itertuples(index=False)
    ]
    with conn:  # BEGIN ... COMMIT; ROLLBACK automático em exceção
        preservacao = preservation_rule(conn, df)          # 1.0: antes de gravar, na mesma transação
        if preservacao is not None:
            report.add(preservacao)
            if not report.ok:
                raise ValidationError(report)
        conn.executemany(UPSERT, rows)
        for rule in post_load_rules(conn, exec_id):
            report.add(rule)
        if not report.ok:
            raise ValidationError(report)
        record_validation(conn, exec_id, report)
        conn.execute("UPDATE execucao SET status = 'sucesso', quantidade_registros = ?, mensagem = ? WHERE id = ?",
                     (len(rows), json.dumps(report.resumo(), ensure_ascii=False), exec_id))
    return len(rows)


def integrity_check(conn: sqlite3.Connection) -> dict:
    """Verificações pós-carga: chaves estrangeiras, duplicidade e linhas sem execução bem-sucedida."""
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    dup = conn.execute("SELECT COUNT(*) FROM (SELECT codigo_ibge, ano FROM pam_municipio_ano "
                       "GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0]
    orphan = conn.execute("SELECT COUNT(*) FROM pam_municipio_ano p JOIN execucao e ON e.id = p.execucao_id "
                          "WHERE e.status <> 'sucesso'").fetchone()[0]
    return {"chaves_estrangeiras_quebradas": len(fk), "duplicados": dup, "linhas_de_execucao_sem_sucesso": orphan}


UPSERT_INMET = """
INSERT INTO inmet_previsao
    (codigo_ibge, data_coleta, data_previsao, periodo, horizonte_dias, temp_min_c, temp_max_c,
     umidade_min_pct, umidade_max_pct, vento_intensidade, vento_direcao, resumo, execucao_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (codigo_ibge, data_coleta, data_previsao, periodo) DO UPDATE SET
    horizonte_dias = excluded.horizonte_dias, temp_min_c = excluded.temp_min_c, temp_max_c = excluded.temp_max_c,
    umidade_min_pct = excluded.umidade_min_pct, umidade_max_pct = excluded.umidade_max_pct,
    vento_intensidade = excluded.vento_intensidade, vento_direcao = excluded.vento_direcao,
    resumo = excluded.resumo, execucao_id = excluded.execucao_id
"""


def load_inmet(conn: sqlite3.Connection, df: pd.DataFrame, exec_id: int, data_coleta: str, report) -> int:
    """Grava a previsão e conclui a execução numa única transação (mesmas garantias da PAM)."""
    from .validate import ValidationError
    from .validate_inmet import post_load_rules_inmet
    rows = [
        (str(r.codigo_ibge), data_coleta, r.data_previsao, r.periodo, int(r.horizonte_dias),
         _none(r.temp_min_c), _none(r.temp_max_c), _none(r.umidade_min_pct), _none(r.umidade_max_pct),
         r.vento_intensidade, r.vento_direcao, r.resumo, exec_id)
        for r in df.itertuples(index=False)
    ]
    with conn:
        conn.executemany(UPSERT_INMET, rows)
        for rule in post_load_rules_inmet(conn, exec_id):
            report.add(rule)
        if not report.ok:
            raise ValidationError(report)
        record_validation(conn, exec_id, report)
        conn.execute("UPDATE execucao SET status = 'sucesso', quantidade_registros = ?, mensagem = ? WHERE id = ?",
                     (len(rows), json.dumps(report.resumo(), ensure_ascii=False), exec_id))
    return len(rows)
