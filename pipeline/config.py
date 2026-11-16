"""Configuração central do pipeline (códigos, URLs e caminhos).

Tudo que identifica uma consulta oficial fica aqui, para que a origem de cada
número seja rastreável e alterável num único lugar.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "rigora.db"

# --- IBGE / SIDRA ------------------------------------------------------------
# Tabela 1613: Produção Agrícola Municipal (PAM), lavouras permanentes.
SIDRA_BASE = "https://apisidra.ibge.gov.br/values"
SIDRA_TABLE = "1613"
UF_MG = "31"                       # código IBGE de Minas Gerais
PRODUCT_CLASSIFICATION = "82"      # classificação "Produto das lavouras permanentes"
PRODUCT_ARABICA = "31619"          # Café (em grão) Arábica
YEARS = list(range(2016, 2026))    # 2016 a 2025 (último ano publicado em nov/2026)

# Variáveis da tabela 1613 usadas pela Rigora
VAR_RENDIMENTO = "112"   # Rendimento médio da produção (kg/ha)
VAR_AREA = "216"         # Área colhida (ha)
VAR_PRODUCAO = "214"     # Quantidade produzida (t)
VARIABLES = {
    VAR_RENDIMENTO: "rendimento_kg_ha",
    VAR_AREA: "area_colhida_ha",
    VAR_PRODUCAO: "producao_t",
}

# Localidades (nome e mesorregião de cada município)
LOCALIDADES_URL = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF_MG}/municipios"

# Município da propriedade demonstrativa
PATROCINIO = "3148103"

# Quantos municípios entram no painel de benchmark (os maiores por área colhida)
TOP_N_MUNICIPIOS = 60

# Saca de café beneficiado
SACA_KG = 60

# --- HTTP --------------------------------------------------------------------
HTTP_TIMEOUT_S = 60      # a consulta de MG inteira levou ~27 s em nov/2026
HTTP_RETRIES = 3
HTTP_BACKOFF_S = 2.0     # espera cresce a cada tentativa: 2 s, 4 s, ...
USER_AGENT = "rigora-agro-pipeline/0.1 (projeto academico FIAP)"

# --- Validação -----------------------------------------------------------------
# Diferença máxima aceita entre o rendimento publicado e produção ÷ área colhida.
# Nos 600 registros oficiais coletados em 14/11/2026 a maior diferença foi 0,05%.
CONSISTENCY_TOLERANCE = 0.01

# Limites de plausibilidade (geram AVISO, nunca correção automática).
# Calibrados nos 600 registros oficiais dos 60 maiores municípios (2016–2025, coletados em 14/11/2026):
#   produtividade observada 13–50 sc/ha; razão município/MG 0,58–1,86; maior variação anual 2,58×.
PLAUSIBLE_SC_HA = (5.0, 80.0)          # rendimento fora desta faixa é suspeito para café arábica
MAX_AREA_HA_MUNICIPIO = 100_000        # maior área colhida observada: 43.850 ha (Patrocínio, 2025)
MAX_YOY_RATIO = 3.0                    # variação anual acima de 3× (ou abaixo de 1/3) é suspeita
STATE_RATIO_RANGE = (0.3, 2.5)         # município muito abaixo/acima da média estadual do mesmo ano
UNIT_FACTORS = (60.0, 1000.0)          # fatores típicos de erro de unidade (sacas↔kg, t↔kg)
UNIT_FACTOR_TOLERANCE = 0.15
# PLA-05: faixas de área colhida (ha) que definem os níveis de triagem interna do Rigora:
# até 10 ha (inclusive) = Informativo; acima de 10 até 500 ha (inclusive) = Atenção; acima de 500 ha = Revisão documental.
# Definidas a partir da distribuição de uma única execução (SIDRA 3, 14/11/2026); podem ser revistas.
# Não avaliam a qualidade dos dados do IBGE e não mudam o que a regra detecta.
PLA05_FAIXAS_HA = (10, 500)
DB_REPORTS_DIR = DATA_DIR / "reports"

# --- INMET (previsão do tempo por município) ------------------------------------
# API pública usada pelo portal do INMET. Não documenta um contrato formal: o formato
# foi verificado em 14/11/2026 (5 dias; os 2 primeiros divididos em manhã/tarde/noite).
INMET_PREVISAO_URL = "https://apiprevmet3.inmet.gov.br/previsao/{codigo_ibge}"
INMET_MUNICIPIO = PATROCINIO
INMET_TIMEOUT_S = 20
INMET_HORIZONTE_ESPERADO_DIAS = 5
INMET_PERIODOS = ("manha", "tarde", "noite")
INMET_MAX_IDADE_HORAS = 24           # previsão coletada há mais tempo é "desatualizada" (aviso)
INMET_TEMP_PLAUSIVEL_C = (-10.0, 45.0)
INMET_MUNICIPIO_NOME = "Patrocínio"
