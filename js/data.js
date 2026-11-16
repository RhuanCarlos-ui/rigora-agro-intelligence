/**
 * Rigora Agro Intelligence - Base de Dados do Protótipo
 * Projeto Global Solution 2026 - FIAP (CitizenTech)
 *
 * CLASSIFICAÇÃO DOS DADOS (exibida em toda a interface):
 *   - "oficial"       : obtido diretamente de fonte pública identificada (IBGE, INMET, Cepea, Conab).
 *   - "estimado"      : calculado pela Rigora a partir de metodologia documentada.
 *   - "demonstrativo" : simula a propriedade fictícia (não representa um produtor real).
 *
 * Recalibração (nov/2026): a propriedade demonstrativa passou a representar um
 * pequeno produtor (12 ha, ~22 sc/ha), coerente com a média oficial do IBGE para
 * Patrocínio (18,5 sc/ha em 2025) e de Minas Gerais (23,7 sc/ha em 2025).
 */

const RIGORA_DATA = {
  meta: {
    referenceDate: '2026-11-14',
    currentSafra: '2027',
    previousSafra: '2026'
  },

  // ---------------------------------------------------------------------------
  // PROPRIEDADE DEMONSTRATIVA (pequeno produtor)
  // ---------------------------------------------------------------------------
  property: {
    id: 'prop-01',
    name: 'Sítio Santa Rita do Cerrado',
    owner: 'Produtor demonstrativo (agricultura familiar)',
    municipality: 'Patrocínio',
    municipalityCode: '3148103',
    state: 'MG',
    region: 'Cerrado Mineiro',
    totalAreaHa: null,   // Bloco 2 (T9): calculada logo após este objeto, como a soma das áreas dos talhões
    crop: 'Café arábica',
    system: 'Sequeiro, colheita semimecanizada',
    dataType: 'demonstrativo'
  },

  safras: [
    { id: '2027', label: 'Safra 2027 (em andamento)', status: 'em_andamento', costWindow: 'set/2026 a ago/2027' },
    { id: '2026', label: 'Safra 2026 (concluída)', status: 'concluida', costWindow: 'set/2025 a ago/2026' }
  ],

  talhoes: [
    { id: 'T01', name: 'Talhão 01 - Encosta do Sol', short: 'T01 Encosta do Sol', variety: 'Catuaí Vermelho IAC 144', plantingYear: 2018, areaHa: 5.0 },
    { id: 'T02', name: 'Talhão 02 - Chapadão Central', short: 'T02 Chapadão Central', variety: 'Mundo Novo IAC 376-4', plantingYear: 2016, areaHa: 4.5 },
    { id: 'T03', name: 'Talhão 03 - Vereda Baixa', short: 'T03 Vereda Baixa', variety: 'Catuaí Amarelo IAC 62', plantingYear: 2020, areaHa: 2.5 }
  ],

  categories: [
    'Fertilizantes e Corretivos',
    'Defensivos',
    'Mão de Obra e Colheita',
    'Mecanização e Combustível',
    'Energia, Manutenção e Outros'
  ],

  // ---------------------------------------------------------------------------
  // LIVRO-CAIXA (lançamentos) — fonte única dos custos. talhaoId 'ALL' = rateio por área.
  // Safra 2026: set/2025 a ago/2026 (concluída). Safra 2027: a partir de set/2026.
  // ---------------------------------------------------------------------------
  entries: [
    { id: 'L001', date: '2025-09-12', safra: '2026', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Calagem e gessagem', value: 9000 },
    { id: 'L002', date: '2025-09-20', safra: '2026', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Distribuição de corretivos (trator e combustível)', value: 2400 },
    { id: 'L003', date: '2025-10-15', safra: '2026', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Adubação NPK - 1ª parcela', value: 14400 },
    { id: 'L004', date: '2025-10-22', safra: '2026', talhaoId: 'ALL', category: 'Defensivos', description: 'Herbicida nas entrelinhas', value: 3600 },
    { id: 'L005', date: '2025-11-10', safra: '2026', talhaoId: 'ALL', category: 'Defensivos', description: 'Fungicida + inseticida via solo', value: 9000 },
    { id: 'L006', date: '2025-11-25', safra: '2026', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Adubação NPK - 2ª parcela', value: 13680 },
    { id: 'L007', date: '2025-12-12', safra: '2026', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Adubação NPK - 3ª parcela', value: 12000 },
    { id: 'L008', date: '2025-12-18', safra: '2026', talhaoId: 'ALL', category: 'Mão de Obra e Colheita', description: 'Roçada e capina manual', value: 4200 },
    { id: 'L009', date: '2026-01-14', safra: '2026', talhaoId: 'ALL', category: 'Defensivos', description: 'Fungicida foliar - 1ª aplicação', value: 6300 },
    { id: 'L010', date: '2026-01-20', safra: '2026', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Adubação foliar (boro e zinco)', value: 4680 },
    { id: 'L011', date: '2026-01-28', safra: '2026', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Pulverização (trator e combustível)', value: 2700 },
    { id: 'L012', date: '2026-02-10', safra: '2026', talhaoId: 'ALL', category: 'Defensivos', description: 'Fungicida foliar - 2ª aplicação', value: 4500 },
    { id: 'L013', date: '2026-02-11', safra: '2026', talhaoId: 'T03', category: 'Defensivos', description: 'Reforço de fungicida na baixada (talhão mais úmido)', value: 1800 },
    { id: 'L014', date: '2026-02-18', safra: '2026', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Adubação NPK - 4ª parcela', value: 7800 },
    { id: 'L015', date: '2026-02-26', safra: '2026', talhaoId: 'ALL', category: 'Energia, Manutenção e Outros', description: 'Energia elétrica e manutenção do terreiro', value: 2400 },
    { id: 'L016', date: '2026-03-09', safra: '2026', talhaoId: 'ALL', category: 'Defensivos', description: 'Controle da broca-do-café', value: 3960 },
    { id: 'L017', date: '2026-03-16', safra: '2026', talhaoId: 'ALL', category: 'Mão de Obra e Colheita', description: 'Arruação pré-colheita', value: 4800 },
    { id: 'L018', date: '2026-03-25', safra: '2026', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Roçada mecanizada', value: 2700 },
    { id: 'L019', date: '2026-04-13', safra: '2026', talhaoId: 'ALL', category: 'Energia, Manutenção e Outros', description: 'Revisão do secador e da derriçadeira', value: 3120 },
    { id: 'L020', date: '2026-04-27', safra: '2026', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Combustível e manutenção do trator', value: 3000 },
    { id: 'L021', date: '2026-05-18', safra: '2026', talhaoId: 'T01', category: 'Mão de Obra e Colheita', description: 'Colheita semimecanizada - T01', value: 6300 },
    { id: 'L022', date: '2026-05-25', safra: '2026', talhaoId: 'T02', category: 'Mão de Obra e Colheita', description: 'Colheita semimecanizada - T02', value: 5100 },
    { id: 'L023', date: '2026-05-29', safra: '2026', talhaoId: 'T03', category: 'Mão de Obra e Colheita', description: 'Colheita manual - T03 (declive)', value: 2600 },
    { id: 'L024', date: '2026-05-30', safra: '2026', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Transporte da colheita', value: 2700 },
    { id: 'L025', date: '2026-05-31', safra: '2026', talhaoId: 'ALL', category: 'Energia, Manutenção e Outros', description: 'Energia da secagem', value: 2400 },
    { id: 'L026', date: '2026-06-12', safra: '2026', talhaoId: 'T01', category: 'Mão de Obra e Colheita', description: 'Repasse e varrição - T01', value: 7400 },
    { id: 'L027', date: '2026-06-16', safra: '2026', talhaoId: 'T02', category: 'Mão de Obra e Colheita', description: 'Repasse e varrição - T02', value: 6200 },
    { id: 'L028', date: '2026-06-19', safra: '2026', talhaoId: 'T03', category: 'Mão de Obra e Colheita', description: 'Repasse e varrição - T03', value: 2760 },
    { id: 'L029', date: '2026-06-26', safra: '2026', talhaoId: 'ALL', category: 'Energia, Manutenção e Outros', description: 'Sacaria e armazenagem', value: 1800 },
    { id: 'L030', date: '2026-07-14', safra: '2026', talhaoId: 'ALL', category: 'Mão de Obra e Colheita', description: 'Beneficiamento e pós-colheita', value: 6000 },
    { id: 'L031', date: '2026-07-21', safra: '2026', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Frete até a cooperativa', value: 2700 },
    // Safra 2027 (em andamento)
    { id: 'L032', date: '2026-09-10', safra: '2027', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Calagem e gessagem', value: 9600 },
    { id: 'L033', date: '2026-09-18', safra: '2027', talhaoId: 'ALL', category: 'Mecanização e Combustível', description: 'Distribuição de corretivos (trator e combustível)', value: 2500 },
    { id: 'L034', date: '2026-10-14', safra: '2027', talhaoId: 'ALL', category: 'Fertilizantes e Corretivos', description: 'Adubação NPK - 1ª parcela', value: 15600 },
    { id: 'L035', date: '2026-10-21', safra: '2027', talhaoId: 'ALL', category: 'Defensivos', description: 'Herbicida nas entrelinhas', value: 3800 },
    { id: 'L036', date: '2026-11-10', safra: '2027', talhaoId: 'ALL', category: 'Defensivos', description: 'Fungicida + inseticida via solo', value: 9400 }
  ],

  // Orçamento demonstrativo da safra em andamento (premissa do produtor)
  budget: { safra: '2027', totalBrl: 168000, monthsInCycle: 12, cycleStart: '2026-09-01' },

  // Colheita registrada (sacas beneficiadas de 60 kg) — demonstrativo
  harvest: [
    { safra: '2026', talhaoId: 'T01', sacas: 120 },
    { safra: '2026', talhaoId: 'T02', sacas: 97 },
    { safra: '2026', talhaoId: 'T03', sacas: 45 }
  ],

  // Vendas registradas — demonstrativo (preços abaixo do Indicador Cepea do mês, como ocorre ao produtor)
  sales: [
    { id: 'V01', date: '2026-06-22', safra: '2026', sacas: 80, price: 1400, buyer: 'Cooperativa (venda à vista)' },
    { id: 'V02', date: '2026-08-18', safra: '2026', sacas: 100, price: 1690, buyer: 'Cooperativa (venda à vista)' },
    { id: 'V03', date: '2026-10-21', safra: '2026', sacas: 50, price: 1510, buyer: 'Corretora regional' }
  ],

  // Histórico agregado do sítio (safras anteriores ao livro-caixa digital) — demonstrativo
  historicalSafras: [
    { safra: '2022', productivity: 17.5, costPerHa: 10200, avgPrice: 1180 },
    { safra: '2023', productivity: 31.0, costPerHa: 11400, avgPrice: 1020 },
    { safra: '2024', productivity: 24.5, costPerHa: 12100, avgPrice: 1350 },
    { safra: '2025', productivity: 19.0, costPerHa: 13000, avgPrice: 2150 }
  ],

  // ---------------------------------------------------------------------------
  // MERCADO — Indicador CEPEA/ESALQ café arábica tipo 6 (São Paulo), médias mensais 2026
  // OFICIAL: valores publicados pelo Cepea/Esalq-USP. Set/2026 não localizado (lacuna exibida).
  // ---------------------------------------------------------------------------
  cepea: {
    name: 'Indicador CEPEA/ESALQ - Café arábica tipo 6, bebida dura para melhor (posto São Paulo)',
    unit: 'R$/saca 60 kg',
    url: 'https://www.cepea.org.br/br/indicador/cafe.aspx',
    collectedAt: '2026-11-14',
    monthly: [
      { month: '2026-01', value: 2175.82, note: 'Derivado: média de fev (R$ 1.864,51) + queda de R$ 311,31 informada pelo Cepea', derived: true, source: 'Cepea, 04/03/2026' },
      { month: '2026-02', value: 1864.51, source: 'Cepea, 04/03/2026' },
      { month: '2026-03', value: 1913.89, source: 'Cepea, Informativo abr/2026' },
      { month: '2026-04', value: 1811.87, source: 'Cepea, Informativo abr/2026' },
      { month: '2026-05', value: 1653.92, source: 'Cepea, 03/06/2026' },
      { month: '2026-06', value: 1476.77, note: 'Valor citado pelo Cepea em termos reais (IGP-DI jun/2026)', source: 'Cepea, Informativo jul/2026' },
      { month: '2026-07', value: 1724.47, source: 'Cepea, Informativo jul/2026' },
      { month: '2026-08', value: 1770.88, source: 'Cepea Agromensal ago/2026 (via Agrolink)' },
      { month: '2026-09', value: null, note: 'Média mensal não localizada nas fontes consultadas' },
      { month: '2026-10', value: 1586.08, source: 'Cepea Agromensal out/2026 (via Agrolink)' }
    ]
  },

  // Referências oficiais complementares (Conab)
  conabRefs: {
    minPrice2025_26: { value: 662.04, unit: 'R$/saca', source: 'Conab / Portaria MAPA nº 780, de 10/03/2025', url: 'https://www.gov.br/conab' },
    arabicaYieldBrazil2025: { value: 24.1, unit: 'sc/ha', source: 'Conab, 4º levantamento safra 2025 (dez/2025)', url: 'https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/safras/safra-de-cafe' }
  },

  // Estoque de insumos — demonstrativo
  inventory: [
    { id: 'INS-01', name: 'Fertilizante NPK 20-00-20', currentQuantity: 9, minQuantity: 12, unit: 'sacas (50 kg)' },
    { id: 'INS-02', name: 'Fungicida cúprico', currentQuantity: 14, minQuantity: 8, unit: 'kg' },
    { id: 'INS-03', name: 'Adubo foliar (boro + zinco)', currentQuantity: 3, minQuantity: 4, unit: 'galões (20 L)' },
    { id: 'INS-04', name: 'Óleo mineral adjuvante', currentQuantity: 18, minQuantity: 10, unit: 'litros' }
  ],

  // Regras de automação (limiares editáveis pelo usuário)
  automationRules: [
    { id: 'RULE-01', name: 'Custo elevado por hectare', thresholdValue: 14500, unit: 'R$/ha', enabled: true, type: 'cost' },
    { id: 'RULE-02', name: 'Estoque mínimo de insumos', thresholdValue: null, unit: '', enabled: true, type: 'stock' },
    { id: 'RULE-03', name: 'Condição climática favorável à ferrugem (estimativa)', thresholdValue: 80, unit: '% UR média', enabled: true, type: 'climate' }
  ],

  // ---------------------------------------------------------------------------
  // CATÁLOGO DE FONTES (área de transparência)
  // ---------------------------------------------------------------------------
  dataSources: [
    {
      id: 'SRC-IBGE', name: 'IBGE - Produção Agrícola Municipal (SIDRA, tabela 1613)', type: 'oficial',
      url: 'https://sidra.ibge.gov.br/tabela/1613',
      content: 'Rendimento médio (kg/ha), área colhida e produção de café arábica por município de MG, 2016-2025.',
      method: 'Consulta à API SIDRA; rendimento convertido para sacas de 60 kg (kg/ha ÷ 60). Média de MG = produção total ÷ área colhida total.',
      limitations: 'Dado anual e municipal: não mede a propriedade. Publicação com cerca de 1 ano de defasagem (2026 ainda não publicado).'
    },
    {
      id: 'SRC-INMET', name: 'INMET - Previsão do tempo por município (Patrocínio, 3148103)', type: 'oficial',
      url: 'https://portal.inmet.gov.br/',
      content: 'Previsão de 5 dias: temperatura mín/máx, umidade relativa mín/máx, condição do tempo e vento.',
      method: 'Consulta à API pública de previsão do INMET. O risco de ferrugem é uma ESTIMATIVA da Rigora (UR média estimada ≥ 80% e T média entre 22 e 26 °C).',
      limitations: 'É previsão, não medição. A API não fornece volume nem probabilidade de chuva.'
    },
    {
      id: 'SRC-CEPEA', name: 'Cepea/Esalq-USP - Indicador do café arábica', type: 'oficial',
      url: 'https://www.cepea.org.br/br/indicador/cafe.aspx',
      content: 'Médias mensais de 2026 do Indicador CEPEA/ESALQ (R$/saca de 60 kg, posto São Paulo).',
      method: 'Valores transcritos dos boletins do Cepea e de veículos que os reproduzem, com a fonte de cada mês.',
      limitations: 'Preço de referência em São Paulo: o produtor recebe valor diferente (frete, qualidade, prazo). Sem API aberta; set/2026 não localizado.'
    },
    {
      id: 'SRC-CONAB', name: 'Conab - Custos de produção e acompanhamento de safra', type: 'oficial',
      url: 'https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/custos-de-producao',
      content: 'Usado como referência: preço mínimo do arábica 2025/26 e produtividade média nacional do arábica 2025.',
      method: 'Valores pontuais citados com a fonte. A planilha de custo por hectare de Patrocínio ainda não foi integrada.',
      limitations: 'Custos da propriedade demonstrativa NÃO vêm da Conab: são premissas demonstrativas (ver livro-caixa).'
    },
    {
      id: 'SRC-LEDGER', name: 'Livro-caixa da propriedade (Rigora)', type: 'demonstrativo',
      url: '',
      content: 'Lançamentos de custos por talhão, colheita, vendas e estoque do Sítio Santa Rita do Cerrado.',
      method: 'Lançamentos gerais são rateados entre talhões pela área. Indicadores calculados em js/metrics.js.',
      limitations: 'Propriedade fictícia criada para demonstração. Lançamentos novos ficam salvos só neste navegador (armazenamento local): não há conta, servidor nem sincronização entre aparelhos, e limpar os dados do navegador apaga esses lançamentos.'
    }
  ]
};

// Bloco 2 (T9): a área total é a soma dos talhões (12 ha), para nunca divergir dos indicadores.
RIGORA_DATA.property.totalAreaHa = RIGORA_DATA.talhoes.reduce((s, t) => s + t.areaHa, 0);

if (typeof window !== 'undefined') {
  window.RIGORA_DATA = RIGORA_DATA;
}
if (typeof module === 'object' && module.exports) {
  module.exports = RIGORA_DATA;
}
