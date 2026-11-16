# Rigora Agro Intelligence — Histórico de mudanças

## 16/11/2026 (tarde) — Primeira coleta real e primeira publicação

| Etapa | Resultado |
|---|---|
| Coleta real (Windows, 10:55) | IBGE/SIDRA execução 5 (8.520 registros), IBGE/Localidades execução 4, INMET execução 6: todas com sucesso. Backup integral de `data/` verificado antes |
| IBGE × referência B5 | Bruto do SIDRA idêntico ao de 14/11 (`035139b5…`): **zero diferenças**; nenhuma nova referência proposta ou aprovada |
| INMET | Previsão de 5 dias, de 16 a 20/11/2026 |
| Publicação | Primeira publicação pelo pipeline: `js/snapshots.js` passou de `410fa9cb…` (cópia manual, guardada em backup) para `ed9d1cfea2456536effb2bcf82c068518b7bd354973b96ce15c9352427f91fea` |
| Validação | Banco íntegro (`integrity_check` ok, nenhuma chave estrangeira quebrada); 446 testes Python e 111 JavaScript nos três fusos; verificador B5 aprovado nos três fusos sobre o snapshot publicado |
| Referência B5 | Histórica (`historica-20261114`, `410fa9cb…`) preservada e ativa |
| Procedimento | Coleta feita com `primeira_coleta_windows.bat` (v1). A v2 (`coleta_e_publicacao_windows_v2.bat`: rollback automático depois da troca, verificador com a referência ativa, nada gravado no projeto se a coleta reprovar) está preparada, mas ainda não foi validada integralmente em execução real |

### Estado atual
- Banco `ad8058117d7a63ec…`; snapshot publicado `ed9d1cfea2456536…`; fixture histórica `410fa9cb…`, inalterada.
- **Versão 1.0 ainda não fechada:** falta a inspeção visual da interface no navegador.

---

## 16/11/2026 — Versão 1.0 (candidata): estabilidade dos dados

| Item | O que mudou | Arquivos principais |
|---|---|---|
| INT-08 | Coleta do SIDRA que deixaria ausente um valor válido é recusada por inteiro, na mesma transação; ocorrências registradas | `pipeline/validate.py`, `pipeline/load_sqlite.py` |
| Backup integral | `python -m pipeline.backup_dados criar / verificar / restaurar`: toda a pasta `data/` com manifesto SHA-256; exigido pelo `--coletar` | `pipeline/backup_dados.py`, `pipeline/publicar.py` |
| Referências B5 | Registro versionado; `propor` nunca ativa; `aprovar` exige hash, responsável e justificativa; publicar e portão usam a ativa | `pipeline/referencias.py`, `tests/fixtures/referencias_b5/registro.json`, `tools/portao.py` |
| Testes | `tests/test_estabilidade_1_0.py` (suíte obrigatória do portão) | `tests/` |
| Documentação | `README.md` na raiz; seção de estabilidade no `pipeline/README.md`; retirada a limitação de Asia/Tokyo, que não procede (o verificador aprova nos três fusos com a previsão presente) | `README.md`, `pipeline/README.md` |

### Estado atual
- `js/snapshots.js` em uso: cópia manual de 14/11/2026 (sha256 `410fa9cb…`); banco `91f9fdc8…` sem nova coleta.
- **Pendente para fechar a 1.0:** primeira coleta real (no Windows; o ambiente Linux de validação não alcança IBGE/INMET), decisão sobre eventuais diferenças do IBGE e primeira publicação.

---

## 15/11/2026 (noite) — Bloco 3: publicação protegida

| Etapa | O que mudou | Arquivos principais |
|---|---|---|
| Bloco 3 | `python -m pipeline.publicar`: simulação por padrão; coleta (`--coletar`) e publicação (`--publicar --confirmar-atual HASH`) só com pedido explícito; validação do snapshot (contrato, procedência contra o banco e os brutos, INMET até 24 h, verificador B5); backup verificado fora do projeto; troca atômica; restauração automática em falha e `--restaurar` | `pipeline/publicar.py`, `tests/test_publicar.py` |
| Bloco 3 | Exportador: `--saida` obrigatório e recusa do `js/snapshots.js` em uso | `pipeline/export_snapshots.py` |
| Bloco 3 | Portão: simulação da publicação, suítes obrigatórias e verificador sobre o snapshot da simulação (`--gerado` virou opcional) | `tools/portao.py`, `tests/test_portao_bloco3.py` |

### Estado atual
- `js/snapshots.js` em uso: cópia manual de 14/11/2026, sem alteração. **A primeira publicação gerada pelo pipeline aguarda autorização.**
- Nenhuma coleta real nem publicação foi feita no Bloco 3: o mecanismo foi testado em pastas e cópias temporárias.
- Validação: `python tools/portao.py`.

### Limitações conhecidas (Bloco 3)
- Publicação ainda não executada no Windows (testada no Linux).
- O verificador B5 compara com a referência de 14/11/2026: uma revisão futura do IBGE exigirá aprovar uma nova referência.
- Hoje a previsão do INMET no banco está vencida: uma publicação agora sairia sem INMET e exigiria `--aceitar-sem-inmet`.

---

## 15/11/2026 — Etapas 4 a Bloco 2 (preparação da versão 1.0)

| Etapa | O que mudou | Arquivos principais |
|---|---|---|
| Bloco 2 | Dia da previsão ao vivo do INMET sem umidade completa é omitido com aviso (nunca "undefined"); lançamentos novos do livro-caixa salvos neste navegador (`localStorage`, chave versionada), com aviso quando não for possível; área total da propriedade derivada dos talhões (12 ha) | `js/app.js`, `js/metrics.js`, `js/data.js`, `index.html`, `tests/metrics.test.js` |
| Bloco 1 | Portão de qualidade em um comando (`tools/portao.py`) e seus testes; documentação revisada; resíduos arquivados em `_arquivo/` | `tools/portao.py`, `tests/test_portao.py`, `pipeline/README.md`, `CHANGELOG.md` |
| Ajuste B7 | IBGE "indisponível" válido mostra "Fonte indisponível" (com o motivo), distinto de registro incompleto e de cópia manual | `js/app.js` |
| B7 | Linha "Procedência" nos cartões do IBGE e do INMET (aba Fontes), lida de `exportacao`; sem ela, "Cópia manual" | `js/app.js` |
| C02 | Um único `data_execucao` por chamada do pipeline (a regra C02/P02 do backfill não mudou) | `pipeline/run_pipeline.py`, `pipeline/load_sqlite.py` |
| B5 | Exportação de teste com o banco real, em cópias fora do projeto, e verificador independente | `tests/check_exported_snapshot.js` |
| B4 | Cabeçalho do snapshot gerado descreve o comportamento real do IBGE e do INMET | `pipeline/export_snapshots.py` |
| B3 | Texto da média de MG, data local dos alertas de qualidade, cópia do INMET recusada na aba Fontes, selo "Oficial · transcrito" do Cepea | `js/app.js` |
| B2 | Conferência rápida do IBGE sem troca parcial de dados; selos de conferência completa, parcial ou divergente | `js/app.js` |
| T0 | Testes de JavaScript isolados do snapshot de produção (fixture própria) | `tests/metrics.test.js` |
| B1 e pós-B1 | Previsão do INMET reavaliada durante a sessão (30 min e ao voltar à aba); dias passados retirados da resposta ao vivo | `js/app.js`, `js/metrics.js` |
| M1 | Cópia salva do INMET só é usada com até 24 h e com os dias de hoje em diante | `js/app.js`, `js/metrics.js` |
| 4e | Grupos da PLA-05 preenchidos na execução 3 do banco, com conferência P00–P19 | `pipeline/backfill_pla05.py`, `data/rigora.db` |

### Estado atual
- `js/snapshots.js` em uso: cópia manual de 14/11/2026. O arquivo gerado pelo exportador foi validado (B5) e ainda não foi publicado.
- Validação: `python tools/portao.py --gerado CAMINHO` executa a suíte JavaScript em três fusos, a suíte Python com o pytest real e o verificador do snapshot com seus autotestes.

### Limitações conhecidas (atualizadas)
- Lançamentos novos ficam salvos só neste navegador: não há conta, servidor nem sincronização.
- Custos da propriedade continuam demonstrativos; Conab não integrada; Cepea transcrito (sem API aberta).
- Previsão ao vivo do INMET com dia sem umidade completa: o dia é omitido, com aviso no card. Pela regra da M1, essa resposta não serve de reserva; na reavaliação de 30 min, a tela pode mostrar a cópia salva até a nova consulta responder.

---

## 14/11/2026 — Evolução v2


## O que mudou

### Confiabilidade dos dados
- **Propriedade demonstrativa recalibrada**: Sítio Santa Rita do Cerrado, 12 ha de arábica em 3 talhões, ~21,8 sc/ha em 2026 (antes: 42,5 ha e 38,4 sc/ha, o dobro da média oficial de Patrocínio).
- **Fonte única de cálculo** (`js/metrics.js`): cards, gráficos, simulador, alertas e automações usam as mesmas funções. A produtividade deixou de divergir entre telas (38,4 × 38,5 × "+9,7%" × "+107,6%").
- **Livro-caixa** como origem dos custos: lançamentos por talhão, rateio por área dos lançamentos gerais, colheita, vendas e estoque.
- **Classificação visível em toda a interface**: Oficial (IBGE, INMET, Cepea, Conab), Estimado (cálculo documentado) e Demonstrativo (propriedade fictícia).
- **Benchmark só com dados oficiais**: municípios com números inventados removidos; ranking e séries vêm do IBGE/PAM (SIDRA 1613, café arábica, 2016–2025).
- **Preço da saca**: série mensal 2026 do Indicador CEPEA/ESALQ (antes R$ 1.250 fixo). Set/2026 não localizado e exibido como lacuna.
- **Comparações apenas no mesmo período**: sítio × IBGE no mesmo ano de colheita; custo 2027 × 2026 no mesmo intervalo do ciclo.
- **Badge de desempenho** mostra variações negativas.
- **Alerta de umidade do solo removido** (não há sensor). O risco de ferrugem virou estimativa documentada sobre a previsão do INMET.
- **Contexto temporal**: todos os indicadores mostram a data de referência; atividades e alertas de março saíram.

### Integrações
- IBGE/SIDRA e INMET mantidos, agora com timeout, validação da resposta, registro de integração e fallback para **cópia oficial datada** (`js/snapshots.js`, coletada em 14/11/2026). Nenhum valor inventado aparece quando a API falha.
- Botão "Atualizar do IBGE" refaz a consulta completa de MG ao vivo.

### Experiência
- Visão Geral: filtro por talhão, safra (topo), comparação com safra anterior, KPIs clicáveis com painel "de onde vem este número", gráficos com tooltip, categoria de custo clicável (abre o livro-caixa filtrado).
- Previsão de 5 dias do INMET separando previsão oficial de leitura agronômica estimada.
- Central de alertas com categorias (financeiro, produtividade, clima, qualidade dos dados), prioridade, data, fonte, impacto, ação, filtro e "marcar como visto".
- Propriedade e Safras: cards de talhão selecionáveis, custo/ha e produtividade por talhão, livro-caixa filtrável, vendas × Cepea, estoque, novo lançamento com validação.
- Inteligência: simulador (área, produtividade, preço, custo/ha, perdas) com margem, preço e produtividade de equilíbrio, impacto de R$ 100/saca, curva de sensibilidade e cenários conservador/base/otimista editáveis; benchmark oficial com filtros de ano, região e município; mercado Cepea com vendas do sítio.
- Automações ligadas aos alertas (limites editáveis, teste mostra os dados de entrada).
- Fontes: área de transparência com link, coleta, metodologia, limitações e status ao vivo/cópia.
- Responsivo (desktop, tablet e celular) e navegação por hash (#overview, #intelligence…).

## Arquivos
- Modificados: `index.html`, `css/style.css` (bloco v2 ao final), `js/app.js`, `js/charts.js`, `js/data.js`
- Novos: `js/metrics.js`, `js/snapshots.js`, `tests/metrics.test.js`, `CHANGELOG.md`

## Testes
- `node tests/metrics.test.js` — 13 testes de cálculo e consistência (somas do livro-caixa, rateio, produtividade, resultado, simulador, ponto de equilíbrio, validação).
- Teste ponta a ponta em navegador (desktop 1440 px e celular 390 px): login, filtros, painel de KPI, novo lançamento, simulador, cenários, benchmark, automações e fontes, sem erros de JavaScript e sem rolagem horizontal.

## Limitações conhecidas
- Custos da propriedade são demonstrativos: a planilha de custo da Conab para Patrocínio ainda não foi integrada.
- Cepea não tem API aberta: a série foi transcrita com a fonte de cada mês.
- A API de previsão do INMET não traz volume nem probabilidade de chuva.
- Talhões sem coordenadas reais: não há mapa (não inventamos geometria).
- Lançamentos novos ficam só na sessão do navegador.
