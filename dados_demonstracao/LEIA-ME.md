# Dados de demonstração

**Estes arquivos não são uma coleta completa.** São as amostras reduzidas usadas nos testes
automatizados (`tests/fixtures/`), copiadas para permitir a demonstração do pipeline sem internet.
Uso: `README_EXECUCAO.md`, seção 5. **Elas não são necessárias para abrir a interface.**

| Arquivo | Conteúdo | Origem |
|---|---|---|
| `sidra_pam_1613_mg_arabica_amostra.json` | PAM tabela 1613, café arábica: Patrocínio, Manhuaçu, Monte Carmelo e Campos Altos, 2016–2025 (rendimento, área colhida, produção) | Valores reais do IBGE copiados da coleta de 14/11/2026 e reescritos no formato de resposta do SIDRA |
| `ibge_localidades_mg_amostra.json` | Nomes e mesorregiões dos mesmos 4 municípios | Estrutura da API de localidades do IBGE, reduzida |
| `inmet_previsao_3148103_amostra.json` | Previsão de 5 dias do INMET para Patrocínio | Previsão real coletada em 14/11/2026, sem os ícones |
| `*.meta.json` | Metadados exigidos pelo pipeline: fonte, URL, data da coleta, SHA-256 e número de registros | **Gerados para a demonstração** em 16/11/2026; a data da coleta é a da amostra (14/11/2026) |

Comportamentos esperados com esta amostra:

- o pipeline grava 40 registros da PAM e 9 da previsão, com status `sucesso`;
- a validação do INMET avisa que a previsão é antiga (regra MET-10): ela é gravada, mas não pode ser
  exibida como atual;
- a simulação da publicação **reprova**, porque a publicação exige os 60 maiores municípios com
  procedência completa.
