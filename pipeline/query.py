"""Consultas prontas ao banco da Rigora.

Uso:
    python -m pipeline.query historico                 # execuções, da mais recente para a mais antiga
    python -m pipeline.query municipio 3148103         # série de um município
    python -m pipeline.query ranking 2025 --top 10     # maiores produtividades do ano
    python -m pipeline.query qualidade [ID]            # regras de qualidade de uma execução (padrão: última da PAM)
    python -m pipeline.query rastrear 3148103 --ano 2025   # origem e validações de um registro
    python -m pipeline.query previsao                  # previsão do INMET mais recente (com a data da coleta)

Grupos da PLA-05 gravados por execução (etapa 3c; somente leitura, sem recalcular):
    python -m pipeline.query grupos [EXECUCAO] [--nivel N]   # grupos de uma execução (padrão: última do SIDRA com sucesso e grupos)
    python -m pipeline.query grupo EXECUCAO GRUPO            # resumo de um grupo e seus municípios
    python -m pipeline.query grupos_municipio 3148103        # todos os grupos do município, em todas as execuções
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from . import config

PLA05_TABELAS = ("validacao_grupo", "validacao_grupo_membro")
PLA05_NIVEIS = ("informativo", "atencao", "revisao_documental")
SEM_TABELAS_PLA05 = ("Este banco ainda não tem as tabelas de grupos da PLA-05 (validacao_grupo e validacao_grupo_membro). "
                     "Elas são criadas na próxima execução do pipeline com a versão atual, e os grupos de execuções antigas "
                     "podem ser preenchidos com python -m pipeline.backfill_pla05 (ver pipeline/README.md); faça um backup do banco antes. "
                     "Nada foi alterado.")


class ConsultaError(RuntimeError):
    """Consulta que não pode ser respondida por este banco (mensagem para o usuário, não erro técnico)."""


def _num_sql(col: str) -> str:
    """Número gravado como texto, sem casas desnecessárias (500.0 -> '500'; 10.5 -> '10.5')."""
    return f"(CASE WHEN {col} = CAST({col} AS INTEGER) THEN CAST(CAST({col} AS INTEGER) AS TEXT) ELSE CAST({col} AS TEXT) END)"


# Faixa como texto, a partir dos limites gravados naquela execução (não recalcula o nível).
_FAIXA = f"""CASE
            WHEN g.faixa_acima_de_ha IS NULL AND g.faixa_ate_ha IS NOT NULL THEN 'até ' || {_num_sql('g.faixa_ate_ha')} || ' ha'
            WHEN g.faixa_acima_de_ha IS NOT NULL AND g.faixa_ate_ha IS NOT NULL
                 THEN {_num_sql('g.faixa_acima_de_ha')} || '–' || {_num_sql('g.faixa_ate_ha')} || ' ha'
            WHEN g.faixa_acima_de_ha IS NOT NULL THEN 'acima de ' || {_num_sql('g.faixa_acima_de_ha')} || ' ha'
            ELSE 'sem limite' END"""
_VALORES = "g.rendimento_kg_ha, round(g.produtividade_sc_ha, 2) AS sc_ha, g.area_colhida_ha, g.producao_t"

QUERIES = {
    "historico": """
        SELECT id, fonte, data_coleta, data_execucao, status, quantidade_registros, arquivo_origem,
               substr(hash_sha256, 1, 12) AS hash
        FROM execucao ORDER BY id DESC""",
    "municipio": """
        SELECT p.ano, p.municipio, p.rendimento_kg_ha, round(p.produtividade_sc_ha, 1) AS sc_ha,
               p.area_colhida_ha, p.producao_t, p.execucao_id, e.data_coleta
        FROM pam_municipio_ano p JOIN execucao e ON e.id = p.execucao_id
        WHERE p.codigo_ibge = ? ORDER BY p.ano""",
    "ranking": """
        SELECT municipio, regiao, round(produtividade_sc_ha, 1) AS sc_ha, area_colhida_ha
        FROM pam_municipio_ano
        WHERE ano = ? AND produtividade_sc_ha IS NOT NULL
        ORDER BY produtividade_sc_ha DESC LIMIT ?""",
    "qualidade": """
        SELECT regra, dimensao, severidade, status, registros_afetados, detalhe
        FROM validacao_regra
        WHERE execucao_id = COALESCE(?, (SELECT max(id) FROM execucao WHERE fonte LIKE 'IBGE/SIDRA%'))
        ORDER BY CASE severidade WHEN 'erro' THEN 0 WHEN 'aviso' THEN 1 ELSE 2 END, regra""",
    "previsao": """
        SELECT p.data_previsao, p.periodo, p.horizonte_dias, p.temp_min_c, p.temp_max_c,
               p.umidade_min_pct, p.umidade_max_pct, p.vento_intensidade, p.resumo,
               p.data_coleta, p.execucao_id
        FROM inmet_previsao p
        WHERE p.data_coleta = (SELECT max(data_coleta) FROM inmet_previsao WHERE codigo_ibge = ?)
          AND p.codigo_ibge = ?
        ORDER BY p.data_previsao, CASE p.periodo WHEN 'manha' THEN 0 WHEN 'tarde' THEN 1 WHEN 'noite' THEN 2 ELSE 3 END""",
    "rastrear": """
        SELECT p.codigo_ibge, p.municipio, p.ano, p.rendimento_kg_ha, round(p.produtividade_sc_ha, 2) AS sc_ha,
               e.id AS execucao_id, e.fonte, e.data_coleta, e.arquivo_origem, substr(e.hash_sha256, 1, 12) AS hash,
               (SELECT count(*) FROM validacao_regra v WHERE v.execucao_id = e.id AND v.status <> 'nao_verificada') AS regras_executadas,
               (SELECT group_concat(o.regra || ': ' || o.detalhe, ' | ') FROM validacao_ocorrencia o
                 WHERE o.execucao_id = e.id AND o.codigo_ibge = p.codigo_ibge AND (o.ano = p.ano OR o.ano IS NULL)) AS ocorrencias
        FROM pam_municipio_ano p JOIN execucao e ON e.id = p.execucao_id
        WHERE p.codigo_ibge = ? AND p.ano = ?""",
    # --- PLA-05 (etapa 3c): só tabelas de grupos + execucao; nenhum JOIN com pam_municipio_ano ---
    "grupos": f"""
        SELECT g.execucao_id, e.status AS status_execucao, g.regra, g.grupo, g.ano, g.nivel, {_FAIXA} AS faixa,
               {_VALORES}, g.total_municipios, g.total_registros
        FROM validacao_grupo g JOIN execucao e ON e.id = g.execucao_id
        WHERE g.execucao_id = COALESCE(?, (SELECT max(g2.execucao_id) FROM validacao_grupo g2
                                           JOIN execucao e2 ON e2.id = g2.execucao_id
                                           WHERE e2.status = 'sucesso' AND e2.fonte LIKE 'IBGE/SIDRA%'))
          AND (? IS NULL OR g.nivel = ?)
        ORDER BY g.grupo""",
    "grupo": f"""
        SELECT g.execucao_id, e.data_coleta, e.status AS status_execucao, g.regra, g.grupo, g.ano, g.nivel,
               {_FAIXA} AS faixa, {_VALORES}, g.total_municipios, g.total_registros
        FROM validacao_grupo g JOIN execucao e ON e.id = g.execucao_id
        WHERE g.execucao_id = ? AND g.grupo = ?""",
    "grupo_membros": """
        SELECT m.codigo_ibge, m.municipio
        FROM validacao_grupo_membro m
        WHERE m.execucao_id = ? AND m.grupo = ?
        ORDER BY m.codigo_ibge""",
    "grupos_municipio": f"""
        SELECT g.execucao_id, e.data_coleta, e.status AS status_execucao, g.grupo, g.ano, g.nivel, {_FAIXA} AS faixa,
               m.municipio, {_VALORES}, g.total_municipios
        FROM validacao_grupo_membro m
        JOIN validacao_grupo g USING (execucao_id, regra, grupo, ano)
        JOIN execucao e ON e.id = g.execucao_id
        WHERE m.codigo_ibge = ?
        ORDER BY g.execucao_id DESC, g.ano, g.grupo""",
}
PLA05_QUERIES = {"grupos", "grupo", "grupo_membros", "grupos_municipio"}


def run(name: str, params: tuple = (), db_path: Path = config.DB_PATH) -> list[sqlite3.Row]:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"banco não encontrado em {db_path}. Rode: python -m pipeline.run_pipeline")
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)  # somente leitura
    conn.row_factory = sqlite3.Row
    try:
        if name in PLA05_QUERIES:
            existentes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not set(PLA05_TABELAS) <= existentes:
                raise ConsultaError(SEM_TABELAS_PLA05)
        return conn.execute(QUERIES[name], params).fetchall()
    finally:
        conn.close()


def _print(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("(nenhum registro)")
        return
    cols = rows[0].keys()
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join("" if r[c] is None else str(r[c]) for c in cols))


def _inteiro_positivo(p: argparse.ArgumentParser, valor: str | None, nome: str) -> int:
    if valor is None or not re.fullmatch(r"[1-9][0-9]*", valor):
        p.error(f"{nome} precisa ser um número inteiro positivo (recebido: {valor!r})")
    return int(valor)


def _pla05(p: argparse.ArgumentParser, a: argparse.Namespace) -> int:
    """Consultas de grupos da PLA-05 (etapa 3c). Somente leitura; mensagens claras quando não há resultado."""
    if a.consulta == "grupos":
        execucao = _inteiro_positivo(p, a.parametro, "EXECUCAO") if a.parametro else None
        rows = run("grupos", (execucao, a.nivel, a.nivel), a.db)
        if not rows:
            filtro = f" com nível {a.nivel}" if a.nivel else ""
            alvo = (f"na execução {execucao}" if execucao else
                    "na execução padrão (a mais recente do IBGE/SIDRA com sucesso que tem grupos gravados; "
                    "pode ser que nenhuma tenha)")
            print(f"Nenhum grupo PLA-05{filtro} {alvo}.")
            return 1
        _print(rows)
        return 0
    if a.consulta == "grupo":
        execucao = _inteiro_positivo(p, a.parametro, "EXECUCAO")
        grupo = _inteiro_positivo(p, a.parametro2, "GRUPO")
        resumo = run("grupo", (execucao, grupo), a.db)
        if not resumo:
            print(f"Grupo {grupo} não encontrado na execução {execucao}.")
            return 1
        membros = run("grupo_membros", (execucao, grupo), a.db)
        print("Grupo")
        _print(resumo)
        print(f"\nMunicípios ({len(membros)})")
        _print(membros)
        return 0
    codigo = a.parametro or ""
    if not re.fullmatch(r"[0-9]{7}", codigo):
        p.error(f"CODIGO_IBGE precisa ter 7 dígitos (recebido: {a.parametro!r})")
    rows = run("grupos_municipio", (codigo,), a.db)
    if not rows:
        print(f"O município {codigo} não aparece em nenhum grupo PLA-05 gravado neste banco.")
        return 1
    _print(rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("consulta", choices=[q for q in QUERIES if q != "grupo_membros"])
    p.add_argument("parametro", nargs="?")
    p.add_argument("parametro2", nargs="?", help="segundo parâmetro (GRUPO em: grupo EXECUCAO GRUPO)")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--ano", type=int, default=max(config.YEARS))
    p.add_argument("--nivel", choices=PLA05_NIVEIS, help="filtro de grupos: informativo, atencao ou revisao_documental")
    p.add_argument("--db", type=Path, default=config.DB_PATH)
    a = p.parse_args(argv)
    if a.consulta in PLA05_QUERIES:
        try:
            return _pla05(p, a)
        except ConsultaError as exc:
            print(exc, file=sys.stderr)
            return 1
    params: tuple = ()
    if a.consulta == "municipio":
        params = (a.parametro or config.PATROCINIO,)
    elif a.consulta == "ranking":
        params = (int(a.parametro or max(config.YEARS)), a.top)
    elif a.consulta == "qualidade":
        params = (int(a.parametro) if a.parametro else None,)
    elif a.consulta == "previsao":
        params = (a.parametro or config.INMET_MUNICIPIO,) * 2
    elif a.consulta == "rastrear":
        params = (a.parametro or config.PATROCINIO, a.ano)
    _print(run(a.consulta, params, a.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
