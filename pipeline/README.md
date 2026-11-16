# Pipeline de dados da Rigora

Fluxo: **Extração (IBGE/SIDRA e INMET, independentes) → Transformação (pandas) → Validação de qualidade → SQLite** (`data/rigora.db`). → **`js/snapshots.js`** (etapa 3e), a cópia oficial que a interface usa quando a consulta ao vivo falha.

## Status

| Etapa | Conteúdo | Situação |
|---|---|---|
| 3a | Extração IBGE/SIDRA (PAM 1613, café arábica, MG, 2016–2025) + tratamento + testes | Pronta |
| 3b | Validação + SQLite (`data/rigora.db`) + histórico de execuções + consultas prontas | Pronta (validada com fixtures; coleta ao vivo executada em 14/11/2026: execuções 1 a 3) |
| 3c | Camada de qualidade: 26 regras em 4 dimensões, relatório por execução, rastreabilidade por registro | Pronta (validada com fixtures e com os 600 registros oficiais da cópia de 14/11/2026; coleta ao vivo executada em 14/11/2026, execução 3) |
| 3d | Previsão do INMET (Patrocínio): extração, tratamento, 14 regras MET, tabela própria com histórico, fontes independentes | Pronta (validada com a previsão real de 14/11/2026 e respostas simuladas; coleta ao vivo executada em 14/11/2026, execução 1) |
| 3e | Geração automática de `js/snapshots.js` a partir do SQLite: contrato preservado, escrita atômica, rastreabilidade | Pronta (validada com fixtures; exportação de teste com o banco real aprovada na B5, em cópias fora do projeto; **ainda não publicada**) |
| 4 | Preenchimento histórico dos grupos da PLA-05 na execução 3 (`pipeline/backfill_pla05.py`) | Executada (etapa 4e, 14/11/2026; conferências P00–P19 aprovadas) |
| C02 | Um único horário (`data_execucao`) para as duas execuções de uma chamada do pipeline, como as conferências C02/P02 exigem | Pronta (15/11/2026) |
| B4 | Cabeçalho do `js/snapshots.js` gerado fiel ao comportamento: IBGE é a base exibida, INMET é reserva pelas regras da M1 | Pronta (15/11/2026) |
| Bloco 1 | Portão de qualidade `tools/portao.py` (seção abaixo); resíduos arquivados em `_arquivo/` | Pronto (15/11/2026) |
| Bloco 3 | Publicação protegida `python -m pipeline.publicar` (seção abaixo) e `--saida` obrigatório no exportador | Pronto e validado no Linux e no Windows (16/11/2026); **primeira coleta real e primeira publicação feitas em 16/11/2026** |
| 1.0 | Estabilidade: regra INT-08 (coleta não apaga dado válido), backup integral de `data/` (`pipeline/backup_dados.py`), referências B5 versionadas (`pipeline/referencias.py`) | Pronto (16/11/2026); validado no Linux |

O `js/snapshots.js` em uso é, desde 16/11/2026, o **primeiro snapshot gerado e publicado pelo pipeline** (SHA-256 `ed9d1cfea2456536…`), com o IBGE da execução 5 e a previsão do INMET de 16 a 20/11/2026. A cópia manual de 14/11/2026 continua como fixture e referência B5 histórica (`tests/fixtures/snapshots_manual_20261114.js`) e como backup da publicação. Novas publicações só acontecem pelo `python -m pipeline.publicar`.

## Como rodar (Python 3.10+)

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q                 # testes, sem internet
python -m pipeline.extract_sidra --dry-run # mostra as URLs consultadas
python -m pipeline.extract_sidra           # coleta ao vivo -> data/raw/ (JSON bruto + .meta.json com hash)
python -m pipeline.transform               # trata -> data/processed/pam_mg_arabica.csv e pam_mg_totais.csv

python -m pipeline.run_pipeline             # IBGE + INMET: coleta ao vivo -> trata -> valida -> grava (cada fonte isolada)
python -m pipeline.run_pipeline --fontes inmet   # só a previsão do INMET
python -m pipeline.extract_inmet --dry-run  # mostra a URL do INMET
python -m pipeline.run_pipeline --sem-coleta   # reprocessa a coleta mais recente de data/raw/ (não acessa a internet)

python -m pipeline.export_snapshots --saida ARQUIVO.js [--db BANCO.db]   # gera um snapshot (--saida obrigatório; não acessa a internet)
python -m pipeline.publicar                 # simulação da publicação protegida: não coleta, não publica (seção abaixo)

python tools/portao.py                      # portão de qualidade: valida o projeto inteiro (seção abaixo)
```

Atualização dos dados da interface: pelo `python -m pipeline.publicar` (seção "Publicação protegida"). O exportador sozinho recusa gravar no `js/snapshots.js` em uso.

Saída do `run_pipeline`: ids das execuções, registros gravados, total no banco, relatório de validação e checagem de integridade. Código de saída 1 se qualquer fonte falhar; a outra fonte é gravada normalmente.

A coleta completa de MG leva cerca de 30 s. Em caso de falha de rede, há 3 tentativas com espera crescente e o erro é explícito; nenhum dado substituto é gerado.

## Portão de qualidade (`tools/portao.py`)

Um comando valida o projeto inteiro e responde **APROVADO** (código 0), **REPROVADO** (1) ou **INCOMPLETO** (2). Só aprova se todas as etapas rodaram de verdade e passaram.

```bash
python tools/portao.py                                              # Linux (snapshot gerado pela simulação da publicação)
python tools\portao.py                                              # Windows
python tools/portao.py --gerado CAMINHO/snapshots_gerado.js         # verificador sobre um arquivo escolhido, fora do projeto
python tools/portao.py --json FORA_DO_PROJETO/portao.json           # relatório em JSON (recusado dentro do projeto)
```

| Etapa | O que faz | Reprova quando |
|---|---|---|
| Inventário inicial | SHA-256 de todos os arquivos; marca os protegidos (`data/`, `js/snapshots.js`, `tests/fixtures/`) | — |
| Ambiente | Versões do Python, do pytest e do Node | pytest ou Node ausente: INCOMPLETO |
| JavaScript | `node tests/metrics.test.js` em America/Sao_Paulo, UTC e Asia/Tokyo; antes, confere se o Node respeitou o fuso (`TZ`) | algum teste falha, a suíte não imprime o resumo ou o Node ignora o fuso |
| Python | `python -m pytest -q -rA -p no:cacheprovider`, com o **pytest real** | falha, erro, nenhum teste coletado; testes ignorados dão INCOMPLETO |
| Suítes obrigatórias | Cada arquivo de `OBRIGATORIOS` (exportador, portão, publicação protegida) existe e tem testes aprovados | arquivo ausente, sem testes aprovados, ou com teste que falhou ou foi pulado |
| Simulação da publicação | `python -m pipeline.publicar` numa pasta temporária fora do projeto: sem coleta, sem publicação | a simulação reprova; sem Node: INCOMPLETO |
| Verificador | `tests/check_exported_snapshot.js` e `--autoteste` nos três fusos, com o snapshot de `--gerado` (ou, sem ele, o da simulação) e a fixture como referência | o verificador ou um autoteste reprova; sem snapshot: INCOMPLETO |
| Inventário final | Compara com o inicial | qualquer arquivo criado, alterado ou removido, protegido ou não |

O portão não grava nada no projeto (pytest sem cache e sem `.pyc`) e não coleta dados. A simulação da publicação lê o banco para copiá-lo e gera o snapshot numa pasta temporária fora do projeto; o inventário final prova que nada no projeto mudou. Pode ser repetido à vontade. Dependências: Python 3.10+ com `pip install -r requirements-dev.txt` e Node.js. A suíte JavaScript para na primeira falha (comportamento do próprio `tests/metrics.test.js`), então o número de aprovados de uma execução que falhou é parcial. Testado no Linux (Python 3.10, pytest 9.2, Node 22) e, até o Bloco 2, no Windows (Python 3.11, pytest 9.2, Node 26). Em 16/11/2026 o portão completo, com as etapas do Bloco 3, foi aprovado também no Windows (Python 3.11.9, Node 26.13.0).

## Publicação protegida do `js/snapshots.js` (`pipeline/publicar.py`, Bloco 3)

O exportador só gera arquivos. Quem coloca um snapshot gerado no lugar do `js/snapshots.js` em uso é o
orquestrador `python -m pipeline.publicar`, com verificação, backup, substituição atômica e restauração.
Ele separa as etapas: **coleta** (`pipeline.run_pipeline`) → **exportação** (`pipeline.export_snapshots`,
numa pasta temporária) → **verificação** → **publicação**.

> **Primeira coleta e primeira publicação: 16/11/2026, no Windows.** A coleta (`--coletar`) trouxe o IBGE na
> execução 5, com o bruto idêntico ao de 14/11 e zero diferenças contra a referência B5, e o INMET na execução 6.
> A publicação (`--publicar --confirmar-atual 410fa9cb428b`) guardou o snapshot anterior em
> `rigora_backups/publicacoes/20261116T135727Z_publicacao/` e publicou `ed9d1cfea2456536…`. Registros completos
> em `rigora_backups/primeira_coleta_20261116_1053/`. O portão final daquele procedimento foi interrompido
> (Ctrl+C) no pytest; a validação depois da publicação foi refeita no projeto: 446 testes Python, 111 testes
> JavaScript nos três fusos e verificador B5 aprovado nos três fusos sobre o snapshot publicado.

### Modos (um por execução)

| Comando | O que faz | Altera dados? |
|---|---|---|
| `python -m pipeline.publicar` | **Simulação** (padrão) | Não. Nada é coletado nem publicado |
| `python -m pipeline.publicar --coletar` | Coleta real (IBGE e INMET) no banco definitivo, com backup verificado do banco antes; depois, a simulação | Só o banco (a coleta); nunca publica |
| `python -m pipeline.publicar --publicar --confirmar-atual HASH` | Publica com os dados que já estão no banco, sem nova coleta | `js/snapshots.js` |
| `python -m pipeline.publicar --coletar --publicar --confirmar-atual HASH` | Coleta e publica | Banco e `js/snapshots.js` |
| `python -m pipeline.publicar --restaurar PASTA --confirmar-atual HASH` | Volta a um snapshot guardado num backup | `js/snapshots.js` |

Proteções contra execução acidental:

- **`HASH`** é o SHA-256 (12 a 64 caracteres) do `js/snapshots.js` publicado **agora**, mostrado pela simulação, ou `ausente` se não houver arquivo. Sem `--confirmar-atual`, `--publicar` e `--restaurar` preparam e validam tudo, mostram o plano e **param sem alterar nada** (código 2). Se o arquivo mudar depois da simulação, a confirmação antiga deixa de valer.
- **Sem previsão do INMET válida**, publicar exige `--aceitar-sem-inmet`. O motivo fica no relatório e em `exportacao.inmet.motivo`.
- **Combinações recusadas** (código 3, nada é alterado): `--restaurar` com `--publicar` ou `--coletar`; `--confirmar-atual` sem `--publicar`/`--restaurar`; `--aceitar-sem-inmet` sem `--publicar`; `--fontes` sem `--coletar`; `--trabalho`, `--relatorio` ou `--backups` dentro do projeto; abreviações de opções.
- Qualquer erro interrompe antes de alterar o destino. Um erro nunca é tratado como autorização para seguir.

### Simulação (`python -m pipeline.publicar`)

1. Confere o ambiente e se sobrou arquivo temporário de uma publicação interrompida (aviso).
2. Copia o banco para uma pasta temporária (o original só é lido; o hash é conferido antes e depois) e copia o snapshot publicado para ser o "arquivo anterior" do exportador, como na B5.
3. Gera o snapshot com o exportador nessa pasta.
4. Valida: contrato do exportador; procedência (cada `execucaoId` de `exportacao` existe no banco com status `sucesso`, mesma data de coleta, mesmo arquivo bruto e mesmo hash, e o hash do arquivo bruto em `data/raw/` confere); atualidade do INMET; verificador B5 (`tests/check_exported_snapshot.js`, no fuso de Brasília, o mesmo do exportador).
5. Ensaia o backup, o arquivo temporário, a substituição e a conferência numa **cópia** do destino, dentro da pasta temporária.
6. Mostra o plano: origem, destino, hash atual, hash novo, onde ficaria o backup e o comando de publicação, marcado como não executado.

A simulação não acessa a internet e não precisa de coleta: usa os dados que já estão no banco. Se uma conferência depende de algo ausente (por exemplo, Node.js para o verificador B5), o resultado é INCOMPLETO e a ausência é informada; nada é dado como verificado. `--trabalho PASTA` (pasta nova, fora do projeto) guarda os arquivos da simulação para conferência; sem ela, a pasta temporária é apagada no fim.

### Coleta real (futura)

`python -m pipeline.publicar --coletar` (ou `--fontes inmet`, `--fontes sidra`) faz a coleta ao vivo pelo `run_pipeline`, depois de um **backup integral e verificado de `data/`** em `../rigora_backups/publicacoes/<data>_coleta/` (seção "Backup integral de `data/`"). Se o banco tiver arquivo `-journal`, `-wal` ou `-shm`, ou se o backup não conferir, a coleta não começa. Se qualquer fonte falhar, nada é gerado nem publicado (código 1). Sozinho, `--coletar` termina com a simulação da publicação.

### Preparar uma publicação

1. Rode `python tools/portao.py` e confira o resultado APROVADO.
2. Rode `python -m pipeline.publicar` e leia o plano: hashes, datas de coleta, se o INMET entra e o comando sugerido.
3. Só então, com autorização, rode o comando do plano. Ele gera e valida de novo no momento da publicação.

### Atualidade do INMET

A previsão entra se foi coletada há **até 24 h, inclusive** (`INMET_MAX_IDADE_HORAS`, a mesma regra da M1 na interface), com datas válidas, de hoje em diante (horário de Brasília), e campos completos. A idade é conferida três vezes: pelo exportador na geração, na validação e de novo logo antes da troca. Se a previsão vencer no meio da publicação, nada é publicado. Previsão vencida não é reaproveitada nem preenchida: sai `inmet: null` com o motivo.

### Backup

Antes de qualquer troca, o snapshot publicado é copiado para `../rigora_backups/publicacoes/<AAAAMMDDTHHMMSSZ>_publicacao/snapshots.js` (fora do projeto; outra pasta com `--backups`), gravado no disco e conferido pelo SHA-256, que fica também em `snapshots.js.sha256`. A pasta é sempre nova (nome repetido ganha `_2`, `_3`…). Backups nunca são apagados nem sobrescritos. O relatório da operação (`relatorio.json` e `relatorio.txt`) fica na mesma pasta, inclusive quando a publicação falha.

### Substituição atômica

1. Confere permissão de escrita e espaço livre no destino e na pasta de backup.
2. Grava o arquivo novo em `js/.snapshots.js.publicando.tmp`: mesma pasta, portanto mesmo sistema de arquivos; criado em modo exclusivo (falha se outra publicação estiver em andamento), gravado no disco (`fsync`), fechado e com hash conferido.
3. Logo antes da troca, confere de novo que o publicado não mudou e que o INMET ainda vale.
4. Troca com `os.replace` (atômico no Windows e no Linux). Se o Windows recusar por arquivo em uso (antivírus, editor), tenta de novo até 5 vezes. No Linux, a entrada da pasta também é gravada no disco.
5. Confere o arquivo publicado: hash igual ao validado e todas as conferências de novo, incluindo o verificador B5.

Se a troca for recusada, o anterior continua no lugar. Se a conferência do publicado falhar ou houver interrupção (inclusive Ctrl+C) depois da troca, o backup é restaurado pelo mesmo mecanismo e o hash restaurado é conferido (resultado "PUBLICAÇÃO DESFEITA"). O arquivo rejeitado fica no backup como `snapshots.js.rejeitado`. Se a restauração não puder ser confirmada, o resultado é FALHA GRAVE (código 4), com a instrução de cópia manual. Queda de energia no meio: o destino fica com o arquivo anterior ou com o novo, nunca com um pedaço; o temporário que sobrar bloqueia a próxima publicação até ser conferido e removido à mão.

### Recuperar uma versão anterior

```bash
python -m pipeline.publicar --restaurar ../rigora_backups/publicacoes/<pasta>                        # mostra o plano e para
python -m pipeline.publicar --restaurar ../rigora_backups/publicacoes/<pasta> --confirmar-atual HASH
```

Confere o backup contra `snapshots.js.sha256`, guarda o arquivo que está publicado num backup novo (`_restauracao`) e troca pelo mesmo mecanismo. Sem o orquestrador, a recuperação manual é copiar `snapshots.js` da pasta do backup para `js/snapshots.js` e conferir o SHA-256.

### Relatórios e códigos de saída

Cada etapa sai numa linha (`ok`, `FALHA`, `aviso`, `info`), seguida de `Resultado: …`. `--relatorio ARQ` (fora do projeto) grava o mesmo em JSON.

| Código | Resultado | O que significa |
|---|---|---|
| 0 | SIMULAÇÃO APROVADA, COLETA CONCLUÍDA E SIMULAÇÃO APROVADA, PUBLICADO, RESTAURADO | Concluído |
| 1 | REPROVADO, PUBLICAÇÃO DESFEITA | Algo falhou; o destino ficou como estava ou foi restaurado, com hash conferido |
| 2 | INCOMPLETO | Falta requisito, confirmação ou decisão sobre o INMET; nada foi alterado |
| 3 | erro de uso | Combinação de opções recusada; nada foi alterado |
| 4 | FALHA GRAVE | A restauração não pôde ser confirmada; o relatório diz onde está o backup |

### Limitações conhecidas

- **Windows:** portão, coleta real e publicação executados no Windows em 16/11/2026 (Python 3.11.9, pytest 9.2.0, Node 26.13.0). O procedimento v2 (`coleta_e_publicacao_windows_v2.bat`, com rollback automático depois da troca e verificador com a referência ativa) está preparado, mas ainda não foi validado integralmente numa execução real.
- **Referência do verificador B5:** o verificador compara com a referência ativa (seção "Referências B5 versionadas"), hoje a fixture de 14/11/2026; qualquer diferença de dado reprova até uma pessoa aprovar uma nova referência. O verificador também exige 60 municípios e os anos 2016–2025.
- **Interrupção:** Ctrl+C depois da troca leva à restauração; antes da troca, nada muda. Um processo encerrado à força (sem chance de reagir) pode deixar o temporário, que bloqueia a próxima publicação até ser conferido.

## Estabilidade 1.0: preservação, backup integral e referências B5

### Coleta não apaga dado válido (regra INT-08)

O SIDRA responde em grade (município × ano) e o tratamento completa a grade com ausentes; sem proteção, uma resposta parcial gravaria ausentes por cima de valores válidos (UPSERT). A regra **INT-08** (`pipeline/validate.py`, chamada em `load_sqlite.load_pam`) compara, **antes da gravação e na mesma transação**, cada registro do banco com o que a nova coleta traz:

- valor gravado que viria ausente → a regra é acionada (erro), **nada é gravado**, a execução fica como `falha`, cada registro afetado vai para `validacao_ocorrencia` e para o relatório de qualidade;
- valor revisado (número → outro número) → aceito; a revisão é conferida depois contra a referência B5;
- registro que não vem na resposta (outro período, município fora da resposta) → não é tocado e continua como estava (comportamento da 3b);
- primeira carga (banco vazio) → não há o que comparar e a regra não entra (as 26 regras de sempre).

Não há aceitação parcial: se o IBGE passar a publicar como ausente um valor que existia, a carga é recusada e a decisão é humana.

### Backup integral de `data/` (`pipeline/backup_dados.py`)

```bash
python -m pipeline.backup_dados criar                      # ../rigora_backups/dados/<data>_dados (fora do projeto)
python -m pipeline.backup_dados verificar PASTA_DO_BACKUP
python -m pipeline.backup_dados restaurar PASTA_DO_BACKUP --destino PASTA_NOVA
```

- Copia **todos** os arquivos de `data/` (banco, brutos, processados, relatórios), byte a byte, gravados no disco e conferidos; grava `MANIFESTO.json` e `MANIFESTO.sha256` (caminho, tamanho, SHA-256).
- Recusa se o banco tiver `-journal`, `-wal` ou `-shm`, se o destino ficar dentro de `data/` ou do projeto, e falha se algum arquivo mudar durante a cópia.
- `verificar` acusa arquivo alterado, ausente ou fora do manifesto.
- `restaurar` nunca sobrescreve: recupera numa pasta nova e confere todos os hashes. A troca é manual, com o Rigora fechado: renomeie `data` para `data_danificado`, renomeie a pasta restaurada para `data` e rode `verificar` de novo se quiser.
- O `python -m pipeline.publicar --coletar` cria e verifica esse backup antes de coletar; sem backup válido, a coleta não começa.

### Referências B5 versionadas (`pipeline/referencias.py`)

```bash
python -m pipeline.referencias listar
python -m pipeline.referencias propor --gerado ARQ_GERADO.js
python -m pipeline.referencias aprovar ID --confirmar SHA256_12+ --responsavel "NOME" --justificativa "MOTIVO"
```

- Registro: `tests/fixtures/referencias_b5/registro.json`. A referência histórica (`tests/fixtures/snapshots_manual_20261114.js`, sha256 `410fa9cb…`) nunca é alterada nem removida e é a ativa hoje.
- `propor` exige um snapshot gerado pelo pipeline (seção `sidra` do banco e previsão do INMET, da qual o verificador deriva os cenários da M1), copia para um arquivo novo `ref-<data>-exec<N>.js` e registra hash, execuções, datas de coleta, hash do bruto e as diferenças apontadas pelo próprio verificador B5. Fica como **proposta**: não vale para nada.
- `aprovar` exige o hash do arquivo, o responsável e a justificativa; a anterior continua no registro e no disco.
- `python -m pipeline.publicar` e `python tools/portao.py` usam a referência **ativa** (hash conferido); `--referencia` escolhe outra.
- Como classificar diferenças: **regressão** (o bruto antigo reprocessado já não reproduz a referência; ou bruto novo com o mesmo hash e alguma diferença); **revisão legítima** (hash do bruto diferente e cada diferença explicada por uma linha alterada no bruto, com a validação aprovada); **divergência inesperada** (diferença sem correspondência no bruto, troca de municípios entre os 60, mudança de estrutura).

## Decisões

- **Sem `requests`**: HTTP com `urllib` (biblioteca padrão). Única dependência: pandas.
- **Dado bruto preservado**: cada coleta salva a resposta exata do IBGE, com data/hora UTC e SHA-256.
- **Sinais do SIDRA**: `-` vira 0; `..`, `...` e `X` viram ausência (NaN). Rendimento sem área colhida vira ausência, não zero.
- **Média de MG** = produção total ÷ área colhida total, só com municípios que produziram no ano.
- **Regressão**: os testes exigem que o tratamento reproduza a cópia oficial usada hoje pelo protótipo (ex.: Patrocínio 2025 = 1.110 kg/ha = 18,5 sc/ha).

## Banco de dados (`data/rigora.db`)

O arquivo fica fora do Git (`.gitignore`); qualquer pessoa o recria com `python -m pipeline.run_pipeline`.

### `execucao` — histórico de cada processamento

| Coluna | Tipo | Regra | Para quê |
|---|---|---|---|
| `id` | INTEGER | chave primária, autoincremento | identifica a execução |
| `fonte` | TEXT | obrigatório | ex.: `IBGE/SIDRA PAM 1613 (café arábica, MG)` |
| `data_coleta` | TEXT (ISO 8601, UTC) | obrigatório | quando o IBGE respondeu (vem do `.meta.json`) |
| `data_execucao` | TEXT (ISO 8601, UTC) | obrigatório | quando o pipeline processou; necessário porque um mesmo bruto pode ser reprocessado |
| `status` | TEXT | `em_andamento`, `sucesso`, `falha`, `interrompida` | resultado |
| `quantidade_registros` | INTEGER | ≥ 0 | linhas gravadas (0 em falha) |
| `arquivo_origem` | TEXT | obrigatório | nome do JSON bruto em `data/raw/` |
| `hash_sha256` | TEXT | 64 caracteres | prova de que o bruto não mudou desde a coleta |
| `mensagem` | TEXT | opcional | erro (falha) ou relatório de validação em JSON (sucesso) |

Cada rodada cria duas execuções: uma para a PAM e uma para as localidades (nomes e mesorregiões), que também são um arquivo bruto oficial.

### `pam_municipio_ano` — Produção Agrícola Municipal tratada

| Coluna | Tipo | Regra | Observação |
|---|---|---|---|
| `codigo_ibge` | TEXT | 7 caracteres; parte da chave primária | texto para não perder zeros e evitar contas sobre códigos |
| `municipio` | TEXT | obrigatório | nome oficial do IBGE |
| `regiao` | TEXT | opcional | mesorregião IBGE |
| `ano` | INTEGER | parte da chave primária | ano da PAM |
| `rendimento_kg_ha` | REAL | NULL ou > 0 | publicado pelo IBGE; **ausência = NULL, nunca 0** |
| `produtividade_sc_ha` | REAL | coluna gerada: `rendimento_kg_ha / 60` | calculada pelo banco, não pode divergir do rendimento |
| `area_colhida_ha` | REAL | NULL ou ≥ 0 | hectares |
| `producao_t` | REAL | NULL ou ≥ 0 | toneladas |
| `execucao_id` | INTEGER | chave estrangeira → `execucao(id)`, `ON DELETE RESTRICT` | execução que gravou o valor atual |

Chave primária `(codigo_ibge, ano)`. Índices: `ix_pam_ano` (rankings por ano), `ix_pam_execucao` (rastrear o que uma execução gravou), `ix_execucao_fonte` (histórico por fonte).

### `validacao_grupo` e `validacao_grupo_membro` — grupos da PLA-05 por execução (etapas 3a e 3b)

As duas tabelas guardam, para cada execução, os grupos de coincidência da PLA-05 e os municípios de cada grupo, com os valores, o nível, os limites da faixa e os nomes **daquela execução**. Não dependem de `pam_municipio_ano`, que é atualizada por UPSERT, e não têm chave estrangeira para ela, de propósito. Estrutura criada na etapa 3a; gravação pelo pipeline desde a etapa 3b (ver "Gravação" abaixo).

`validacao_grupo` — uma linha por grupo:

| Coluna | Tipo | Regra | Observação |
|---|---|---|---|
| `execucao_id` | INTEGER | chave estrangeira → `execucao(id)`, `ON DELETE RESTRICT` | execução que identificou o grupo |
| `regra` | TEXT | só `'PLA-05'`; com `execucao_id`, chave estrangeira → `validacao_regra`, `ON DELETE RESTRICT` | |
| `grupo` | INTEGER | ≥ 1 | número do grupo **dentro da execução** (o mesmo do JSON); não é identificador global |
| `ano` | INTEGER | obrigatório | ano da PAM |
| `nivel` | TEXT | `informativo`, `atencao` ou `revisao_documental` | triagem interna do Rigora, não avaliação dos dados do IBGE |
| `rendimento_kg_ha` | REAL | > 0 | valor coincidente |
| `produtividade_sc_ha` | REAL | coluna gerada: `rendimento_kg_ha / 60.0` | mesma convenção de `pam_municipio_ano` |
| `area_colhida_ha` | REAL | > 0 | valor coincidente |
| `producao_t` | REAL | ≥ 0 | valor coincidente |
| `total_municipios` | INTEGER | ≥ 1 | municípios distintos do grupo |
| `total_registros` | INTEGER | ≥ 2 e ≥ `total_municipios` | difere de `total_municipios` só com chave repetida (INT-01) |
| `faixa_acima_de_ha`, `faixa_ate_ha` | REAL | opcionais (NULL = sem limite) | limites da faixa usados naquela execução |

Chave primária `(execucao_id, regra, grupo)`. Únicos: `(execucao_id, regra, grupo, ano)` (alvo da chave do membro) e `(execucao_id, regra, ano, rendimento_kg_ha, area_colhida_ha, producao_t)` (sem grupo repetido). Índice `ix_grupo_nivel (execucao_id, nivel)`.

`validacao_grupo_membro` — uma linha por município de um grupo:

| Coluna | Tipo | Regra | Observação |
|---|---|---|---|
| `execucao_id`, `regra`, `grupo`, `ano` | | chave estrangeira composta → `validacao_grupo(execucao_id, regra, grupo, ano)`, `ON DELETE RESTRICT` | o membro só existe num grupo da mesma execução e do mesmo ano |
| `codigo_ibge` | TEXT | 7 caracteres | como em `pam_municipio_ano` |
| `municipio` | TEXT | opcional | nome nos dados validados daquela execução; NULL se ausente (nunca inventado) |

Chave primária `(execucao_id, regra, grupo, codigo_ibge)`. Único `(execucao_id, regra, ano, codigo_ibge)`: um município fica em um único grupo por ano, na mesma execução (em anos ou execuções diferentes, pode repetir). Índice `ix_membro_municipio (codigo_ibge, ano)`.

- **Exclusões bloqueadas:** apagar uma execução, a regra PLA-05 de uma execução ou um grupo que tenha dependentes é recusado (`RESTRICT`). Para apagar, a ordem explícita é membros, depois grupos, depois regras.
- **Gravação (etapa 3b):** `record_validation()` grava os grupos junto com as regras e ocorrências, na mesma transação da execução (carga com sucesso em `load_pam`, ou registro da falha em `fail_execution`). A fonte é só `report.secoes["pla05_grupos"]`, a mesma seção do relatório JSON: números dos grupos, ordem, níveis, valores, totais e nomes são copiados dela, sem recalcular nada e sem consultar `pam_municipio_ano`. Os limites da faixa vêm de `classificacao.niveis` daquela execução. A produtividade é a coluna gerada do banco.
- **Quando não há linhas:** PLA-05 aprovada sem coincidências, PLA-05 não verificada (base vazia) e execuções do INMET não gravam nada nessas tabelas. Uma execução reprovada pela validação grava os grupos do seu relatório de falha (como o JSON), sem dados em `pam_municipio_ano`.
- **Regravação e histórico:** regravar a validação da mesma execução apaga membros, depois grupos, depois regras, e grava de novo: o resultado é idêntico, sem duplicar. Cada execução nova tem seus próprios grupos (numerados a partir de 1); os de execuções anteriores não mudam, nem quando uma coleta nova atualiza nomes ou valores em `pam_municipio_ano`.
- **Atomicidade:** qualquer erro na gravação (de dados, regras, ocorrências, grupos ou membros) desfaz a transação inteira; não ficam grupos ou membros parciais. Uma seção com `versao` desconhecida é recusada com erro explícito.
- **Criação no banco existente:** as tabelas usam `CREATE TABLE IF NOT EXISTS` e são criadas por `load_sqlite.connect()`; as tabelas e os dados atuais não mudam. No `data/rigora.db`, elas foram criadas em 14/11/2026 pela gravação do preenchimento histórico (etapa 4e; ver "Preenchimento histórico dos grupos da PLA-05"). Em outro banco que ainda não as tenha, são criadas na primeira execução do pipeline com esta versão: **faça um backup do banco antes**. `PRAGMA user_version` não é usado.

### Garantias

- **Sem duplicação**: chave primária + `INSERT … ON CONFLICT DO UPDATE` (UPSERT).
- **Sem perda indevida**: o que não vier numa coleta nova continua no banco; revisões do IBGE atualizam o valor e o `execucao_id`.
- **Sem estado parcial**: os dados e o fechamento da execução entram numa só transação. Qualquer erro desfaz a gravação e a execução fica como `falha`, com a mensagem.
- **Execuções que morreram no meio** (`em_andamento`) viram `interrompida` na rodada seguinte.
- **Bruto adulterado**: se o hash do arquivo não bater com o `.meta.json`, nada é gravado.

### Qualidade dos dados (`pipeline/validate.py`, etapa 3c)

Severidades: **erro** = impeditivo (a execução inteira é recusada, nada é gravado, o motivo fica registrado); **aviso** = os dados são gravados e cada registro afetado fica anotado; **info** = contagem. Nenhuma regra corrige valores; ausência continua NULL.

| Regra | Dimensão | Severidade | O que verifica | Origem |
|---|---|---|---|---|
| INT-01 | integridade | erro | chave única (código IBGE, ano) | 3b |
| INT-02 | integridade | erro | código com 7 dígitos da UF 31 | 3b |
| INT-03 | integridade | erro | município com nome | 3b |
| INT-04 | integridade | erro | ano dentro de 2016–2025 | 3b |
| INT-05 | integridade | aviso | código existe na lista oficial de municípios | 3c |
| INT-06 | integridade | aviso | nome no SIDRA = nome oficial (ignora acentos) | 3c |
| INT-07 | integridade | erro | no banco: chaves estrangeiras, duplicidade, execução válida (roda na transação de gravação) | 3b, agora registrada |
| COM-01 | completude | info | ausentes por campo e zeros reais (`-` do SIDRA) contados separadamente | 3b, ampliada |
| COM-02 | completude | aviso | registro com só parte dos valores | 3c |
| COM-03 | completude | aviso | anos faltando na coleta ou por município | 3c |
| COM-04 | completude | info | municípios sem produção em todo o período | 3c |
| COM-05 | completude | info | municípios da UF ausentes do SIDRA | 3c |
| COM-06 | completude | erro | rendimento igual a zero (ausência deve ser NULL) | 3b |
| COM-07 | completude | erro | rendimento sem área colhida | 3b |
| COM-08 | completude | erro | produção com área colhida zero | 3c |
| COM-09 | completude | aviso | área colhida com produção zero | 3c |
| CON-01 | consistência | aviso | rendimento ≈ produção ÷ área (tolerância 1%) | 3b |
| CON-02 | consistência | erro | diferença próxima de ×60 ou ×1000 (troca de unidade) | 3c |
| CON-03 | consistência | erro | unidade declarada pela fonte (campo MN) é a esperada; "não verificada" se a fonte não informar | 3c |
| CON-04 | consistência | erro | no banco: produtividade = rendimento ÷ 60 | 3c |
| CON-05 | consistência | aviso | município fora de 0,3×–2,5× a média dos demais municípios da UF no ano | 3c |
| PLA-01 | plausibilidade | erro | valores negativos | 3b |
| PLA-02 | plausibilidade | aviso | produtividade fora de 5–80 sc/ha | 3c |
| PLA-03 | plausibilidade | aviso | área colhida acima de 100.000 ha num município | 3c |
| PLA-04 | plausibilidade | aviso | variação anual de rendimento acima de 3× ou abaixo de 1/3 | 3c |
| PLA-05 | plausibilidade | aviso | valores idênticos em municípios diferentes no mesmo ano; cada grupo recebe um nível de triagem interna pela área (ver abaixo) | 3c; textos e níveis na etapa 1 da PLA-05 |

Limites calibrados nos 600 registros oficiais dos 60 maiores municípios (coleta de 14/11/2026): produtividade 13–50 sc/ha; razão município/MG 0,58–1,86; maior variação anual 2,58× (bienalidade de Patrocínio, 2022→2023). Nesses dados, só a PLA-05 é acionada: Caratinga e Botelhos têm valores idênticos em 2021, e Divino e Botelhos em 2016. São dados publicados; o aviso indica revisão, não erro confirmado.

### PLA-05: grupos e níveis de triagem interna

A PLA-05 marca os registros cujo rendimento, área colhida e produção são idênticos aos de outro município no mesmo ano (só registros com os três campos preenchidos e área maior que zero). O que ela detecta não mudou. Desde a etapa 1 da evolução da regra, os registros iguais formam um **grupo**, e cada grupo recebe um nível pela área colhida (`PLA05_FAIXAS_HA` em `config.py`):

| Nível | Área colhida do grupo |
|---|---|
| Informativo | até 10 ha, inclusive |
| Atenção | acima de 10 até 500 ha, inclusive |
| Revisão documental | acima de 500 ha |

- **O que os níveis são:** uma triagem interna do Rigora para organizar a leitura do relatório. Os limites foram definidos a partir da distribuição observada em uma única execução (SIDRA 3, 14/11/2026: 275 grupos, 726 registros) e podem ser revistos.
- **O que não são:** uma avaliação da qualidade dos dados do IBGE. Uma coincidência, isoladamente, não comprova erro. A severidade continua sendo aviso, e nenhum nível bloqueia a carga.
- **Texto da regra** (`validacao_regra.detalhe`): número de grupos, registros e municípios, com a contagem por nível.
- **Texto de cada ocorrência** (`validacao_ocorrencia.detalhe`): nível, ano, valores (padrão brasileiro, produtividade em sc/ha com 1 casa) e os municípios parceiros, com nome e código IBGE, em ordem de código. Grupos de até 4 municípios mostram todos os parceiros; nos maiores aparecem os 3 primeiros e "e mais N".
- **Histórico:** execuções gravadas antes desta mudança (como a SIDRA 3) mantêm o texto antigo ("…possível erro de origem"); só execuções novas usam o texto novo. Os registros marcados são os mesmos nos dois casos.

### Relatório e rastreabilidade

- `validacao_regra`: resultado de cada regra em cada execução (aprovada, acionada, não verificada), inclusive nas execuções que falharam.
- `validacao_ocorrencia`: cada registro apontado por uma regra, com o motivo.
- `execucao.mensagem`: resumo (analisados, aprovados, rejeitados, avisos, ausentes, sem cobertura, regras acionadas).
- `data/reports/qualidade_execucao_NNNN.json`: relatório completo da execução, com fonte, data de coleta, URL, arquivo bruto e hash.

#### Seção `pla05_grupos` no relatório JSON (etapa 2 da PLA-05)

Os relatórios do IBGE/SIDRA gravados a partir da etapa 2 trazem, depois de `regras`, a chave `pla05_grupos` com todos os grupos de coincidência da PLA-05. A seção não entra no retorno de `process()`, na saída do terminal nem em `execucao.mensagem`. Desde a etapa 3b, os mesmos grupos também são gravados nas tabelas `validacao_grupo` e `validacao_grupo_membro`, copiados desta seção. Relatórios do INMET não têm a seção.

| Chave | Conteúdo |
|---|---|
| `versao` | Versão da estrutura (1) |
| `regra` | `"PLA-05"` |
| `status_regra` | `acionada` (há grupos), `aprovada` (sem coincidências; totais zero e `grupos: []`) ou `nao_verificada` (base vazia; a regra não foi executada) |
| `classificacao` | Descrição da triagem interna, `faixas_ha` usados nesta execução e a legenda dos níveis (`informativo`, `atencao`, `revisao_documental`), com rótulo e limites |
| `resumo` | `total_grupos`, `total_registros`, `total_municipios` (distintos), `grupos_por_nivel` e `registros_por_nivel` |
| `grupos` | Um item por grupo: `grupo` (1 a n), `ano`, `nivel`, `rendimento_kg_ha`, `produtividade_sc_ha`, `area_colhida_ha`, `producao_t`, `total_municipios`, `total_registros` e `municipios` (`codigo_ibge` como texto e `municipio`) |

- **Ordem:** grupos por ano, rendimento, área e produção, em ordem crescente (a mesma dos textos da etapa 1); municípios por código IBGE. A mesma entrada produz sempre a mesma seção.
- **Valores:** inteiros quando o valor é inteiro, como o SIDRA publica; decimais ficam como estão. `produtividade_sc_ha` é exatamente `rendimento_kg_ha / 60`, sem arredondamento.
- **Nomes:** vêm dos dados validados nesta execução, nunca de uma consulta posterior à `pam_municipio_ano` (que é atualizada por UPSERT). Nome ausente fica `null`.
- **Limites gravados:** `classificacao.faixas_ha` registra os limites de `PLA05_FAIXAS_HA` usados naquela execução; se o `config.py` mudar, os relatórios antigos continuam explicando os próprios níveis.
- **Execução reprovada:** quando a validação recusa a coleta, o relatório de falha também traz a seção, com os grupos dos dados avaliados (que não entraram no banco).
- **`total_registros` e `total_municipios`:** são iguais, exceto quando o mesmo município aparece duas vezes no mesmo ano (chave repetida, rejeitada pela INT-01).
- **Relatórios anteriores:** não são regravados. Se a chave `pla05_grupos` não existe, o relatório é anterior à etapa 2; isso não significa "sem grupos" (é o caso de `qualidade_execucao_0003.json`).
- **O que a seção não é:** os níveis são uma classificação interna de triagem do Rigora, não uma avaliação da qualidade dos dados do IBGE. A coincidência, isoladamente, não comprova erro nos dados.

## Consultas

```bash
python -m pipeline.query historico               # execuções, mais recente primeiro
python -m pipeline.query municipio 3148103       # série de Patrocínio com a execução de cada valor
python -m pipeline.query ranking 2025 --top 10   # maiores produtividades do ano
python -m pipeline.query qualidade               # regras da última execução da PAM (ou: qualidade 7)
python -m pipeline.query rastrear 3148103 --ano 2025   # fonte, coleta, arquivo, hash, execução e ocorrências de um registro
python -m pipeline.query previsao                # previsão do INMET mais recente, com a data da coleta
```

### Grupos da PLA-05 (etapa 3c)

Consultas somente leitura sobre `validacao_grupo` e `validacao_grupo_membro`. Mostram o que foi gravado na execução de origem (valores, nível, faixa e nomes daquela execução), sem recalcular e sem consultar `pam_municipio_ano`.

```bash
python -m pipeline.query grupos                          # grupos da execução padrão (ver abaixo)
python -m pipeline.query grupos 3                        # grupos da execução 3
python -m pipeline.query grupos 3 --nivel revisao_documental   # só um nível
python -m pipeline.query grupo 3 16                      # resumo do grupo 16 da execução 3 e seus municípios
python -m pipeline.query grupos_municipio 3148103        # todos os grupos de Patrocínio, em todas as execuções
python -m pipeline.query grupos --db outro.db            # qualquer comando aceita --db
```

| Comando | Argumentos | O que mostra |
|---|---|---|
| `grupos` | `[EXECUCAO]` (inteiro positivo, opcional); `--nivel` `informativo`, `atencao` ou `revisao_documental` | Uma linha por grupo: execução e status, regra, grupo, ano, nível, faixa, rendimento (kg/ha), produtividade (sc/ha, 2 casas), área (ha), produção (t), municípios e registros. Sem `EXECUCAO`, usa a execução **mais recente do IBGE/SIDRA com status `sucesso` que tem grupos gravados** |
| `grupo` | `EXECUCAO GRUPO` (inteiros positivos, obrigatórios) | Dois blocos: o resumo do grupo (os mesmos campos, mais a data da coleta) e os municípios (código IBGE e nome), em ordem de código |
| `grupos_municipio` | `CODIGO_IBGE` (7 dígitos, obrigatório) | Todos os grupos em que o município aparece, em todas as execuções, **inclusive as reprovadas** (coluna `status_execucao`), da mais recente para a mais antiga, com o nome do município naquela execução |

Exemplo de saída (banco de teste montado com as fixtures, coincidência injetada entre Patrocínio e Manhuaçu em 2024):

```text
$ python -m pipeline.query grupo 2 2 --db teste.db
Grupo
execucao_id | data_coleta | status_execucao | regra | grupo | ano | nivel | faixa | rendimento_kg_ha | sc_ha | area_colhida_ha | producao_t | total_municipios | total_registros
2 | 2026-11-14T21:38:26+00:00 | sucesso | PLA-05 | 2 | 2024 | revisao_documental | acima de 500 ha | 1500.0 | 25.0 | 42830.0 | 64225.0 | 2 | 2

Municípios (2)
codigo_ibge | municipio
3139409 | Manhuaçu
3148103 | Patrocínio
```

Mensagens em vez de erros técnicos:

| Situação | Saída | Código de saída |
|---|---|---|
| Banco sem as tabelas de grupos (anterior à etapa 3a) | "Este banco ainda não tem as tabelas de grupos da PLA-05 (…). Elas são criadas na próxima execução do pipeline com a versão atual, e os grupos de execuções antigas podem ser preenchidos com python -m pipeline.backfill_pla05 (…); faça um backup do banco antes. Nada foi alterado." | 1 |
| Grupo ou execução inexistente | "Grupo 99 não encontrado na execução 3." / "Nenhum grupo PLA-05 na execução 999." | 1 |
| Município sem grupos | "O município 3100000 não aparece em nenhum grupo PLA-05 gravado neste banco." | 1 |
| Argumento inválido (código com outro tamanho, execução não numérica, nível fora da lista) | mensagem do argparse | 2 |

Limitações conhecidas:

- **Só o que foi gravado:** execuções processadas antes da etapa 3b não têm grupos nas tabelas. A SIDRA 3 do `data/rigora.db` também não tem a seção `pla05_grupos` no relatório (gravado antes da etapa 2); os grupos dela foram gravados pelo preenchimento histórico (etapa 4e, `pipeline/backfill_pla05.py`): 275 grupos e 726 membros.
- **Banco atual:** desde a etapa 4e, o `data/rigora.db` tem as duas tabelas e os grupos da execução 3; a mensagem "Este banco ainda não tem as tabelas…" só aparece em bancos anteriores à etapa 3a que não passaram pelo preenchimento histórico. As consultas não criam tabelas nem alteram o banco (a conexão é aberta em modo somente leitura).
- **Valores exibidos:** os números aparecem como o SQLite os guarda (`1500.0`); só a produtividade é arredondada para 2 casas na exibição (o valor gravado não muda). A faixa é escrita a partir dos limites gravados, não da configuração atual.
- **Parceiros:** `grupos_municipio` mostra o total de municípios do grupo; a lista completa fica em `grupo EXECUCAO GRUPO`.

Exemplo de SQL direto (qualquer cliente SQLite, ex.: DB Browser for SQLite):

```sql
-- Produtividade de Patrocínio por ano e de qual coleta veio cada número
SELECT p.ano, round(p.produtividade_sc_ha, 1) AS sc_ha, e.data_coleta, e.arquivo_origem
FROM pam_municipio_ano p
JOIN execucao e ON e.id = p.execucao_id
WHERE p.codigo_ibge = '3148103'
ORDER BY p.ano;

-- Últimas falhas e o motivo
SELECT id, fonte, data_execucao, mensagem FROM execucao WHERE status <> 'sucesso' ORDER BY id DESC;
```

## Preenchimento histórico dos grupos da PLA-05 (`pipeline/backfill_pla05.py`, etapa 4)

Execuções do IBGE/SIDRA gravadas antes da etapa 3b não têm linhas em `validacao_grupo` e `validacao_grupo_membro`. No `data/rigora.db`, era o caso da execução 3 (726 registros na PLA-05), preenchida na etapa 4e (ver "Resultado da etapa 4e"). O script reconstrói os grupos dessa execução **a partir dos brutos já coletados** (`data/raw`), pelo mesmo caminho do pipeline (validação do payload, transformação, junção com as localidades e `validate_pam`), e só os grava depois de conferir que a reconstrução coincide com o que está no banco. Não coleta nada, não acessa a internet e não altera as tabelas históricas.

### Os três modos

| Modo | Opção | Quando usar | Abre o banco | Resultado esperado |
|---|---|---|---|---|
| Verificar | (nenhuma) | **antes** da gravação | somente leitura (`mode=ro`) | C01–C16 aprovadas, código 0 |
| Gravar | `--gravar --backup ARQUIVO` | uma única vez por execução | escrita, numa transação só | JSON com 275 grupos e 726 membros (execução 3), código 0 |
| Conferir gravados | `--conferir-gravados` | **depois** da gravação | somente leitura (`mode=ro`) | P00–P19 aprovadas, código 0 |

`--gravar` e `--conferir-gravados` não podem ser usados juntos (código 2).

```bash
# Verificar (antes de gravar): somente leitura
python -m pipeline.backfill_pla05 --db data/rigora.db --execucao 3 --localidades-execucao 2

# Gravar: só com backup idêntico ao banco (ver "Procedimento da execução 3")
python -m pipeline.backfill_pla05 --db data/rigora.db --execucao 3 --localidades-execucao 2 \
    --gravar --backup data/rigora_antes_backfill_pla05.db

# Conferir gravados (depois de gravar): somente leitura
python -m pipeline.backfill_pla05 --db data/rigora.db --execucao 3 --localidades-execucao 2 --conferir-gravados
```

No Windows (PowerShell ou Prompt de Comando), os caminhos podem ser escritos com `\` (`data\rigora.db`); o comando de gravação fica numa linha só, sem a `\` de continuação.

| Parâmetro | Obrigatório | Significado |
|---|---|---|
| `--db` | sim (sem valor padrão) | banco SQLite |
| `--execucao` | sim | execução do IBGE/SIDRA |
| `--localidades-execucao` | sim | execução do IBGE/Localidades da mesma chamada do pipeline |
| `--raw` | não | pasta dos brutos; padrão: `raw` ao lado do banco |
| `--gravar` | não | grava depois de verificar |
| `--backup` | com `--gravar` | cópia do banco com o mesmo SHA-256; sem ela, `--gravar` é recusado (código 2). Ignorado nos outros modos |
| `--conferir-gravados` | não | confere uma execução que já tem grupos gravados |

Códigos de saída: **0** tudo aprovado; **1** conferência com falha, divergência ou gravação interrompida ("Backfill interrompido: …"); **2** argumentos inválidos.

### Verificar antes × conferir depois

Os dois modos são somente leitura e reconstroem os grupos a partir dos brutos, mas respondem a perguntas diferentes:

- **Verificar** pergunta "é seguro gravar?". Exige que a execução **ainda não tenha** grupos (C06) e compara a reconstrução com `pam_municipio_ano` e com as ocorrências gravadas. Depois da gravação, ele passa a falhar em C06 de propósito: é a proteção contra gravar duas vezes. Uma falha em C06 depois da gravação não indica erro.
- **Conferir gravados** pergunta "o que foi gravado está certo?". Exige que a execução **já tenha** grupos (P06) e compara, campo a campo, os grupos e membros gravados com a reconstrução. Não substitui a verificação antes da gravação.

### Verificar: conferências C01–C16

O banco é aberto com `mode=ro`; nenhuma tabela é criada e nenhum arquivo é escrito. Todas precisam passar para gravar:

| Código | Conferência |
|---|---|
| C01 | a execução é do IBGE/SIDRA e terminou com sucesso |
| C02 | a execução de localidades é do IBGE/Localidades, terminou com sucesso e tem a mesma `data_execucao` |
| C03, C04 | o SHA-256 dos dois brutos é igual ao gravado em `execucao` e no `.meta.json` |
| C05 | a regra PLA-05 está gravada na execução |
| C06 | a execução ainda não tem grupos gravados |
| C07 | a reconstrução é aprovada pela validação, como a execução original |
| C08 | status e contagem de cada regra iguais aos gravados em `validacao_regra` |
| C09 | total de registros igual ao da regra e ao de ocorrências PLA-05 gravadas; total de municípios igual ao das ocorrências |
| C10 | a distribuição por nível soma os totais |
| C09, C10 (execução 3) | além disso, os números da auditoria da etapa 4: 275 grupos, 726 registros, 269 municípios; 142/102/31 grupos e 441/221/64 registros nos níveis informativo/atenção/revisão documental (constante `ESPERADO`, aplicada sempre que `--execucao 3`) |
| C11 | os pares (código IBGE, ano) dos grupos são exatamente as ocorrências PLA-05 gravadas |
| C12 | as linhas desses pares em `pam_municipio_ano` ainda pertencem à execução (nenhuma coleta posterior as substituiu) |
| C13, C14 | rendimento, área, produção e nomes iguais aos de `pam_municipio_ano` |
| C15 | sem grupos repetidos; numeração 1..n |
| C16 | ensaio completo da gravação numa cópia do banco em memória |

### Gravar

Repete toda a verificação, exige o backup e grava numa transação só (`BEGIN IMMEDIATE … COMMIT`):

- **backup obrigatório:** o arquivo de `--backup` precisa existir, não pode ser o próprio banco e precisa ter o mesmo SHA-256 do banco naquele momento; senão nada é gravado. O script só lê o backup (para o hash); quem cria o backup é você, antes, com o banco fechado. **Não grave no banco original sem um backup conferido.**
- cria as duas tabelas e os dois índices, se faltarem (o mesmo esquema de `load_sqlite`); só esses objetos (e os índices internos `sqlite_autoindex_*` das duas tabelas) podem surgir, e os que já existiam não podem mudar;
- insere grupos e membros por `load_sqlite.record_pla05_groups` (só `INSERT` simples; sem `INSERT OR REPLACE`, UPSERT, `UPDATE` ou `DELETE`; `record_validation` não é chamado);
- confere a quantidade gravada, `PRAGMA foreign_key_check` e o conteúdo das cinco tabelas históricas (`execucao`, `pam_municipio_ano`, `validacao_regra`, `validacao_ocorrencia`, `inmet_previsao`), que precisa ser idêntico ao de antes;
- qualquer falha **antes** do `COMMIT` desfaz a transação inteira, inclusive a criação das tabelas; depois do `COMMIT`, roda `PRAGMA integrity_check`.

Imprime um JSON com grupos, membros, objetos criados, integridade e os hashes do banco e do backup.

**Sem duplicação.** Uma segunda gravação é recusada na verificação (C06); se a verificação fosse contornada, a transação também recusa; por fim, a chave primária e as restrições de unicidade do banco recusam.

**Falha no próprio `COMMIT` (`CommitError`).** Se o `COMMIT` falhar (ex.: `disk I/O error`), o script:

- só executa `ROLLBACK` se a transação ainda estiver ativa (se o SQLite já a encerrou, não tenta);
- mostra o **erro original** do `COMMIT` ("Backfill interrompido: falha no COMMIT: OperationalError: …"), dizendo se a transação estava ativa e se o `ROLLBACK` foi executado ou também falhou; uma falha secundária do `ROLLBACK` aparece na mesma mensagem, depois da causa, sem escondê-la;
- encerra a conexão e sai com código 1.

Nesse caso **o estado do arquivo é indeterminado**. Não abra o banco em nenhuma ferramenta (abrir com escrita faz o SQLite aplicar ou descartar o journal), não apague nem renomeie o `-journal` (ele é a evidência e é o que permite ao SQLite desfazer a transação) e não repita a gravação sem diagnóstico. Recupere a partir do backup ou investigue a causa primeiro. Uma causa conhecida: pastas em que o processo não tem permissão para **apagar** arquivos (o SQLite apaga o `-journal` ao confirmar a transação; sem essa permissão, o `COMMIT` falha mesmo com os dados escritos). Foi o que ocorreu na primeira tentativa da etapa 4d, numa cópia de trabalho.

**Journal pendente antes de gravar.** O modo gravar não confere `-journal` ou `-wal` ao lado do banco. Antes de gravar, confirme que não há `rigora.db-journal`, `rigora.db-wal` nem `rigora.db-shm` na pasta `data/`; se houver, não grave.

### Conferir gravados: conferências P00–P19

Somente leitura: o banco é aberto só com `mode=ro`, nada é inserido, atualizado, apagado ou recalculado no banco. A comparação é exata: cada valor gravado e o seu tipo no SQLite (`typeof`) contra o valor reconstruído como a coluna o guarda (afinidade REAL: um inteiro vira real), sem tolerância nem arredondamento.

| Código | Conferência |
|---|---|
| P00 | não há `-journal` nem `-wal` ao lado do banco. Se houver, **o banco não é aberto** e a conferência para aqui (abrir poderia aplicar ou desfazer uma transação pendente) |
| P01, P02 | execuções do IBGE/SIDRA e do IBGE/Localidades da mesma chamada, com sucesso |
| P03, P04 | SHA-256 dos brutos igual ao registrado |
| P05 | regra PLA-05 gravada na execução |
| P06 | a execução **já tem** grupos gravados |
| P07, P08 | reconstrução aprovada; status e contagem das regras iguais aos gravados |
| P09, P10 | quantidade de grupos e de membros igual à reconstrução (e, na execução 3, 275 e 726) |
| P11 | números dos grupos iguais aos reconstruídos |
| P12 | ano, nível, faixa (`faixa_acima_de_ha`, `faixa_ate_ha`), rendimento, área, produção, `total_municipios` e `total_registros` de cada grupo idênticos, em valor e tipo |
| P13 | `produtividade_sc_ha` (coluna gerada) igual a rendimento / 60 |
| P14 | código IBGE, nome e ano de cada membro idênticos; nenhum membro ausente ou a mais |
| P15 | municípios distintos e distribuição por nível iguais à reconstrução (execução 3: 269 municípios; grupos 142/102/31; membros 441/221/64) |
| P16 | os pares (código IBGE, ano) dos membros são exatamente as ocorrências PLA-05 gravadas |
| P17 | grupos e membros vinculados à execução e à regra PLA-05 |
| P18 | `PRAGMA foreign_key_check` das duas tabelas vazio |
| P19 | sem grupos (mesmo ano e valores) nem membros (mesmo município e ano) duplicados |

Cada divergência indica grupo, membro e campo (ex.: "grupo 12, campo area_colhida_ha: gravado 1500.0 (real), reconstruído 1501.0 (real)"); até 20 por conferência, com o total. Qualquer divergência: código 1. O modo não repete as conferências de `pam_municipio_ano` (C12–C14): depois da gravação, os grupos são o registro histórico da execução e não dependem dessa tabela.

### Procedimento da execução 3 (`data/rigora.db`)

| Passo | O que fazer | Situação em 14/11/2026 |
|---|---|---|
| 1 | Verificar no banco real (somente leitura): C01–C16 aprovadas | feito (etapa 4b): todas aprovadas, banco inalterado |
| 2 | Backup de referência com o banco fechado (`data/rigora_antes_etapa4.db`), conferido por SHA-256 | feito (etapa 4c) |
| 3 | Ensaio numa cópia de trabalho: gravar na cópia e conferir com `--conferir-gravados` | feito (etapa 4d, 2ª tentativa, `data/rigora_teste_etapa4d_2.db`): 275 grupos, 726 membros, P00–P19 aprovadas, tabelas históricas idênticas |
| 4 | Gravação definitiva: confirmar que nenhum programa está com o banco aberto e que não há `-journal`, `-wal` ou `-shm`; criar o backup final `data/rigora_antes_backfill_pla05.db` com o mesmo SHA-256 do banco (sem sobrescrever o de referência); gravar com `--gravar --backup data/rigora_antes_backfill_pla05.db` | feito (etapa 4e, fase B): backup final idêntico ao banco antes da gravação; gravação com código 0, 275 grupos e 726 membros, `integrity_check` ok |
| 5 | Conferir o banco real com `--conferir-gravados` (P00–P19), comparar as cinco tabelas históricas com o backup e registrar os hashes finais | feito (etapa 4e, fase B): P00–P19 aprovadas (20/20), tabelas históricas idênticas ao backup final, hashes registrados |

Em qualquer gravação futura com este procedimento, se o passo 4 terminar em `CommitError`, siga "Falha no próprio `COMMIT`" acima: pare, preserve o journal, não abra o banco. Para a execução 3, uma nova gravação é recusada (C06): os grupos já estão gravados.

### Resultado da etapa 4e (14/11/2026)

- **Fase A:** documentação dos modos verificar, gravar e conferir gravados (esta seção).
- **Fase B:** preenchimento definitivo da execução 3 do IBGE/SIDRA, com a execução 2 do IBGE/Localidades, no banco definitivo `data/rigora.db`.
- **Backup final:** `data/rigora_antes_backfill_pla05.db` (1.449.984 bytes), criado antes da gravação e idêntico ao banco naquele momento.
- **Gravação:** código de saída 0; 275 grupos e 726 membros gravados, de 269 municípios distintos (grupos 142/102/31 e membros 441/221/64 nos níveis informativo/atenção/revisão documental).
- **Conferência:** `--conferir-gravados` com P00–P19 aprovadas (20/20).
- **Integridade:** `PRAGMA integrity_check` ok e `PRAGMA foreign_key_check` vazio.
- **Tabelas históricas:** `execucao`, `pam_municipio_ano`, `validacao_regra`, `validacao_ocorrencia` e `inmet_previsao` com conteúdo idêntico ao do backup final; os 16 objetos de esquema anteriores sem mudança; os objetos novos são só os das duas tabelas de grupos.
- **Backups preservados:** o backup final e o backup anterior `data/rigora_antes_etapa4.db` mantiveram o hash de antes da gravação.

| Arquivo | SHA-256 antes da gravação | SHA-256 depois |
|---|---|---|
| `data/rigora.db` | `5900bd1f158f618c51cb0f851d8c0ea13a6a57f37f9e551160802217dca7291a` | `91f9fdc8f4e2adf271b1fbf9ec0dfd5d9fc0382ed649e6488ef231dece3f4e49` |
| `data/rigora_antes_backfill_pla05.db` | `5900bd1f158f618c51cb0f851d8c0ea13a6a57f37f9e551160802217dca7291a` | `5900bd1f158f618c51cb0f851d8c0ea13a6a57f37f9e551160802217dca7291a` |
| `data/rigora_antes_etapa4.db` | `5900bd1f158f618c51cb0f851d8c0ea13a6a57f37f9e551160802217dca7291a` | `5900bd1f158f618c51cb0f851d8c0ea13a6a57f37f9e551160802217dca7291a` |

**Ambiente:** a gravação e a conferência foram executadas no ambiente Linux conectado à pasta do projeto, com Python 3.10.12 e SQLite 3.37.2, e não diretamente no Python do Windows. Durante a fase B não foram executados testes.

### Arquivos de ensaio da etapa 4d em `data/`

Ficaram em `data/` dois conjuntos de arquivos do ensaio da etapa 4d. Nenhum deles substitui o banco definitivo nem os backups, e nenhum deve ser usado em comandos:

- `data/rigora_teste_etapa4d.db` e `data/rigora_teste_etapa4d.db-journal`: primeira tentativa, interrompida no `COMMIT`. **Devem ser preservados como estão: não abrir, não remover, não renomear e não usar.** Abrir a cópia em qualquer ferramenta faria o SQLite aplicar o journal; apagar ou separar os dois arquivos destrói a evidência.
- `data/rigora_teste_etapa4d_2.db`: cópia da segunda tentativa, que serviu só ao ensaio. **Arquivada em 15/11/2026** (Bloco 1) em `_arquivo/data/rigora_teste_etapa4d_2.db`, com o mesmo SHA-256; ver `_arquivo/MANIFESTO.md`. O par da primeira tentativa continua aqui, como manda o item acima.

### Decisões e limitações

- **Classificação retroativa:** os níveis são os das faixas atuais (`config.PLA05_FAIXAS_HA`, 10 e 500 ha), aplicadas a uma execução feita antes da etapa 1. Os limites usados ficam gravados em cada grupo (`faixa_acima_de_ha`, `faixa_ate_ha`).
- **Bloqueio por coleta nova:** se uma coleta posterior do SIDRA tiver substituído linhas da execução em `pam_municipio_ano`, as conferências deixam de valer e o preenchimento é recusado (C12).
- **Histórico preservado:** o texto da regra em `validacao_regra` (anterior à etapa 1) e o relatório JSON da execução não são alterados; o relatório continua sem a seção `pla05_grupos`.
- **Criação das tabelas:** no `data/rigora.db`, as duas tabelas foram criadas pela gravação do backfill (passo 4, etapa 4e), dentro da mesma transação. Uma execução posterior do pipeline usa `CREATE TABLE IF NOT EXISTS` e não as altera.
- **Sem rede:** o script não coleta nada e não acessa a internet.

## INMET — previsão do tempo (etapa 3d)

**Fonte:** API pública de previsão do INMET, `https://apiprevmet3.inmet.gov.br/previsao/3148103` (Patrocínio, MG), a mesma que o portal https://portal.inmet.gov.br/ e a interface da Rigora já consultam. Não há contrato formal publicado; o formato abaixo foi verificado na resposta real de 14/11/2026.

| Campo da fonte | Coluna | Unidade / tratamento |
|---|---|---|
| chave `dd/mm/aaaa` | `data_previsao` | ISO `aaaa-mm-dd`; inválida → NULL e erro MET-01 (não é corrigida) |
| `manha` / `tarde` / `noite` (2 primeiros dias) ou registro único | `periodo` | `manha`, `tarde`, `noite` ou `dia` |
| — | `horizonte_dias` | dias desde a 1ª data da previsão (0 a 4) |
| `temp_min`, `temp_max` | `temp_min_c`, `temp_max_c` | °C, como publicado |
| `umidade_min`, `umidade_max` | `umidade_min_pct`, `umidade_max_pct` | %, como publicado |
| `int_vento`, `dir_vento` | `vento_intensidade`, `vento_direcao` | texto da fonte (ex.: "Fracos", "SW-W") |
| `resumo` | `resumo` | texto |

**A fonte não fornece:** precipitação (mm ou probabilidade) e velocidade do vento em número. Esses campos não existem no banco; não há valor substituto. Campos `nascer`, `ocaso`, tendências, ícones e `hora` não são usados (o significado de `hora` não está documentado).

**É previsão, não observação.** A tabela chama-se `inmet_previsao`. Observações medidas em estação, se forem integradas, terão tabela própria.

### Tabela `inmet_previsao`

Chave primária `(codigo_ibge, data_coleta, data_previsao, periodo)`: cada coleta guarda a sua previsão (a previsão para 18/11 feita em 14/11 e a feita em 16/11 ficam ambas). Reprocessar o mesmo bruto atualiza as mesmas linhas, sem duplicar. `execucao_id` → `execucao(id)`. O banco também recusa umidade fora de 0–100% e mínimas acima das máximas (CHECK). Índices: `(codigo_ibge, data_previsao)` e `(execucao_id)`.

### Regras de qualidade do INMET

| Regra | Dimensão | Severidade | O que verifica |
|---|---|---|---|
| MET-01 | integridade | erro | data de previsão válida |
| MET-02 | integridade | erro | uma previsão por data e período (inclui datas repetidas no JSON, que o Python descartaria em silêncio) |
| MET-03 | integridade | aviso | registros exatamente iguais mantidos uma vez (não há informação perdida) |
| MET-04 | consistência | erro | temperatura mínima ≤ máxima |
| MET-05 | plausibilidade | aviso | temperatura entre −10 e 45 °C |
| MET-06 | consistência | erro | umidade entre 0 e 100% |
| MET-07 | consistência | erro | umidade mínima ≤ máxima |
| MET-08 | completude | aviso | temperatura e umidade presentes (ausente fica NULL) |
| MET-09 | completude | erro | precipitação não negativa; **não verificada** enquanto a fonte não informar precipitação |
| MET-10 | completude | aviso | previsão atual: coletada há até 24 h e começando no dia da coleta (previsão antiga é gravada como histórico, nunca exibida como atual) |
| MET-11 | completude | aviso | cobertura de 5 dias consecutivos |
| MET-12 | integridade | aviso | município da resposta = Patrocínio (não verificada se a resposta não trouxer `entidade`) |
| MET-13 | completude | info | registra os campos que a fonte não fornece |
| MET-14 | integridade | erro | no banco: chave estrangeira e chave única (roda na transação de gravação) |

### Independência das fontes

IBGE e INMET têm execuções, transações, validações e relatórios separados. Se o INMET estiver fora do ar ou a previsão for recusada, os dados do IBGE são gravados normalmente, e o contrário também. A falha fica registrada em `execucao` (status `falha` e motivo) e, quando houver, no relatório de qualidade.

## Geração do `js/snapshots.js` (etapa 3e)

Módulo: `pipeline/export_snapshots.py`. Lê o banco **somente leitura** e escreve `js/snapshots.js` no formato que `js/app.js` já usa.

### Contrato preservado

`window.RIGORA_SNAPSHOTS = { sidra, inmet, exportacao }`

| Seção | Campos | Quem lê |
|---|---|---|
| `sidra` | `source`, `query`, `collectedAt`, `municipiosMG`, `fields`, `mg: {ano: [kg/ha, área ha, produção t]}`, `municipios: [{c, n, meso, y: {ano: [kg/ha, área, produção]}}]` (60 maiores por área colhida no último ano; empate desfeito pelo código IBGE) | `app.js` (benchmark, produtividade de referência) |
| `inmet` | `collectedAt`, `data: {"3148103": {"dd/mm/aaaa": registro \| {manha, tarde, noite}}}`, com `resumo`, `temp_min`, `temp_max`, `umidade_min`, `umidade_max`, `int_vento`, `dir_vento`, `dia_semana` — ou `null` | `app.js`, só quando a consulta ao vivo ao INMET falha |
| `exportacao` | **Acréscimo da 3e**: `geradoEm` e, por fonte, modo, execução, data da coleta, arquivo bruto, hash, resumo da validação, registros e motivos | `js/app.js`: linha "Procedência" dos cartões do IBGE e do INMET na aba Fontes (desde a B7). Sem `exportacao`, a tela mostra "Cópia manual" |

Campos do INMET que não estão no banco e a interface não exibe (`estacao`, `nascer`, `ocaso`, `hora`, `temp_*_tende`) não são exportados. `dia_semana` é calculado pela data (calendário). Precipitação e velocidade do vento não existem na fonte e não aparecem.

### Regras

- Só entram execuções com `status = 'sucesso'`; a mais recente de cada fonte (maior `id`).
- **IBGE sem execução válida** ou **cobertura parcial** (menos de 90% dos municípios com produção que o arquivo anterior declarava): mantém a seção `sidra` anterior e registra o motivo. Sem arquivo anterior, a exportação é cancelada.
- **INMET**: previsão coletada há mais de 24 h (`INMET_MAX_IDADE_HORAS`) vira `inmet: null` e a interface mostra "Previsão indisponível". Datas anteriores a hoje (horário de Brasília) saem. Dia com temperatura ou umidade ausente sai do arquivo, com o motivo em `exportacao.inmet.datasOmitidas`. Nada vira zero.
- **Sem nenhuma execução do INMET no banco**: mantém a previsão do arquivo anterior só se ainda tiver até 24 h; senão, `null`.
- **`--saida` obrigatório** (Bloco 3): não há destino padrão, e o exportador recusa o `js/snapshots.js` em uso (código 1, com a indicação do `pipeline.publicar`). Se a saída já existir, é lida como arquivo anterior.
- **Escrita atômica**: grava `<saida>.tmp`, relê, valida o contrato e só então substitui. Em qualquer erro o arquivo anterior fica intacto e o código de saída é 1.
- **Reexecutável**: rodar de novo com o mesmo banco produz o mesmo conteúdo; só muda `exportacao.geradoEm`.
- `dataColeta` (quando a fonte respondeu) e `geradoEm` (quando o arquivo foi escrito) são campos distintos. O arquivo é sempre uma cópia salva; não indica coleta ao vivo.
