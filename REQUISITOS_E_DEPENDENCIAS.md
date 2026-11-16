# Requisitos e dependências

## Software

| Item | Obrigatório? | Uso | Versões em que o projeto foi executado |
|---|---|---|---|
| Python | Sim, para pipeline e testes | Pipeline, SQLite (`sqlite3` da biblioteca padrão), testes | 3.10.12 (Linux), 3.11.9 (Windows). Mínimo declarado: 3.10 |
| pandas | Sim, para o pipeline | Tratamento dos dados do SIDRA e do INMET | `pandas>=2.0` (`requirements.txt`); executado com 2.3.3 |
| pytest | Só para os testes | Suíte Python | `pytest>=7` (`requirements-dev.txt`); executado com 9.2.0 |
| Node.js | Só para os testes JavaScript e o verificador B5 | `tests/metrics.test.js`, `tests/check_exported_snapshot.js` | 22.23.4 (Linux), 26.13.0 (Windows) |
| Navegador | Sim, para a interface | `index.html` (HTML, CSS e JavaScript, sem bibliotecas externas) | Edge, Chrome ou Firefox atuais |

O pipeline usa só a biblioteca padrão para HTTP (`urllib`), hashes (`hashlib`) e banco (`sqlite3`).
A interface não usa frameworks nem CDNs: os gráficos são SVG gerados por `js/charts.js`.

## Instalação

```
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` inclui `requirements.txt` (pandas) e acrescenta o pytest.

## Variáveis de ambiente

Nenhuma é necessária, e **não há `.env`** no projeto. A única variável usada é `TZ`, opcional, para
rodar os testes JavaScript em outro fuso horário (veja `README_EXECUCAO.md`, seção 5). Por isso não
foi criado um `.env.example`.

## Serviços externos (opcionais)

| Serviço | Endereço | Quem usa |
|---|---|---|
| IBGE/SIDRA, tabela 1613 (PAM) | `https://apisidra.ibge.gov.br/values/t/1613/...` | Pipeline (coleta) e interface (consulta ao vivo) |
| IBGE/Localidades | `https://servicodados.ibge.gov.br/api/v1/localidades/estados/31/municipios` | Pipeline |
| INMET, previsão por município | `https://apiprevmet3.inmet.gov.br/previsao/3148103` | Pipeline e interface |

As URLs exatas ficam em `pipeline/config.py`. Para vê-las sem acessar a internet:

```
python -m pipeline.extract_sidra --dry-run
python -m pipeline.extract_inmet --dry-run
```

## Espaço e tempo

- Código: cerca de 1,2 MB. Um banco criado pela coleta real fica com cerca de 2 MB, e os arquivos
  brutos com cerca de 9 MB.
- Testes Python: de 45 s a 100 s. Testes JavaScript: cerca de 5 s por fuso. Portão completo: de 1 a
  3 minutos.
