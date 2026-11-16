"""Publicação protegida do js/snapshots.js (Bloco 3).

Uso (um modo por execução):
    python -m pipeline.publicar
        A. SIMULAÇÃO (padrão). Não acessa a internet, não altera o banco e não toca no js/snapshots.js.
           Gera o snapshot a partir de uma cópia temporária do banco, valida, ensaia o backup e a
           substituição numa cópia temporária do destino e mostra o plano de uma publicação real.
    python -m pipeline.publicar --coletar [--fontes sidra,inmet]
        B. COLETA REAL no banco definitivo (IBGE e INMET), com backup verificado do banco antes.
           Depois, a mesma simulação. Nunca publica.
    python -m pipeline.publicar --publicar --confirmar-atual HASH [--aceitar-sem-inmet]
        C. PUBLICAÇÃO a partir dos dados que já estão no banco, sem nova coleta.
    python -m pipeline.publicar --coletar --publicar --confirmar-atual HASH [--aceitar-sem-inmet]
        B + C. Coleta e publica.
    python -m pipeline.publicar --restaurar PASTA_DO_BACKUP --confirmar-atual HASH
        Recupera o snapshot guardado num backup de publicação.

HASH é o SHA-256 (ou os 12 primeiros caracteres) do js/snapshots.js publicado AGORA, mostrado pela
simulação. Sem --confirmar-atual, --publicar e --restaurar preparam e validam tudo, mostram o plano
e param sem alterar nada. Sem previsão do INMET válida, publicar exige --aceitar-sem-inmet.

Etapas separadas: coleta (pipeline.run_pipeline) -> exportação (pipeline.export_snapshots, numa pasta
temporária) -> verificação (contrato do exportador, procedência contra o banco e os brutos, atualidade
do INMET, verificador B5 em tests/check_exported_snapshot.js) -> publicação (backup verificado,
arquivo temporário na mesma pasta do destino, substituição atômica, nova verificação e, se ela
falhar, restauração do backup com o hash conferido).

Códigos de saída: 0 concluído (simulação aprovada, publicação ou restauração feitas); 1 reprovado ou
falha (o destino ficou como estava ou foi restaurado, com hash conferido); 2 incompleto (falta
requisito ou confirmação; nada foi alterado); 3 uso inválido; 4 FALHA GRAVE: a restauração não pôde
ser confirmada (o relatório diz onde está o backup e como restaurar à mão).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import backup_dados, config, referencias
from . import export_snapshots as E

FUSO_VERIFICACAO = "America/Sao_Paulo"     # o exportador decide "hoje" pelo horário de Brasília
TOLERANCIA_RELOGIO = timedelta(minutes=5)  # mesma tolerância da M1 (js/metrics.js)
TENTATIVAS_SUBSTITUICAO = 5                # Windows: antivírus/indexador/editor podem segurar o arquivo
ESPERA_TENTATIVA_S = 0.5
MARGEM_ESPACO = 1 << 20                    # 1 MiB além do tamanho do arquivo
TIMEOUT_VERIFICADOR_S = 300
CONCLUIDO, REPROVADO, INCOMPLETO, USO, GRAVE = 0, 1, 2, 3, 4
OK, FALHA, AVISO, INFO = "ok", "FALHA", "aviso", "info"


class Parada(Exception):
    """Interrompe o fluxo com um código e um resultado. Nunca é tratada como autorização para seguir."""

    def __init__(self, codigo: int, resultado: str, motivo: str):
        super().__init__(motivo)
        self.codigo, self.resultado, self.motivo = codigo, resultado, motivo


class Uso(Exception):
    pass


# =============================================================================
# Caminhos, opções e relatório
# =============================================================================
@dataclass
class Caminhos:
    raiz: Path
    banco: Path
    destino: Path
    bruto: Path
    referencia: Path
    verificador: Path
    backups: Path

    @classmethod
    def padrao(cls, raiz: Path = config.ROOT, backups: Path | None = None, referencia: Path | None = None) -> "Caminhos":
        raiz = Path(raiz).resolve()
        if referencia:
            ref = Path(referencia).resolve()
        elif (raiz / referencias.REGISTRO).exists():
            ref = referencias.ativa(raiz)               # referência B5 ativa e aprovada, com hash conferido
        else:
            ref = raiz / referencias.HISTORICA
        return cls(raiz=raiz, banco=raiz / "data" / "rigora.db", destino=raiz / "js" / "snapshots.js",
                   bruto=raiz / "data" / "raw",
                   referencia=ref,
                   verificador=raiz / "tests" / "check_exported_snapshot.js",
                   backups=Path(backups).resolve() if backups else raiz.parent / "rigora_backups" / "publicacoes")


@dataclass
class Opcoes:
    coletar: bool = False
    publicar: bool = False
    restaurar: Path | None = None
    confirmar_atual: str | None = None
    aceitar_sem_inmet: bool = False
    fontes: tuple = ("sidra", "inmet")
    trabalho: Path | None = None


@dataclass
class Relatorio:
    modo: str
    inicio: str
    etapas: list = field(default_factory=list)
    dados: dict = field(default_factory=dict)
    resultado: str = ""
    motivo: str = ""
    codigo: int = INCOMPLETO
    saida: object = None

    def etapa(self, nome: str, status: str, detalhe: str = "") -> None:
        self.etapas.append({"etapa": nome, "status": status, "detalhe": detalhe})
        if self.saida:
            self.saida(f"  {status:<6}{nome}: {detalhe}")

    def como_dict(self) -> dict:
        return {"modo": self.modo, "inicio": self.inicio, "resultado": self.resultado, "codigo": self.codigo,
                "motivo": self.motivo, "etapas": self.etapas, "dados": self.dados}

    def texto(self) -> str:
        linhas = [f"Publicação protegida do Rigora · modo: {self.modo} · início {self.inicio}"]
        linhas += [f"  {e['status']:<6}{e['etapa']}: {e['detalhe']}" for e in self.etapas]
        linhas.append(f"Resultado: {self.resultado}" + (f" — {self.motivo}" if self.motivo else ""))
        return "\n".join(linhas) + "\n"


def descrever_modo(op: Opcoes) -> str:
    if op.restaurar:
        return "RESTAURAÇÃO de backup"
    if op.coletar and op.publicar:
        return "COLETA REAL + PUBLICAÇÃO"
    if op.publicar:
        return "PUBLICAÇÃO (dados já existentes no banco, sem nova coleta)"
    if op.coletar:
        return "COLETA REAL + simulação da publicação (não publica)"
    return "SIMULAÇÃO (padrão: sem coleta, sem publicação)"


# =============================================================================
# Arquivos: hash, cópia verificada, temporário, substituição
# =============================================================================
def sha256_arquivo(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def sha256_ou_none(p: Path) -> str | None:
    return sha256_arquivo(p) if Path(p).is_file() else None


def dentro(filho: Path, pai: Path) -> bool:
    filho, pai = Path(filho).resolve(), Path(pai).resolve()
    return filho == pai or pai in filho.parents


def copiar_com_hash(origem: Path, destino: Path, esperado: str) -> str:
    """Copia byte a byte para um arquivo NOVO (nunca sobrescreve), grava no disco (fsync) e confere o hash."""
    with open(origem, "rb") as fi, open(destino, "xb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
        fo.flush()
        os.fsync(fo.fileno())
    h = sha256_arquivo(destino)
    if h != esperado:
        raise OSError(f"cópia {destino} com hash divergente ({h[:12]} ≠ {esperado[:12]})")
    return h


def estagio_de(destino: Path) -> Path:
    """Arquivo temporário da publicação: oculto, na MESMA pasta do destino (mesmo sistema de arquivos)."""
    return destino.with_name(f".{destino.name}.publicando.tmp")


def gravar_estagio(destino: Path, conteudo: bytes) -> Path:
    """Cria o temporário (falha se já existir: outra publicação em andamento ou interrompida) e grava no disco."""
    est = estagio_de(destino)
    f = open(est, "xb")                             # FileExistsError: o arquivo de outro processo não é tocado
    try:
        with f:
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _remover_estagio(est)                       # só o que este processo acabou de criar
        raise
    return est


def _remover_estagio(est: Path) -> None:
    try:
        if est.exists():
            est.unlink()
    except OSError:
        pass


def sincronizar_pasta(pasta: Path) -> None:
    """Grava no disco a entrada da pasta após a troca (POSIX). No Windows não há equivalente: ignorado."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(pasta), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _trocar(origem: Path, destino: Path) -> None:
    """A troca em si: os.replace (atômico na mesma pasta, no Windows e no Linux)."""
    os.replace(origem, destino)


def substituir(estagio: Path, destino: Path) -> int:
    """os.replace com novas tentativas quando o Windows recusa por arquivo em uso. Devolve as tentativas."""
    for i in range(1, TENTATIVAS_SUBSTITUICAO + 1):
        try:
            _trocar(estagio, destino)
            sincronizar_pasta(destino.parent)
            return i
        except PermissionError:
            if i == TENTATIVAS_SUBSTITUICAO:
                raise
            time.sleep(ESPERA_TENTATIVA_S * i)
    return TENTATIVAS_SUBSTITUICAO


def conferir_gravavel(destino: Path) -> None:
    if not destino.parent.is_dir():
        raise PermissionError(f"pasta do destino não existe: {destino.parent}")
    if not os.access(destino.parent, os.W_OK):
        raise PermissionError(f"sem permissão de escrita na pasta {destino.parent}")
    if destino.exists():
        with open(destino, "r+b"):                  # Windows: falha se somente leitura ou bloqueado por outro programa
            pass


def conferir_espaco(pasta: Path, necessario: int) -> None:
    livre = shutil.disk_usage(pasta).free
    if livre < necessario + MARGEM_ESPACO:
        raise OSError(f"espaço insuficiente em {pasta}: {livre} bytes livres, necessários {necessario + MARGEM_ESPACO}")


def nova_pasta(base: Path, tipo: str, agora: datetime) -> Path:
    """Pasta nova e única (nunca reaproveita nem sobrescreve um backup)."""
    base.mkdir(parents=True, exist_ok=True)
    nome = f"{agora.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}_{tipo}"
    for n in range(1, 1000):
        p = base / (nome if n == 1 else f"{nome}_{n}")
        try:
            p.mkdir()
            return p
        except FileExistsError:
            continue
    raise OSError(f"não foi possível criar uma pasta nova em {base}")


# =============================================================================
# Verificação do snapshot
# =============================================================================
def _data(ts) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


def conferir_inmet(snap: dict, agora: datetime) -> tuple[bool, str]:
    """Regra de atualidade no instante `agora` (mesmos limites da M1 e do exportador: até 24 h, inclusive)."""
    i = snap.get("inmet")
    if i is None:
        motivo = ((snap.get("exportacao") or {}).get("inmet") or {}).get("motivo") or "sem motivo registrado"
        return True, f"sem previsão no arquivo: {motivo}"
    col = _data(i.get("collectedAt")) if isinstance(i, dict) else None
    if col is None:
        return False, "previsão sem data de coleta válida"
    if col - agora > TOLERANCIA_RELOGIO:
        return False, f"data de coleta da previsão no futuro ({i['collectedAt']})"
    idade = (agora - col).total_seconds() / 3600
    if idade > config.INMET_MAX_IDADE_HORAS:
        return False, f"previsão coletada há {idade:.1f} h, acima do limite de {config.INMET_MAX_IDADE_HORAS} h: não pode ser apresentada como atual"
    hoje = agora.astimezone(E.BRASILIA).date()
    for dias in (i.get("data") or {}).values():
        for d in (dias or {}):
            try:
                dia = datetime.strptime(d, "%d/%m/%Y").date()
            except (TypeError, ValueError):
                return False, f"previsão com data inválida: {d}"
            if dia < hoje:
                return False, f"previsão com dia que já passou: {d}"
    return True, f"previsão coletada há {idade:.1f} h (limite {config.INMET_MAX_IDADE_HORAS} h)"


def conferir_procedencia(snap: dict, banco: Path, bruto: Path) -> list[str]:
    """A seção exportacao bate com o banco (execução, status, data, hash) e com os arquivos brutos?"""
    p = []
    ex = snap.get("exportacao") or {}
    if ex.get("geradoPor") != "python -m pipeline.export_snapshots":
        p.append("exportacao.geradoPor não é o exportador do pipeline")
    gerado = _data(ex.get("geradoEm"))
    if gerado is None:
        p.append("exportacao.geradoEm não é data ISO com fuso")
    conn = sqlite3.connect(f"file:{Path(banco).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for fonte in ("sidra", "inmet"):
            info = ex.get(fonte)
            if not isinstance(info, dict):
                p.append(f"exportacao.{fonte} ausente")
                continue
            if "execucaoId" not in info:
                if fonte == "sidra":
                    p.append("exportacao.sidra sem execucaoId")
                continue
            row = conn.execute("SELECT * FROM execucao WHERE id = ?", (info["execucaoId"],)).fetchone()
            if row is None:
                p.append(f"exportacao.{fonte}.execucaoId {info['execucaoId']} não existe no banco")
                continue
            if row["status"] != "sucesso" or info.get("statusExecucao") != row["status"]:
                p.append(f"exportacao.{fonte}: execução {row['id']} com status {row['status']!r} (arquivo diz {info.get('statusExecucao')!r})")
            if info.get("dataColeta") != row["data_coleta"]:
                p.append(f"exportacao.{fonte}.dataColeta ({info.get('dataColeta')}) diferente do banco ({row['data_coleta']})")
            if info.get("hashSha256") != row["hash_sha256"]:
                p.append(f"exportacao.{fonte}.hashSha256 diferente do registrado no banco")
            if info.get("arquivoBruto") != row["arquivo_origem"]:
                p.append(f"exportacao.{fonte}.arquivoBruto diferente do registrado no banco")
            arq = Path(bruto) / Path(str(row["arquivo_origem"])).name
            if not arq.is_file():
                p.append(f"arquivo bruto de {fonte} ausente ({arq.name}): o hash não pode ser conferido")
            elif sha256_arquivo(arq) != row["hash_sha256"]:
                p.append(f"hash do arquivo bruto {arq.name} diferente do registrado no banco")
            col = _data(row["data_coleta"])
            if gerado and col and gerado < col:
                p.append(f"arquivo gerado antes da coleta de {fonte} que diz conter")
    finally:
        conn.close()
    s, i = snap.get("sidra") or {}, snap.get("inmet")
    es, ei = ex.get("sidra") or {}, ex.get("inmet") or {}
    if es.get("modo") != "sqlite":
        p.append(f"seção sidra não veio do banco (modo {es.get('modo')!r})")
    elif _data(s.get("collectedAt")) != _data(es.get("dataColeta")):
        p.append("sidra.collectedAt diferente de exportacao.sidra.dataColeta")
    if i is None:
        if ei.get("modo") != "indisponivel" or not isinstance(ei.get("motivo"), str):
            p.append("inmet null sem modo 'indisponivel' e motivo em exportacao.inmet")
    elif isinstance(i, dict):
        if ei.get("modo") == "indisponivel":
            p.append("exportacao.inmet diz indisponível, mas há previsão no arquivo")
        elif ei.get("modo") == "sqlite" and _data(i.get("collectedAt")) != _data(ei.get("dataColeta")):
            p.append("inmet.collectedAt diferente de exportacao.inmet.dataColeta")
    return p


def verificar_com_node(arquivo: Path, cam: Caminhos) -> tuple[str, str]:
    """Verificador B5 (tests/check_exported_snapshot.js) no fuso de Brasília. Sem Node: INCOMPLETO."""
    node = shutil.which("node")
    if not node:
        return INFO, "Node.js não encontrado: o verificador B5 não pôde rodar"
    for f in (cam.verificador, cam.referencia):
        if not Path(f).is_file():
            return FALHA, f"arquivo do verificador ausente: {f}"
    env = dict(os.environ, TZ=FUSO_VERIFICACAO)
    try:
        r = subprocess.run([node, str(cam.verificador), str(arquivo), str(cam.referencia)], cwd=str(cam.verificador.parent.parent),
                           env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_VERIFICADOR_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return FALHA, f"verificador não rodou: {exc}"
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "Resultado: APROVADO" in out:
        return OK, f"APROVADO (TZ={FUSO_VERIFICACAO})"
    falhas = [ln.strip() for ln in out.splitlines() if "FALHA" in ln][:3]
    m = re.search(r"Resultado: (.+)", out)
    return FALHA, f"{m.group(1) if m else 'sem resultado'} (código {r.returncode})" + (f": {' | '.join(falhas)}" if falhas else "")


def validar_snapshot(arquivo: Path, cam: Caminhos, banco: Path, agora: datetime, verificador) -> tuple[list[str], dict]:
    """Todas as conferências antes de publicar. Lista vazia = válido. Node ausente -> Parada INCOMPLETO."""
    info: dict = {}
    try:
        texto = Path(arquivo).read_text(encoding="utf-8")
        snap = E.parse_snapshots_js(texto)
    except (OSError, UnicodeDecodeError, ValueError, E.ExportError) as exc:
        return [f"snapshot ilegível ou malformado: {exc}"], info
    problemas = [f"contrato: {x}" for x in E.validate_contract(snap)]
    if problemas:
        return problemas, info
    problemas += [f"procedência: {x}" for x in conferir_procedencia(snap, banco, cam.bruto)]
    ok, motivo = conferir_inmet(snap, agora)
    info["inmet"] = {"incluido": snap.get("inmet") is not None, "detalhe": motivo}
    if not ok:
        problemas.append(f"INMET: {motivo}")
    info["sidra"] = {"dataColeta": (snap["exportacao"].get("sidra") or {}).get("dataColeta"),
                     "execucaoId": (snap["exportacao"].get("sidra") or {}).get("execucaoId")}
    info["geradoEm"] = snap["exportacao"].get("geradoEm")
    if problemas:
        return problemas, info
    st, det = verificador(Path(arquivo), cam)
    info["verificador"] = det
    if st == INFO:
        raise Parada(INCOMPLETO, "INCOMPLETO", det)
    if st != OK:
        problemas.append(f"verificador B5: {det}")
    return problemas, info


# =============================================================================
# Publicação atômica com backup e restauração
# =============================================================================
def publicar_arquivo(novo: Path, destino: Path, pasta_backup: Path, hash_confirmado: str | None, validar, rel: Relatorio,
                     antes_de_substituir=lambda: None) -> dict:
    """Backup verificado -> temporário na pasta do destino -> substituição -> verificação -> restauração se preciso.

    `hash_confirmado`: SHA-256 completo do destino no momento da confirmação (None = destino não existia).
    `validar(caminho) -> list[str]`: conferência do arquivo publicado. `antes_de_substituir()` pode lançar Parada.
    """
    conteudo = Path(novo).read_bytes()
    h_novo = hashlib.sha256(conteudo).hexdigest()
    h_atual = sha256_ou_none(destino)
    if h_atual != hash_confirmado:
        raise Parada(INCOMPLETO, "INCOMPLETO", f"o snapshot publicado mudou desde a confirmação (agora {str(h_atual)[:12]}); nada foi alterado")
    info = {"destino": str(destino), "hashAnterior": h_atual, "hashNovo": h_novo, "backup": None, "tentativasSubstituicao": 0}
    try:
        conferir_gravavel(destino)
        conferir_espaco(destino.parent, len(conteudo))
    except OSError as exc:
        raise Parada(REPROVADO, "REPROVADO", f"destino não pode ser substituído ({exc}); nada foi alterado")
    rel.etapa("Destino", OK, f"{destino} gravável; espaço suficiente")

    # 1. Backup fora da pasta de produção, com hash conferido
    if h_atual is not None:
        alvo = pasta_backup / destino.name
        try:
            conferir_espaco(pasta_backup, destino.stat().st_size)
            copiar_com_hash(destino, alvo, h_atual)
            (pasta_backup / f"{destino.name}.sha256").write_text(f"{h_atual}  {destino.name}\n", encoding="utf-8")
        except OSError as exc:
            raise Parada(REPROVADO, "REPROVADO", f"backup falhou ({exc}); o destino não foi alterado. Backup parcial, se houver, mantido em {pasta_backup}")
        info["backup"] = str(alvo)
        rel.etapa("Backup", OK, f"{alvo} (sha256 {h_atual[:12]}, conferido)")
    else:
        rel.etapa("Backup", INFO, "não havia snapshot publicado: nada a guardar")

    # 2. Temporário na mesma pasta do destino, gravado no disco e conferido
    est = estagio_de(destino)
    try:
        gravar_estagio(destino, conteudo)
        if sha256_arquivo(est) != h_novo:
            raise OSError("arquivo temporário com hash diferente do validado")
    except FileExistsError:
        raise Parada(INCOMPLETO, "INCOMPLETO", f"já existe {est} (outra publicação em andamento ou interrompida); nada foi alterado")
    except OSError as exc:
        _remover_estagio(est)
        raise Parada(REPROVADO, "REPROVADO", f"falha ao preparar o arquivo novo ({exc}); o destino não foi alterado")
    rel.etapa("Arquivo temporário", OK, f"{est.name} gravado no disco (sha256 {h_novo[:12]})")

    substituido = False
    try:
        # 3. Última conferência antes da troca
        if sha256_ou_none(destino) != h_atual:
            raise Parada(REPROVADO, "REPROVADO", "o snapshot publicado mudou durante a publicação; nada foi substituído")
        antes_de_substituir()
        # 4. Substituição atômica (mesma pasta, arquivo fechado)
        try:
            info["tentativasSubstituicao"] = substituir(est, destino)
        except OSError as exc:
            raise Parada(REPROVADO, "REPROVADO", f"substituição recusada pelo sistema ({exc}); o snapshot anterior continua no lugar")
        substituido = True
        rel.etapa("Substituição", OK, f"{destino.name} trocado ({info['tentativasSubstituicao']} tentativa(s))")
        # 5. Conferência do arquivo publicado
        h_pub = sha256_arquivo(destino)
        problemas = [] if h_pub == h_novo else [f"hash publicado {h_pub[:12]} ≠ validado {h_novo[:12]}"]
        if not problemas:
            problemas = validar(destino)
        if problemas:
            raise RuntimeError("; ".join(problemas))
        rel.etapa("Conferência do publicado", OK, f"sha256 {h_pub[:12]} igual ao validado; conferências repetidas no arquivo publicado")
        return info
    except BaseException as exc:                       # inclui Ctrl+C e qualquer Parada
        _remover_estagio(est)
        if not substituido:
            atual = sha256_ou_none(destino)
            rel.etapa("Destino preservado", OK if atual == h_atual else AVISO, f"sha256 {str(atual)[:12]}")
            raise
        motivo = exc.motivo if isinstance(exc, Parada) else f"{type(exc).__name__}: {exc}"
        rel.etapa("Conferência do publicado", FALHA, motivo)
        ok, det = restaurar_anterior(destino, pasta_backup, h_atual, rel)
        info["restauracao"] = det
        if ok:
            raise Parada(REPROVADO, "PUBLICAÇÃO DESFEITA", f"{motivo}. Snapshot anterior restaurado do backup ({det})")
        raise Parada(GRAVE, "FALHA GRAVE", f"{motivo}. A restauração NÃO foi confirmada ({det}). "
                                           f"Restaure à mão: copie {pasta_backup / destino.name} para {destino}")


def restaurar_anterior(destino: Path, pasta_backup: Path, h_anterior: str | None, rel: Relatorio) -> tuple[bool, str]:
    """Volta o destino ao estado anterior a partir do backup, com hash conferido. Guarda o arquivo rejeitado."""
    try:
        if destino.exists():
            rej = pasta_backup / f"{destino.name}.rejeitado"
            if not rej.exists():
                shutil.copyfile(destino, rej)
        if h_anterior is None:                         # não havia snapshot: o publicado sai do lugar (fica no backup)
            os.replace(destino, pasta_backup / f"{destino.name}.retirado")
            ok = not destino.exists()
            rel.etapa("Restauração", OK if ok else FALHA, "não havia snapshot anterior; o arquivo novo foi retirado")
            return ok, "arquivo novo retirado"
        origem = pasta_backup / destino.name
        if sha256_ou_none(origem) != h_anterior:
            raise OSError(f"backup {origem} ausente ou com hash divergente")
        gravar_estagio(destino, origem.read_bytes())
        substituir(estagio_de(destino), destino)
        h = sha256_arquivo(destino)
        ok = h == h_anterior
        rel.etapa("Restauração", OK if ok else FALHA, f"sha256 restaurado {h[:12]} {'=' if ok else '≠'} anterior {h_anterior[:12]}")
        return ok, f"sha256 {h[:12]}"
    except BaseException as exc:
        _remover_estagio(estagio_de(destino))
        rel.etapa("Restauração", FALHA, f"{type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"


# =============================================================================
# Fluxos
# =============================================================================
def _coletor_real(fontes, banco: Path, cam: Caminhos) -> dict:
    if cam.raiz != config.ROOT.resolve():
        raise Parada(USO, "USO INVÁLIDO", "coleta real só no projeto do próprio pipeline")
    from .run_pipeline import run_all
    return run_all(list(fontes), sem_coleta=False, db_path=banco)


def _coletar(op: Opcoes, cam: Caminhos, rel: Relatorio, coletor, agora: datetime) -> None:
    for suf in ("-journal", "-wal", "-shm"):
        if Path(str(cam.banco) + suf).exists():
            raise Parada(INCOMPLETO, "INCOMPLETO", f"o banco tem arquivo {suf}: pode estar em uso ou com transação pendente; coleta não iniciada")
    try:                                               # 1.0: backup integral de data/, verificado, antes de coletar
        pasta = backup_dados.criar(cam.banco.parent, cam.backups, "coleta", agora)
    except (backup_dados.BackupError, OSError) as exc:
        raise Parada(REPROVADO, "REPROVADO", f"backup integral de data/ falhou ({exc}); coleta não iniciada")
    m = json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))
    rel.etapa("Backup de data/", OK, f"{pasta} ({m['total']} arquivos, manifesto SHA-256 conferido)")
    rel.dados["backupDados"] = str(pasta)
    rel.dados["backupBanco"] = str(pasta / "data" / cam.banco.name)
    resultado = (coletor or _coletor_real)(op.fontes, cam.banco, cam)
    rel.dados["coleta"] = resultado
    falhas = [f for f, r in (resultado or {}).items() if (r or {}).get("status") != "sucesso"]
    if falhas or not resultado:
        raise Parada(REPROVADO, "REPROVADO", f"coleta com falha em {', '.join(falhas) or 'todas as fontes'}: nada será gerado nem publicado. "
                                             f"Backup do banco anterior em {pasta}")
    rel.etapa("Coleta", OK, ", ".join(f"{f}: {r.get('status')}" for f, r in resultado.items()))


def _gerar(cam: Caminhos, trabalho: Path, rel: Relatorio, momento: datetime) -> tuple[Path, Path]:
    """Cópia do banco e do snapshot publicado na pasta de trabalho; exportação ali mesmo."""
    if not cam.banco.is_file():
        raise Parada(INCOMPLETO, "INCOMPLETO", f"banco não encontrado: {cam.banco}")
    (trabalho / "banco").mkdir(parents=True)
    (trabalho / "gerado").mkdir()
    h_banco = sha256_arquivo(cam.banco)
    copia = trabalho / "banco" / cam.banco.name
    try:
        copiar_com_hash(cam.banco, copia, h_banco)
    except OSError as exc:
        raise Parada(REPROVADO, "REPROVADO", f"cópia do banco falhou: {exc}")
    rel.dados["banco"] = {"caminho": str(cam.banco), "sha256": h_banco}
    rel.etapa("Cópia do banco", OK, f"{cam.banco.name} (sha256 {h_banco[:12]}) copiado para a pasta de trabalho; o original só é lido")
    gerado = trabalho / "gerado" / cam.destino.name
    if cam.destino.is_file():                         # o exportador lê o arquivo anterior do próprio destino (como na B5)
        copiar_com_hash(cam.destino, gerado, sha256_arquivo(cam.destino))
    try:
        info = E.export(copia, gerado, now=momento)
    except (E.ExportError, sqlite3.Error, OSError, ValueError) as exc:
        raise Parada(REPROVADO, "REPROVADO", f"exportação falhou: {exc}; nada foi publicado")
    if sha256_arquivo(cam.banco) != h_banco:
        raise Parada(REPROVADO, "REPROVADO", "o banco definitivo mudou durante a geração; nada foi publicado")
    rel.etapa("Exportação", OK, f"{gerado} (sidra: {info['sidra'].get('modo')}, inmet: {info['inmet'].get('modo')})")
    return copia, gerado


def _confirmacao(op: Opcoes, h_atual: str | None) -> bool:
    c = (op.confirmar_atual or "").lower()
    if not c:
        return False
    return h_atual is None and c == "ausente" or h_atual is not None and c != "ausente" and h_atual.startswith(c)


def _plano(rel: Relatorio, cam: Caminhos, h_atual, h_novo, sem_inmet: bool, motivo_inmet: str, momento: datetime,
          mostrar_comando: bool = True) -> None:
    hc = h_atual[:12] if h_atual else "ausente"
    cmd = f"python -m pipeline.publicar --publicar --confirmar-atual {hc}" + (" --aceitar-sem-inmet" if sem_inmet else "")
    plano = {"origem": rel.dados.get("banco"), "destino": str(cam.destino), "hashAtual": h_atual, "hashNovo": h_novo,
             "backupPrevisto": str(cam.backups / f"{momento.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}_publicacao" / cam.destino.name),
             "inmet": "incluído" if not sem_inmet else f"não incluído: {motivo_inmet}", "comando": cmd}
    rel.dados["plano"] = plano
    rel.etapa("Plano", INFO, f"origem {cam.banco} · destino {cam.destino} · hash atual {hc} · hash novo {h_novo[:12]} · "
                             f"backup {plano['backupPrevisto']} · INMET {plano['inmet']}")
    if mostrar_comando:
        rel.etapa("Comando", INFO, f"{cmd}   (não executado)")


def _fluxo(op: Opcoes, cam: Caminhos, rel: Relatorio, agora, verificador, coletor) -> None:
    real = op.publicar
    est = estagio_de(cam.destino)
    if est.exists():
        msg = f"existe {est}, sobra de uma publicação interrompida: confira e remova antes de publicar"
        if real:
            raise Parada(INCOMPLETO, "INCOMPLETO", msg)
        rel.etapa("Resíduo", AVISO, msg)
    if op.coletar:
        _coletar(op, cam, rel, coletor, agora())
    else:
        rel.etapa("Coleta", INFO, "não solicitada: usa os dados que já estão no banco; nenhuma consulta à internet")

    temporario = op.trabalho is None
    trabalho = Path(tempfile.mkdtemp(prefix="rigora_publicar_")) if temporario else op.trabalho
    if not temporario:
        if trabalho.exists() and any(trabalho.iterdir()):
            raise Parada(USO, "USO INVÁLIDO", f"--trabalho precisa ser uma pasta nova ou vazia: {trabalho}")
        trabalho.mkdir(parents=True, exist_ok=True)
    rel.dados["trabalho"] = str(trabalho)
    try:
        momento = agora()
        copia, gerado = _gerar(cam, trabalho, rel, momento)
        problemas, info = validar_snapshot(gerado, cam, copia, agora(), verificador)
        rel.dados["gerado"] = {"caminho": str(gerado), "sha256": sha256_arquivo(gerado), **info}
        if problemas:
            for x in problemas:
                rel.etapa("Validação", FALHA, x)
            raise Parada(REPROVADO, "REPROVADO", f"{len(problemas)} problema(s) no snapshot gerado; nada foi publicado")
        rel.etapa("Validação", OK, f"contrato, procedência, INMET ({info['inmet']['detalhe']}), verificador B5 {info.get('verificador')}")
        sem_inmet = not info["inmet"]["incluido"]
        h_atual, h_novo = sha256_ou_none(cam.destino), sha256_arquivo(gerado)
        confirmado = _confirmacao(op, h_atual)
        _plano(rel, cam, h_atual, h_novo, sem_inmet, info["inmet"]["detalhe"], momento,
               mostrar_comando=not (real and confirmado and (op.aceitar_sem_inmet or not sem_inmet)))

        def revalidar(p: Path) -> list[str]:
            return validar_snapshot(p, cam, copia, agora(), verificador)[0]

        def inmet_ainda_vale() -> None:
            ok, motivo = conferir_inmet(E.parse_snapshots_js(gerado.read_text(encoding="utf-8")), agora())
            if not ok:
                raise Parada(REPROVADO, "REPROVADO", f"INMET deixou de valer antes da troca ({motivo}); gere de novo")

        if not real:
            # Ensaio do mecanismo real numa cópia temporária do destino (nunca no destino)
            ensaio = trabalho / "ensaio"
            (ensaio / "js").mkdir(parents=True)
            destino_ensaio = ensaio / "js" / cam.destino.name
            if h_atual:
                copiar_com_hash(cam.destino, destino_ensaio, h_atual)
            sub = Relatorio("ensaio", rel.inicio)
            publicar_arquivo(gerado, destino_ensaio, nova_pasta(ensaio / "backups", "publicacao", momento), h_atual, revalidar, sub, inmet_ainda_vale)
            rel.etapa("Ensaio em cópia", OK, "backup, temporário, substituição e conferência executados numa cópia do destino: "
                                             + " · ".join(e["etapa"] for e in sub.etapas))
            if sem_inmet:
                rel.etapa("INMET", AVISO, f"uma publicação real agora sairia SEM previsão ({info['inmet']['detalhe']}) e exigiria --aceitar-sem-inmet")
            raise Parada(CONCLUIDO, "COLETA CONCLUÍDA E SIMULAÇÃO APROVADA" if op.coletar else "SIMULAÇÃO APROVADA",
                         "nada foi publicado; js/snapshots.js sem alteração")

        # Publicação real: só com confirmação e decisão sobre o INMET
        if sem_inmet and not op.aceitar_sem_inmet:
            raise Parada(INCOMPLETO, "INCOMPLETO", f"sem previsão do INMET válida ({info['inmet']['detalhe']}); "
                                                   "para publicar sem INMET, repita com --aceitar-sem-inmet. Nada foi alterado")
        if not confirmado:
            raise Parada(INCOMPLETO, "INCOMPLETO", f"publicação não confirmada: o snapshot publicado agora tem sha256 {str(h_atual)[:12]}; "
                                                   "repita com --confirmar-atual e esse valor. Nada foi alterado")
        if sem_inmet:
            rel.etapa("INMET", AVISO, f"publicação SEM previsão, aceita com --aceitar-sem-inmet ({info['inmet']['detalhe']})")
        pasta = nova_pasta(cam.backups, "publicacao", agora())
        rel.dados["pastaBackup"] = str(pasta)
        rel.dados["publicacao"] = publicar_arquivo(gerado, cam.destino, pasta, h_atual, revalidar, rel, inmet_ainda_vale)
        raise Parada(CONCLUIDO, "PUBLICADO", f"{cam.destino} substituído; backup do anterior em {pasta}")
    finally:
        if temporario:
            shutil.rmtree(trabalho, ignore_errors=True)


def _restaurar(op: Opcoes, cam: Caminhos, rel: Relatorio, agora) -> None:
    pasta = Path(op.restaurar).resolve()
    arq, reg = pasta / cam.destino.name, pasta / f"{cam.destino.name}.sha256"
    if not arq.is_file() or not reg.is_file():
        raise Parada(INCOMPLETO, "INCOMPLETO", f"{pasta} não é um backup de publicação ({arq.name} e {reg.name} são obrigatórios)")
    esperado = reg.read_text(encoding="utf-8").split()[0]
    if sha256_arquivo(arq) != esperado:
        raise Parada(REPROVADO, "REPROVADO", f"backup corrompido: {arq} não confere com {reg.name}; nada foi alterado")
    try:
        E.parse_snapshots_js(arq.read_text(encoding="utf-8"))
    except (ValueError, E.ExportError, UnicodeDecodeError) as exc:
        raise Parada(REPROVADO, "REPROVADO", f"backup não é um snapshot legível ({exc}); nada foi alterado")
    rel.etapa("Backup a restaurar", OK, f"{arq} (sha256 {esperado[:12]}, conferido)")
    h_atual = sha256_ou_none(cam.destino)
    rel.dados["plano"] = {"origem": str(arq), "destino": str(cam.destino), "hashAtual": h_atual, "hashNovo": esperado}
    rel.etapa("Plano", INFO, f"{arq} → {cam.destino} · hash atual {str(h_atual)[:12]} · hash restaurado {esperado[:12]}")
    if not _confirmacao(op, h_atual):
        raise Parada(INCOMPLETO, "INCOMPLETO", f"restauração não confirmada: o snapshot publicado agora tem sha256 {str(h_atual)[:12]}; "
                                               "repita com --confirmar-atual e esse valor. Nada foi alterado")
    nova = nova_pasta(cam.backups, "restauracao", agora())
    rel.dados["pastaBackup"] = str(nova)

    def conferir(p: Path) -> list[str]:
        try:
            E.parse_snapshots_js(p.read_text(encoding="utf-8"))
        except (ValueError, E.ExportError, UnicodeDecodeError) as exc:
            return [f"arquivo restaurado ilegível: {exc}"]
        return [] if sha256_arquivo(p) == esperado else ["hash restaurado diferente do backup"]

    rel.dados["publicacao"] = publicar_arquivo(arq, cam.destino, nova, h_atual, conferir, rel)
    raise Parada(CONCLUIDO, "RESTAURADO", f"{cam.destino} voltou ao backup {pasta.name}; o arquivo substituído foi guardado em {nova}")


def _gravar_relatorio(rel: Relatorio, pasta: Path) -> None:
    try:
        (pasta / "relatorio.json").write_text(json.dumps(rel.como_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (pasta / "relatorio.txt").write_text(rel.texto(), encoding="utf-8")
    except OSError:
        pass


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def executar(op: Opcoes, cam: Caminhos, agora=_agora, verificador=None, coletor=None, saida=print) -> Relatorio:
    """Roda o modo pedido e devolve o relatório. Nunca deixa uma exceção virar "continue"."""
    rel = Relatorio(descrever_modo(op), agora().isoformat(timespec="seconds"), saida=saida)
    rel.etapa("Modo", INFO, rel.modo)
    try:
        if op.restaurar:
            _restaurar(op, cam, rel, agora)
        else:
            _fluxo(op, cam, rel, agora, verificador or verificar_com_node, coletor)
        raise Parada(REPROVADO, "REPROVADO", "fluxo terminou sem resultado")   # não deve acontecer
    except Parada as p:
        rel.codigo, rel.resultado, rel.motivo = p.codigo, p.resultado, p.motivo
    except Exception as exc:                            # erro inesperado: nunca é autorização para seguir
        rel.codigo, rel.resultado, rel.motivo = REPROVADO, "REPROVADO", f"erro inesperado: {type(exc).__name__}: {exc}"
    if "pastaBackup" in rel.dados:                      # publicação ou restauração real: relatório junto do backup
        _gravar_relatorio(rel, Path(rel.dados["pastaBackup"]))
    if saida:
        saida(f"\nResultado: {rel.resultado} — {rel.motivo}")
    return rel


# =============================================================================
# Linha de comando
# =============================================================================
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Uso(message)


def analisar_argumentos(argv: list[str]) -> tuple[Opcoes, argparse.Namespace]:
    ap = _Parser(prog="python -m pipeline.publicar", description=__doc__, allow_abbrev=False,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coletar", action="store_true", help="coleta real (IBGE e INMET) no banco definitivo; não publica sozinho")
    ap.add_argument("--publicar", action="store_true", help="publica js/snapshots.js (exige --confirmar-atual)")
    ap.add_argument("--restaurar", type=Path, metavar="PASTA", help="restaura o snapshot de uma pasta de backup (exige --confirmar-atual)")
    ap.add_argument("--confirmar-atual", metavar="HASH", help="SHA-256 (12+ caracteres) do js/snapshots.js publicado agora, ou 'ausente'")
    ap.add_argument("--aceitar-sem-inmet", action="store_true", help="com --publicar: aceita publicar sem previsão do INMET válida")
    ap.add_argument("--fontes", help="com --coletar: sidra, inmet ou sidra,inmet (padrão)")
    ap.add_argument("--trabalho", type=Path, metavar="PASTA", help="pasta nova, fora do projeto, para os arquivos temporários (mantidos)")
    ap.add_argument("--relatorio", type=Path, metavar="ARQ", help="grava o relatório em JSON (fora do projeto)")
    ap.add_argument("--backups", type=Path, metavar="PASTA", help="pasta dos backups (padrão: ../rigora_backups/publicacoes, fora do projeto)")
    ap.add_argument("--referencia", type=Path, metavar="ARQ", help="referência do verificador B5 (padrão: a ativa em tests/fixtures/referencias_b5)")
    ap.add_argument("--raiz", type=Path, default=config.ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.restaurar and (a.coletar or a.publicar):
        raise Uso("--restaurar não pode ser combinado com --coletar ou --publicar")
    if a.confirmar_atual and not (a.publicar or a.restaurar):
        raise Uso("--confirmar-atual só vale com --publicar ou --restaurar")
    if a.confirmar_atual and not re.fullmatch(r"[0-9a-fA-F]{12,64}|ausente", a.confirmar_atual):
        raise Uso("--confirmar-atual: informe 12 a 64 caracteres hexadecimais do SHA-256, ou 'ausente'")
    if a.aceitar_sem_inmet and not a.publicar:
        raise Uso("--aceitar-sem-inmet só vale com --publicar")
    if a.fontes and not a.coletar:
        raise Uso("--fontes só vale com --coletar")
    fontes = tuple(f.strip() for f in (a.fontes or "sidra,inmet").split(",") if f.strip())
    if not fontes or any(f not in ("sidra", "inmet") for f in fontes):
        raise Uso("--fontes: use sidra, inmet ou sidra,inmet")
    if a.restaurar and a.trabalho:
        raise Uso("--trabalho não se aplica a --restaurar")
    raiz = a.raiz.resolve()
    for nome in ("trabalho", "relatorio", "backups"):
        v = getattr(a, nome)
        if v is not None and dentro(v, raiz):
            raise Uso(f"--{nome} precisa ficar fora do projeto ({Path(v).resolve()})")
    op = Opcoes(coletar=a.coletar, publicar=a.publicar, restaurar=a.restaurar, confirmar_atual=a.confirmar_atual,
                aceitar_sem_inmet=a.aceitar_sem_inmet, fontes=fontes, trabalho=a.trabalho.resolve() if a.trabalho else None)
    return op, a


def main(argv: list[str] | None = None, verificador=None, coletor=None, agora=_agora) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        op, a = analisar_argumentos(sys.argv[1:] if argv is None else argv)
    except Uso as exc:
        print(f"erro de uso: {exc}\nAjuda: python -m pipeline.publicar --help", file=sys.stderr)
        return USO
    try:
        cam = Caminhos.padrao(a.raiz, a.backups, a.referencia)
    except referencias.ReferenciaError as exc:
        print(f"erro: referência B5 inválida: {exc}", file=sys.stderr)
        return INCOMPLETO
    if dentro(cam.backups, cam.raiz):
        print(f"erro de uso: a pasta de backups precisa ficar fora do projeto ({cam.backups})", file=sys.stderr)
        return USO
    print(f"Publicação protegida do Rigora · {descrever_modo(op)}", flush=True)
    rel = executar(op, cam, agora=agora, verificador=verificador, coletor=coletor,
                   saida=lambda t: print(t, flush=True))
    if a.relatorio is not None:
        try:
            a.relatorio.write_text(json.dumps(rel.como_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            print(f"aviso: relatório não gravado em {a.relatorio}: {exc}", file=sys.stderr)
    return rel.codigo


if __name__ == "__main__":
    raise SystemExit(main())
