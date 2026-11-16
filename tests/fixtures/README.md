# Fixtures

Fixture reduzida para testes: valores reais da PAM (IBGE/SIDRA tabela 1613, café arábica) copiados da cópia oficial coletada em 14/11/2026 (js/snapshots.js), reescritos no formato de resposta do SIDRA. Contém apenas os campos usados pelo pipeline.

- `sidra_pam_sample.json`: Patrocínio, Manhuaçu, Monte Carmelo e Campos Altos, 2016–2025, variáveis 112, 216 e 214.
- `localidades_sample.json`: os mesmos municípios, só com nome e mesorregião (estrutura da API de localidades do IBGE, reduzida).

- `inmet_previsao_sample.json`: previsão real do INMET para Patrocínio (3148103), coletada em 14/11/2026 às 16:13 UTC pela interface da Rigora (a mesma cópia de `js/snapshots.js`), no formato da API (`{"3148103": {"dd/mm/aaaa": ...}}`). Reduzida: sem os ícones em base64 e sem os campos `entidade`, `uf`, `fonte`, `cod_icone` e `tempo`, que não foram guardados naquela coleta.

- `snapshots_manual_20261114.js`: cópia exata do `js/snapshots.js` da v2, montado à mão a partir das coletas de 14/11/2026 (IBGE às 16:11 UTC, INMET às 16:13 UTC). É a referência do contrato da interface para os testes da etapa 3e.
