# Rigora Agro Intelligence 1.0: como abrir e executar

## Para usar o Rigora: dois cliques, sem instalar nada

1. Extraia o ZIP do kit, se ainda não extraiu.
2. Abra a pasta `06_CODIGO_FONTE`.
3. Dê dois cliques em **`index.html`**.
4. O Rigora abre no navegador. Clique em **Entrar no Dashboard**. O acesso é demonstrativo e não
   pede senha.
5. Use o menu lateral para navegar: Visão Geral, Propriedades, Inteligência, Automações e Fontes.

Não é preciso instalar Python, Node.js nem bibliotecas, nem abrir o terminal ou iniciar um
servidor. A interface é HTML, CSS e JavaScript comuns, com scripts clássicos e caminhos relativos.
Não há `fetch` de arquivos locais, módulos ES ou bibliotecas externas, e os gráficos são SVG gerados
pelo próprio código. Por isso não existe um `INICIAR_RIGORA.bat`: ele não é necessário.

| Situação | O que a interface mostra |
|---|---|
| **Sem internet** | Os dados oficiais do IBGE vêm da cópia oficial de **14/11/2026** (`js/snapshots.js`), com selo de cópia e data. A previsão do INMET aparece como **"indisponível"**: a cópia tem mais de 24 h e, pela regra M1, não pode ser exibida como atual. A fonte Inter cai para a fonte do sistema (Segoe UI no Windows) |
| **Com internet** | A previsão de 5 dias do INMET é consultada ao vivo. O botão "Atualizar do IBGE" confere os dados oficiais ao vivo |

Os dados da propriedade (Sítio Santa Rita do Cerrado) são **demonstrativos** e aparecem sempre com
esse selo. Lançamentos novos ficam salvos só no navegador em que foram feitos.

> **Por que o `js/snapshots.js` desta pasta é de 14/11/2026.** O snapshot publicado no projeto
> original é um artefato de produção e não foi copiado. No lugar dele está a **cópia manual de
> 14/11/2026**, que é a fixture oficial dos testes (`tests/fixtures/snapshots_manual_20261114.js`,
> SHA-256 `410fa9cb…`). Ela tem o mesmo formato e contém só dados públicos do IBGE e do INMET.

---

## Para quem quer rodar os testes e o pipeline (opcional)

As seções abaixo são para quem quiser examinar a parte técnica: testes automatizados, pipeline de
dados, backup e publicação. **Nada disso é necessário para abrir o Rigora.** Os comandos são para
Windows. Cada bloco diz se é para o **PowerShell** ou para o **Prompt de Comando (CMD)**, ou se é
igual nos dois.

> **Como estes comandos foram conferidos.** Em 16/11/2026, a sequência foi executada no Linux,
> numa cópia isolada desta pasta, com os comandos equivalentes (`05_EVIDENCIAS/C_testes_copia_isolada_20261116`
> e `D_simulacoes_e_protecoes_copia_isolada`). No Windows, o mesmo código passou pelo portão de
> qualidade antes e depois da primeira coleta real (Python 3.11.9 e Node 26.13.0); veja
> `05_EVIDENCIAS/A_primeira_coleta_real_windows_20261116`. Os comandos de PowerShell e CMD seguem a
> sintaxe padrão do Windows, mas esta versão do guia ainda não foi executada, linha por linha, num
> Windows novo.

### 1. Pré-requisitos

| Ferramenta | Para quê | Versão |
|---|---|---|
| Python | Pipeline, banco SQLite e testes Python | **3.10 ou superior** (testado com 3.10.12 e 3.11.9) |
| pip | Instalar as dependências | O que vem com o Python |
| Node.js | Testes JavaScript e verificador B5 | Testado com 22.23 e 26.13 |
| Internet | **Opcional**: coleta real do IBGE e do INMET | — |

Nenhuma conta, chave de API, senha ou arquivo `.env` é necessário.

### 2. Abrir o terminal na pasta

No Explorador de Arquivos, abra `06_CODIGO_FONTE`, clique na barra de endereço, digite
`powershell` e pressione Enter. Todos os comandos abaixo são executados **nesta pasta**.

### 3. Instalação das dependências

As dependências são o **pandas**, usado pelo pipeline, e o **pytest**, usado pelos testes. O
ambiente virtual fica **fora** desta pasta, de propósito: o portão de qualidade confere o hash de
todos os arquivos daqui e ficaria lento com milhares de arquivos de biblioteca.

**PowerShell:**

```powershell
python -m venv ..\.venv-rigora
..\.venv-rigora\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

**CMD:**

```bat
python -m venv ..\.venv-rigora
..\.venv-rigora\Scripts\activate.bat
python -m pip install -r requirements-dev.txt
```

O ambiente virtual é opcional: sem ele, rode só o `pip install`.

### 4. Testes automatizados (não precisam de internet nem de banco)

**Testes Python (PowerShell ou CMD):**

```
python -m pytest -q
```

Resultado esperado: `446 passed`, em 45 s a 100 s. Linhas `ERROR` no log capturado são falhas
**provocadas de propósito** pelos testes (rede ausente, arquivo adulterado) e não indicam problema.

**Testes JavaScript (PowerShell ou CMD):**

```
node tests\metrics.test.js
```

Resultado esperado: `111 testes aprovados, 0 reprovados.`

**Em outros fusos horários.** PowerShell:

```powershell
$env:TZ = "UTC";        node tests\metrics.test.js
$env:TZ = "Asia/Tokyo"; node tests\metrics.test.js
Remove-Item Env:TZ
```

CMD, uma linha de cada vez:

```bat
set TZ=UTC
node tests\metrics.test.js
set TZ=Asia/Tokyo
node tests\metrics.test.js
set TZ=
```

### 5. Pipeline sem internet (dados de demonstração)

A pasta `dados_demonstracao` tem uma **amostra reduzida**: 4 municípios, 2016–2025, e uma previsão
do INMET de 14/11/2026, com os metadados de que o pipeline precisa. **Não é uma coleta completa.**

PowerShell:

```powershell
New-Item -ItemType Directory -Force data\raw | Out-Null
Copy-Item dados_demonstracao\* data\raw\
python -m pipeline.run_pipeline --sem-coleta
```

CMD:

```bat
mkdir data\raw
copy dados_demonstracao\* data\raw\
python -m pipeline.run_pipeline --sem-coleta
```

Resultado esperado: resumo em JSON com `"status": "sucesso"` para `sidra` e `inmet`, e um aviso
esperado do INMET, `[MET-10] coletada há … h`. São criados `data\rigora.db`, `data\processed\` e
`data\reports\`.

Consultas e exportação (PowerShell ou CMD):

```
python -m pipeline.query historico
python -m pipeline.query municipio 3148103
python -m pipeline.query rastrear 3148103 --ano 2025
python -m pipeline.query qualidade
python -m pipeline.export_snapshots --saida ..\saida_demo\snapshots_demo.js
python -m pipeline.export_snapshots --saida js\snapshots.js
python -m pipeline.publicar
```

O penúltimo comando **deve ser recusado**: o exportador nunca grava no arquivo usado pela
interface. O último é a simulação da publicação. Com a amostra, o resultado esperado é
**REPROVADO**, porque a publicação exige os 60 maiores municípios com procedência completa. Nada é
publicado.

### 6. Coleta real (precisa de internet)

PowerShell:

```powershell
if (Test-Path data) { Remove-Item -Recurse -Force data }
python -m pipeline.run_pipeline
python -m pipeline.publicar
python tools\portao.py
```

CMD:

```bat
if exist data rmdir /s /q data
python -m pipeline.run_pipeline
python -m pipeline.publicar
python tools\portao.py
```

Resultado esperado: **SIMULAÇÃO APROVADA** e **APROVADO**, desde que a previsão do INMET tenha
menos de 24 h e o IBGE não tenha revisado os dados desde 14/11/2026. Se houver revisão, o
verificador B5 **reprova** e lista as diferenças, que é o comportamento correto. Não é necessário
publicar. Publicação e restauração foram testadas numa cópia isolada
(`05_EVIDENCIAS/D_simulacoes_e_protecoes_copia_isolada`, arquivos 06 e 07).

### 7. Backup e restauração (opcional)

Precisa de um `data\` criado pela seção 5 ou 6. PowerShell ou CMD:

```
python -m pipeline.backup_dados criar
python -m pipeline.backup_dados verificar ..\rigora_backups\dados\PASTA_CRIADA
python -m pipeline.backup_dados restaurar ..\rigora_backups\dados\PASTA_CRIADA --destino ..\restauracao_teste
```

Troque `PASTA_CRIADA` pelo nome mostrado pelo `criar`. A restauração recusa uma pasta de destino que
já tenha arquivos.

### 8. Como interpretar os resultados

| Situação | `python tools\portao.py` | Por quê |
|---|---|---|
| Logo depois de extrair, sem `data\` | **INCOMPLETO** (código 2) | Sem banco não há simulação da publicação nem verificador |
| Com a amostra da seção 5 | **REPROVADO** (código 1) | A amostra tem 4 municípios |
| Depois da coleta real da seção 6 | **APROVADO** (código 0) | Se o INMET tiver menos de 24 h e o IBGE não tiver mudado |

Publicação (`python -m pipeline.publicar`): 0 concluído · 1 reprovado ou desfeito · 2 incompleto ·
3 erro de uso · 4 falha grave.

### 9. Problemas comuns

| Problema | Solução |
|---|---|
| `index.html` abre no Bloco de Notas | Clique com o botão direito → Abrir com → Edge ou Chrome |
| A tela abre sem cores nem layout | A pasta foi copiada sem as subpastas `css` e `js`. Extraia o ZIP inteiro |
| Previsão do INMET "indisponível" | Esperado sem internet ou com a cópia vencida (regra M1) |
| `python` não é reconhecido | Instale o Python 3.10+ marcando "Add python.exe to PATH", ou use `py` |
| `Activate.ps1` bloqueado | No PowerShell: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `node` não é reconhecido | Instale o Node.js (LTS) e abra um novo terminal |
| `No module named pandas` | Ative o ambiente e rode `python -m pip install -r requirements-dev.txt` |
| Portão INCOMPLETO "banco não encontrado" | Esperado sem `data\`; veja a seção 5 ou 6 |
| Acentos estranhos no terminal | Use o Windows Terminal ou rode `chcp 65001` |

### 10. O que depende de serviços externos

| Funcionalidade | Depende de | Sem internet |
|---|---|---|
| Previsão do INMET na interface | apiprevmet3.inmet.gov.br | "indisponível" (regra M1) |
| Conferência ao vivo do IBGE | apisidra.ibge.gov.br | Cópia oficial de 14/11/2026 |
| Fonte Inter | fonts.googleapis.com | Fonte do sistema |
| Coleta real do pipeline | IBGE e INMET | Seção 5 (amostra) |
| Testes automatizados | Nada | — |
