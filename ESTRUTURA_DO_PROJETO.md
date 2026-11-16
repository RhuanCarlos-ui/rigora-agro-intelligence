# Estrutura do projeto

A pasta `06_CODIGO_FONTE` **é o próprio projeto**. O `index.html` fica na raiz para abrir com dois
cliques. Abaixo dele, as pastas têm **a mesma estrutura do projeto original**: `css/`, `js/`,
`pipeline/`, `tests/` e `tools/`. A estrutura não foi dividida em `frontend/`, `pipeline/` e
`tests/` separados porque a interface, os testes e o pipeline localizam os arquivos por caminho
relativo (por exemplo, o `index.html` carrega `js/app.js`, e os testes leem `js/metrics.js`).
Mudar as pastas quebraria a abertura direta e os testes. A tabela abaixo mostra a que camada cada
item pertence.

```
06_CODIGO_FONTE/
├── index.html                  ABRIR AQUI · interface: acesso, Visão Geral, Propriedades, Inteligência, Automações, Fontes
├── COMO_ABRIR_O_RIGORA.txt     instruções de 4 passos
├── css/style.css               FRONTEND · identidade visual e layout responsivo
├── js/
│   ├── app.js                  FRONTEND · navegação, telas, consultas ao vivo, alertas, automações
│   ├── metrics.js              FRONTEND · fonte única de cálculo (custos, produtividade, simulação, regras M1/B1)
│   ├── charts.js               FRONTEND · gráficos em SVG próprio, sem bibliotecas externas
│   ├── data.js                 FRONTEND · dados DEMONSTRATIVOS da propriedade (talhões, livro-caixa, colheita, vendas)
│   └── snapshots.js            FRONTEND · cópia oficial usada sem internet (aqui: a fixture de 14/11/2026)
├── pipeline/                   BACKEND (Python) · coleta, tratamento, validação, banco, exportação, publicação
│   ├── config.py               códigos IBGE, URLs, caminhos e limites das regras
│   ├── http.py                 HTTP com timeout, 3 tentativas e erro explícito (urllib)
│   ├── extract_sidra.py        coleta da PAM (SIDRA 1613) e das localidades; bruto + .meta.json com SHA-256
│   ├── extract_inmet.py        coleta da previsão do INMET (Patrocínio)
│   ├── transform.py            tratamento da PAM com pandas (sinais "-", "..", "X")
│   ├── transform_inmet.py      tratamento da previsão do INMET
│   ├── validate.py             26 regras de qualidade da PAM + INT-08
│   ├── validate_inmet.py       14 regras MET da previsão
│   ├── load_sqlite.py          gravação transacional no SQLite (UPSERT, histórico de execuções)
│   ├── run_pipeline.py         orquestração: IBGE e INMET independentes
│   ├── query.py                consultas prontas ao banco
│   ├── export_snapshots.py     gera um snapshot a partir do banco (--saida obrigatório)
│   ├── publicar.py             publicação protegida: simulação, coleta, publicação, restauração
│   ├── backup_dados.py         backup integral de data/ com manifesto SHA-256
│   ├── referencias.py          referências B5 versionadas (listar, propor, aprovar)
│   ├── backfill_pla05.py       preenchimento histórico dos grupos da PLA-05 (etapa 4, já executado no original)
│   └── README.md               documentação técnica do pipeline, do banco e das regras
├── tools/portao.py             QUALIDADE · portão: valida o projeto inteiro em um comando
├── tests/                      TESTES
│   ├── metrics.test.js         111 testes JavaScript (cálculos, interface simulada, regras da previsão)
│   ├── check_exported_snapshot.js   verificador B5 do snapshot gerado (+ 18 autotestes)
│   ├── test_*.py               446 testes Python em 15 arquivos
│   └── fixtures/               dados de teste seguros (amostras reais reduzidas) e referência B5
├── dados_demonstracao/         amostra reduzida para rodar o pipeline sem internet
├── requirements.txt            dependência do pipeline (pandas)
├── requirements-dev.txt        dependências de teste (pandas + pytest)
├── .gitignore                  exclui data/, __pycache__/ e caches do controle de versão
├── README.md                   visão geral técnica do projeto (estado registrado em 16/11/2026)
├── CHANGELOG.md                histórico de mudanças
├── README_EXECUCAO.md          como abrir, testar e executar (Windows: PowerShell e CMD)
├── REQUISITOS_E_DEPENDENCIAS.md versões, dependências e serviços externos
├── ESTRUTURA_DO_PROJETO.md     este arquivo
└── MANIFESTO_ARQUIVOS.txt      SHA-256 e tamanho de cada arquivo desta pasta
```

## Abertura direta pelo `index.html` (sem servidor)

| Verificação no código | Resultado |
|---|---|
| Scripts | 5 scripts clássicos (`<script src="js/...">`) com caminhos relativos; nenhum `type="module"` nem `import` |
| `fetch` de arquivos locais ou JSON | Nenhum. Os dados offline estão em `js/snapshots.js` e `js/data.js`, carregados como scripts |
| `fetch` externos | Só as APIs públicas do IBGE e do INMET, com timeout e alternativa datada quando falham |
| Bibliotecas externas | Nenhuma; os gráficos são SVG gerados por `js/charts.js` |
| Recurso externo opcional | Fonte Inter do Google Fonts; sem internet, a página usa a fonte do sistema |
| Caminhos absolutos | Nenhum |
| Persistência | `localStorage` do navegador, com tratamento de erro quando não está disponível |

Nenhum arquivo do código precisou ser alterado para abrir com dois cliques.

## O que foi copiado e o que ficou de fora

| Do projeto original | No pacote? | Motivo |
|---|---|---|
| `index.html`, `css/`, `js/app.js`, `js/metrics.js`, `js/charts.js`, `js/data.js` | Sim, idênticos | Código da interface |
| `js/snapshots.js` publicado em 16/11/2026 (`ed9d1cfe…`) | **Não** | Artefato de produção. No lugar dele está a fixture `tests/fixtures/snapshots_manual_20261114.js`, cópia exata, com o mesmo formato e só dados públicos |
| `pipeline/`, `tools/`, `tests/`, `tests/fixtures/` | Sim, idênticos | Código, testes e dados de teste |
| `requirements*.txt`, `.gitignore`, `README.md`, `CHANGELOG.md`, `pipeline/README.md` | Sim, idênticos | Dependências e documentação técnica |
| `data/rigora.db` e os bancos antigos de teste | **Não** | Banco real de produção e cópias de etapas anteriores |
| `data/raw/` (respostas brutas do IBGE e do INMET) | **Não** | Arquivos de coleta da produção; a coleta real do guia recria tudo a partir das fontes oficiais |
| `data/processed/`, `data/reports/` | **Não** | Gerados pelo pipeline. Dois relatórios de qualidade estão em `05_EVIDENCIAS/B_relatorios_de_qualidade` como evidência |
| `data/*.db-journal` | **Não** | Resíduo de journal do SQLite |
| `_arquivo/` | **Não** | Resíduos arquivados de etapas antigas |
| `__pycache__/` | **Não** | Cache do Python |
| Procedimentos `.bat` da coleta no Windows (fora do projeto) | **Não** | Contêm o hash esperado da produção e foram feitos para a máquina do desenvolvedor |

`MANIFESTO_ARQUIVOS.txt` lista todos os arquivos desta pasta com SHA-256. Os arquivos de código,
exceto `js/snapshots.js`, têm o mesmo SHA-256 dos arquivos correspondentes do projeto original em
16/11/2026.

## Camadas

| Camada | Onde | Linhas aproximadas |
|---|---|---|
| Interface | `index.html`, `css/`, `js/` (sem `snapshots.js`) | 5,2 mil (HTML 0,7 · CSS 1,8 · JS 2,8) |
| Pipeline | `pipeline/*.py` | 4,6 mil |
| Portão | `tools/portao.py` | 0,4 mil |
| Testes | `tests/*.py`, `tests/*.js` | 7,6 mil |
