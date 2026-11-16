/**
 * Rigora Agro Intelligence - Aplicação Principal (JavaScript puro)
 * Projeto Global Solution 2026 - FIAP (CitizenTech)
 *
 * v2 (nov/2026):
 *  - Todos os indicadores vêm de js/metrics.js (fonte única de cálculo).
 *  - Dados classificados em oficial / estimado / demonstrativo em toda a interface.
 *  - Integrações reais preservadas (IBGE SIDRA e INMET) com timeout, validação e
 *    fallback para cópia oficial datada (js/snapshots.js), nunca para valores inventados.
 *  - Filtros, cards, gráficos, alertas e simulador conectados ao mesmo estado.
 */

const RigoraApp = (function () {
  'use strict';

  const M = window.RigoraMetrics;
  const C = window.RigoraCharts;
  const D = window.RIGORA_DATA;
  const SNAP = window.RIGORA_SNAPSHOTS || {};
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const $ = (id) => document.getElementById(id);
  const esc = (s) => C.esc(s === null || s === undefined ? '' : s);

  const MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const CAT_COLORS = {
    'Fertilizantes e Corretivos': '#248763',
    'Defensivos': '#44A08D',
    'Mão de Obra e Colheita': '#163D32',
    'Mecanização e Combustível': '#7AA88F',
    'Energia, Manutenção e Outros': '#A3B8AD'
  };

  /* ==========================================================================
     LIVRO-CAIXA SALVO NO NAVEGADOR (Bloco 2, T3)
     Lançamentos novos ficam no localStorage deste navegador, numa chave versionada.
     Os lançamentos de demonstração (js/data.js) nunca são gravados nem substituídos.
     Nada é apagado em silêncio: registro inválido ou repetido fica guardado em "ignorados";
     conteúdo ilegível ou de outra versão é copiado para outra chave antes de ser
     substituído e, se essa cópia falhar, nada é gravado.
     ========================================================================== */
  const LEDGER_KEY = 'rigora.lancamentos.v1';
  const LEDGER_VERSION = 1;
  const LEDGER_WINDOW = ['2026-09-01', '2027-08-31'];            // ciclo da safra 2027 (mesma regra do formulário)
  const LEDGER_DESC_MAX = 90;                                    // igual ao maxlength do campo no index.html
  const ledgerStore = { status: 'vazio', raw: null, ignored: [], backupKey: null, savedAt: null, lastError: '' };
  const isRecord = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

  function browserStorage() {
    try { return typeof localStorage !== 'undefined' && localStorage ? localStorage : null; } catch (e) { return null; }
  }

  function isoDateOk(s) {
    const m = typeof s === 'string' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(s) : null;
    if (!m) return false;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return d.getFullYear() === +m[1] && d.getMonth() === +m[2] - 1 && d.getDate() === +m[3];
  }

  /** Lançamento novo válido: as regras do formulário e as do registro salvo. */
  function ledgerEntryOk(e) {
    return isRecord(e)
      && typeof e.id === 'string' && /^L\d{6,}$/.test(e.id)
      && isoDateOk(e.date) && e.date >= LEDGER_WINDOW[0] && e.date <= LEDGER_WINDOW[1]
      && e.safra === '2027'
      && (e.talhaoId === 'ALL' || D.talhoes.some(t => t.id === e.talhaoId))
      && D.categories.includes(e.category)
      && typeof e.description === 'string' && e.description.trim().length > 0 && e.description.length <= LEDGER_DESC_MAX
      && typeof e.value === 'number' && Number.isFinite(e.value) && e.value > 0;
  }

  const ledgerRecord = (e) => ({ id: e.id, date: e.date, safra: '2027', talhaoId: e.talhaoId, category: e.category, description: e.description, value: e.value });

  /** Lê os lançamentos novos salvos neste navegador. Nunca lança erro e nunca grava. */
  function loadStoredEntries() {
    const ls = browserStorage();
    if (!ls) { ledgerStore.status = 'indisponivel'; return []; }
    let raw;
    try { raw = ls.getItem(LEDGER_KEY); } catch (e) { ledgerStore.status = 'indisponivel'; return []; }
    if (raw === null || raw === undefined) { ledgerStore.status = 'vazio'; return []; }
    let obj = null;
    try { obj = JSON.parse(raw); } catch (e) { obj = null; }
    if (!isRecord(obj) || obj.versao !== LEDGER_VERSION || !Array.isArray(obj.lancamentos)) {
      ledgerStore.status = 'corrompido';
      ledgerStore.raw = String(raw);
      return [];
    }
    const known = new Set(D.entries.map(e => e.id));
    const out = [];
    const ignored = Array.isArray(obj.ignorados) ? obj.ignorados.slice() : [];
    obj.lancamentos.forEach(e => {
      if (ledgerEntryOk(e) && !known.has(e.id)) { known.add(e.id); out.push(Object.assign(ledgerRecord(e), { isNew: true, stored: true })); }
      else ignored.push(e);
    });
    ledgerStore.ignored = ignored;
    ledgerStore.savedAt = typeof obj.salvoEm === 'string' ? obj.salvoEm : null;
    ledgerStore.status = ignored.length ? 'parcial' : 'ok';
    return out;
  }

  /** Grava os lançamentos novos da página. Retorna { ok, message }. Nunca lança erro. */
  function saveStoredEntries() {
    const ls = browserStorage();
    const fail = (message) => { ledgerStore.lastError = message; return { ok: false, message }; };
    if (!ls) return fail('o armazenamento deste navegador não está disponível');
    if (ledgerStore.status === 'corrompido' && !ledgerStore.backupKey) {
      const key = `${LEDGER_KEY}.corrompido.${Date.now()}`;
      try {
        ls.setItem(key, ledgerStore.raw);
        if (ls.getItem(key) !== ledgerStore.raw) throw new Error('cópia não confirmada');
        ledgerStore.backupKey = key;
      } catch (e) {
        return fail('não foi possível guardar à parte os dados ilegíveis já salvos; por segurança, nada foi gravado');
      }
    }
    const fresh = state.entries.filter(e => e.isNew);
    const payload = { versao: LEDGER_VERSION, salvoEm: new Date().toISOString(), lancamentos: fresh.map(ledgerRecord), ignorados: ledgerStore.ignored };
    try { ls.setItem(LEDGER_KEY, JSON.stringify(payload)); } catch (e) {
      return fail('o navegador recusou a gravação (armazenamento cheio ou bloqueado)');
    }
    fresh.forEach(e => { e.stored = true; });
    ledgerStore.savedAt = payload.salvoEm;
    ledgerStore.lastError = '';
    ledgerStore.status = ledgerStore.ignored.length ? 'parcial' : 'ok';
    return { ok: true, message: '' };
  }

  /** Há algo que o usuário precisa saber sobre o armazenamento dos lançamentos? */
  function ledgerWarn() {
    return ['indisponivel', 'corrompido', 'parcial'].includes(ledgerStore.status) || !!ledgerStore.lastError;
  }

  /** Situação do armazenamento em uma frase (aba Fontes, livro-caixa, avisos). */
  function ledgerNote() {
    const s = ledgerStore;
    const salvos = state.entries.filter(e => e.isNew && e.stored).length;
    const soSessao = state.entries.filter(e => e.isNew && !e.stored).length;
    const parts = [];
    if (s.status === 'indisponivel') parts.push('Armazenamento deste navegador indisponível: lançamentos novos valem só até fechar ou recarregar a página.');
    else if (s.status === 'corrompido' && !s.backupKey) parts.push('Os lançamentos salvos antes neste navegador não puderam ser lidos (conteúdo inválido ou de outra versão) e foram mantidos como estavam; antes da próxima gravação, uma cópia deles é guardada à parte.');
    else parts.push(`${salvos} lançamento(s) novo(s) salvo(s) só neste navegador${s.savedAt ? ` (última gravação ${dateTimeBR(s.savedAt)})` : ''}.`);
    if (s.ignored.length) parts.push(`${s.ignored.length} registro(s) salvo(s) inválido(s) ou repetido(s) não entram nos cálculos e continuam guardados sem alteração.`);
    if (s.backupKey) parts.push(`Os dados ilegíveis anteriores foram guardados na chave ${s.backupKey}.`);
    if (s.lastError) parts.push(`A última gravação falhou (${s.lastError}).`);
    if (soSessao) parts.push(`${soSessao} lançamento(s) só nesta sessão: somem ao fechar ou recarregar a página.`);
    return parts.join(' ');
  }

  /** Identificador novo e único (L + 6 dígitos), sem colidir com lançamentos nem registros guardados. */
  function newEntryId() {
    const ids = new Set(state.entries.map(e => e.id).concat(ledgerStore.ignored.map(e => (isRecord(e) ? e.id : null))));
    let n = Date.now() % 1000000;
    let id;
    do { id = `L${String(n).padStart(6, '0')}`; n = (n + 1) % 1000000; } while (ids.has(id));
    return id;
  }

  /* ==========================================================================
     ESTADO
     ========================================================================== */
  const state = {
    currentTab: 'overview',
    talhoes: clone(D.talhoes),
    entries: clone(D.entries).concat(loadStoredEntries()),   // Bloco 2 (T3): demonstração + lançamentos salvos neste navegador
    harvest: clone(D.harvest),
    sales: clone(D.sales),
    inventory: clone(D.inventory),
    rules: clone(D.automationRules),
    filters: { safra: D.meta.currentSafra, talhao: 'ALL', compare: true, alertCat: 'all' },
    seenAlerts: new Set(loadSeen()),
    alerts: [],
    ibge: { status: 'snapshot', collectedAt: SNAP.sidra ? SNAP.sidra.collectedAt : null, liveCheckedAt: null, municipios: SNAP.sidra ? clone(SNAP.sidra.municipios) : [], mg: SNAP.sidra ? clone(SNAP.sidra.mg) : {}, loading: false, message: '', conferencia: null },
    inmet: { status: 'loading', collectedAt: null, days: null, message: '' },
    sim: null,
    activeScenario: 'base',
    scenarios: null,
    integrationLog: []
  };

  function loadSeen() {
    try { return JSON.parse(localStorage.getItem('rigora.seenAlerts') || '[]'); } catch (e) { return []; }
  }
  function saveSeen() {
    try { localStorage.setItem('rigora.seenAlerts', JSON.stringify([...state.seenAlerts])); } catch (e) { /* armazenamento indisponível */ }
  }

  /* ==========================================================================
     UTILITÁRIOS DE FORMATAÇÃO E RÓTULOS
     ========================================================================== */
  const fmtR = (v, d = 0) => C.formatCurrency(v, d);
  const fmtN = (v, d = 1) => C.formatNumber(v, d);
  const fmtPct = (v, d = 1, sign = true) => (v === null || v === undefined || isNaN(v)) ? '—' : `${sign && v > 0 ? '+' : ''}${fmtN(v, d)}%`;
  const monthLabel = (ym) => { const [y, m] = ym.split('-'); return `${MONTHS[+m - 1]}/${y.slice(2)}`; };
  const dateBR = (iso) => { if (!iso) return '—'; const [y, m, d] = iso.slice(0, 10).split('-'); return `${d}/${m}/${y}`; };
  const dateTimeBR = (iso) => { if (!iso) return '—'; const d = new Date(iso); return `${d.toLocaleDateString('pt-BR')} ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`; };
  const talhaoName = (id) => id === 'ALL' ? 'Rateio geral' : (state.talhoes.find(t => t.id === id) || {}).short || id;

  function dtag(type, label) {
    const map = { oficial: ['dtag-oficial', 'Oficial'], estimado: ['dtag-estimado', 'Estimado'], demonstrativo: ['dtag-demo', 'Demonstrativo'] };
    const [cls, def] = map[type] || map.demonstrativo;
    return `<span class="dtag ${cls}">${esc(label || def)}</span>`;
  }

  function deltaBadge(pct, { invert = false, suffix = '', neutralText = 'sem comparação' } = {}) {
    if (pct === null || pct === undefined || isNaN(pct)) return `<span class="kpi-badge neutral">${esc(neutralText)}</span>`;
    const good = invert ? pct < 0 : pct > 0;
    const cls = Math.abs(pct) < 0.05 ? 'neutral' : (good ? 'positive' : 'alert');
    const arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '•');
    return `<span class="kpi-badge ${cls}">${arrow} ${fmtPct(pct)}${suffix ? ' ' + esc(suffix) : ''}</span>`;
  }

  function marketPrice() {
    const valid = D.cepea.monthly.filter(m => m.value !== null && !m.derived);
    return valid[valid.length - 1];
  }

  function logIntegration(source, message, level = 'info') {
    state.integrationLog.unshift({ at: new Date().toISOString(), source, message, level });
    const el = $('integration-log');
    if (el) renderIntegrationLog();
    if (window.console) console[level === 'error' ? 'warn' : 'info'](`[Rigora][${source}] ${message}`);
  }

  async function fetchWithTimeout(url, ms) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms);
    try {
      const r = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      if (e.name === 'AbortError') throw new Error(`tempo limite de ${ms / 1000}s excedido`);
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  /* ==========================================================================
     CÁLCULOS DERIVADOS (sempre via RigoraMetrics)
     ========================================================================== */
  function summary(safra, talhaoId) {
    return M.safraSummary({
      talhoes: state.talhoes, entries: state.entries, harvest: state.harvest, sales: state.sales,
      safra, talhaoId: talhaoId || 'ALL', marketPrice: marketPrice().value
    });
  }

  /** Histórico agregado (2022-2025) + 2026 calculado do livro-caixa. */
  function farmHistory() {
    const area = M.sum(state.talhoes, t => t.areaHa);
    const rows = D.historicalSafras.map(h => {
      const costPerSack = h.costPerHa / h.productivity;
      return { safra: h.safra, productivity: h.productivity, costPerHa: h.costPerHa, costPerSack, avgPrice: h.avgPrice, resultPerHa: h.productivity * h.avgPrice - h.costPerHa, source: 'agregado' };
    });
    const s = summary('2026');
    rows.push({ safra: '2026', productivity: s.productivity, costPerHa: s.costPerHa, costPerSack: s.costPerSack, avgPrice: s.avgSalePrice, resultPerHa: s.result / area, source: 'livro-caixa', stock: s.stockSc });
    return rows;
  }

  function ibgeYield(code, year) {
    const m = state.ibge.municipios.find(x => x.c === code);
    if (!m || !m.y[year] || m.y[year][0] === null) return null;
    return M.kgHaToScHa(m.y[year][0]);
  }
  function mgYield(year) {
    const v = state.ibge.mg[year];
    return v ? M.kgHaToScHa(v[0]) : null;
  }
  function ibgeLatestYear() {
    const years = Object.keys(state.ibge.mg || {}).sort();
    return years[years.length - 1];
  }

  /** Mesmo ponto do ciclo na safra anterior (ex.: 1/set a 14/nov). */
  function samePointPreviousSafra() {
    const ref = D.meta.referenceDate; // 2026-11-14
    const prevTo = `${+ref.slice(0, 4) - 1}${ref.slice(4)}`;
    return { from: `${+ref.slice(0, 4) - 1}-09-01`, to: prevTo };
  }

  function budgetProgress(talhaoId) {
    const area = M.sum(state.talhoes, t => t.areaHa);
    const share = (!talhaoId || talhaoId === 'ALL') ? 1 : (state.talhoes.find(t => t.id === talhaoId).areaHa / area);
    const budget = D.budget.totalBrl * share;
    const start = new Date(D.budget.cycleStart + 'T00:00:00');
    const ref = new Date(D.meta.referenceDate + 'T00:00:00');
    const elapsedPct = Math.min(100, ((ref - start) / (365 * 86400000)) * 100);
    const spent = M.costOf(M.filterEntries(state.entries, { safra: D.budget.safra }), state.talhoes, talhaoId);
    return { budget, spent, spentPct: (spent / budget) * 100, elapsedPct };
  }

  /* ==========================================================================
     INICIALIZAÇÃO E NAVEGAÇÃO
     ========================================================================== */
  function init() {
    buildScenarios();
    state.sim = clone(state.scenarios.base);
    populateStaticSelects();
    renderSimControls();
    renderAll();
    setupEventListeners();
    if (ledgerWarn()) {                                           // Bloco 2 (T3): avisa, nunca em silêncio
      logIntegration('Livro-caixa', ledgerNote(), 'error');
      showToast(ledgerNote(), 'warning');
    }
    fetchInmetForecast();
    startInmetAutoRefresh();
    refreshIbge(false);
  }

  function setupEventListeners() {
    document.addEventListener('click', (e) => {
      const wrap = document.querySelector('.notif-wrapper');
      const dd = $('notif-dropdown');
      if (wrap && dd && !wrap.contains(e.target)) dd.classList.remove('show');
    });
    const modal = $('modal-activity');
    if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeNewActivityModal(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closeDrawer(); closeNewActivityModal(); }
    });
    window.addEventListener('resize', debounce(renderCurrentTabCharts, 200));
    window.addEventListener('hashchange', () => {
      const tab = location.hash.replace('#', '');
      if (['overview', 'properties', 'intelligence', 'automations', 'sources'].includes(tab) && tab !== state.currentTab && $('view-app').style.display !== 'none') navigate(tab);
    });
  }

  function login() {
    $('view-login').style.display = 'none';
    $('view-app').style.display = 'flex';
    const tab = location.hash.replace('#', '');
    navigate(['overview', 'properties', 'intelligence', 'automations', 'sources'].includes(tab) ? tab : 'overview');
    showToast('Bem-vindo à Rigora. Você está no ambiente demonstrativo.', 'success');
  }

  function logout(e) {
    if (e) e.preventDefault();
    $('view-app').style.display = 'none';
    $('view-login').style.display = 'flex';
    showToast('Sessão encerrada.', 'info');
  }

  function navigate(tabId, event) {
    if (event) event.preventDefault();
    state.currentTab = tabId;
    document.querySelectorAll('.sidebar-nav .nav-link').forEach(l => l.classList.remove('active'));
    const link = $(`nav-${tabId}`);
    if (link) link.classList.add('active');
    const titles = { overview: 'Visão Geral', properties: 'Propriedade e Safras', intelligence: 'Inteligência e Projeções', automations: 'Automações', sources: 'Fontes de Dados e Transparência' };
    $('page-current-title').textContent = titles[tabId] || 'Rigora';
    document.querySelectorAll('.content-view').forEach(v => v.classList.remove('active'));
    const view = $(`tab-${tabId}`);
    if (view) view.classList.add('active');
    try { if (location.hash !== `#${tabId}`) history.replaceState(null, '', `#${tabId}`); } catch (e) { /* hash indisponível */ }
    renderCurrentTabCharts();
    $('sidebar').classList.remove('open');
    const main = document.querySelector('.main-wrapper');
    if (main) main.scrollTop = 0;
    window.scrollTo(0, 0);
  }

  function scrollToSection(e, id) {
    if (e) e.preventDefault();
    const el = $(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderAll() {
    renderOverview();
    renderProperties();
    renderIntelligence();
    renderAutomations();
    renderSources();
    renderAlerts();
  }

  function renderCurrentTabCharts() {
    if (state.currentTab === 'overview') renderOverviewCharts();
    if (state.currentTab === 'properties') renderPropertyCharts();
    if (state.currentTab === 'intelligence') { updateSimulator(); renderBenchmark(); renderMarket(); }
  }

  /* ==========================================================================
     FILTROS GLOBAIS
     ========================================================================== */
  function onSafraChange(safra) {
    state.filters.safra = safra;
    renderOverview();
    renderPropertyCharts();
    renderTalhaoCards();
    const ls = $('ledger-filter-safra');
    if (ls) { ls.value = safra; renderLedger(); }
    flash('ov-kpis');
    showToast(`Exibindo a ${safra === D.meta.currentSafra ? 'safra 2027 (em andamento)' : 'safra 2026 (concluída)'}.`, 'info');
  }

  function onPropertyChange() {
    showToast('Somente a propriedade demonstrativa está cadastrada nesta versão.', 'info');
  }

  function setTalhao(id) {
    state.filters.talhao = id;
    renderOverview();
    renderTalhaoCards();
    flash('ov-kpis');
  }

  function setCompare(on) {
    state.filters.compare = on;
    renderOverview();
  }

  function flash(id) {
    const el = $(id);
    if (!el) return;
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  }

  function populateStaticSelects() {
    const opts = (items) => items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
    $('act-talhao').innerHTML = opts([['ALL', 'Rateio geral (todos os talhões, pela área)'], ...state.talhoes.map(t => [t.id, `${t.short} · ${fmtN(t.areaHa, 1)} ha`])]);
    $('act-category').innerHTML = opts(D.categories.map(c => [c, c]));
    $('ledger-filter-safra').innerHTML = opts([['', 'Todas as safras'], ...D.safras.map(s => [s.id, s.label])]);
    $('ledger-filter-safra').value = state.filters.safra;
    $('ledger-filter-talhao').innerHTML = opts([['', 'Todos os talhões'], ...state.talhoes.map(t => [t.id, t.short])]);
    $('ledger-filter-category').innerHTML = opts([['', 'Todas as categorias'], ...D.categories.map(c => [c, c])]);
  }

  /* ==========================================================================
     VISÃO GERAL
     ========================================================================== */
  function renderOverview() {
    const { safra, talhao } = state.filters;
    const area = talhao === 'ALL' ? M.sum(state.talhoes, t => t.areaHa) : state.talhoes.find(t => t.id === talhao).areaHa;
    const isCurrent = safra === D.meta.currentSafra;
    $('ov-context-title').textContent = `${isCurrent ? 'Safra 2027 · em andamento' : 'Safra 2026 · concluída'} · ${talhao === 'ALL' ? 'Sítio Santa Rita do Cerrado' : talhaoName(talhao)}`;
    $('ov-context-sub').textContent = `Patrocínio (MG) · ${fmtN(area, 1)} ha de café arábica · ciclo de custos ${D.safras.find(s => s.id === safra).costWindow} · dados até ${dateBR(D.meta.referenceDate)}`;

    $('ov-filter-talhao').innerHTML = [['ALL', 'Todos'], ...state.talhoes.map(t => [t.id, t.id])].map(([v, l]) =>
      `<button role="radio" aria-checked="${talhao === v}" class="seg-btn ${talhao === v ? 'active' : ''}" onclick="RigoraApp.setTalhao('${v}')" title="${esc(v === 'ALL' ? 'Propriedade inteira' : talhaoName(v))}">${esc(l)}</button>`).join('');

    $('ov-kpis').innerHTML = buildKpis().map((k, i) => `
      <button class="kpi-card clickable" onclick="RigoraApp.openKpi(${i})" aria-label="Detalhar ${esc(k.title)}">
        <div class="kpi-card-header"><span class="kpi-title">${esc(k.title)}</span>${dtag(k.type, k.typeLabel)}</div>
        <div class="kpi-value">${k.value}</div>
        <div class="kpi-footer">${k.footer}</div>
        <span class="kpi-more">Detalhar →</span>
      </button>`).join('');

    renderOverviewCharts();
  }

  /** Cards de indicadores (cada um carrega a explicação usada no painel de detalhe). */
  function buildKpis() {
    const { safra, talhao, compare } = state.filters;
    const price = marketPrice();
    const prevPrice = D.cepea.monthly.filter(m => m.value && !m.derived && m.month < price.month).slice(-1)[0];
    const priceKpi = {
      key: 'price', title: 'Preço do café (Cepea)', type: 'oficial', typeLabel: 'Oficial · transcrito',
      value: fmtR(price.value, 2),
      footer: `${deltaBadge(M.pctChange(price.value, prevPrice.value), { suffix: `vs ${monthLabel(prevPrice.month)}` })}<span class="kpi-note">média de ${monthLabel(price.month)}</span>`,
      detail: () => ({
        formula: 'Média mensal do Indicador CEPEA/ESALQ do arábica tipo 6 (posto São Paulo).',
        rows: D.cepea.monthly.map(m => [monthLabel(m.month), m.value === null ? 'não localizado' : fmtR(m.value, 2), m.derived ? 'derivado' : (m.value === null ? '—' : 'oficial')]),
        rowHead: ['Mês', 'R$/saca', 'Tipo'],
        notes: [`Fonte: ${D.cepea.name}. ${D.cepea.monthly.find(m => m.month === price.month).source}.`, 'É preço de referência em São Paulo: o valor recebido pelo produtor varia com frete, qualidade e prazo.'],
        link: ['intelligence', 'sec-market', 'Ver gráfico de mercado']
      })
    };

    if (safra === D.meta.currentSafra) {
      const cur = summary(safra, talhao);
      const pt = samePointPreviousSafra();
      const prevSame = M.costOf(M.filterEntries(state.entries, { safra: D.meta.previousSafra, from: pt.from, to: pt.to }), state.talhoes, talhao);
      const area = cur.areaHa;
      const bp = budgetProgress(talhao);
      const sim = M.simulate(Object.assign({}, state.scenarios.base, { areaHa: area }));
      return [
        {
          key: 'cost', title: 'Custo acumulado', type: 'demonstrativo', value: fmtR(cur.cost),
          footer: `<span class="kpi-badge ${bp.spentPct - bp.elapsedPct > 3 ? 'alert' : 'neutral'}">${fmtN(bp.spentPct, 0)}% do orçamento</span><span class="kpi-note">${fmtN(bp.elapsedPct, 0)}% do ciclo decorrido</span>`,
          detail: () => ({
            formula: 'Soma dos lançamentos da safra 2027. Lançamentos gerais são rateados pela área de cada talhão.',
            rows: M.costByCategory(M.filterEntries(state.entries, { safra }), state.talhoes, talhao).map(c => [c.category, fmtR(c.value), `${fmtN(c.value / cur.cost * 100, 1)}%`]),
            rowHead: ['Categoria', 'Valor', '%'],
            notes: [`Orçamento demonstrativo do produtor: ${fmtR(bp.budget)} (${fmtR(bp.budget / area)}/ha).`, `Ritmo: ${fmtN(bp.spentPct, 1)}% do orçamento gasto com ${fmtN(bp.elapsedPct, 1)}% do ciclo (01/09/2026 a 31/08/2027) decorrido. Adubação e correção concentram gastos no início do ciclo.`],
            link: ['properties', null, 'Abrir livro-caixa']
          })
        },
        {
          key: 'costha', title: 'Custo por hectare', type: 'demonstrativo', value: fmtR(cur.costPerHa),
          footer: compare ? `${deltaBadge(M.pctChange(cur.cost / area, prevSame / area), { invert: true, suffix: 'vs mesmo período' })}<span class="kpi-note">até ${dateBR(D.meta.referenceDate).slice(0, 5)}</span>` : `<span class="kpi-note">${fmtN(area, 1)} ha</span>`,
          detail: () => ({
            formula: 'Custo acumulado ÷ área do recorte. Comparado ao mesmo intervalo do ciclo anterior (01/09/2025 a 14/11/2025).',
            rows: [['Safra 2027 (01/09 a 14/11/2026)', fmtR(cur.cost), fmtR(cur.costPerHa)], ['Safra 2026 (01/09 a 14/11/2025)', fmtR(prevSame), fmtR(prevSame / area)]],
            rowHead: ['Período', 'Custo', 'R$/ha'],
            notes: ['Comparar o mesmo intervalo evita confundir sazonalidade dos gastos com aumento de custo.'],
            link: ['properties', null, 'Ver custo por talhão']
          })
        },
        {
          key: 'prod', title: 'Produtividade esperada', type: 'estimado', value: `${fmtN(state.scenarios.base.productivity, 1)} <small>sc/ha</small>`,
          footer: `<span class="kpi-note">premissa do cenário base</span><span class="kpi-note">Patrocínio ${ibgeLatestYear()}: ${fmtN(ibgeYield('3148103', ibgeLatestYear()), 1)} (IBGE)</span>`,
          detail: () => ({
            formula: 'Média da produtividade do sítio nas safras 2022 a 2026 (inclui anos de alta e de baixa bienalidade).',
            rows: farmHistory().map(h => [h.safra, `${fmtN(h.productivity, 1)} sc/ha`, h.source === 'livro-caixa' ? 'livro-caixa' : 'agregado']),
            rowHead: ['Safra', 'Produtividade', 'Origem'],
            notes: ['A colheita 2027 só acontece entre maio e agosto de 2027: este número é uma premissa, não uma medição.', `Referência oficial: Patrocínio teve ${fmtN(ibgeYield('3148103', ibgeLatestYear()), 1)} sc/ha e Minas Gerais ${fmtN(mgYield(ibgeLatestYear()), 1)} sc/ha em ${ibgeLatestYear()} (IBGE/PAM).`],
            link: ['intelligence', 'sec-sim', 'Ajustar no simulador']
          })
        },
        {
          key: 'result', title: 'Resultado projetado', type: 'estimado', value: fmtR(sim.result),
          footer: `<span class="kpi-badge ${sim.margin >= 0 ? 'positive' : 'alert'}">margem ${fmtN(sim.margin, 1)}%</span><span class="kpi-note">equilíbrio ${fmtR(sim.breakEvenPrice)}/sc</span>`,
          detail: () => ({
            formula: 'Simulador, cenário base: produção líquida × preço − área × custo/ha.',
            rows: [['Área', `${fmtN(area, 1)} ha`], ['Produtividade', `${fmtN(state.scenarios.base.productivity, 1)} sc/ha`], ['Perdas', `${fmtN(state.scenarios.base.lossPct, 1)}%`], ['Preço (Cepea out/2026)', fmtR(state.scenarios.base.price, 2)], ['Custo/ha (orçamento)', fmtR(state.scenarios.base.costPerHa)], ['Produção líquida', `${fmtN(sim.netSc, 0)} sc`], ['Receita', fmtR(sim.revenue)], ['Custo', fmtR(sim.cost)], ['Resultado', fmtR(sim.result)]],
            rowHead: ['Premissa', 'Valor'],
            notes: ['Projeção para planejamento, não previsão garantida de lucro.'],
            link: ['intelligence', 'sec-sim', 'Abrir simulador']
          })
        },
        priceKpi
      ];
    }

    // Safra concluída (2026)
    const cur = summary(safra, talhao);
    const hist25 = D.historicalSafras.find(h => h.safra === '2025');
    const isAll = talhao === 'ALL';
    const prevProd = isAll ? hist25.productivity : null;
    const prevCostSack = isAll ? hist25.costPerHa / hist25.productivity : null;
    const area = cur.areaHa;
    const prevResult = isAll ? (hist25.productivity * hist25.avgPrice - hist25.costPerHa) * area : null;
    const cmp = (v) => compare ? v : null;
    const byTalhao = state.talhoes.map(t => summary(safra, t.id));
    return [
      {
        key: 'production', title: 'Produção colhida', type: 'demonstrativo', value: `${fmtN(cur.productionSc, 0)} <small>sacas</small>`,
        footer: `${deltaBadge(cmp(isAll ? M.pctChange(cur.productionSc, hist25.productivity * area) : null), { suffix: 'vs 2025', neutralText: isAll ? 'comparação desligada' : '2025 sem dado por talhão' })}<span class="kpi-note">${fmtN(cur.soldSc, 0)} vendidas · ${fmtN(cur.stockSc, 0)} em estoque</span>`,
        detail: () => ({
          formula: 'Soma das sacas beneficiadas (60 kg) registradas por talhão.',
          rows: byTalhao.map(s => [talhaoName(s.talhaoId), `${fmtN(s.productionSc, 0)} sc`, `${fmtN(s.productivity, 1)} sc/ha`]),
          rowHead: ['Talhão', 'Produção', 'Produtividade'],
          notes: ['Vendas e estoque estão em Propriedade e Safras > Comercialização.'],
          link: ['properties', null, 'Ver talhões']
        })
      },
      {
        key: 'prod', title: 'Produtividade', type: 'demonstrativo', value: `${fmtN(cur.productivity, 1)} <small>sc/ha</small>`,
        footer: `${deltaBadge(cmp(prevProd ? M.pctChange(cur.productivity, prevProd) : null), { suffix: 'vs 2025', neutralText: isAll ? 'comparação desligada' : '2025 sem dado por talhão' })}<span class="kpi-note">IBGE 2026 ainda não publicado</span>`,
        detail: () => ({
          formula: 'Produção (sacas) ÷ área colhida (ha). Mesmo cálculo usado nos gráficos, no histórico e no simulador.',
          rows: byTalhao.map(s => [talhaoName(s.talhaoId), `${fmtN(s.areaHa, 1)} ha`, `${fmtN(s.productivity, 1)} sc/ha`]),
          rowHead: ['Talhão', 'Área', 'Produtividade'],
          notes: [`Comparação oficial só é feita no mesmo ano: em 2025 o sítio teve ${fmtN(hist25.productivity, 1)} sc/ha e Patrocínio ${fmtN(ibgeYield('3148103', '2025'), 1)} sc/ha (IBGE/PAM).`, 'O IBGE publica a PAM de 2026 apenas em 2027.'],
          link: ['intelligence', 'sec-bench', 'Ver benchmark oficial']
        })
      },
      {
        key: 'costsack', title: 'Custo por saca', type: 'demonstrativo', value: fmtR(cur.costPerSack),
        footer: `${deltaBadge(cmp(prevCostSack ? M.pctChange(cur.costPerSack, prevCostSack) : null), { invert: true, suffix: 'vs 2025', neutralText: isAll ? 'comparação desligada' : '2025 sem dado por talhão' })}<span class="kpi-note">${fmtR(cur.costPerHa)}/ha</span>`,
        detail: () => ({
          formula: 'Custo total da safra ÷ sacas produzidas. Lançamentos gerais rateados pela área.',
          rows: byTalhao.map(s => [talhaoName(s.talhaoId), fmtR(s.cost), fmtR(s.costPerHa), fmtR(s.costPerSack)]),
          rowHead: ['Talhão', 'Custo', 'R$/ha', 'R$/saca'],
          notes: [`Preço Cepea de ${monthLabel(price.month)}: ${fmtR(price.value, 2)}/saca — ${fmtN(price.value / cur.costPerSack, 1)}× o custo por saca.`],
          link: ['properties', null, 'Abrir livro-caixa']
        })
      },
      {
        key: 'result', title: 'Resultado operacional', type: 'estimado', value: fmtR(cur.result),
        footer: `${deltaBadge(cmp(prevResult ? M.pctChange(cur.result, prevResult) : null), { suffix: 'vs 2025', neutralText: isAll ? 'comparação desligada' : '2025 sem dado por talhão' })}<span class="kpi-note">margem ${fmtN(cur.margin, 1)}%</span>`,
        detail: () => ({
          formula: 'Receita das vendas realizadas + estoque valorado pelo Cepea mais recente − custo total.',
          rows: [['Receita realizada', fmtR(cur.revenueRealized)], [`Estoque (${fmtN(cur.stockSc, 0)} sc × ${fmtR(price.value, 2)})`, fmtR(cur.stockValue)], ['Custo total', fmtR(-cur.cost)], ['Resultado', fmtR(cur.result)]],
          rowHead: ['Componente', 'Valor'],
          notes: ['É "estimado" porque o estoque ainda não foi vendido: o valor usa o Indicador Cepea, sem descontos de frete e qualidade.', 'Não inclui pró-labore, depreciação nem custo de oportunidade da terra.'],
          link: ['intelligence', 'sec-market', 'Comparar com o mercado']
        })
      },
      priceKpi
    ];
  }

  let kpiCache = [];
  function openKpi(i) {
    kpiCache = buildKpis();
    const k = kpiCache[i];
    if (!k) return;
    const d = k.detail();
    $('drawer-eyebrow').innerHTML = `${dtag(k.type, k.typeLabel)} <span>${esc(state.filters.safra === D.meta.currentSafra ? 'Safra 2027' : 'Safra 2026')} · ${esc(state.filters.talhao === 'ALL' ? 'propriedade' : talhaoName(state.filters.talhao))}</span>`;
    $('drawer-title').innerHTML = `${esc(k.title)}: <span class="drawer-value">${k.value}</span>`;
    $('drawer-body').innerHTML = `
      <div class="drawer-block"><div class="drawer-label">Como é calculado</div><p>${esc(d.formula)}</p></div>
      <div class="drawer-block"><div class="drawer-label">O que compõe este número</div>
        <table class="data-table compact"><thead><tr>${d.rowHead.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${d.rows.map(r => `<tr>${r.map((c, ci) => `<td class="${ci ? 'num' : ''}">${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>
      </div>
      <div class="drawer-block"><div class="drawer-label">Observações</div><ul>${d.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div>
      ${d.link ? `<button class="btn-primary btn-inline" onclick="RigoraApp.closeDrawer(); RigoraApp.navigate('${d.link[0]}'); ${d.link[1] ? `setTimeout(() => RigoraApp.scrollToSection(null,'${d.link[1]}'), 80);` : ''}">${esc(d.link[2])} →</button>` : ''}`;
    $('kpi-drawer').classList.add('open');
    $('kpi-drawer').setAttribute('aria-hidden', 'false');
    $('kpi-drawer-overlay').classList.add('show');
  }

  function closeDrawer() {
    const d = $('kpi-drawer');
    if (!d) return;
    d.classList.remove('open');
    d.setAttribute('aria-hidden', 'true');
    $('kpi-drawer-overlay').classList.remove('show');
  }

  function renderOverviewCharts() {
    const { safra, talhao, compare } = state.filters;
    const entries = M.filterEntries(state.entries, { safra });
    const months = M.costByMonth(entries, state.talhoes, talhao);
    const prevSafra = safra === '2027' ? '2026' : null;
    let prevMap = {};
    if (compare && prevSafra) {
      M.costByMonth(M.filterEntries(state.entries, { safra: prevSafra }), state.talhoes, talhao).forEach(m => {
        const [y, mm] = m.month.split('-');
        prevMap[`${+y + 1}-${mm}`] = m;
      });
    }
    $('ov-cost-subtitle').textContent = `${safra === '2027' ? 'Safra 2027 até ' + dateBR(D.meta.referenceDate) : 'Safra 2026 completa'} · ${talhao === 'ALL' ? 'propriedade' : talhaoName(talhao)} · barras = mês, linha = acumulado`;
    C.renderComboBarLine('chart-monthly-costs', months.map(m => ({
      label: monthLabel(m.month), short: monthLabel(m.month), value: m.value, cumulative: m.cumulative,
      extra: prevMap[m.month] ? `Mesmo mês da safra anterior: ${fmtR(prevMap[m.month].value)}` : ''
    })), { emptyMessage: 'Sem lançamentos neste recorte.' });

    const cats = M.costByCategory(entries, state.talhoes, talhao);
    const total = M.sum(cats, c => c.value);
    C.renderHBar('chart-cost-category', cats.map(c => ({
      label: c.category, value: c.value, color: CAT_COLORS[c.category],
      tip: `<strong>${esc(c.category)}</strong><br>${fmtR(c.value)} (${fmtN(c.value / total * 100, 1)}%)<br><em>Clique para ver os lançamentos</em>`
    })), { isCurrency: true, compact: true, labelWidth: 190, onClickAttr: (d) => `RigoraApp.drillCategory('${esc(d.label)}')`, ariaLabel: 'Custos por categoria' });

    const years = ['2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026'];
    const hist = farmHistory();
    const farm = years.map(y => { const h = hist.find(x => x.safra === y); return h ? h.productivity : null; });
    const patro = years.map(y => ibgeYield('3148103', y));
    const mg = years.map(y => mgYield(y));
    C.renderMultiLine('chart-productivity-vs-ibge', years, [
      { name: 'Minas Gerais (IBGE)', color: '#94A3B8', values: mg, dash: '4 4', width: 2, pointRadius: 3, tips: years.map((y, i) => mg[i] === null ? '' : `<strong>Minas Gerais · ${y}</strong><br>${fmtN(mg[i], 1)} sc/ha · Oficial (IBGE/PAM)`) },
      { name: 'Patrocínio (IBGE)', color: '#0369A1', values: patro, tips: years.map((y, i) => patro[i] === null ? '' : `<strong>Patrocínio · ${y}</strong><br>${fmtN(patro[i], 1)} sc/ha · Oficial (IBGE/PAM)`) },
      { name: 'Sítio', color: '#163D32', values: farm, width: 3, tips: years.map((y, i) => farm[i] === null ? '' : `<strong>Sítio · ${y}</strong><br>${fmtN(farm[i], 1)} sc/ha · Demonstrativo${patro[i] ? `<br>vs Patrocínio: ${fmtPct(M.pctChange(farm[i], patro[i]))}` : '<br>IBGE do ano ainda não publicado'}`) }
    ], { unit: 'sc/ha', minZero: true, ariaLabel: 'Produtividade do sítio comparada com Patrocínio e Minas Gerais' });
  }

  function drillCategory(category) {
    navigate('properties');
    $('ledger-filter-safra').value = state.filters.safra;
    $('ledger-filter-talhao').value = state.filters.talhao === 'ALL' ? '' : state.filters.talhao;
    $('ledger-filter-category').value = category;
    renderLedger();
    setTimeout(() => { const el = $('activities-table-body'); if (el) el.closest('.dashboard-card').scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 60);
  }

  /* ==========================================================================
     CLIMA - INMET (integração real com timeout, validação e fallback datado)
     ========================================================================== */
  /* B1: no máximo uma consulta por vez; a última resposta ao vivo fica como reserva (mesmas regras da M1). */
  let inmetPending = null;
  let inmetSeq = 0;
  let inmetLastLive = null;
  let inmetTimer = null;
  let inmetVisibilityBound = false;

  /** Cópias de reserva, da mais nova à mais antiga escolhida pela data de coleta: última consulta ao vivo e js/snapshots.js. */
  function inmetCandidates() {
    return [{ origin: 'consulta anterior', copy: inmetLastLive }, { origin: 'cópia salva', copy: SNAP.inmet, always: true }];
  }

  function fetchInmetForecast() {
    if (inmetPending) return inmetPending;                     // consulta em andamento: não abre outra
    const seq = ++inmetSeq;
    inmetPending = queryInmet(seq).finally(() => { inmetPending = null; });
    return inmetPending;
  }

  async function queryInmet(seq) {
    const url = 'https://apiprevmet3.inmet.gov.br/previsao/3148103';
    if (!state.inmet.days) {                                   // sem previsão válida na tela: mostra o carregamento
      state.inmet.status = 'loading';
      renderWeather();
    }
    let next, msg, level = 'info';
    const tentativa = new Date().toISOString();                // B3 (N2): hora da tentativa de consulta
    try {
      const data = await fetchWithTimeout(url, 9000);
      // Mesma regra de data da M1 (metrics.inmetSnapshotForecast): só dias de hoje em diante, no calendário local
      const today = M.localDateISO(new Date());
      const days = parseInmet(data).filter(d => { const iso = M.brDateToISO(d.date); return iso !== null && iso >= today; });
      if (!days.length) throw new Error('a resposta ao vivo só trouxe dias que já passaram');   // vai para a reserva ou indisponível
      // Bloco 2 (T2): dia sem faixa de umidade completa é omitido (nunca "undefined" na tela)
      const usable = M.inmetUsableDays(days);
      if (!usable.days.length) throw new Error('nenhum dia da resposta ao vivo tem umidade completa');
      const collectedAt = new Date().toISOString();
      // A cópia guardada continua sendo a resposta recebida: pela M1, resposta com dia incompleto não serve de reserva
      next = { status: 'live', collectedAt, days: usable.days, omitted: usable.omitted, message: '', raw: { collectedAt, data }, checkedAt: tentativa };
      msg = `Previsão recebida ao vivo (${usable.days.length} dias).${usable.omitted.length ? ` ${usable.omitted.length} dia(s) sem umidade completa omitido(s): ${usable.omitted.map(x => String(x).slice(0, 5)).join(', ')}.` : ''}`;
    } catch (err) {
      // Reserva válida mais recente (até 24 h, só dias de hoje em diante), avaliada no momento da resposta
      const fb = M.pickInmetCopy(inmetCandidates(), new Date());
      level = 'error';
      if (fb.ok) {
        next = { status: 'snapshot', collectedAt: fb.collectedAt, days: fb.days, message: err.message, fallback: fb, raw: fb.copy, checkedAt: tentativa };
        msg = `Consulta ao vivo falhou (${err.message}). Exibindo previsão oficial salva em ${dateTimeBR(fb.collectedAt)}${fb.origin === 'consulta anterior' ? ' (última consulta ao vivo)' : ''}${fb.removedPast ? ` (${fb.removedPast} dia(s) já passado(s) omitido(s))` : ''}.`;
      } else {
        next = { status: 'error', collectedAt: null, days: null, message: err.message, fallback: fb, raw: null, checkedAt: tentativa };
        msg = `Consulta falhou (${err.message}). ${fb.reason === 'ausente' ? '' : `Cópia salva não usada: ${fb.message}. `}Nenhum dado exibido.`;
      }
    }
    if (seq !== inmetSeq) return;                              // resposta de uma consulta mais antiga: descartada
    if (next.status === 'live') inmetLastLive = next.raw;
    state.inmet = next;
    logIntegration('INMET', msg, level);
    renderInmetDependents();
  }

  function renderInmetDependents() {
    renderWeather();
    renderAlerts();
    renderSources();
    renderAutomations();
  }

  /** Reavalia a previsão exibida (idade, dias passados) e consulta de novo quando preciso. Nunca lança erro. */
  function checkInmetForecast(trigger) {
    let d;
    try {
      d = M.inmetRecheck(state.inmet, new Date(), { candidates: inmetCandidates() });
      if (d.state) {
        state.inmet = d.state;
        state.inmet.checkedAt = new Date().toISOString();       // B3 (N2): data da avaliação
        logIntegration('INMET', `Reavaliação (${trigger}): ${d.reason}.`, d.state.status === 'error' ? 'error' : 'info');
        renderInmetDependents();
      }
    } catch (e) {
      d = { fetch: true };
    }
    const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
    return d.fetch && !hidden ? fetchInmetForecast() : Promise.resolve();
  }

  /** Um único intervalo de 30 min e um único listener de visibilidade, mesmo se chamado mais de uma vez. */
  function startInmetAutoRefresh() {
    if (!inmetTimer) inmetTimer = setInterval(() => checkInmetForecast('verificação periódica'), M.INMET_RECHECK_MINUTES * 60000);
    if (!inmetVisibilityBound) {
      document.addEventListener('visibilitychange', () => { if (document.visibilityState !== 'hidden') checkInmetForecast('retorno à aba'); });
      inmetVisibilityBound = true;
    }
  }

  function parseInmet(data) {
    return M.parseInmetForecast(data, '3148103');
  }

  /** Frase sobre a cópia salva que não foi usada (vencida, inválida ou só com dias passados). */
  /** B3 (N2): dia local (aaaa-mm-dd) de uma data ISO, ou de agora. Nunca o dia em UTC. */
  function localDay(iso) {
    const t = iso ? Date.parse(iso) : NaN;
    return M.localDateISO(Number.isFinite(t) ? new Date(t) : new Date());
  }

  function inmetCopyNote(fb) {
    if (!fb || fb.ok || fb.reason === 'ausente') return '';
    return fb.collectedAt ? `A última cópia salva, de ${dateTimeBR(fb.collectedAt)}, não foi usada: ${fb.message}.` : `A cópia salva não foi usada: ${fb.message}.`;
  }

  /** Bloco 2 (T2): faixa de umidade do dia, ou aviso. Nunca "undefined", NaN nem faixa pela metade. */
  function humidityText(d, prefix) {
    return M.inmetHumidityOk(d) ? `${prefix}${d.umidade_min}–${d.umidade_max}%` : `${prefix}indisponível`;
  }
  const textOf = (v) => (v === null || v === undefined ? '' : String(v));

  /** Dias omitidos da resposta ao vivo que ainda são de hoje em diante. */
  function inmetOmittedNote(s) {
    if (s.status !== 'live' || !Array.isArray(s.omitted) || !s.omitted.length) return '';
    const today = M.localDateISO(new Date());
    const dates = s.omitted.filter(x => { const iso = M.brDateToISO(x); return iso !== null && iso >= today; });
    if (!dates.length) return '';
    return `${dates.length} dia(s) da resposta do INMET sem umidade completa (${dates.map(x => x.slice(0, 5)).join(', ')}) ficaram de fora da previsão e da estimativa de ferrugem. A Rigora não exibe valores incompletos.`;
  }

  function weatherBadge() {
    const s = state.inmet;
    if (s.status === 'live') { const t = M.collectionStamp(s.collectedAt, new Date()); return ['success', `Ao vivo · ${t.sameDay ? t.time : `${t.day} · ${t.time}`}`]; }
    if (s.status === 'snapshot') { const t = M.collectionStamp(s.collectedAt, new Date()); return ['warning', `Cópia de ${t.day} · ${t.time}`]; }
    if (s.status === 'loading') return ['info', 'Consultando INMET…'];
    return ['danger', 'Indisponível'];
  }

  function renderWeather() {
    const body = $('weather-body');
    if (!body) return;
    const [cls, txt] = weatherBadge();
    $('weather-status-badge').className = `status-pill ${cls}`;
    $('weather-status-badge').innerHTML = `<span class="status-dot"></span> ${esc(txt)}`;
    const s = state.inmet;
    if (s.status === 'loading') { body.innerHTML = '<div class="skeleton" style="height:150px"></div>'; return; }
    if (!s.days) {
      const note = inmetCopyNote(s.fallback);
      body.innerHTML = `<div class="empty-state"><strong>Previsão indisponível agora.</strong><br>O INMET não respondeu (${esc(s.message)}).${note ? ' ' + esc(note) : ''} A Rigora não exibe valores substitutos.<br><button class="btn-secondary btn-inline sm" onclick="RigoraApp.fetchInmetForecast()">Tentar novamente</button></div>`;
      return;
    }
    const today = s.days[0];
    const fav = s.days.filter(d => d.risk.favorable);
    const rule = state.rules.find(r => r.id === 'RULE-03');
    $('weather-subtitle').textContent = `Patrocínio (MG) · previsão INMET para ${s.days.length} dias · consulta ${dateTimeBR(s.collectedAt)}`;
    body.innerHTML = `
      <div class="weather-today">
        <div><div class="weather-temp">${today.temp_max}°<span class="weather-min"> / ${today.temp_min}°</span></div>
        <div class="weather-desc">${esc(today.resumo)}</div></div>
        <div class="weather-meta"><div>${esc(humidityText(today, 'Umidade '))}</div><div>Vento ${esc(today.vento)}</div><div>${esc(today.weekday)}, ${esc(today.date.slice(0, 5))}</div></div>
      </div>
      ${inmetOmittedNote(s) ? `<div class="stale-note">${esc(inmetOmittedNote(s))}</div>` : ''}
      ${s.status === 'snapshot' ? `<div class="stale-note">Consulta ao vivo indisponível (${esc(s.message)}). Mostrando a última previsão oficial salva, de ${dateTimeBR(s.collectedAt)}.</div>` : ''}
      <div class="forecast-strip">${s.days.map(d => `
        <div class="fc-day" data-tip="${esc(`<strong>${esc(textOf(d.weekday))} ${d.date.slice(0, 5)}</strong><br>${esc(textOf(d.resumo))}<br>${d.temp_min}° a ${d.temp_max}° · ${humidityText(d, 'UR ')}<br><em>Estimativa Rigora:</em> ${d.risk.urMean === null ? 'sem estimativa de ferrugem (umidade incompleta)' : `T média ${fmtN(d.risk.tMean, 1)}°, UR média ${fmtN(d.risk.urMean, 0)}% → ${d.risk.favorable ? 'favorável à ferrugem' : 'não favorável'}`}`)}" tabindex="0">
          <div class="fc-date">${esc(textOf(d.weekday).slice(0, 3))} ${esc(d.date.slice(0, 2))}</div>
          <div class="fc-temp">${d.temp_max}°<span>${d.temp_min}°</span></div>
          <div class="fc-ur">${esc(humidityText(d, M.inmetHumidityOk(d) ? '' : 'UR '))}</div>
          <span class="risk-dot ${d.risk.favorable ? 'on' : ''}" aria-label="${d.risk.favorable ? 'condição favorável' : 'condição não favorável'}"></span>
        </div>`).join('')}</div>
      <div class="weather-split">
        <div><div class="split-label">${dtag('oficial', 'Previsão · INMET')}</div><p>${esc(today.resumo)}. Temperaturas entre ${today.temp_min} e ${today.temp_max} °C. A API não informa volume nem probabilidade de chuva.</p></div>
        <div><div class="split-label">${dtag('estimado', 'Leitura agronômica · Rigora')}</div><p>${fav.length
          ? `${fav.length} de ${s.days.length} dias com UR média ≥ ${rule.thresholdValue}% e T média entre 22 e 26 °C (condição favorável à ferrugem). ${fav.length >= 3 ? 'Programe inspeção nos talhões de baixada (T03).' : 'Ainda não há sequência longa: mantenha o monitoramento.'}`
          : `Nenhum dos ${s.days.length} dias previstos atende ao critério de risco de ferrugem (UR média ≥ ${rule.thresholdValue}% e T média 22–26 °C). Sem recomendação de ação.`}</p></div>
      </div>`;
    C.attachTooltips(body);
  }

  /* ==========================================================================
     IBGE / SIDRA (integração real: conferência rápida + atualização completa)
     ========================================================================== */
  async function refreshIbge(full) {
    if (state.ibge.loading) return;
    state.ibge.loading = true;
    const tentativa = new Date().toISOString();                // B3 (N2): hora da tentativa de consulta
    renderBenchmarkStatus();
    const btn = $('btn-refresh-ibge');
    if (btn) { btn.disabled = true; btn.textContent = full ? 'Consultando IBGE… (até 60 s)' : 'Atualizar do IBGE'; }
    try {
      if (full) {
        const url = 'https://apisidra.ibge.gov.br/values/t/1613/n6/in%20n3%2031/v/112,216,214/p/2016-2025/c82/31619/f/c';
        const rows = await fetchWithTimeout(url, 60000);
        if (!Array.isArray(rows) || rows.length < 2) throw new Error('resposta vazia do SIDRA');
        const map = {};
        rows.slice(1).forEach(x => {
          const v = parseFloat(x.V); if (isNaN(v)) return;
          map[x.D1C] = map[x.D1C] || {}; map[x.D1C][x.D3C] = map[x.D1C][x.D3C] || [null, null, null];
          const idx = { '112': 0, '216': 1, '214': 2 }[x.D2C]; if (idx !== undefined) map[x.D1C][x.D3C][idx] = v;
        });
        let updated = 0;
        state.ibge.municipios.forEach(m => { if (map[m.c]) { m.y = map[m.c]; updated++; } });
        const totals = {};
        Object.values(map).forEach(ys => Object.entries(ys).forEach(([y, v]) => {
          if (v[1] && v[2]) { totals[y] = totals[y] || [0, 0]; totals[y][0] += v[1]; totals[y][1] += v[2]; }
        }));
        Object.entries(totals).forEach(([y, [a, p]]) => { state.ibge.mg[y] = [Math.round(p * 1000 / a), a, p]; });
        state.ibge.status = 'live';
        state.ibge.liveCheckedAt = new Date().toISOString();
        logIntegration('IBGE', `Atualização completa ao vivo: ${Object.keys(map).length} municípios de MG, ${updated} do painel atualizados.`);
        showToast('Dados do IBGE atualizados ao vivo.', 'success');
      } else {
        const url = `https://apisidra.ibge.gov.br/values/t/1613/n6/3148103/v/112/p/${IBGE_CHECK_YEARS[0]}-${IBGE_CHECK_YEARS[IBGE_CHECK_YEARS.length - 1]}/c82/31619/f/c`;
        const rows = await fetchWithTimeout(url, 12000);
        if (!Array.isArray(rows) || rows.length < 2) throw new Error('resposta vazia do SIDRA');
        // B2: só compara. Nenhum valor da cópia é trocado (troca parcial quebrava produção = área × rendimento).
        const pat = state.ibge.municipios.find(m => m.c === '3148103');
        if (!pat) throw new Error('Patrocínio não está na cópia local');
        const conf = compareIbgeCheck(rows, pat, IBGE_CHECK_YEARS);
        if (!conf.anos.length) throw new Error('resposta do SIDRA sem valores de rendimento para Patrocínio');
        state.ibge.conferencia = { checkedAt: new Date().toISOString(), esperados: IBGE_CHECK_YEARS.slice(), anos: conf.anos, faltantes: conf.faltantes, divergencias: conf.divergencias };
        state.ibge.status = conf.divergencias.length ? 'divergent' : (conf.faltantes.length ? 'partial' : 'checked');
        state.ibge.liveCheckedAt = state.ibge.conferencia.checkedAt;
        const cobertura = `${conf.anos.length} de ${IBGE_CHECK_YEARS.length} anos esperados comparados`;
        logIntegration('IBGE', conf.divergencias.length
          ? `Conferência ao vivo de Patrocínio: ${conf.divergencias.length} de ${conf.anos.length} ano(s) divergem da cópia local (${cobertura}). ${ibgeDivergenceNote(state.ibge.conferencia)}`
          : conf.faltantes.length
            ? `Conferência ao vivo de Patrocínio parcial: ${cobertura}, todos iguais à cópia local. Sem valor na resposta: ${conf.faltantes.join(', ')}.`
            : `Conferência ao vivo de Patrocínio concluída: ${cobertura}, todos iguais à cópia local.`,
          conf.divergencias.length ? 'error' : 'info');
      }
    } catch (err) {
      state.ibge.message = err.message;
      state.ibge.attemptedAt = tentativa;
      if (!['live', 'checked', 'partial', 'divergent'].includes(state.ibge.status)) state.ibge.status = 'snapshot';
      logIntegration('IBGE', `Consulta ao SIDRA falhou (${err.message}). Mantida a cópia oficial coletada em ${dateTimeBR(state.ibge.collectedAt)}.`, 'error');
      if (full) showToast('Não foi possível consultar o IBGE agora. Os dados exibidos continuam sendo a cópia oficial datada.', 'warning');
    } finally {
      state.ibge.loading = false;
      if (btn) { btn.disabled = false; btn.textContent = 'Atualizar do IBGE'; }
      renderBenchmarkStatus();
      renderBenchmark();
      renderOverview();
      renderHistoryTable();
      renderAlerts();
      renderSources();
    }
  }

  /** Anos que a conferência rápida pede ao SIDRA (e que precisam voltar para ela ser completa). */
  const IBGE_CHECK_YEARS = ['2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025'];

  /** B2: rendimento ao vivo de Patrocínio x cópia, ano a ano, só nos anos esperados. Não altera nada. */
  function compareIbgeCheck(rows, pat, esperados) {
    const anos = [], divergencias = [];
    rows.slice(1).forEach(x => {
      if (!x || typeof x.V !== 'string' || x.V.trim() === '' || !esperados.includes(x.D3C)) return;
      const v = Number(x.V);
      if (!Number.isFinite(v) || anos.includes(x.D3C)) return;
      const local = pat.y && pat.y[x.D3C] ? pat.y[x.D3C][0] : null;
      anos.push(x.D3C);
      if (local !== v) divergencias.push({ ano: x.D3C, copia: local, aoVivo: v });
    });
    anos.sort();
    return { anos, faltantes: esperados.filter(a => !anos.includes(a)), divergencias };
  }

  function ibgeCoverageNote(c) {
    if (!c || !c.esperados) return '';
    const base = `Conferidos ${c.anos.length} de ${c.esperados.length} anos esperados (${c.esperados[0]}–${c.esperados[c.esperados.length - 1]}).`;
    return c.faltantes && c.faltantes.length ? `${base} Sem valor na resposta do IBGE: ${c.faltantes.join(', ')}.` : base;
  }

  function ibgeDivergenceNote(c) {
    if (!c || !c.divergencias || !c.divergencias.length) return '';
    const kg = (v) => (typeof v === 'number' ? `${v.toLocaleString('pt-BR')} kg/ha` : 'sem valor');
    const anos = c.divergencias.map(d => `${d.ano} (cópia ${kg(d.copia)}; IBGE ao vivo ${kg(d.aoVivo)})`).join(', ');
    return `Rendimento diferente em ${anos}. ${ibgeCoverageNote(c)} Nenhum valor foi trocado: o painel continua mostrando a cópia oficial coletada em ${dateTimeBR(state.ibge.collectedAt)}.`;
  }

  function renderBenchmarkStatus() {
    const b = $('regional-status-badge');
    if (!b) return;
    const s = state.ibge;
    let cls = 'warning', txt = `Cópia oficial de ${dateTimeBR(s.collectedAt)}`;
    if (s.loading) { cls = 'info'; txt = 'Consultando IBGE…'; }
    else if (s.status === 'live') { cls = 'success'; txt = `Atualizado ao vivo · ${dateTimeBR(s.liveCheckedAt)}`; }
    else if (s.status === 'checked') { cls = 'success'; txt = `Dados de Patrocínio conferidos ao vivo · ${dateTimeBR(s.liveCheckedAt)}`; }
    else if (s.status === 'partial') { cls = 'info'; txt = `Dados de Patrocínio parcialmente conferidos · ${dateTimeBR(s.liveCheckedAt)}`; }
    else if (s.status === 'divergent') { cls = 'warning'; txt = `Dados de Patrocínio divergem do IBGE · ${dateTimeBR(s.liveCheckedAt)}`; }
    b.className = `status-pill ${cls}`;
    b.innerHTML = `<span class="status-dot"></span> ${esc(txt)}`;
    const falha = s.message ? `Última falha: ${s.message}` : '';
    const nota = s.loading ? '' : s.status === 'divergent' ? ibgeDivergenceNote(s.conferencia)
      : (s.status === 'checked' || s.status === 'partial') ? ibgeCoverageNote(s.conferencia) : '';
    b.title = [nota, falha].filter(Boolean).join(' ');
  }

  /* ==========================================================================
     PROPRIEDADE E SAFRAS
     ========================================================================== */
  function renderProperties() {
    const ctx = $('prop-context-sub');                            // Bloco 2 (T9): área total = soma dos talhões
    if (ctx) ctx.textContent = `Patrocínio (MG) · Cerrado Mineiro · ${fmtN(M.sum(state.talhoes, t => t.areaHa), 1)} ha em ${state.talhoes.length} talhões de café arábica · sequeiro`;
    renderTalhaoCards();
    renderLedger();
    renderSales();
    renderInventory();
    renderPropertyCharts();
  }

  function renderTalhaoCards() {
    const el = $('talhao-cards');
    if (!el) return;
    const sel = state.filters.talhao;
    el.innerHTML = state.talhoes.map(t => {
      const s26 = summary('2026', t.id);
      const s27 = summary('2027', t.id);
      return `
        <button class="talhao-card ${sel === t.id ? 'selected' : ''}" onclick="RigoraApp.selectTalhao('${t.id}')" aria-pressed="${sel === t.id}">
          <div class="talhao-card-head"><strong>${esc(t.name)}</strong><span class="talhao-area">${fmtN(t.areaHa, 1)} ha</span></div>
          <div class="talhao-variety">${esc(t.variety)} · plantio ${t.plantingYear}</div>
          <div class="talhao-metrics">
            <div><span>Produtividade 2026</span><strong>${fmtN(s26.productivity, 1)} sc/ha</strong></div>
            <div><span>Custo/ha 2026</span><strong>${fmtR(s26.costPerHa)}</strong></div>
            <div><span>Custo/saca 2026</span><strong>${fmtR(s26.costPerSack)}</strong></div>
            <div><span>Gasto 2027 até agora</span><strong>${fmtR(s27.cost)}</strong></div>
          </div>
        </button>`;
    }).join('') + `
      <button class="talhao-card all ${sel === 'ALL' ? 'selected' : ''}" onclick="RigoraApp.selectTalhao('ALL')" aria-pressed="${sel === 'ALL'}">
        <div class="talhao-card-head"><strong>Propriedade inteira</strong><span class="talhao-area">${fmtN(M.sum(state.talhoes, t => t.areaHa), 1)} ha</span></div>
        <div class="talhao-variety">Clique num talhão para filtrar gráficos, livro-caixa e visão geral</div>
      </button>`;
  }

  function selectTalhao(id) {
    state.filters.talhao = id;
    $('ledger-filter-talhao').value = id === 'ALL' ? '' : id;
    renderTalhaoCards();
    renderLedger();
    renderPropertyCharts();
    renderOverview();
  }

  function renderPropertyCharts() {
    const safra = state.filters.safra;
    const sums = state.talhoes.map(t => summary(safra, t.id));
    const all = summary(safra);
    $('talhao-cost-subtitle').textContent = `Safra ${safra}${safra === '2027' ? ' até ' + dateBR(D.meta.referenceDate) : ''} · inclui rateio dos lançamentos gerais pela área`;
    C.renderHBar('chart-talhao-cost', sums.map(s => ({
      label: talhaoName(s.talhaoId), value: s.costPerHa, highlight: state.filters.talhao === s.talhaoId,
      tip: `<strong>${esc(talhaoName(s.talhaoId))}</strong><br>${fmtR(s.costPerHa)}/ha · total ${fmtR(s.cost)}<br>${s.costPerHa > all.costPerHa ? 'Acima' : 'Abaixo'} da média do sítio (${fmtR(all.costPerHa)}/ha)`
    })), { isCurrency: true, compact: false, labelWidth: 150, refValue: all.costPerHa, refLabel: 'média do sítio', onClickAttr: (d) => `RigoraApp.selectTalhao('${state.talhoes.find(t => t.short === d.label).id}')` });
    const s26 = state.talhoes.map(t => summary('2026', t.id));
    const avg26 = summary('2026');
    C.renderHBar('chart-talhao-productivity', s26.map(s => ({
      label: talhaoName(s.talhaoId), value: s.productivity, highlight: state.filters.talhao === s.talhaoId,
      color: s.productivity < avg26.productivity * 0.9 ? '#D97706' : undefined,
      tip: `<strong>${esc(talhaoName(s.talhaoId))}</strong><br>${fmtN(s.productivity, 1)} sc/ha (${fmtN(s.productionSc, 0)} sacas)<br>${fmtPct(M.pctChange(s.productivity, avg26.productivity))} vs média do sítio`
    })), { unit: 'sc/ha', labelWidth: 150, refValue: avg26.productivity, refLabel: `média ${fmtN(avg26.productivity, 1)}`, onClickAttr: (d) => `RigoraApp.selectTalhao('${state.talhoes.find(t => t.short === d.label).id}')` });
  }

  function renderLedger() {
    const tbody = $('activities-table-body');
    if (!tbody) return;
    const safra = $('ledger-filter-safra').value;
    const talhaoId = $('ledger-filter-talhao').value;
    const category = $('ledger-filter-category').value;
    const rows = state.entries
      .filter(e => (!safra || e.safra === safra) && (!category || e.category === category) && (!talhaoId || e.talhaoId === talhaoId || e.talhaoId === 'ALL'))
      .sort((a, b) => b.date.localeCompare(a.date));
    const attributed = talhaoId ? M.costOf(rows, state.talhoes, talhaoId) : M.sum(rows, r => r.value);
    $('ledger-summary').innerHTML = `${rows.length} lançamento(s) · ${talhaoId ? `custo atribuído ao ${esc(talhaoName(talhaoId))}: <strong>${fmtR(attributed)}</strong> (com rateio)` : `total <strong>${fmtR(attributed)}</strong>`}`
      + `<br><span class="muted small">${ledgerWarn() ? `<strong>Atenção:</strong> ${esc(ledgerNote())}` : 'Lançamentos novos ficam salvos só neste navegador.'}</span>`;
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">Nenhum lançamento com esses filtros.</td></tr>'; return; }
    tbody.innerHTML = rows.map(e => {
      const part = talhaoId && e.talhaoId === 'ALL' ? M.allocateEntry(e, state.talhoes).find(p => p.talhaoId === talhaoId).value : null;
      return `<tr class="${e.isNew ? 'row-new' : ''}">
        <td>${dateBR(e.date)}</td>
        <td>${e.talhaoId === 'ALL' ? '<span class="muted">Rateio geral</span>' : `<strong>${esc(talhaoName(e.talhaoId))}</strong>`}</td>
        <td><span class="cat-chip" style="--c:${CAT_COLORS[e.category] || '#64748B'}">${esc(e.category)}</span></td>
        <td>${esc(e.description)}${e.isNew ? ` <span class="dtag dtag-demo">${e.stored ? 'salvo neste navegador' : 'só nesta sessão'}</span>` : ''}</td>
        <td class="num"><strong>${fmtR(e.value)}</strong>${part !== null ? `<div class="muted small">parte do talhão: ${fmtR(part)}</div>` : ''}</td>
      </tr>`;
    }).join('');
  }

  function clearLedgerFilters() {
    $('ledger-filter-safra').value = '';
    $('ledger-filter-talhao').value = '';
    $('ledger-filter-category').value = '';
    renderLedger();
  }

  function renderSales() {
    const tbody = $('sales-table-body');
    if (!tbody) return;
    const s = summary('2026');
    $('sales-summary').textContent = `${fmtN(s.soldSc, 0)} de ${fmtN(s.productionSc, 0)} sacas vendidas · preço médio ${fmtR(s.avgSalePrice)} · ${fmtN(s.stockSc, 0)} sacas em estoque`;
    tbody.innerHTML = state.sales.map(v => {
      const c = D.cepea.monthly.find(m => m.month === v.date.slice(0, 7));
      return `<tr><td>${dateBR(v.date)}</td><td>${esc(v.buyer)}</td><td class="num">${v.sacas}</td><td class="num"><strong>${fmtR(v.price)}</strong></td>
        <td class="num">${c && c.value ? `${fmtR(c.value, 2)} <span class="muted small">(${fmtPct(M.pctChange(v.price, c.value))})</span>` : '—'}</td></tr>`;
    }).join('');
  }

  function renderInventory() {
    const tbody = $('inventory-table-body');
    if (!tbody) return;
    tbody.innerHTML = state.inventory.map(i => {
      const low = i.currentQuantity < i.minQuantity;
      return `<tr><td><strong>${esc(i.name)}</strong></td><td class="num">${i.currentQuantity} ${esc(i.unit)}</td><td class="num">${i.minQuantity}</td>
        <td><span class="status-pill ${low ? 'warning' : 'success'}"><span class="status-dot"></span> ${low ? 'Abaixo do mínimo' : 'Adequado'}</span></td></tr>`;
    }).join('');
  }

  /* Modal de lançamento --------------------------------------------------- */
  function openNewActivityModal() {
    $('modal-activity').classList.add('show');
    $('act-date').value = D.meta.referenceDate;
    $('act-error').textContent = '';
    const hint = $('act-storage-hint');                           // Bloco 2 (T3)
    if (hint) {
      const s = ledgerStore.status;
      hint.textContent = s === 'indisponivel'
        ? 'Atenção: o armazenamento deste navegador não está disponível. O lançamento valerá só até fechar ou recarregar a página.'
        : (s === 'corrompido' && !ledgerStore.backupKey
          ? 'Atenção: os lançamentos salvos antes não puderam ser lidos. Uma cópia deles será guardada à parte antes de gravar.'
          : 'O lançamento fica salvo só neste navegador, neste computador. Não há conta nem sincronização com outros aparelhos.');
    }
    if (state.filters.talhao !== 'ALL') $('act-talhao').value = state.filters.talhao;
    setTimeout(() => $('act-desc').focus(), 50);
  }

  function closeNewActivityModal() {
    const m = $('modal-activity');
    if (!m || !m.classList.contains('show')) return;
    m.classList.remove('show');
    $('form-new-activity').reset();
  }

  function submitNewActivity() {
    const talhaoId = $('act-talhao').value;
    const category = $('act-category').value;
    const date = $('act-date').value;
    const desc = $('act-desc').value.trim();
    const value = parseFloat($('act-cost').value);
    const err = [];
    if (!date || date < '2026-09-01' || date > '2027-08-31') err.push('A data deve estar no ciclo da safra 2027 (01/09/2026 a 31/08/2027).');
    if (!desc) err.push('Informe uma descrição.');
    if (isNaN(value) || value <= 0) err.push('Informe um valor maior que zero.');
    const entry = { id: newEntryId(), date, safra: '2027', talhaoId, category, description: desc, value, isNew: true, stored: false };
    if (!err.length && !ledgerEntryOk(entry)) err.push(`Confira talhão, categoria e descrição (até ${LEDGER_DESC_MAX} caracteres).`);
    if (err.length) { $('act-error').textContent = err.join(' '); return; }
    state.entries.push(entry);
    const saved = saveStoredEntries();                            // Bloco 2 (T3): grava neste navegador
    closeNewActivityModal();
    $('ledger-filter-safra').value = '2027';
    renderAll();
    if (state.currentTab === 'properties') renderPropertyCharts();
    if (saved.ok) {
      showToast(`Lançamento salvo neste navegador: ${fmtR(value)} em ${category} (${talhaoName(talhaoId)}). Indicadores, gráficos e alertas recalculados.`, 'success');
    } else {
      logIntegration('Livro-caixa', `Lançamento não gravado no navegador: ${saved.message}.`, 'error');
      showToast(`Lançamento registrado só nesta sessão: ${saved.message}. Ele some ao fechar ou recarregar a página.`, 'warning');
    }
  }

  /* ==========================================================================
     INTELIGÊNCIA: SIMULADOR
     ========================================================================== */
  function buildScenarios() {
    const hist = farmHistory();
    const prods = hist.map(h => h.productivity);
    const avg = M.sum(prods) / prods.length;
    const area = M.sum(state.talhoes, t => t.areaHa);
    const official = D.cepea.monthly.filter(m => m.value && !m.derived);
    const minP = official.reduce((a, b) => a.value < b.value ? a : b);
    const maxP = official.reduce((a, b) => a.value > b.value ? a : b);
    const latest = marketPrice();
    const costHa = D.budget.totalBrl / area;
    state.scenarios = {
      conservative: { label: 'Conservador', areaHa: area, productivity: M.round(Math.min(...prods), 1), price: minP.value, costPerHa: M.round(costHa * 1.10, 0), lossPct: 8,
        why: { productivity: 'menor produtividade do sítio (2022)', price: `menor média mensal Cepea 2026 (${monthLabel(minP.month)})`, costPerHa: 'orçamento +10%', lossPct: 'perdas na colheita e secagem acima do normal' } },
      base: { label: 'Base', areaHa: area, productivity: M.round(avg, 1), price: latest.value, costPerHa: M.round(costHa, 0), lossPct: 5,
        why: { productivity: 'média do sítio 2022–2026', price: `Cepea ${monthLabel(latest.month)} (último mês publicado)`, costPerHa: 'orçamento do produtor', lossPct: 'premissa demonstrativa' } },
      optimistic: { label: 'Otimista', areaHa: area, productivity: M.round(avg * 1.15, 1), price: maxP.value, costPerHa: M.round(costHa * 0.95, 0), lossPct: 3,
        why: { productivity: 'média do sítio +15%', price: `maior média mensal Cepea 2026 (${monthLabel(maxP.month)})`, costPerHa: 'orçamento −5%', lossPct: 'colheita bem executada' } }
    };
    state.scenarioDefaults = clone(state.scenarios);
  }

  const SIM_FIELDS = [
    { key: 'areaHa', label: 'Área cultivada', unit: 'ha', min: 1, max: 50, step: 0.5, dec: 1, src: () => [dtag('demonstrativo'), '12,0 ha no sítio'] },
    { key: 'productivity', label: 'Produtividade', unit: 'sc/ha', min: 5, max: 50, step: 0.1, dec: 1, src: () => [dtag('oficial', 'IBGE'), `Patrocínio ${ibgeLatestYear()}: ${fmtN(ibgeYield('3148103', ibgeLatestYear()), 1)} · MG: ${fmtN(mgYield(ibgeLatestYear()), 1)}`] },
    { key: 'price', label: 'Preço da saca', unit: 'R$', min: 500, max: 3000, step: 10, dec: 2, money: true, src: () => [dtag('oficial', 'Cepea'), `${monthLabel(marketPrice().month)}: ${fmtR(marketPrice().value, 2)}`] },
    { key: 'costPerHa', label: 'Custo operacional por ha', unit: 'R$/ha', min: 3000, max: 30000, step: 100, dec: 0, money: true, src: () => { const s = summary('2026'); return [dtag('demonstrativo'), `safra 2026: ${fmtR(s.costPerHa)}/ha`]; } },
    { key: 'lossPct', label: 'Perda na produção', unit: '%', min: 0, max: 30, step: 0.5, dec: 1, src: () => [dtag('estimado', 'Premissa'), 'colheita, secagem e classificação'] }
  ];

  function renderSimControls() {
    const el = $('sim-controls');
    el.innerHTML = SIM_FIELDS.map(f => `
      <div class="sim-field">
        <div class="sim-field-header">
          <label class="sim-field-label" for="sim-num-${f.key}">${esc(f.label)}</label>
          <div class="num-input"><input type="number" id="sim-num-${f.key}" min="${f.min}" max="${f.max}" step="${f.step}" oninput="RigoraApp.onSimInput('${f.key}', this.value, 'num')" aria-label="${esc(f.label)} (${esc(f.unit)})"><span>${esc(f.unit)}</span></div>
        </div>
        <input type="range" class="sim-slider" id="sim-range-${f.key}" min="${f.min}" max="${f.max}" step="${f.step}" oninput="RigoraApp.onSimInput('${f.key}', this.value, 'range')" aria-label="${esc(f.label)}">
        <div class="sim-ref" id="sim-ref-${f.key}"></div>
      </div>`).join('') + `
      <div class="sim-actions">
        <button class="btn-secondary btn-inline sm" onclick="RigoraApp.applyScenario('base')">Restaurar base</button>
        <button class="btn-secondary btn-inline sm" onclick="RigoraApp.useFarm2026()">Usar dados reais da safra 2026</button>
      </div>`;
    syncSimInputs();
  }

  function syncSimInputs(except) {
    SIM_FIELDS.forEach(f => {
      const v = state.sim[f.key];
      const num = $(`sim-num-${f.key}`), rng = $(`sim-range-${f.key}`);
      if (num && except !== `num-${f.key}`) num.value = f.dec === 2 ? v.toFixed(2) : M.round(v, f.dec);
      if (rng && except !== `range-${f.key}`) rng.value = Math.min(Math.max(v, f.min), f.max);
      const ref = $(`sim-ref-${f.key}`);
      if (ref) { const [tag, txt] = f.src(); ref.innerHTML = `${tag}<span>${esc(txt)}</span>`; }
    });
  }

  function onSimInput(key, raw, origin) {
    const v = parseFloat(String(raw).replace(',', '.'));
    if (isNaN(v)) { $('sim-errors').textContent = 'Digite um número válido.'; return; }
    state.sim[key] = v;
    state.activeScenario = null;
    document.querySelectorAll('.seg-scenarios .seg-btn').forEach(b => b.classList.remove('active'));
    syncSimInputs(`${origin}-${key}`);
    updateSimulator();
  }

  function applyScenario(name) {
    state.sim = clone(state.scenarios[name]);
    state.activeScenario = name;
    document.querySelectorAll('.seg-scenarios .seg-btn').forEach(b => b.classList.toggle('active', b.dataset.scn === name));
    syncSimInputs();
    updateSimulator();
    flash('sim-hero');
  }

  function useFarm2026() {
    const s = summary('2026');
    state.sim = { areaHa: s.areaHa, productivity: M.round(s.productivity, 1), price: M.round(s.avgSalePrice, 2), costPerHa: M.round(s.costPerHa, 0), lossPct: 0 };
    state.activeScenario = null;
    document.querySelectorAll('.seg-scenarios .seg-btn').forEach(b => b.classList.remove('active'));
    syncSimInputs();
    updateSimulator();
    showToast('Simulador preenchido com a safra 2026 do livro-caixa (perda 0%: a produção registrada já é líquida).', 'info');
  }

  function updateSimulator() {
    const p = state.sim;
    const r = M.simulate(p);
    const errEl = $('sim-errors');
    if (!r.valid) {
      errEl.textContent = r.errors.join(' ');
      $('sim-hero').classList.add('invalid');
      return;
    }
    errEl.textContent = '';
    $('sim-hero').classList.remove('invalid');
    const neg = r.result < 0;
    const cepea = marketPrice();
    $('sim-hero').innerHTML = `
      <div class="sim-hero-main">
        <div class="sim-hero-label">Resultado operacional estimado ${dtag('estimado')}</div>
        <div class="sim-hero-value ${neg ? 'neg' : ''}">${fmtR(r.result)}</div>
        <div class="sim-hero-sub">margem <strong>${fmtN(r.margin, 1)}%</strong> · ${fmtR(r.resultPerHa)}/ha</div>
      </div>
      <div class="sim-hero-side">
        <div class="be-box ${neg ? 'neg' : ''}">
          <div class="be-label">Preço de equilíbrio</div>
          <div class="be-value">${fmtR(r.breakEvenPrice)}<small>/saca</small></div>
          <div class="be-note">${neg ? 'O preço simulado não cobre o custo.' : `O preço pode cair ${fmtN(r.safetyMarginPct, 0)}% antes de dar prejuízo.`}</div>
        </div>
      </div>`;
    const kpis = [
      ['Produção líquida', `${fmtN(r.netSc, 0)} sc`, `${fmtN(r.lostSc, 0)} sc perdidas (${fmtR(r.lostRevenue)})`],
      ['Receita bruta', fmtR(r.revenue), `${fmtN(r.netSc, 0)} sc × ${fmtR(p.price, 2)}`],
      ['Custo total', fmtR(r.cost), `${fmtN(p.areaHa, 1)} ha × ${fmtR(p.costPerHa)}`],
      ['Custo por saca', fmtR(r.costPerSack), `Cepea ${monthLabel(cepea.month)}: ${fmtR(cepea.value)}`],
      ['Produtividade de equilíbrio', `${fmtN(r.breakEvenProductivity, 1)} sc/ha`, `com preço e custo simulados`],
      ['Impacto de R$ 100 na saca', fmtR(r.priceSensitivityPer100), 'no resultado, para mais ou para menos']
    ];
    $('sim-kpis').innerHTML = kpis.map(k => `<div class="sim-metric-card"><div class="sim-metric-label">${esc(k[0])}</div><div class="sim-metric-value">${k[1]}</div><div class="sim-metric-delta">${esc(k[2])}</div></div>`).join('');

    const lo = Math.max(100, Math.min(r.breakEvenPrice * 0.6, p.price * 0.6));
    const hi = Math.max(p.price * 1.35, r.breakEvenPrice * 1.5, 2300);
    C.renderSensitivity('chart-sim-sensitivity', M.priceSensitivity(p, lo, hi, 30), {
      markers: [
        { price: p.price, label: `simulado ${fmtR(p.price)}`, color: '#163D32' },
        { price: r.breakEvenPrice, label: `equilíbrio ${fmtR(r.breakEvenPrice)}`, color: '#DC2626', offset: 14 },
        { price: cepea.value, label: `Cepea ${monthLabel(cepea.month)}`, color: '#0369A1', offset: 28 }
      ]
    });
    renderScenarioTable();
  }

  const SCN_ROWS = [
    { key: 'areaHa', label: 'Área (ha)', step: 0.5, dec: 1 },
    { key: 'productivity', label: 'Produtividade (sc/ha)', step: 0.1, dec: 1 },
    { key: 'price', label: 'Preço da saca (R$)', step: 10, dec: 2 },
    { key: 'costPerHa', label: 'Custo por ha (R$)', step: 100, dec: 0 },
    { key: 'lossPct', label: 'Perdas (%)', step: 0.5, dec: 1 }
  ];

  function renderScenarioTable() {
    const names = ['conservative', 'base', 'optimistic'];
    const res = names.map(n => ({ n, label: state.scenarios[n].label, r: M.simulate(state.scenarios[n]) }));
    const head = `<thead><tr><th>Premissa</th>${res.map(x => `<th class="num ${state.activeScenario === x.n ? 'active-col' : ''}">${esc(x.label)}<button class="link-btn sm" onclick="RigoraApp.applyScenario('${x.n}')">usar</button></th>`).join('')}</tr></thead>`;
    const inputRows = SCN_ROWS.map(row => `<tr><td>${esc(row.label)}</td>${names.map(n => {
      const s = state.scenarios[n];
      const why = s.why[row.key] ? ` title="${esc(s.why[row.key])}"` : '';
      return `<td class="num"><input class="cell-input" type="number" step="${row.step}" value="${row.dec === 2 ? s[row.key].toFixed(2) : M.round(s[row.key], row.dec)}"${why} onchange="RigoraApp.editScenario('${n}','${row.key}',this.value)" aria-label="${esc(row.label)} - ${esc(s.label)}">${s.why[row.key] ? `<div class="cell-why">${esc(s.why[row.key])}</div>` : ''}</td>`;
    }).join('')}</tr>`).join('');
    const out = [
      ['Produção líquida', x => `${fmtN(x.r.netSc, 0)} sc`],
      ['Receita', x => fmtR(x.r.revenue)],
      ['Custo', x => fmtR(x.r.cost)],
      ['Resultado', x => `<strong class="${x.r.result < 0 ? 'neg' : ''}">${fmtR(x.r.result)}</strong>`],
      ['Margem', x => `${fmtN(x.r.margin, 1)}%`],
      ['Preço de equilíbrio', x => fmtR(x.r.breakEvenPrice)]
    ];
    const outRows = out.map(([l, f]) => `<tr class="result-row"><td>${esc(l)}</td>${res.map(x => `<td class="num">${x.r.valid ? f(x) : '<span class="neg">inválido</span>'}</td>`).join('')}</tr>`).join('');
    $('scenario-table').innerHTML = `${head}<tbody>${inputRows}<tr class="sep"><td colspan="4">Resultados</td></tr>${outRows}</tbody>`;
    C.renderScenarioBars('chart-scenarios', res);
  }

  function editScenario(name, key, raw) {
    const v = parseFloat(String(raw).replace(',', '.'));
    if (isNaN(v)) { showToast('Valor inválido.', 'warning'); renderScenarioTable(); return; }
    state.scenarios[name][key] = v;
    state.scenarios[name].why[key] = 'editado por você';
    if (state.activeScenario === name) { state.sim = clone(state.scenarios[name]); syncSimInputs(); updateSimulator(); }
    else renderScenarioTable();
    if (name === 'base') renderOverview();
  }

  function resetScenarios() {
    state.scenarios = clone(state.scenarioDefaults);
    applyScenario(state.activeScenario || 'base');
    renderOverview();
    showToast('Premissas dos cenários restauradas.', 'info');
  }

  /* ==========================================================================
     INTELIGÊNCIA: BENCHMARK, MERCADO E HISTÓRICO
     ========================================================================== */
  function renderIntelligence() {
    const years = Object.keys(state.ibge.mg).sort();
    const ySel = $('bench-year');
    if (ySel && !ySel.options.length) {
      ySel.innerHTML = years.slice().reverse().map(y => `<option value="${y}">${y}</option>`).join('');
      const mesos = [...new Set(state.ibge.municipios.map(m => m.meso))].filter(Boolean).sort();
      $('bench-meso').innerHTML = `<option value="">Todas as regiões de MG</option>` + mesos.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
      $('bench-muni').innerHTML = state.ibge.municipios.slice().sort((a, b) => a.n.localeCompare(b.n, 'pt-BR')).map(m => `<option value="${m.c}">${esc(m.n)}</option>`).join('');
      $('bench-muni').value = '3148103';
    }
    renderBenchmarkStatus();
    renderHistoryTable();
  }

  function renderBenchmark() {
    const ySel = $('bench-year');
    if (!ySel || !ySel.value) return;
    const year = ySel.value;
    const meso = $('bench-meso').value;
    const code = $('bench-muni').value;
    const sel = state.ibge.municipios.find(m => m.c === code);
    const pool = state.ibge.municipios.filter(m => (!meso || m.meso === meso) && m.y[year] && m.y[year][0]);
    const ranked = pool.map(m => ({ m, sc: M.kgHaToScHa(m.y[year][0]) })).sort((a, b) => b.sc - a.sc);
    let top = ranked.slice(0, 15);
    const selPos = ranked.findIndex(x => x.m.c === code);
    if (selPos >= 15) top = top.concat([ranked[selPos]]);
    const mgv = mgYield(year);
    $('bench-source-line').textContent = `IBGE · Produção Agrícola Municipal (SIDRA 1613) · café arábica · ${state.ibge.municipios.length} maiores municípios produtores de MG (por área colhida em 2025) · média de MG com os municípios com produção em cada ano`;
    $('bench-rank-title').textContent = `Ranking de produtividade · ${year}`;
    $('bench-rank-sub').textContent = `${meso || 'Todas as regiões'} · ${pool.length} municípios no painel · linha = média de MG (${fmtN(mgv, 1)} sc/ha)`;
    C.renderHBar('chart-bench-ranking', top.map((x, i) => ({
      label: `${(ranked.indexOf(x) + 1)}. ${x.m.n}`, value: x.sc, highlight: x.m.c === code,
      color: x.m.c === code ? '#163D32' : '#0369A1', muted: x.m.c !== code,
      tip: `<strong>${esc(x.m.n)}</strong> · ${esc(x.m.meso || '')}<br>${fmtN(x.sc, 1)} sc/ha (${fmtN(x.m.y[year][0], 0)} kg/ha)<br>Área colhida: ${fmtN(x.m.y[year][1], 0)} ha<br>Produção: ${fmtN(x.m.y[year][2], 0)} t · Oficial IBGE`
    })), { unit: 'sc/ha', labelWidth: 175, rowHeight: 26, refValue: mgv, refLabel: 'MG', onClickAttr: (d) => `RigoraApp.pickMunicipio('${top.find(x => d.label.endsWith(x.m.n)).m.c}')`, emptyMessage: 'Nenhum município com dado para este ano e região.' });

    const years = Object.keys(state.ibge.mg).sort();
    const series = [{ name: sel.n, color: '#163D32', width: 3, values: years.map(y => ibgeYield(code, y)) }];
    if (code !== '3148103') series.push({ name: 'Patrocínio', color: '#0369A1', values: years.map(y => ibgeYield('3148103', y)) });
    series.push({ name: 'Minas Gerais', color: '#94A3B8', dash: '4 4', width: 2, pointRadius: 3, values: years.map(y => mgYield(y)) });
    series.forEach(s => { s.tips = s.values.map((v, i) => v === null ? '' : `<strong>${esc(s.name)} · ${years[i]}</strong><br>${fmtN(v, 1)} sc/ha · Oficial IBGE`); });
    $('bench-series-title').textContent = `Evolução ${years[0]}–${years[years.length - 1]} · ${sel.n}`;
    C.renderMultiLine('chart-bench-series', years, series, { unit: 'sc/ha', minZero: true });
    $('bench-series-legend').innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');

    // Insights calculados a partir dos dados oficiais
    const selSc = ibgeYield(code, year);
    const vals = years.map(y => ibgeYield(code, y)).filter(v => v !== null);
    const yoy = vals.slice(1).map((v, i) => Math.abs(M.pctChange(v, vals[i])));
    const avgSwing = M.sum(yoy) / (yoy.length || 1);
    const best = years.reduce((a, y) => (ibgeYield(code, y) || 0) > (ibgeYield(code, a) || 0) ? y : a, years[0]);
    const worst = years.reduce((a, y) => (ibgeYield(code, y) ?? Infinity) < (ibgeYield(code, a) ?? Infinity) ? y : a, years[0]);
    $('bench-insights').innerHTML = [
      [`${sel.n} em ${year}`, selSc === null ? 'Sem dado neste ano.' : `${fmtN(selSc, 1)} sc/ha, ${fmtPct(M.pctChange(selSc, mgv))} em relação à média de Minas Gerais (${fmtN(mgv, 1)}).`],
      ['Posição no ranking', selPos >= 0 ? `${selPos + 1}º de ${ranked.length} municípios ${meso ? 'da região' : 'do painel'} em ${year}.` : 'Fora do recorte de região selecionado.'],
      ['Oscilação ano a ano', `Em média ${fmtN(avgSwing, 0)}% de um ano para o outro (${years[0]}–${years[years.length - 1]}). Melhor ano: ${best}; pior: ${worst}. Planejar pela média evita decisões no pico ou no vale.`]
    ].map(([t, d]) => `<div class="insight-card">${dtag('estimado', 'Calculado de dado oficial')}<strong>${esc(t)}</strong><p>${esc(d)}</p></div>`).join('');
  }

  function pickMunicipio(code) {
    $('bench-muni').value = code;
    renderBenchmark();
  }

  function renderMarket() {
    const months = D.cepea.monthly.map(m => monthLabel(m.month));
    const vals = D.cepea.monthly.map(m => m.value);
    const tips = D.cepea.monthly.map(m => m.value === null ? '' : `<strong>Cepea · ${monthLabel(m.month)}</strong><br>${fmtR(m.value, 2)}/saca<br>${m.derived ? 'Derivado: ' + m.note : 'Oficial · ' + m.source}${m.note && !m.derived ? '<br>' + m.note : ''}`);
    const s26 = summary('2026');
    C.renderMultiLine('chart-market', months, [
      { name: 'Cepea', color: '#0369A1', values: vals, tips }
    ], {
      isCurrency: true, minZero: false,
      refLines: [{ value: s26.costPerSack, label: `custo/saca 2026: ${fmtR(s26.costPerSack)}`, color: '#DC2626' }],
      markers: state.sales.filter(v => v.safra === '2026').map(v => ({ category: monthLabel(v.date.slice(0, 7)), value: v.price, color: '#B45309', tip: `<strong>Venda ${dateBR(v.date)}</strong><br>${v.sacas} sacas a ${fmtR(v.price)}<br>${esc(v.buyer)} · Demonstrativo` }))
    });
    $('market-note').innerHTML = `Fonte: ${esc(D.cepea.name)}, Cepea/Esalq-USP. Jan/2026 derivado da variação informada pelo Cepea; set/2026 não localizado (lacuna no gráfico). Transcrito em ${dateBR(D.cepea.collectedAt)}. <a href="${D.cepea.url}" target="_blank" rel="noopener">Abrir fonte ↗</a>`;
  }

  function renderHistoryTable() {
    const tbody = $('historical-table-body');
    if (!tbody) return;
    tbody.innerHTML = farmHistory().map(h => {
      const ibge = ibgeYield('3148103', h.safra);
      return `<tr>
        <td><strong>${h.safra}</strong> ${h.source === 'livro-caixa' ? '<span class="dtag dtag-demo">livro-caixa</span>' : ''}</td>
        <td class="num">${fmtN(h.productivity, 1)} sc/ha</td>
        <td class="num">${ibge === null ? '<span class="muted">não publicado</span>' : `${fmtN(ibge, 1)} <span class="muted small">(${fmtPct(M.pctChange(h.productivity, ibge))})</span>`}</td>
        <td class="num">${fmtR(h.costPerHa)}</td>
        <td class="num">${fmtR(h.costPerSack)}</td>
        <td class="num">${fmtR(h.avgPrice)}${h.stock ? `<div class="muted small">+${fmtN(h.stock, 0)} sc em estoque</div>` : ''}</td>
        <td class="num"><strong>${fmtR(h.resultPerHa)}</strong></td>
      </tr>`;
    }).join('');
  }

  /* ==========================================================================
     ALERTAS (gerados por regras sobre dados existentes)
     ========================================================================== */
  function buildAlerts() {
    const out = [];
    const price = marketPrice();
    const official = D.cepea.monthly.filter(m => m.value && !m.derived);
    const peak = official.reduce((a, b) => a.value > b.value ? a : b);
    const s26 = summary('2026');
    const dropPct = M.pctChange(price.value, peak.value);
    if (dropPct < -10) {
      out.push({ id: `price-${price.month}`, cat: 'financeiro', priority: 'media', title: `Preço ${fmtN(Math.abs(dropPct), 0)}% abaixo do pico de 2026`,
        desc: `Cepea ${monthLabel(price.month)}: ${fmtR(price.value, 2)} contra ${fmtR(peak.value, 2)} em ${monthLabel(peak.month)}.`,
        refDate: price.month, source: ['oficial', 'Cepea'],
        impact: `As ${fmtN(s26.stockSc, 0)} sacas em estoque valem ${fmtR(s26.stockSc * (peak.value - price.value))} a menos que no pico.`,
        action: 'Compare vender agora ou esperar no simulador (preço de equilíbrio).', link: ['intelligence', 'sec-sim'] });
    }
    const bp = budgetProgress('ALL');
    const gap = bp.spentPct - bp.elapsedPct;
    out.push({ id: 'budget-2027', cat: 'financeiro', priority: gap > 3 ? 'media' : 'baixa', title: gap > 3 ? `Gasto ${fmtN(gap, 0)} p.p. acima do ritmo do orçamento` : 'Gasto da safra 2027 dentro do ritmo',
      desc: `${fmtR(bp.spent)} gastos (${fmtN(bp.spentPct, 1)}% do orçamento de ${fmtR(bp.budget)}) com ${fmtN(bp.elapsedPct, 1)}% do ciclo decorrido.`,
      refDate: D.meta.referenceDate, source: ['demonstrativo', 'Livro-caixa'],
      impact: 'Correção e adubação concentram gastos no início do ciclo; diferenças pequenas são esperadas.',
      action: gap > 3 ? 'Revise os próximos lançamentos por categoria antes da 2ª parcela de adubação.' : null, link: ['properties', null] });

    const r1 = state.rules.find(r => r.id === 'RULE-01');
    if (r1.enabled) {
      const budgetHa = D.budget.totalBrl / M.sum(state.talhoes, t => t.areaHa);
      const over = [{ label: 'Custo/ha realizado 2026', v: s26.costPerHa }, { label: 'Custo/ha orçado 2027', v: budgetHa }].filter(x => x.v > r1.thresholdValue);
      over.forEach(x => out.push({ id: `rule1-${x.label}-${r1.thresholdValue}`, cat: 'financeiro', priority: 'alta', title: `${x.label} acima do limite`,
        desc: `${fmtR(x.v)}/ha contra limite configurado de ${fmtR(r1.thresholdValue)}/ha (Regra 1).`, refDate: D.meta.referenceDate, source: ['demonstrativo', 'Livro-caixa + regra'],
        impact: `${fmtR((x.v - r1.thresholdValue) * 12)} acima do teto na área total.`, action: 'Veja quais categorias puxam o custo no livro-caixa.', link: ['properties', null] }));
    }

    const t03 = state.talhoes.map(t => summary('2026', t.id)).filter(s => s.productivity < s26.productivity * 0.9);
    t03.forEach(s => out.push({ id: `prod-${s.talhaoId}-2026`, cat: 'produtividade', priority: 'media', title: `${talhaoName(s.talhaoId)}: produtividade ${fmtN(Math.abs(M.pctChange(s.productivity, s26.productivity)), 0)}% abaixo do sítio`,
      desc: `${fmtN(s.productivity, 1)} sc/ha contra ${fmtN(s26.productivity, 1)} sc/ha na média da propriedade (safra 2026).`, refDate: '2026', source: ['demonstrativo', 'Colheita registrada'],
      impact: `Custo por saca de ${fmtR(s.costPerSack)}, contra ${fmtR(s26.costPerSack)} no sítio.`, action: 'Avalie com o técnico (EMATER/cooperativa) manejo de doenças na baixada antes da florada.', link: ['properties', null] }));

    const lastY = ibgeLatestYear();
    const farmLast = D.historicalSafras.find(h => h.safra === lastY);
    const patLast = ibgeYield('3148103', lastY);
    if (farmLast && patLast) {
      const d = M.pctChange(farmLast.productivity, patLast);
      out.push({ id: `bench-${lastY}`, cat: 'produtividade', priority: 'baixa', title: `Sítio ${d >= 0 ? 'acima' : 'abaixo'} da média de Patrocínio em ${lastY}`,
        desc: `${fmtN(farmLast.productivity, 1)} sc/ha no sítio contra ${fmtN(patLast, 1)} sc/ha no município (${fmtPct(d)}). Mesmo ano de colheita.`, refDate: lastY, source: ['oficial', 'IBGE/PAM'],
        impact: 'O IBGE de 2026 ainda não foi publicado; a próxima comparação sai em 2027.', action: null, link: ['intelligence', 'sec-bench'] });
    }

    const low = state.inventory.filter(i => i.currentQuantity < i.minQuantity);
    if (state.rules.find(r => r.id === 'RULE-02').enabled && low.length) {
      out.push({ id: `stock-${low.map(i => i.id + i.currentQuantity).join('-')}`, cat: 'produtividade', priority: 'media', title: `${low.length} insumo(s) abaixo do estoque mínimo`,
        desc: low.map(i => `${i.name}: ${i.currentQuantity} de ${i.minQuantity} ${i.unit}`).join('; ') + '.', refDate: D.meta.referenceDate, source: ['demonstrativo', 'Estoque'],
        impact: 'Falta de adubo pode atrasar a 2ª parcela na janela de chuvas.', action: 'Solicite reposição à cooperativa.', link: ['properties', null] });
    }

    const r3 = state.rules.find(r => r.id === 'RULE-03');
    if (r3.enabled && state.inmet.days) {
      const fav = state.inmet.days.filter(d => (d.risk.urMean >= r3.thresholdValue) && d.risk.tMean >= 22 && d.risk.tMean <= 26);
      if (fav.length) {
        out.push({ id: `clima-${state.inmet.days[0].date}-${fav.length}-${r3.thresholdValue}`, cat: 'clima', priority: fav.length >= 3 ? 'alta' : 'baixa',
          title: fav.length >= 3 ? 'Condição favorável à ferrugem prevista' : `Condição favorável à ferrugem em ${fav.length} dia(s)`,
          desc: `${fav.map(d => d.date.slice(0, 5)).join(', ')}: UR média estimada ≥ ${r3.thresholdValue}% e T média entre 22 e 26 °C.`, refDate: state.inmet.days[0].date.split('/').reverse().join('-'),
          source: ['estimado', `INMET ${state.inmet.status === 'live' ? 'ao vivo' : 'cópia salva'} + critério Rigora`],
          impact: 'Estimativa a partir de previsão (umidade mín/máx), não de medição no talhão.', action: fav.length >= 3 ? 'Programe inspeção no T03 (baixada).' : null, link: ['overview', null] });
      }
    }

    if (state.inmet.status === 'snapshot' || state.inmet.status === 'error') {
      out.push({ id: `dq-inmet-${state.inmet.status}`, cat: 'dados', priority: 'media', title: state.inmet.status === 'error' ? 'Previsão do INMET indisponível' : 'Previsão do INMET vinda de cópia salva',
        desc: `Consulta ao vivo falhou: ${state.inmet.message}.${state.inmet.collectedAt ? ' Exibindo previsão oficial de ' + dateTimeBR(state.inmet.collectedAt) + '.' : ''}${inmetCopyNote(state.inmet.fallback) ? ' ' + inmetCopyNote(state.inmet.fallback) : ''}`, refDate: localDay(state.inmet.checkedAt), source: ['oficial', 'INMET'],
        impact: 'Previsões envelhecem rápido: confira a data antes de decidir pulverizações.', action: null, link: ['sources', null] });
    }
    if (state.ibge.status === 'divergent') {
      out.push({ id: 'dq-ibge-divergente', cat: 'dados', priority: 'media', title: 'Dados de Patrocínio divergem do IBGE',
        desc: ibgeDivergenceNote(state.ibge.conferencia), refDate: localDay(state.ibge.conferencia && state.ibge.conferencia.checkedAt), source: ['oficial', 'IBGE'],
        impact: 'O IBGE pode ter revisado a série. Até conferir, o benchmark de Patrocínio usa a cópia oficial datada.', action: 'Use "Atualizar do IBGE" para trazer a série completa de área, produção e rendimento.', link: ['intelligence', 'sec-bench'] });
    }
    if (state.ibge.status === 'snapshot') {
      out.push({ id: 'dq-ibge-snapshot', cat: 'dados', priority: 'baixa', title: 'Benchmark do IBGE vindo de cópia oficial datada',
        desc: `Dados coletados do SIDRA em ${dateTimeBR(state.ibge.collectedAt)}${state.ibge.message ? `; consulta ao vivo falhou (${state.ibge.message})` : ''}.`, refDate: localDay(state.ibge.attemptedAt), source: ['oficial', 'IBGE'],
        impact: 'Dado anual: a cópia só fica desatualizada quando o IBGE publicar 2026.', action: null, link: ['intelligence', 'sec-bench'] });
    }
    out.push({ id: 'dq-conab', cat: 'dados', priority: 'baixa', title: 'Custos ainda não comparados com a Conab',
      desc: 'A planilha de custo de produção da Conab para Patrocínio não foi integrada. Os custos do sítio são demonstrativos.', refDate: localDay(), source: ['demonstrativo', 'Catálogo de fontes'],
      impact: 'Sem referência oficial, não dá para dizer se o custo/ha está alto ou baixo para a região.', action: 'Próxima integração prevista no roadmap.', link: ['sources', null] });
    if (D.cepea.monthly.some(m => m.value === null)) {
      out.push({ id: 'dq-cepea-gap', cat: 'dados', priority: 'baixa', title: 'Lacuna na série de preços do Cepea',
        desc: `Média de ${D.cepea.monthly.filter(m => m.value === null).map(m => monthLabel(m.month)).join(', ')} não localizada nas fontes consultadas.`, refDate: D.cepea.collectedAt, source: ['oficial', 'Cepea'],
        impact: 'O gráfico de mercado mostra o mês em branco em vez de estimar o valor.', action: null, link: ['intelligence', 'sec-market'] });
    }
    return out;
  }

  const CAT_LABEL = { financeiro: 'Financeiro', produtividade: 'Produtividade', clima: 'Clima', dados: 'Qualidade dos dados' };
  const PRI_LABEL = { alta: 'Alta', media: 'Média', baixa: 'Baixa' };
  const PRI_ORDER = { alta: 0, media: 1, baixa: 2 };

  function renderAlerts() {
    state.alerts = buildAlerts().sort((a, b) => PRI_ORDER[a.priority] - PRI_ORDER[b.priority]);
    const unseen = state.alerts.filter(a => !state.seenAlerts.has(a.id));
    const badge = $('notif-count');
    if (badge) { badge.textContent = unseen.length; badge.style.display = unseen.length ? '' : 'none'; }

    const f = state.filters.alertCat;
    const chips = [['all', 'Todos'], ['unseen', 'Não vistos'], ...Object.entries(CAT_LABEL)];
    $('alerts-filter').innerHTML = chips.map(([k, l]) => {
      const n = k === 'all' ? state.alerts.length : k === 'unseen' ? unseen.length : state.alerts.filter(a => a.cat === k).length;
      return `<button role="radio" aria-checked="${f === k}" class="chip ${f === k ? 'active' : ''}" onclick="RigoraApp.filterAlerts('${k}')">${esc(l)} <span>${n}</span></button>`;
    }).join('');
    $('alerts-summary').textContent = `${state.alerts.length} alerta(s) · ${unseen.length} não visto(s) · gerados a partir dos dados disponíveis`;

    const list = state.alerts.filter(a => f === 'all' || (f === 'unseen' ? !state.seenAlerts.has(a.id) : a.cat === f));
    $('overview-alerts-list').innerHTML = list.length ? list.map(a => {
      const seen = state.seenAlerts.has(a.id);
      return `<div class="alert-item pri-${a.priority} ${seen ? 'seen' : ''}">
        <div class="alert-item-header">
          <span class="alert-item-title">${esc(a.title)}</span>
          <span class="pri-pill pri-${a.priority}">${PRI_LABEL[a.priority]}</span>
        </div>
        <div class="alert-item-desc">${esc(a.desc)}</div>
        <details class="alert-details"><summary>Impacto e ação</summary>
          <div><strong>Impacto:</strong> ${esc(a.impact)}</div>
          ${a.action ? `<div><strong>Ação sugerida:</strong> ${esc(a.action)}</div>` : '<div class="muted">Sem ação recomendada: dados insuficientes para recomendar.</div>'}
        </details>
        <div class="alert-item-foot">
          <span class="alert-cat">${CAT_LABEL[a.cat]}</span>${dtag(a.source[0], a.source[1])}<span class="alert-item-time">ref. ${esc(a.refDate.length === 7 ? monthLabel(a.refDate) : (a.refDate.length === 4 ? a.refDate : dateBR(a.refDate)))}</span>
          <span class="alert-actions">
            ${a.link ? `<button class="link-btn sm" onclick="RigoraApp.goAlert('${a.link[0]}','${a.link[1] || ''}')">Abrir</button>` : ''}
            <button class="link-btn sm" onclick="RigoraApp.toggleSeen('${esc(a.id)}')">${seen ? 'Marcar como não visto' : 'Marcar como visto'}</button>
          </span>
        </div>
      </div>`;
    }).join('') : '<div class="empty-state">Nenhum alerta neste filtro.</div>';

    const nl = $('notif-items-list');
    if (nl) {
      nl.innerHTML = unseen.length ? unseen.slice(0, 6).map(a => `<div class="notif-item pri-${a.priority}" onclick="RigoraApp.navigate('overview'); RigoraApp.filterAlerts('${a.cat}')">
          <div style="font-weight:600">${esc(a.title)}</div><div class="muted small">${CAT_LABEL[a.cat]} · prioridade ${PRI_LABEL[a.priority].toLowerCase()}</div></div>`).join('')
        : '<div class="notif-item muted">Tudo visto por aqui.</div>';
    }
  }

  function filterAlerts(cat) { state.filters.alertCat = cat; renderAlerts(); }
  function toggleSeen(id) {
    if (state.seenAlerts.has(id)) state.seenAlerts.delete(id); else state.seenAlerts.add(id);
    saveSeen();
    renderAlerts();
  }
  function markAllAlertsSeen() {
    state.alerts.forEach(a => state.seenAlerts.add(a.id));
    saveSeen();
    renderAlerts();
    showToast('Todos os alertas marcados como vistos.', 'success');
  }
  function goAlert(tab, section) {
    navigate(tab);
    if (section) setTimeout(() => scrollToSection(null, section), 80);
  }

  /* ==========================================================================
     AUTOMAÇÕES
     ========================================================================== */
  function renderAutomations() {
    const el = $('automations-grid');
    if (!el) return;
    const r1 = state.rules[0], r2 = state.rules[1], r3 = state.rules[2];
    const s26 = summary('2026');
    const low = state.inventory.filter(i => i.currentQuantity < i.minQuantity);
    const fav = state.inmet.days ? state.inmet.days.filter(d => d.risk.urMean >= r3.thresholdValue && d.risk.tMean >= 22 && d.risk.tMean <= 26).length : null;
    const card = (r, body, status) => `
      <div class="rule-card ${r.enabled ? '' : 'disabled'}">
        <div class="rule-header">
          <div class="rule-title">${esc(r.name)}</div>
          <label class="switch"><input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="RigoraApp.onRuleToggle('${r.id}', this.checked)" aria-label="Ativar ${esc(r.name)}"><span class="slider"></span></label>
        </div>
        <div class="rule-body">${body}</div>
        <div class="rule-status">${status}</div>
        <div class="rule-footer"><button class="btn-secondary btn-inline sm" onclick="RigoraApp.testRule('${r.id}')">Testar agora</button></div>
      </div>`;
    el.innerHTML = [
      card(r1, `<div class="rule-condition-box">Dispara quando o <strong>custo por hectare</strong> (realizado 2026 ou orçado 2027) passa de
          <span class="inline-input">R$ <input type="number" step="100" min="0" value="${r1.thresholdValue}" onchange="RigoraApp.onRuleThresholdChange('RULE-01', this.value)" aria-label="Limite de custo por hectare"></span>/ha</div>
          <div class="rule-inputs">${dtag('demonstrativo', 'Livro-caixa')} 2026: ${fmtR(s26.costPerHa)}/ha · orçado 2027: ${fmtR(D.budget.totalBrl / 12)}/ha</div>`,
        `${s26.costPerHa > r1.thresholdValue || D.budget.totalBrl / 12 > r1.thresholdValue ? '<span class="status-pill warning"><span class="status-dot"></span> Disparada</span>' : '<span class="status-pill success"><span class="status-dot"></span> Dentro do limite</span>'}`),
      card(r2, `<div class="rule-condition-box">Dispara quando qualquer insumo fica <strong>abaixo do estoque mínimo</strong> cadastrado.</div>
          <div class="rule-inputs">${dtag('demonstrativo', 'Estoque')} ${state.inventory.length} insumos monitorados</div>`,
        low.length ? `<span class="status-pill warning"><span class="status-dot"></span> ${low.length} abaixo do mínimo</span>` : '<span class="status-pill success"><span class="status-dot"></span> Estoque adequado</span>'),
      card(r3, `<div class="rule-condition-box">Conta os dias previstos com <strong>UR média ≥
          <span class="inline-input"><input type="number" step="1" min="50" max="100" value="${r3.thresholdValue}" onchange="RigoraApp.onRuleThresholdChange('RULE-03', this.value)" aria-label="Umidade relativa mínima"></span>%</strong> e T média entre 22 e 26 °C.</div>
          <div class="rule-inputs">${dtag('oficial', 'INMET')} ${dtag('estimado', 'Critério Rigora')} UR média = (mín + máx) ÷ 2, a partir da previsão</div>`,
        fav === null ? '<span class="status-pill info"><span class="status-dot"></span> Aguardando previsão</span>' : (fav ? `<span class="status-pill warning"><span class="status-dot"></span> ${fav} dia(s) favorável(is)</span>` : '<span class="status-pill success"><span class="status-dot"></span> Sem condição favorável</span>'))
    ].join('');
  }

  function onRuleToggle(id, on) {
    const r = state.rules.find(x => x.id === id);
    r.enabled = on;
    logToRuleConsole(`[${new Date().toLocaleTimeString('pt-BR')}] ${r.name}: ${on ? 'ativada' : 'desativada'}.`);
    renderAutomations();
    renderAlerts();
  }

  function onRuleThresholdChange(id, value) {
    const r = state.rules.find(x => x.id === id);
    const v = parseFloat(value);
    if (isNaN(v) || v <= 0) { showToast('Informe um limite válido.', 'warning'); renderAutomations(); return; }
    r.thresholdValue = v;
    logToRuleConsole(`[${new Date().toLocaleTimeString('pt-BR')}] Limite de "${r.name}" alterado para ${id === 'RULE-01' ? fmtR(v) + '/ha' : v + '%'}.`);
    renderAutomations();
    renderAlerts();
    if (state.inmet.days) renderWeather();
    showToast('Limite atualizado. Alertas recalculados.', 'success');
  }

  function testRule(id) {
    const r = state.rules.find(x => x.id === id);
    const t = new Date().toLocaleTimeString('pt-BR');
    if (!r.enabled) showToast('A regra está desativada; o teste roda mesmo assim, mas ela não gera alertas.', 'warning');
    if (id === 'RULE-01') {
      const s26 = summary('2026');
      const b = D.budget.totalBrl / 12;
      logToRuleConsole(`[${t}] REGRA 1 · custo por hectare
  entrada: realizado 2026 = ${fmtR(s26.costPerHa)}/ha (livro-caixa, ${s26.entriesCount} lançamentos)
  entrada: orçado 2027   = ${fmtR(b)}/ha (orçamento demonstrativo)
  limite: ${fmtR(r.thresholdValue)}/ha
  resultado: ${[s26.costPerHa, b].some(v => v > r.thresholdValue) ? 'DISPARADA' : 'dentro do limite'}`);
    } else if (id === 'RULE-02') {
      const low = state.inventory.filter(i => i.currentQuantity < i.minQuantity);
      logToRuleConsole(`[${t}] REGRA 2 · estoque mínimo
${state.inventory.map(i => `  ${i.currentQuantity < i.minQuantity ? '✗' : '✓'} ${i.name}: ${i.currentQuantity}/${i.minQuantity} ${i.unit}`).join('\n')}
  resultado: ${low.length ? low.length + ' item(ns) abaixo do mínimo' : 'estoque adequado'}`);
    } else {
      if (!state.inmet.days) { logToRuleConsole(`[${t}] REGRA 3 · sem previsão disponível. Nenhuma avaliação feita (sem dado, sem alerta).`); return; }
      logToRuleConsole(`[${t}] REGRA 3 · condição favorável à ferrugem (estimativa sobre previsão INMET ${state.inmet.status === 'live' ? 'ao vivo' : 'de ' + dateTimeBR(state.inmet.collectedAt)})
${state.inmet.days.map(d => `  ${d.date}: T média ${fmtN(d.risk.tMean, 1)} °C · ${d.risk.urMean === null ? 'UR indisponível' : `UR média ${fmtN(d.risk.urMean, 0)}%`} → ${d.risk.urMean >= r.thresholdValue && d.risk.tMean >= 22 && d.risk.tMean <= 26 ? 'favorável' : 'não favorável'}`).join('\n')}
  limite de UR: ${r.thresholdValue}% · faixa de T: 22–26 °C`);
    }
    showToast('Teste executado. Veja o registro abaixo.', 'info');
  }

  function logToRuleConsole(msg) {
    const el = $('rule-test-console');
    if (!el) return;
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.textContent = msg;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
  }

  function clearRuleLog() {
    $('rule-test-console').innerHTML = '<div class="muted">// Registro limpo.</div>';
  }

  /* ==========================================================================
     FONTES E TRANSPARÊNCIA
     ========================================================================== */
  /** B3 (V3): o preço do Cepea é de fonte oficial, mas transcrito à mão. */
  const SOURCE_TYPE_LABEL = { 'SRC-CEPEA': 'Oficial · transcrito' };

  /* B7a: procedência dos dados, lida de SNAP.exportacao na hora de renderizar (nunca ao carregar).
   * Três situações: exportação válida (texto por fonte), sem exportação ("Cópia manual") e registro
   * malformado ("Registro de procedência incompleto"). Campos desconhecidos são ignorados. Nunca lança erro. */
  const PROVENANCE_SOURCES = { 'SRC-IBGE': 'sidra', 'SRC-INMET': 'inmet' };
  const PROVENANCE_MANUAL = 'Cópia manual';
  const PROVENANCE_INCOMPLETE = 'Registro de procedência incompleto';
  const PROVENANCE_UNAVAILABLE = 'Fonte indisponível';

  function provenanceText(sourceId) {
    try {
      const key = PROVENANCE_SOURCES[sourceId];
      if (!key) return null;
      const ex = SNAP.exportacao;
      if (ex === undefined) return PROVENANCE_MANUAL;
      const obj = (o) => o !== null && typeof o === 'object' && !Array.isArray(o);
      const iso = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v) && Number.isFinite(Date.parse(v));
      const texto = (v) => typeof v === 'string' && v.trim() !== '';
      const motivo = (v) => v.trim().replace(/[.\s]+$/, '');
      if (!obj(ex) || !iso(ex.geradoEm)) return PROVENANCE_INCOMPLETE;
      const sec = ex[key];
      if (!obj(sec) || !texto(sec.modo)) return PROVENANCE_INCOMPLETE;
      const gerado = dateTimeBR(ex.geradoEm);
      const temDados = SNAP[key] !== null && SNAP[key] !== undefined;
      if (sec.modo === 'sqlite') {
        if (!temDados || !Number.isInteger(sec.execucaoId) || !iso(sec.dataColeta)
          || typeof sec.hashSha256 !== 'string' || !/^[0-9a-f]{64}$/i.test(sec.hashSha256)) return PROVENANCE_INCOMPLETE;
        return `Arquivo gerado em ${gerado} a partir da execução ${sec.execucaoId} (coleta de ${dateTimeBR(sec.dataColeta)}; bruto SHA-256 ${sec.hashSha256.slice(0, 12)}…).`;
      }
      if (sec.modo === 'mantido_do_arquivo_anterior') {
        if (!temDados || !texto(sec.motivo)) return PROVENANCE_INCOMPLETE;
        return `Dados mantidos do arquivo anterior. Motivo: ${motivo(sec.motivo)}.`;
      }
      if (sec.modo === 'indisponivel' && key === 'inmet') {
        if (temDados || !texto(sec.motivo)) return PROVENANCE_INCOMPLETE;
        if (sec.execucaoId === undefined) return `Arquivo gerado em ${gerado} sem previsão. Motivo: ${motivo(sec.motivo)}.`;
        if (!Number.isInteger(sec.execucaoId)) return PROVENANCE_INCOMPLETE;
        return `Arquivo gerado em ${gerado} sem previsão: a execução ${sec.execucaoId} não foi exportada. Motivo: ${motivo(sec.motivo)}.`;
      }
      // IBGE "indisponível": registro válido diz que a fonte não estava disponível. Motivo opcional;
      // se vier, precisa ser texto (outro tipo é registro malformado). INMET segue as regras acima.
      if (sec.modo === 'indisponivel' && key === 'sidra') {
        if (sec.motivo !== undefined && sec.motivo !== null && typeof sec.motivo !== 'string') return PROVENANCE_INCOMPLETE;
        return texto(sec.motivo) ? `${PROVENANCE_UNAVAILABLE}. Motivo: ${motivo(sec.motivo)}.` : `${PROVENANCE_UNAVAILABLE}.`;
      }
      return PROVENANCE_INCOMPLETE;
    } catch (e) {
      return PROVENANCE_INCOMPLETE;
    }
  }

  function sourceStatus(id) {
    if (id === 'SRC-IBGE') {
      const s = state.ibge;
      if (s.loading) return ['info', 'Consultando…', '—'];
      if (s.status === 'live') return ['success', 'Atualizado ao vivo', dateTimeBR(s.liveCheckedAt)];
      if (s.status === 'checked') return ['success', 'Dados de Patrocínio conferidos ao vivo', dateTimeBR(s.liveCheckedAt)];
      if (s.status === 'partial') return ['info', 'Dados de Patrocínio parcialmente conferidos', dateTimeBR(s.liveCheckedAt)];
      if (s.status === 'divergent') return ['warning', 'Dados de Patrocínio divergem do IBGE', dateTimeBR(s.liveCheckedAt)];
      return ['warning', 'Cópia oficial (API falhou)', dateTimeBR(s.collectedAt)];
    }
    if (id === 'SRC-INMET') {
      const s = state.inmet;
      if (s.status === 'live') return ['success', 'Ao vivo', dateTimeBR(s.collectedAt)];
      if (s.status === 'snapshot') return ['warning', 'Cópia oficial (API falhou)', dateTimeBR(s.collectedAt)];
      if (s.status === 'loading') return ['info', 'Consultando…', '—'];
      return ['danger', 'Indisponível', inmetCopyNote(s.fallback) || '—'];
    }
    if (id === 'SRC-CEPEA') return ['info', 'Transcrito manualmente', dateBR(D.cepea.collectedAt)];
    if (id === 'SRC-CONAB') return ['info', 'Referências pontuais', dateBR(D.meta.referenceDate)];
    if (id === 'SRC-LEDGER') return ledgerWarn() ? ['warning', 'Demonstrativo · aviso de armazenamento', ledgerNote()] : ['info', 'Demonstrativo · salvo neste navegador', ledgerNote()];
    return ['info', 'Demonstrativo (sessão)', dateBR(D.meta.referenceDate)];
  }

  function renderSources() {
    const el = $('data-sources-grid');
    if (!el) return;
    el.innerHTML = D.dataSources.map(s => {
      const [cls, txt, when] = sourceStatus(s.id);
      return `<article class="source-card">
        <div class="source-head">${dtag(s.type, SOURCE_TYPE_LABEL[s.id])}<span class="status-pill ${cls}"><span class="status-dot"></span> ${esc(txt)}</span></div>
        <h4>${esc(s.name)}</h4>
        <dl>
          <dt>O que traz</dt><dd>${esc(s.content)}</dd>
          <dt>Metodologia</dt><dd>${esc(s.method)}</dd>
          <dt>Limitações</dt><dd>${esc(s.limitations)}</dd>
          <dt>Coleta / atualização</dt><dd>${esc(when)}</dd>${PROVENANCE_SOURCES[s.id] ? `
          <dt>Procedência</dt><dd>${esc(provenanceText(s.id))}</dd>` : ''}
        </dl>
        ${s.url ? `<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Abrir fonte original ↗</a>` : '<span class="muted small">Dados internos da propriedade demonstrativa</span>'}
      </article>`;
    }).join('');
    renderIntegrationLog();
  }

  function renderIntegrationLog() {
    const el = $('integration-log');
    if (!el) return;
    el.innerHTML = state.integrationLog.length ? state.integrationLog.map(l => `<div class="log-entry ${l.level}">[${dateTimeBR(l.at)}] ${esc(l.source)} · ${esc(l.message)}</div>`).join('') : '<div class="muted">Nenhuma consulta registrada ainda.</div>';
  }

  /* ==========================================================================
     INTERFACE GERAL
     ========================================================================== */
  function toggleNotifications() { $('notif-dropdown').classList.toggle('show'); }
  function toggleMobileSidebar() { $('sidebar').classList.toggle('open'); }

  function showToast(message, type = 'info') {
    const c = $('toast-container');
    if (!c) return;
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.innerHTML = `<div style="flex:1">${esc(message)}</div><button aria-label="Fechar aviso" onclick="this.parentElement.remove()">&times;</button>`;
    c.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, 4200);
  }

  function debounce(fn, wait) {
    let to;
    return function (...a) { clearTimeout(to); to = setTimeout(() => fn.apply(this, a), wait); };
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    login, logout, navigate, scrollToSection,
    onSafraChange, onPropertyChange, setTalhao, setCompare, openKpi, closeDrawer, drillCategory,
    selectTalhao, renderLedger, clearLedgerFilters, openNewActivityModal, closeNewActivityModal, submitNewActivity,
    onSimInput, applyScenario, useFarm2026, editScenario, resetScenarios,
    renderBenchmark, pickMunicipio, refreshIbge, fetchInmetForecast, checkInmetForecast, startInmetAutoRefresh,
    filterAlerts, toggleSeen, markAllAlertsSeen, goAlert,
    onRuleToggle, onRuleThresholdChange, testRule, clearRuleLog,
    toggleNotifications, toggleMobileSidebar,
    _state: state
  };
})();
