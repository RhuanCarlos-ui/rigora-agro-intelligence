"""Orquestra o pipeline: Extração SIDRA -> Transformação (pandas) -> Validação -> SQLite.

Fontes independentes: uma falha no INMET não afeta o IBGE, e vice-versa. Cada fonte
tem sua própria execução, transação, validação e relatório.

Uso:
    python -m pipeline.run_pipeline                        # IBGE e INMET, coleta ao vivo
    python -m pipeline.run_pipeline --fontes inmet         # só uma fonte (sidra | inmet)
    python -m pipeline.run_pipeline --sem-coleta           # reprocessa as coletas mais recentes de data/raw/
    python -m pipeline.run_pipeline --sem-coleta --pam ARQ --localidades ARQ --inmet ARQ [--db CAMINHO]

Uma execução que falha fica registrada como 'falha' na tabela execucao, com a
mensagem de erro, e nenhum dado dela é gravado.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, load_sqlite
from .extract_sidra import extract, validate_localidades_payload, validate_pam_payload
from .http import SourceError
from .transform import _latest, attach_localidades, localidades_to_frame, mg_totals, pam_rows_to_frame
from .validate import ValidationError, to_storage_frame, validate_pam

log = logging.getLogger(__name__)

FONTE_PAM = "IBGE/SIDRA PAM 1613 (café arábica, MG)"
FONTE_LOC = "IBGE/Localidades (municípios de MG)"
FONTE_INMET = f"INMET/Previsão do tempo ({config.INMET_MUNICIPIO_NOME}, {config.INMET_MUNICIPIO})"


class PipelineError(RuntimeError):
    pass


@dataclass
class RawFile:
    path: Path
    meta: dict

    @classmethod
    def open(cls, path: Path) -> "RawFile":
        meta_path = path.with_suffix(".meta.json")
        if not meta_path.exists():
            raise PipelineError(f"{path.name} não tem metadados ({meta_path.name}). Gere a coleta com: python -m pipeline.extract_sidra")
        return cls(path, json.loads(meta_path.read_text(encoding="utf-8")))

    def actual_hash(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def write_quality_report(exec_id: int, report, raw: "RawFile", status: str,
                         reports_dir: Path | None = None, fonte: str = FONTE_PAM) -> Path:
    """Relatório de qualidade da execução em JSON (data/reports/), com a origem do dado."""
    reports_dir = reports_dir or config.DB_REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"qualidade_execucao_{exec_id:04d}.json"
    doc = {"execucao_id": exec_id, "status_execucao": status, "fonte": fonte,
           "data_coleta": raw.meta.get("collected_at"), "url": raw.meta.get("url"),
           "arquivo_bruto": raw.path.name, "hash_sha256": raw.meta.get("sha256"), **report.as_dict(),
           **getattr(report, "secoes", {})}  # seções extras (ex.: pla05_grupos) só no arquivo
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def process(pam_path: Path, loc_path: Path, db_path: Path = config.DB_PATH,
            processed_dir: Path = config.PROCESSED_DIR) -> dict:
    pam_raw, loc_raw = RawFile.open(pam_path), RawFile.open(loc_path)
    conn = load_sqlite.connect(db_path)
    try:
        interrupted = load_sqlite.mark_interrupted(conn)
        if interrupted:
            log.warning("%d execução(ões) anteriores estavam em andamento e foram marcadas como interrompidas", interrupted)
        # Um único horário para as duas execuções da mesma chamada: sem isso, a virada de um segundo entre as
        # duas gravações dava horários diferentes e a conferência C02/P02 do backfill recusava o par.
        agora = load_sqlite._now()
        loc_id = load_sqlite.start_execution(conn, FONTE_LOC, loc_raw.meta["collected_at"], loc_raw.path.name, loc_raw.meta["sha256"],
                                             data_execucao=agora)
        pam_id = load_sqlite.start_execution(conn, FONTE_PAM, pam_raw.meta["collected_at"], pam_raw.path.name, pam_raw.meta["sha256"],
                                             data_execucao=agora)
        try:
            for raw in (loc_raw, pam_raw):
                if raw.actual_hash() != raw.meta["sha256"]:
                    raise PipelineError(f"{raw.path.name} foi alterado depois da coleta (hash diferente do registrado)")
            loc_payload = validate_localidades_payload(json.loads(loc_raw.path.read_text(encoding="utf-8")))
            rows = validate_pam_payload(json.loads(pam_raw.path.read_text(encoding="utf-8")))
            sidra = pam_rows_to_frame(rows)
            sidra_names = sidra.drop_duplicates("cod_ibge").set_index("cod_ibge")["municipio"].to_dict()
            loc = localidades_to_frame(loc_payload)
            pam = attach_localidades(sidra, loc)
            df = to_storage_frame(pam)
            report = validate_pam(df, localidades=loc, sidra_names=sidra_names, raw_rows=rows)
            if not report.ok:
                raise ValidationError(report)
            for w in report.warnings:
                log.warning("validação: %s", w)

            stored = load_sqlite.load_pam(conn, df, pam_id, report)
            load_sqlite.finish_execution(conn, loc_id, len(loc_payload))

            # CSVs só depois de os dados passarem na validação e entrarem no banco
            processed_dir.mkdir(parents=True, exist_ok=True)
            pam.to_csv(processed_dir / "pam_mg_arabica.csv", index=False, encoding="utf-8")
            mg_totals(pam).to_csv(processed_dir / "pam_mg_totais.csv", index=False, encoding="utf-8")
        except Exception as exc:  # registra a falha (com o relatório de qualidade, se houver) e propaga
            msg = f"{type(exc).__name__}: {exc}"
            failed_report = exc.report if isinstance(exc, ValidationError) else None
            for eid in (pam_id, loc_id):
                status = conn.execute("SELECT status FROM execucao WHERE id = ?", (eid,)).fetchone()[0]
                if status == "em_andamento":
                    load_sqlite.fail_execution(conn, eid, msg, failed_report if eid == pam_id else None)
            if failed_report is not None:
                write_quality_report(pam_id, failed_report, pam_raw, "falha", Path(db_path).parent / "reports")
            raise
        report_path = write_quality_report(pam_id, report, pam_raw, "sucesso", Path(db_path).parent / "reports")
        checks = load_sqlite.integrity_check(conn)
        total = conn.execute("SELECT COUNT(*) FROM pam_municipio_ano").fetchone()[0]
        return {"execucao_pam": pam_id, "execucao_localidades": loc_id, "gravados": stored,
                "total_no_banco": total, "validacao": report.as_dict(), "integridade": checks,
                "relatorio_qualidade": str(report_path)}
    finally:
        conn.close()


def process_inmet(path: Path, db_path: Path = config.DB_PATH, processed_dir: Path = config.PROCESSED_DIR,
                  now: datetime | None = None) -> dict:
    """Bruto do INMET -> pandas -> validação -> SQLite, numa execução própria."""
    from .extract_inmet import validate_inmet_payload
    from .transform_inmet import inmet_to_frame
    from .validate_inmet import dedupe_identical, validate_inmet

    raw = RawFile.open(path)
    conn = load_sqlite.connect(db_path)
    try:
        load_sqlite.mark_interrupted(conn)
        exec_id = load_sqlite.start_execution(conn, FONTE_INMET, raw.meta["collected_at"], raw.path.name, raw.meta["sha256"])
        try:
            if raw.actual_hash() != raw.meta["sha256"]:
                raise PipelineError(f"{raw.path.name} foi alterado depois da coleta (hash diferente do registrado)")
            raw_bytes = raw.path.read_bytes()
            city = validate_inmet_payload(json.loads(raw_bytes.decode("utf-8")), config.INMET_MUNICIPIO)
            df, removed = dedupe_identical(inmet_to_frame(city, config.INMET_MUNICIPIO))
            report = validate_inmet(df, collected_at=raw.meta["collected_at"], city=city, raw=raw_bytes,
                                    identical_removed=removed, now=now)
            if not report.ok:
                raise ValidationError(report)
            for w in report.warnings:
                log.warning("validação INMET: %s", w)
            stored = load_sqlite.load_inmet(conn, df, exec_id, raw.meta["collected_at"], report)
            processed_dir.mkdir(parents=True, exist_ok=True)
            df.drop(columns=["data_bruta"]).assign(data_coleta=raw.meta["collected_at"]).to_csv(
                processed_dir / "inmet_previsao.csv", index=False, encoding="utf-8")
        except Exception as exc:
            failed = exc.report if isinstance(exc, ValidationError) else None
            if conn.execute("SELECT status FROM execucao WHERE id = ?", (exec_id,)).fetchone()[0] == "em_andamento":
                load_sqlite.fail_execution(conn, exec_id, f"{type(exc).__name__}: {exc}", failed)
            if failed is not None:
                write_quality_report(exec_id, failed, raw, "falha", Path(db_path).parent / "reports", FONTE_INMET)
            raise
        path_rep = write_quality_report(exec_id, report, raw, "sucesso", Path(db_path).parent / "reports", FONTE_INMET)
        total = conn.execute("SELECT COUNT(*) FROM inmet_previsao").fetchone()[0]
        return {"execucao_inmet": exec_id, "gravados": stored, "total_no_banco": total,
                "validacao": report.as_dict(), "relatorio_qualidade": str(path_rep)}
    finally:
        conn.close()


def run_all(fontes: list[str], sem_coleta: bool, db_path: Path, pam: Path | None = None,
            localidades: Path | None = None, inmet: Path | None = None,
            processed_dir: Path = config.PROCESSED_DIR) -> dict:
    """Roda cada fonte isoladamente; a falha de uma não interrompe nem desfaz a outra.

    `processed_dir` é onde os CSVs são gravados (padrão: data/processed); testes passam uma pasta temporária.
    """
    out: dict = {}
    if "sidra" in fontes:
        try:
            if sem_coleta:
                pam_path = pam or _latest("sidra_pam_1613_mg_arabica_*.json")
                loc_path = localidades or _latest("ibge_localidades_mg_*.json")
            else:
                pam_meta, loc_meta = extract()
                pam_path, loc_path = config.ROOT / pam_meta.path, config.ROOT / loc_meta.path
            out["sidra"] = {"status": "sucesso", **process(pam_path, loc_path, db_path, processed_dir)}
        except (SourceError, PipelineError, ValidationError, FileNotFoundError, OSError) as exc:
            log.error("IBGE/SIDRA: %s", exc)
            out["sidra"] = {"status": "falha", "erro": f"{type(exc).__name__}: {exc}"}
    if "inmet" in fontes:
        from .extract_inmet import extract as extract_inmet
        try:
            if sem_coleta:
                inmet_path = inmet or _latest(f"inmet_previsao_{config.INMET_MUNICIPIO}_*.json")
            else:
                meta = extract_inmet()
                inmet_path = config.ROOT / meta.path
            out["inmet"] = {"status": "sucesso", **process_inmet(inmet_path, db_path, processed_dir)}
        except (SourceError, PipelineError, ValidationError, FileNotFoundError, OSError) as exc:
            log.error("INMET: %s", exc)
            out["inmet"] = {"status": "falha", "erro": f"{type(exc).__name__}: {exc}"}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sem-coleta", action="store_true", help="não consulta o IBGE; reprocessa arquivos de data/raw/")
    p.add_argument("--pam", type=Path)
    p.add_argument("--localidades", type=Path)
    p.add_argument("--inmet", type=Path, help="JSON bruto do INMET (com --sem-coleta)")
    p.add_argument("--fontes", default="sidra,inmet", help="sidra, inmet ou sidra,inmet (padrão)")
    p.add_argument("--db", type=Path, default=config.DB_PATH)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fontes = [f.strip() for f in args.fontes.split(",") if f.strip()]
    result = run_all(fontes, args.sem_coleta, args.db, args.pam, args.localidades, args.inmet)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if all(r["status"] == "sucesso" for r in result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
