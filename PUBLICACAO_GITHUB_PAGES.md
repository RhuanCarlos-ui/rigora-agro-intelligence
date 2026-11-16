# Rigora no GitHub Pages: o que funciona e o que depende de backend

**Endereço público:** https://rhuancarlos-ui.github.io/rigora-agro-intelligence/

O GitHub Pages serve só arquivos estáticos: HTML, CSS e JavaScript. A interface do Rigora foi
feita assim desde o início, sem servidor, sem bibliotecas externas e com caminhos relativos. Por
isso ela funciona no Pages, inclusive no subdiretório `/rigora-agro-intelligence/`. O pipeline
em Python, o banco SQLite e as rotinas de publicação **não rodam** no Pages. O código deles está
neste repositório para leitura, testes e execução local, mas nenhuma tela finge que eles estão
ativos.

## Funciona no navegador, pelo endereço público

| Funcionalidade | Como funciona no Pages |
|---|---|
| Tela de acesso demonstrativa | Sem validação de senha (ambiente demonstrativo) |
| Navegação pelas cinco áreas | Visão Geral, Propriedades, Inteligência, Automações e Fontes, também pelo endereço (`#overview`, `#intelligence`…) |
| Indicadores e gráficos | Calculados no navegador (`js/metrics.js`) e desenhados em SVG (`js/charts.js`) |
| Seleção de safra, filtro de talhão, comparação e "de onde vem este número" | No navegador |
| Livro-caixa, comercialização, estoque | Dados **demonstrativos** (`js/data.js`) |
| Novo lançamento | Validado e salvo **só no navegador de quem lançou** (`localStorage`); não é compartilhado com outros visitantes |
| Simulador, cenários e sensibilidade ao preço | No navegador |
| Benchmark oficial (60 municípios, 2016–2025) | Cópia oficial do IBGE de **14/11/2026** (`js/snapshots.js`), indicada como cópia |
| Mercado (Cepea) | Série transcrita, com a fonte de cada mês |
| Central de alertas e automações | Regras e testes executados no navegador |
| Fontes e transparência | Mostram a data da cópia, a procedência e o status ao vivo × cópia |
| Previsão do INMET ao vivo | O navegador consulta `apiprevmet3.inmet.gov.br`. Se a API não responder ou recusar o acesso, a tela mostra "indisponível", porque a cópia salva tem mais de 24 h (regra M1) |
| Conferência ao vivo do IBGE ("Atualizar do IBGE") | O navegador consulta `apisidra.ibge.gov.br`. Se não responder, a cópia oficial datada continua sendo usada e o motivo é mostrado |

## Depende de Python, SQLite ou backend (não funciona no Pages)

| Funcionalidade | Por que não roda no Pages | Onde está o código |
|---|---|---|
| Coleta do IBGE e do INMET pelo pipeline | Precisa de Python executando no servidor | `pipeline/extract_*.py`, `pipeline/run_pipeline.py` |
| Tratamento, 26 regras de qualidade, INT-08, 14 regras MET | Python + pandas | `pipeline/transform*.py`, `pipeline/validate*.py` |
| Banco com histórico de execuções e rastreabilidade | SQLite é um arquivo local; o Pages não executa consultas | `pipeline/load_sqlite.py`, `pipeline/query.py` |
| Geração e publicação protegida do `js/snapshots.js` | Precisa gravar arquivos com backup e troca atômica | `pipeline/export_snapshots.py`, `pipeline/publicar.py` |
| Backup integral e restauração | Operam sobre a pasta `data/` local | `pipeline/backup_dados.py` |
| Aprovação de referências B5 | Fluxo com responsável e hash, feito localmente | `pipeline/referencias.py` |
| Portão de qualidade e testes automatizados | Rodam com Python e Node.js na máquina de quem testa | `tools/portao.py`, `tests/` |
| Atualização automática da cópia oficial | Sem rotina agendada nesta versão | — |
| Lançamentos compartilhados entre usuários, contas, login real | Não existe servidor nem banco multiusuário | — |

Para rodar essas partes localmente, veja `README_EXECUCAO.md`.

## Arquitetura proposta para disponibilizar o backend (não implementada)

1. **Atualização agendada pelo GitHub Actions.** Um workflow diário roda `python -m pipeline.run_pipeline`
   e depois `python tools/portao.py`. Só com o portão APROVADO o workflow roda
   `python -m pipeline.publicar --publicar` e faz commit do novo `js/snapshots.js`, publicando-o no
   Pages. Mesmo sem servidor, isso manteria a cópia do IBGE e do INMET atualizada. O banco ficaria
   como artefato versionado do workflow, e os backups como artefatos com retenção definida.
2. **API para consultas e lançamentos.** Um serviço pequeno (por exemplo, FastAPI) expõe as
   consultas do `pipeline/query.py` e recebe os lançamentos, com autenticação. Ele ficaria num
   provedor com banco gerenciado (por exemplo, PostgreSQL), porque o SQLite não serve para gravações
   simultâneas de vários usuários. A interface passaria a chamar essa API, mantendo a cópia oficial
   como alternativa quando a API falhar.
3. **Segredos fora do repositório.** Credenciais da API e do banco ficariam em *GitHub Secrets* ou no
   cofre do provedor, nunca no código.

## Diferenças desta publicação em relação ao projeto original

| Arquivo | Diferença | Motivo |
|---|---|---|
| `js/snapshots.js` | É a cópia oficial de 14/11/2026 (fixture dos testes, `410fa9cb…`), não o snapshot de produção | O snapshot de produção não é publicado; a fixture tem só dados públicos |
| `index.html` | Uma linha a mais: `<link rel="icon" href="data:,">` | Evita o pedido de `/favicon.ico`, que dava erro 404 no Pages |
| `.nojekyll` | Arquivo novo e vazio | O Pages serve os arquivos como estão, sem processamento do Jekyll |
| `README.md` | Seção inicial com o endereço público | Orientar quem chega pelo GitHub |
| `PUBLICACAO_GITHUB_PAGES.md` | Este arquivo | Documentar o que funciona no Pages |

Os guias `README_EXECUCAO.md` e `ESTRUTURA_DO_PROJETO.md` foram escritos para o kit acadêmico do
projeto. Neles, `06_CODIGO_FONTE` corresponde à raiz deste repositório, e as referências a
`05_EVIDENCIAS` apontam para a pasta de evidências do kit, que não faz parte do repositório.

Todos os outros arquivos são idênticos aos do projeto original (conferência por SHA-256). O
repositório **não** contém o banco de produção, os arquivos brutos das coletas, os backups, o
snapshot de produção, credenciais nem dados pessoais.
