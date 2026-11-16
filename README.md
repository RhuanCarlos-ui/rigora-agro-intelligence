# Rigora Agro Intelligence

> **Teste no navegador:** https://rhuancarlos-ui.github.io/rigora-agro-intelligence/
>
> A interface roda direto no GitHub Pages: clique em **Entrar no Dashboard** e use o menu lateral.
> Os dados da propriedade são **demonstrativos**, e os dados oficiais do IBGE vêm da cópia de
> 14/11/2026. O pipeline em Python, o banco SQLite e a publicação protegida não rodam no Pages;
> veja [`PUBLICACAO_GITHUB_PAGES.md`](PUBLICACAO_GITHUB_PAGES.md). Para executar tudo
> localmente, veja [`README_EXECUCAO.md`](README_EXECUCAO.md).


Painel de inteligência para a cafeicultura de Patrocínio (MG): indicadores de uma propriedade
demonstrativa, comparação com os dados oficiais do IBGE (municípios e Minas Gerais), previsão do
tempo do INMET com estimativa de risco de ferrugem, alertas e regras operacionais, e um pipeline de
dados que coleta, valida, guarda e publica as cópias oficiais usadas pela interface.

Projeto acadêmico (FIAP). A propriedade e seus custos são **demonstrativos**; IBGE e INMET são fontes
oficiais, sempre exibidas com a data de coleta e a procedência.

## Como executar

**Interface:** abra `index.html` num navegador (não precisa de servidor). Ela consulta IBGE e INMET
ao vivo quando a rede permite e, se a consulta falhar, usa a cópia oficial salva em `js/snapshots.js`
com a data de coleta (nunca como "ao vivo").

**Pipeline e testes** (Python 3.10+ e Node.js):

```bash
pip install -r requirements-dev.txt
python tools/portao.py                 # portão de qualidade: valida tudo (APROVADO / REPROVADO / INCOMPLETO)
python -m pipeline.publicar            # simulação da publicação: não coleta nem publica
```

## Estado dos dados (16/11/2026)

| Item | Situação |
|---|---|
| Primeira coleta real | 16/11/2026, 10:55 (Brasília), no Windows: IBGE/SIDRA (execução 5), IBGE/Localidades (execução 4) e INMET (execução 6), todas com sucesso |
| IBGE/SIDRA | PAM 2016–2025, 8.520 registros, todos da execução 5. O bruto novo tem o mesmo SHA-256 do de 14/11 (`035139b5…`): **zero diferenças** contra a referência B5 |
| INMET | Previsão de 5 dias para Patrocínio, de **16 a 20/11/2026**, coletada às 13:55 UTC. Vale como reserva por 24 h |
| Snapshot publicado | `js/snapshots.js`, SHA-256 `ed9d1cfea2456536effb2bcf82c068518b7bd354973b96ce15c9352427f91fea`, gerado às 13:57 UTC pelo `python -m pipeline.publicar` (primeira publicação) |
| Snapshot anterior | Cópia manual de 14/11/2026 (`410fa9cb…`), guardada em `rigora_backups/publicacoes/20261116T135727Z_publicacao/` |
| Banco | `data/rigora.db` íntegro (`integrity_check` ok, nenhuma chave estrangeira quebrada), SHA-256 `ad8058117d7a63ec…` |
| Testes | 446 Python e 111 JavaScript nos três fusos; verificador B5 aprovado nos três fusos sobre o snapshot publicado |
| Referência B5 | A histórica de 14/11/2026 continua ativa e inalterada; nenhuma nova referência foi necessária |

A versão 1.0 ainda **não está fechada**: falta a inspeção visual da interface no navegador.

## Arquitetura

| Camada | Arquivos | Papel |
|---|---|---|
| Interface | `index.html`, `css/`, `js/app.js`, `js/charts.js` | Visão geral, Propriedades, Inteligência (simulador, benchmark, mercado), Automações e Fontes |
| Métricas | `js/metrics.js` | Cálculos de custo, produtividade, simulação, regras da previsão (M1/B1) |
| Dados da propriedade | `js/data.js` | Talhões, livro-caixa, colheita, vendas, estoque, regras (demonstrativos) |
| Cópia oficial | `js/snapshots.js` | IBGE (SIDRA) e INMET salvos, com `exportacao` (procedência) |
| Pipeline | `pipeline/` | Coleta → tratamento → validação → SQLite (`data/rigora.db`) → exportação → publicação |
| Qualidade | `tests/`, `tools/portao.py` | Testes JavaScript (3 fusos), Python (pytest), verificador B5 |

Detalhes do pipeline, do banco, das regras de qualidade e da publicação: [`pipeline/README.md`](pipeline/README.md).

## Fontes de dados

| Fonte | O que traz | Como chega |
|---|---|---|
| IBGE/SIDRA, PAM tabela 1613 | Café arábica: rendimento, área colhida e produção por município de MG, 2016–2025 | Pipeline (banco + `js/snapshots.js`); conferência ao vivo do rendimento de Patrocínio |
| IBGE/Localidades | Nomes e mesorregiões dos municípios | Pipeline |
| INMET, previsão por município | Previsão de 5 dias para Patrocínio (temperatura, umidade, vento, resumo) | Consulta ao vivo na interface; cópia de reserva de até 24 h |
| Cepea/Esalq | Indicador do café arábica | Transcrito manualmente (sem API aberta) |
| Propriedade demonstrativa | Custos, colheita, vendas | `js/data.js`; lançamentos novos ficam no navegador |

## Indicadores, regras e alertas

- Custo por hectare e por saca, produtividade (sc/ha), resultado por safra e por talhão, com rateio pela área (12 ha, soma dos talhões).
- Comparação com Patrocínio e com a média de Minas Gerais **no mesmo ano**; benchmark entre os 60 maiores municípios.
- Simulador de cenários (produtividade, preço, perdas) e sensibilidade ao preço.
- Estimativa de favorabilidade à ferrugem pela previsão do INMET (UR média ≥ limite e T média 22–26 °C), sempre rotulada como estimativa.
- Alertas de qualidade de dados (fonte indisponível, cópia vencida, divergência com o IBGE) e de custos, com marcação de lidos.

## Persistência

- Lançamentos novos do livro-caixa: `localStorage` do navegador, chave versionada `rigora.lancamentos.v1`, com validação e aviso quando o armazenamento falha. Não há servidor nem sincronização.
- Dados oficiais: SQLite `data/rigora.db`, com histórico de execuções, validação por registro e grupos da PLA-05.

## Backup, recuperação e publicação

- `python -m pipeline.backup_dados criar | verificar | restaurar` — backup integral de `data/` com manifesto SHA-256, fora do projeto; restauração sempre numa pasta nova.
- `python -m pipeline.publicar` — publicação protegida do `js/snapshots.js`: simulação por padrão; coleta e publicação só com pedido explícito, confirmação do hash atual, backup verificado, troca atômica e restauração automática em falha.
- `python -m pipeline.referencias listar | propor | aprovar` — referências versionadas do verificador B5; uma nova referência só vale depois de aprovada por uma pessoa.

## Testes

`python tools/portao.py` executa: testes JavaScript em America/Sao_Paulo, UTC e Asia/Tokyo; a suíte Python com pytest (pipeline, SIDRA, INMET, SQLite, exportação, publicação, backup, referências, portão); a conferência das suítes obrigatórias; a simulação da publicação; o verificador B5 e seus autotestes nos três fusos; e o inventário de hashes do projeto antes e depois.

## Limitações conhecidas

- Custos da propriedade são demonstrativos; Conab não integrada; Cepea transcrito.
- O SIDRA é consultado para 2016–2025 (`pipeline/config.py`); um novo ano da PAM exige atualizar a configuração e o verificador B5.
- Uma nova coleta que deixaria ausente um valor válido do IBGE é recusada por inteiro (regra INT-08); não há aceitação parcial.
- A previsão salva do INMET só vale por 24 h; depois disso, sem consulta ao vivo, a interface mostra "indisponível".
- Lançamentos novos ficam só no navegador em que foram feitos.
- O procedimento de coleta e publicação no Windows, versão 2 (`rigora_backups/v1_0_20261116/coleta_e_publicacao_windows_v2.bat`), está preparado, mas ainda não foi validado integralmente numa execução real. A primeira coleta usou a versão 1 do procedimento.

Histórico de mudanças: [`CHANGELOG.md`](CHANGELOG.md).
