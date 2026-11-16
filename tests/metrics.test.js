/**
 * Testes dos cálculos da Rigora (executar com: node tests/metrics.test.js)
 */
const assert = require('assert');
const M = require('../js/metrics.js');
const D = require('../js/data.js');

let passed = 0;
function test(name, fn) { fn(); passed++; console.log('  ok -', name); }
const pendentes = [];
function testAsync(name, fn) { pendentes.push([name, fn]); }   // rodam em sequência no fim do arquivo
const close = (a, b, eps = 0.01) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

console.log('Rigora - testes de métricas');

test('custo total da safra 2026 bate com o livro-caixa', () => {
  const e = M.filterEntries(D.entries, { safra: '2026' });
  close(M.costOf(e, D.talhoes), 162000);
});

test('rateio por talhão soma o custo total (sem perda nem duplicação)', () => {
  const e = M.filterEntries(D.entries, { safra: '2026' });
  const parts = D.talhoes.map(t => M.costOf(e, D.talhoes, t.id));
  close(M.sum(parts), 162000);
});

test('custos por categoria somam o total', () => {
  const e = M.filterEntries(D.entries, { safra: '2026' });
  close(M.sum(M.costByCategory(e, D.talhoes), c => c.value), 162000);
});

test('acumulado mensal termina no total', () => {
  const e = M.filterEntries(D.entries, { safra: '2026' });
  const m = M.costByMonth(e, D.talhoes);
  close(m[m.length - 1].cumulative, 162000);
});

test('produtividade da propriedade = produção / área (fonte única)', () => {
  const s = M.safraSummary({ talhoes: D.talhoes, entries: D.entries, harvest: D.harvest, sales: D.sales, safra: '2026', marketPrice: 1586.08 });
  close(s.productionSc, 262);
  close(s.productivity, 262 / 12);
  close(s.costPerSack, 162000 / 262);
});

test('produção por talhão soma a produção total', () => {
  const total = M.sum(D.talhoes.map(t => M.safraSummary({ talhoes: D.talhoes, entries: D.entries, harvest: D.harvest, sales: D.sales, safra: '2026', talhaoId: t.id }).productionSc));
  close(total, 262);
});

test('resultado = receita realizada + estoque valorado - custo', () => {
  const s = M.safraSummary({ talhoes: D.talhoes, entries: D.entries, harvest: D.harvest, sales: D.sales, safra: '2026', marketPrice: 1586.08 });
  close(s.revenueRealized, 80 * 1400 + 100 * 1690 + 50 * 1510);
  close(s.stockSc, 32);
  close(s.result, s.revenueRealized + 32 * 1586.08 - 162000);
});

test('safra em andamento não inventa produção', () => {
  const s = M.safraSummary({ talhoes: D.talhoes, entries: D.entries, harvest: D.harvest, sales: D.sales, safra: '2027' });
  assert.strictEqual(s.productionSc, null);
  assert.strictEqual(s.result, null);
  close(s.cost, 9600 + 2500 + 15600 + 3800 + 9400);
});

test('simulador: fórmulas e ponto de equilíbrio', () => {
  const r = M.simulate({ areaHa: 12, productivity: 22, price: 1500, costPerHa: 14000, lossPct: 5 });
  close(r.grossSc, 264); close(r.netSc, 250.8); close(r.revenue, 376200);
  close(r.cost, 168000); close(r.result, 208200);
  close(r.breakEvenPrice, 168000 / 250.8);
  // no preço de equilíbrio o resultado é zero
  close(M.simulate({ areaHa: 12, productivity: 22, price: r.breakEvenPrice, costPerHa: 14000, lossPct: 5 }).result, 0);
  // na produtividade de equilíbrio o resultado é zero
  close(M.simulate({ areaHa: 12, productivity: r.breakEvenProductivity, price: 1500, costPerHa: 14000, lossPct: 5 }).result, 0);
});

test('simulador rejeita entradas inválidas', () => {
  assert.strictEqual(M.simulate({ areaHa: 0, productivity: 22, price: 1500, costPerHa: 14000, lossPct: 5 }).valid, false);
  assert.strictEqual(M.simulate({ areaHa: 12, productivity: 22, price: 1500, costPerHa: 14000, lossPct: 100 }).valid, false);
  assert.strictEqual(M.simulate({ areaHa: 12, productivity: NaN, price: 1500, costPerHa: 14000, lossPct: 5 }).valid, false);
});

test('variação percentual negativa é preservada (badge abaixo da referência)', () => {
  close(M.pctChange(15, 20), -25);
  assert.strictEqual(M.pctChange(10, 0), null);
});

test('conversão IBGE kg/ha -> sc/ha', () => {
  close(M.kgHaToScHa(1110), 18.5);
});

test('favorabilidade à ferrugem segue o critério documentado', () => {
  assert.strictEqual(M.rustFavorability({ temp_min: 19, temp_max: 27, umidade_min: 70, umidade_max: 100 }).favorable, true);
  assert.strictEqual(M.rustFavorability({ temp_min: 18, temp_max: 31, umidade_min: 40, umidade_max: 100 }).favorable, false);
});

/* ==========================================================================
 * M1 (Etapa 5B): validade temporal da cópia salva do INMET (fallback)
 * Datas fixas: nenhum teste depende do relógio do computador.
 * ========================================================================== */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ROOT = path.join(__dirname, '..');
/* T0: os testes usam uma fixture própria e nunca leem o js/snapshots.js de produção,
 * que poderá ser substituído pelo arquivo exportado (B6) sem mudar a referência dos testes. */
const SNAPSHOT_FIXTURE = 'tests/fixtures/snapshots_manual_20261114.js';
const SNAPSHOT_FIXTURE_SHA256 = '410fa9cb428b440f4eb71f2de29998cb72922595d8ec04e4657cffcc2d61b0d5';   // cópia manual de 14/11 aprovada
const SNAPSHOT_PRODUCAO = 'js/snapshots.js';
const lidos = new Set();                                       // arquivos lidos pelos testes (conferido no T0-02)
const read = (f) => { lidos.add(f); return fs.readFileSync(path.join(ROOT, f), 'utf8'); };
const copy = (o) => JSON.parse(JSON.stringify(o));
const H = 3600 * 1000;

/** Fixture do snapshot num contexto isolado (sem tocar no arquivo). */
function loadSnapshots() {
  const sandbox = { window: {} };
  vm.runInNewContext(read(SNAPSHOT_FIXTURE), sandbox);
  return sandbox.window.RIGORA_SNAPSHOTS;
}
test('T0-01 fixture do snapshot é a cópia de referência aprovada (hash conferido)', () => {
  const bytes = fs.readFileSync(path.join(ROOT, SNAPSHOT_FIXTURE));
  assert.strictEqual(require('crypto').createHash('sha256').update(bytes).digest('hex'), SNAPSHOT_FIXTURE_SHA256);
  const s = loadSnapshots();
  assert.ok(s && s.sidra && s.inmet, 'fixture sem as seções sidra e inmet');
  assert.strictEqual(s.sidra.municipios.length, 60);
});
const SNAP_INMET = loadSnapshots().inmet;                  // coletado em 2026-11-14T16:13:28.004Z, dias 14 a 18/11
const COLETA = Date.parse(SNAP_INMET.collectedAt);
const at = (ms) => new Date(ms);
const dia = (d) => copy(SNAP_INMET.data['3148103'][d]);    // registro real de um dia da cópia
const snapCom = (collectedAt, dias) => ({ collectedAt, data: { '3148103': dias } });

test('M1-01 cópia com menos de 24 h é usada, com todos os dias de hoje em diante', () => {
  const r = M.inmetSnapshotForecast(SNAP_INMET, at(COLETA + 2 * H), { today: '2026-11-14' });
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(r.days.map(d => d.date), ['14/11/2026', '15/11/2026', '16/11/2026', '17/11/2026', '18/11/2026']);
  assert.strictEqual(r.removedPast, 0);
  assert.strictEqual(r.collectedAt, SNAP_INMET.collectedAt);
  close(r.ageHours, 2);
});

test('M1-02 cópia com exatamente 24 h ainda é usada (limite inclusivo, como no pipeline)', () => {
  const r = M.inmetSnapshotForecast(SNAP_INMET, at(COLETA + 24 * H), { today: '2026-11-15' });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.days[0].date, '15/11/2026');           // 14/11 já passou e sai
  assert.strictEqual(r.removedPast, 1);
  assert.strictEqual(M.INMET_SNAPSHOT_MAX_AGE_HOURS, 24);
});

test('M1-03 cópia com mais de 24 h é recusada, sem dias, guardando a data da última cópia', () => {
  const r = M.inmetSnapshotForecast(SNAP_INMET, at(COLETA + 24 * H + 1), { today: '2026-11-15' });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.reason, 'vencida');
  assert.strictEqual(r.days, null);
  assert.strictEqual(r.collectedAt, SNAP_INMET.collectedAt);
  assert.ok(/24 h/.test(r.message), r.message);
  assert.strictEqual(M.inmetSnapshotForecast(SNAP_INMET, at(COLETA + 5 * 24 * H)).reason, 'vencida');
});

test('M1-04 cópia sem collectedAt é recusada', () => {
  const s = copy(SNAP_INMET); delete s.collectedAt;
  const r = M.inmetSnapshotForecast(s, at(COLETA + H));
  assert.strictEqual(r.ok, false); assert.strictEqual(r.reason, 'data_invalida'); assert.strictEqual(r.days, null);
  assert.strictEqual(M.inmetSnapshotForecast(Object.assign(copy(SNAP_INMET), { collectedAt: null }), at(COLETA + H)).reason, 'data_invalida');
});

test('M1-05 collectedAt inválido (texto, data inexistente, número ou no futuro) é recusado', () => {
  for (const v of ['ontem', '2026-13-45T10:00:00Z', '', 1794673872000]) {
    const r = M.inmetSnapshotForecast(Object.assign(copy(SNAP_INMET), { collectedAt: v }), at(COLETA + H));
    assert.strictEqual(r.reason, 'data_invalida', `collectedAt=${v}`);
  }
  const futuro = M.inmetSnapshotForecast(SNAP_INMET, at(COLETA - H));
  assert.strictEqual(futuro.reason, 'data_invalida');
  assert.ok(/futuro/.test(futuro.message));
  // diferença pequena de relógio (até 5 min) é aceita
  assert.strictEqual(M.inmetSnapshotForecast(SNAP_INMET, at(COLETA - 4 * 60 * 1000), { today: '2026-11-14' }).ok, true);
});

test('M1-06 previsão com dias passados e futuros: só ficam hoje e os seguintes', () => {
  const s = snapCom('2026-11-16T09:00:00Z', { '14/11/2026': dia('14/11/2026'), '15/11/2026': dia('15/11/2026'), '16/11/2026': dia('16/11/2026'), '17/11/2026': dia('17/11/2026'), '18/11/2026': dia('18/11/2026') });
  const r = M.inmetSnapshotForecast(s, at(Date.parse('2026-11-16T12:00:00Z')), { today: '2026-11-16' });
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(r.days.map(d => d.date), ['16/11/2026', '17/11/2026', '18/11/2026']);
  assert.strictEqual(r.removedPast, 2);
  assert.strictEqual(r.days[0].date, '16/11/2026');            // o "hoje" exibido é de fato hoje
});

test('M1-07 previsão só com dias passados é recusada', () => {
  const s = snapCom('2026-11-16T09:00:00Z', { '14/11/2026': dia('14/11/2026'), '15/11/2026': dia('15/11/2026') });
  const r = M.inmetSnapshotForecast(s, at(Date.parse('2026-11-16T12:00:00Z')), { today: '2026-11-16' });
  assert.strictEqual(r.ok, false); assert.strictEqual(r.reason, 'sem_dias_atuais'); assert.strictEqual(r.days, null);
  assert.strictEqual(r.collectedAt, '2026-11-16T09:00:00Z');
});

test('M1-08 dia com manhã e sem tarde ou noite (e só noite) é lido sem erro', () => {
  const d = dia('14/11/2026');
  const s = snapCom(SNAP_INMET.collectedAt, { '14/11/2026': { manha: d.manha }, '15/11/2026': { noite: dia('15/11/2026').noite } });
  const days = M.parseInmetForecast(s.data);
  assert.deepStrictEqual(days[0].periods, { manha: d.manha.resumo });
  assert.strictEqual(days[0].temp_max, d.manha.temp_max);
  assert.strictEqual(days[0].resumo, d.manha.resumo);
  assert.deepStrictEqual(Object.keys(days[1].periods), ['noite']);
  const r = M.inmetSnapshotForecast(s, at(COLETA + H), { today: '2026-11-14' });
  assert.strictEqual(r.ok, true); assert.strictEqual(r.days.length, 2);
});

test('M1-09 estrutura inválida nunca lança erro: a cópia é recusada', () => {
  const quebra = new Proxy({}, { get() { throw new Error('acesso quebrado'); } });
  const casos = [
    null, undefined, 'texto', [], 42,
    { collectedAt: SNAP_INMET.collectedAt },
    { collectedAt: SNAP_INMET.collectedAt, data: null },
    { collectedAt: SNAP_INMET.collectedAt, data: {} },
    { collectedAt: SNAP_INMET.collectedAt, data: { '3148103': [] } },
    snapCom(SNAP_INMET.collectedAt, { '14/11/2026': null }),
    snapCom(SNAP_INMET.collectedAt, { '14/11/2026': { tarde: Object.assign(dia('14/11/2026').tarde, { temp_max: '31' }) } }),
    snapCom(SNAP_INMET.collectedAt, { '14/11/2026': { tarde: Object.assign(dia('14/11/2026').tarde, { dia_semana: undefined }) } }),
    snapCom(SNAP_INMET.collectedAt, { '14/11/2026': { tarde: Object.assign(dia('14/11/2026').tarde, { umidade_min: null }) } }),
    snapCom(SNAP_INMET.collectedAt, { '31/02/2026': dia('16/11/2026') }),
    snapCom(SNAP_INMET.collectedAt, { 'amanhã': dia('16/11/2026') }),
    quebra
  ];
  casos.forEach((c, i) => {
    let r;
    assert.doesNotThrow(() => { r = M.inmetSnapshotForecast(c, at(COLETA + H), { today: '2026-11-14' }); }, `caso ${i}`);
    assert.strictEqual(r.ok, false, `caso ${i}`);
    assert.strictEqual(r.days, null, `caso ${i}`);
  });
  assert.strictEqual(M.inmetSnapshotForecast(null, at(COLETA)).reason, 'ausente');
  assert.strictEqual(M.inmetSnapshotForecast(casos[9], at(COLETA + H)).reason, 'estrutura');
});

test('M1-10/11 cálculo da ferrugem nos dias válidos é o mesmo de antes', () => {
  const r = M.inmetSnapshotForecast(SNAP_INMET, at(COLETA + 2 * H), { today: '2026-11-14' });
  r.days.forEach(d => {
    const tarde = SNAP_INMET.data['3148103'][d.date].tarde || SNAP_INMET.data['3148103'][d.date];
    assert.deepStrictEqual(d.risk, M.rustFavorability(tarde));
  });
  assert.deepStrictEqual(r.days.filter(d => d.risk.favorable).map(d => d.date), ['16/11/2026', '17/11/2026']);
});

test('M1-12 leitura de previsão completa mantém o formato usado pela interface (consulta ao vivo)', () => {
  const days = M.parseInmetForecast(SNAP_INMET.data);
  assert.strictEqual(days.length, 5);
  const d0 = days[0], t0 = SNAP_INMET.data['3148103']['14/11/2026'].tarde;
  assert.deepStrictEqual(Object.keys(d0), ['date', 'weekday', 'resumo', 'temp_min', 'temp_max', 'umidade_min', 'umidade_max', 'vento', 'tendencia', 'periods', 'risk']);
  assert.strictEqual(d0.resumo, t0.resumo); assert.strictEqual(d0.vento, `${t0.int_vento} (${t0.dir_vento})`);
  assert.deepStrictEqual(Object.keys(d0.periods), ['manha', 'tarde', 'noite']);
  assert.strictEqual(days[4].periods, null);                     // dias sem períodos: como antes
});

test('M1-13 data de hoje no fuso local, sem erro de um dia por conversão para UTC', () => {
  assert.strictEqual(M.localDateISO(new Date(2026, 10, 14, 23, 30)), '2026-11-14');
  assert.strictEqual(M.localDateISO(new Date(2026, 10, 15, 0, 30)), '2026-11-15');
  assert.strictEqual(M.brDateToISO('14/11/2026'), '2026-11-14');
  assert.strictEqual(M.brDateToISO('31/02/2026'), null);
  const original = process.env.TZ;
  let aplicados = 0;
  try {
    for (const tz of ['America/Sao_Paulo', 'UTC', 'Asia/Tokyo']) {
      process.env.TZ = tz;
      // 23:30 do dia 14 no fuso local: em São Paulo já é dia 15 em UTC; o dia 14 tem de continuar
      const agora = new Date(2026, 10, 14, 23, 30);
      if (tz === 'America/Sao_Paulo' && agora.getTimezoneOffset() !== 180) continue;   // fuso não aplicado neste sistema
      aplicados++;
      const s = snapCom(new Date(2026, 10, 14, 20, 0).toISOString(), { '14/11/2026': dia('14/11/2026'), '15/11/2026': dia('15/11/2026') });
      const r = M.inmetSnapshotForecast(s, agora);
      assert.strictEqual(r.ok, true, tz);
      assert.strictEqual(r.days[0].date, '14/11/2026', tz);
      // 00:30 do dia 15 no fuso local: o dia 14 sai
      const r2 = M.inmetSnapshotForecast(s, new Date(2026, 10, 15, 0, 30));
      assert.deepStrictEqual(r2.days.map(d => d.date), ['15/11/2026'], tz);
    }
  } finally {
    if (original === undefined) delete process.env.TZ; else process.env.TZ = original;
  }
  assert.ok(aplicados >= 1);
});

/* --------------------------------------------------------------------------
 * Fluxo real da interface: js/app.js carregado num contexto isolado, com DOM mínimo,
 * data fixa e fetch simulado. Nada é gravado; nenhum arquivo do projeto é alterado.
 * ------------------------------------------------------------------------ */
class FakeEl {
  constructor() {
    this.innerHTML = ''; this.textContent = ''; this.className = ''; this.value = ''; this.disabled = false;
    this.style = {}; this.dataset = {}; this.options = [];
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }
  setAttribute() {} getAttribute() { return null; } addEventListener() {} appendChild() {} remove() {} scrollIntoView() {}
  querySelector() { return new FakeEl(); } querySelectorAll() { return []; } closest() { return null; } contains() { return false; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; }
}

function loadApp({ nowMs, inmetSnapshot, fetchImpl }) {
  const els = {};
  const listeners = {};                                          // document.addEventListener (B1: visibilitychange)
  const intervals = [];                                          // setInterval simulado (B1)
  const sandbox = {
    console: { info() {}, warn() {}, log() {}, error() {} },
    document: {
      getElementById: (id) => (els[id] = els[id] || new FakeEl()),
      createElement: () => new FakeEl(), querySelector: () => new FakeEl(), querySelectorAll: () => [],
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
      visibilityState: 'visible',
      body: new FakeEl()
    },
    localStorage: { getItem: () => null, setItem() {} },
    location: { hash: '' }, history: { replaceState() {} },
    innerWidth: 1280, innerHeight: 800,
    fetch: fetchImpl, AbortController, setTimeout, clearTimeout,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; }, clearInterval() {},
    addEventListener() {},                                       // window (resize/hashchange): usado só pelo init (B2)
    __now: nowMs                                                 // relógio da página; os testes do B1 o avançam
  };
  sandbox.window = sandbox; sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext('(() => { const R = Date; globalThis.Date = class extends R { constructor(...a) { if (a.length) super(...a); else super(globalThis.__now); } static now() { return globalThis.__now; } }; })();', sandbox);
  for (const f of ['js/data.js', SNAPSHOT_FIXTURE, 'js/metrics.js', 'js/charts.js']) vm.runInContext(read(f), sandbox, { filename: f });
  sandbox.RIGORA_SNAPSHOTS.inmet = inmetSnapshot === undefined ? sandbox.RIGORA_SNAPSHOTS.inmet : inmetSnapshot;
  vm.runInContext(read('js/app.js'), sandbox, { filename: 'js/app.js' });
  const app = vm.runInContext('RigoraApp', sandbox);
  return {
    app, state: app._state, el: (id) => els[id] || new FakeEl(), listeners, intervals,
    snap: sandbox.RIGORA_SNAPSHOTS,                              // cópia carregada pela página (B2: não pode mudar)
    setNow: (ms) => { sandbox.__now = ms; },
    setVisibility: (v) => { sandbox.document.visibilityState = v; },
    fire: (type) => (listeners[type] || []).forEach(fn => fn())
  };
}
const falhaAoVivo = async () => { throw new Error('Failed to fetch'); };
const climaAlertas = (state) => state.alerts.filter(a => a.cat === 'clima');

testAsync('M1-14 cópia vencida: tela indisponível, data da última cópia informada e nenhum alerta RULE-03', async () => {
  const { app, state, el } = loadApp({ nowMs: COLETA + 25 * H, fetchImpl: falhaAoVivo });
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'error');
  assert.strictEqual(state.inmet.days, null);
  assert.strictEqual(state.inmet.fallback.reason, 'vencida');
  assert.ok(el('weather-body').innerHTML.includes('Previsão indisponível agora'));
  assert.ok(el('weather-body').innerHTML.includes('não foi usada'), el('weather-body').innerHTML);
  assert.ok(!el('weather-status-badge').innerHTML.includes('Consultando'));
  assert.strictEqual(climaAlertas(state).length, 0);
  const dq = state.alerts.find(a => a.id === 'dq-inmet-error');
  assert.ok(dq && dq.desc.includes('não foi usada'), dq && dq.desc);
  assert.ok(el('automations-grid').innerHTML.includes('Aguardando previsão'));
});

testAsync('M1-15 cópia válida: selo de cópia com a data e RULE-03 calculada como antes', async () => {
  const { app, state, el } = loadApp({ nowMs: COLETA + 2 * H, fetchImpl: falhaAoVivo });
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(state.inmet.collectedAt, SNAP_INMET.collectedAt);
  assert.ok(el('weather-status-badge').innerHTML.includes('Cópia de'));
  assert.ok(el('weather-body').innerHTML.includes('última previsão oficial salva'));
  const clima = climaAlertas(state);
  assert.strictEqual(clima.length, 1);
  assert.ok(clima[0].desc.startsWith('16/11, 17/11:'), clima[0].desc);
  assert.strictEqual(clima[0].priority, 'baixa');
});

testAsync('M1-16 cópia malformada ou com acesso quebrado: a tela sai de "Consultando INMET…"', async () => {
  const quebrada = new Proxy({}, { get() { throw new Error('acesso quebrado'); } });
  for (const snap of [snapCom(SNAP_INMET.collectedAt, { '14/11/2026': null }), { collectedAt: SNAP_INMET.collectedAt, data: 'x' }, quebrada]) {
    const { app, state, el } = loadApp({ nowMs: COLETA + H, inmetSnapshot: snap, fetchImpl: falhaAoVivo });
    await app.fetchInmetForecast();
    assert.strictEqual(state.inmet.status, 'error');
    assert.ok(!el('weather-status-badge').innerHTML.includes('Consultando'), el('weather-status-badge').innerHTML);
    assert.ok(el('weather-status-badge').innerHTML.includes('Indisponível'));
    assert.ok(el('weather-body').innerHTML.includes('Previsão indisponível agora'));
    assert.strictEqual(climaAlertas(state).length, 0);
    assert.ok(state.alerts.some(a => a.id === 'dq-inmet-error'));        // a central de alertas foi atualizada
  }
});

testAsync('M1-17 sem cópia salva: indisponível, como antes', async () => {
  const { app, state, el } = loadApp({ nowMs: COLETA + H, inmetSnapshot: null, fetchImpl: falhaAoVivo });
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'error');
  assert.ok(!el('weather-body').innerHTML.includes('não foi usada'));
  assert.ok(el('weather-body').innerHTML.includes('Previsão indisponível agora'));
});

testAsync('M1-18 consulta ao vivo válida: aceita como ao vivo, só com os dias de hoje em diante', async () => {
  // Ajustado na correção pós-B1: antes este teste exigia aceitar dias já passados (defeito confirmado na auditoria)
  const resposta = copy(SNAP_INMET.data);
  const agora = new Date(2026, 10, 14, 12, 0).getTime();          // 14/11 12:00 no fuso local: os 5 dias são de hoje em diante
  const { app, state, el } = loadApp({ nowMs: agora, fetchImpl: async () => ({ ok: true, json: async () => resposta }) });
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(state.inmet.days.length, 5);
  assert.strictEqual(state.inmet.days[0].date, '14/11/2026');
  assert.strictEqual(state.inmet.fallback, undefined);
  assert.ok(el('weather-status-badge').innerHTML.includes('Ao vivo'));
  assert.strictEqual(climaAlertas(state).length, 1);
});

/* ==========================================================================
 * B1 (Etapa 5B / M2): previsão do INMET reavaliada durante a sessão
 * Relógio da página, intervalos, visibilidade e rede simulados. Nenhuma consulta real ao INMET.
 * ========================================================================== */
const MIN = 60 * 1000;
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
const badge = (el) => el('weather-status-badge').innerHTML.replace(/<[^>]*>/g, '').trim();   // texto visível do selo
const unicos = (state) => new Set(state.alerts.map(a => a.id)).size === state.alerts.length;

/** Rede simulada: 'ok', 'falha' ou 'lenta' (fica pendente até o teste resolver). */
function rede(modo = 'ok') {
  const r = { modo, chamadas: 0, pendentes: [], dados: () => copy(SNAP_INMET.data) };
  r.fetch = () => {
    r.chamadas++;
    if (r.modo === 'ok') return Promise.resolve({ ok: true, json: async () => r.dados() });
    if (r.modo === 'falha') return Promise.reject(new Error('Failed to fetch'));
    return new Promise((res, rej) => r.pendentes.push({
      ok: () => res({ ok: true, json: async () => r.dados() }), falha: () => rej(new Error('Failed to fetch'))
    }));
  };
  return r;
}
function abrir(nowMs, net, opts = {}) {
  const ctx = loadApp(Object.assign({ nowMs, fetchImpl: (...a) => net.fetch(...a) }, opts));
  ctx.tick = () => ctx.intervals.forEach(i => i.fn());          // dispara a verificação periódica
  return ctx;
}

test('B1-U1 reavaliação: decide quando consultar de novo, com as regras da M1 (horários locais)', () => {
  const live = (ms) => ({ status: 'live', collectedAt: new Date(ms).toISOString(), days: M.parseInmetForecast(SNAP_INMET.data), raw: { collectedAt: new Date(ms).toISOString(), data: SNAP_INMET.data } });
  const t0 = new Date(2026, 10, 14, 15, 0).getTime();                 // 14/11 15:00 no fuso local
  assert.strictEqual(M.inmetRecheck({ status: 'loading' }, at(t0)).fetch, false);
  assert.strictEqual(M.inmetRecheck({ status: 'error', days: null }, at(t0)).fetch, true);
  const cedo = M.inmetRecheck(live(t0), at(t0 + 10 * MIN));
  assert.strictEqual(cedo.fetch, false); assert.strictEqual(cedo.state, null);                              // atualizado
  assert.strictEqual(M.inmetRecheck(live(t0), at(t0 + 30 * MIN)).fetch, true);                              // 30 min
  const v0 = new Date(2026, 10, 14, 23, 50).getTime();
  const virada = M.inmetRecheck(live(v0), at(new Date(2026, 10, 15, 0, 0).getTime()));                    // 10 min depois, outro dia
  assert.strictEqual(virada.fetch, true);
  assert.strictEqual(virada.state.days[0].date, '15/11/2026');                                              // 14/11 sai na hora
  const venceu = M.inmetRecheck(live(t0), at(t0 + 25 * H));                                               // sem reserva
  assert.strictEqual(venceu.state.status, 'error'); assert.strictEqual(venceu.state.days, null); assert.strictEqual(venceu.fetch, true);
  // ao vivo com 25 h vence; a cópia salva com 23 h ainda vale e assume (instantes absolutos)
  const troca = M.inmetRecheck(live(COLETA - 2 * H), at(COLETA + 23 * H), { candidates: [{ origin: 'cópia salva', copy: SNAP_INMET, always: true }] });
  assert.strictEqual(troca.state.status, 'snapshot'); assert.strictEqual(troca.state.fallback.origin, 'cópia salva'); assert.strictEqual(troca.fetch, true);
  assert.strictEqual(M.INMET_RECHECK_MINUTES, 30);
  assert.doesNotThrow(() => M.inmetRecheck(new Proxy({}, { get() { throw new Error('x'); } }), at(t0)));
});

test('B1-U2 cópia de reserva: a válida mais recente; vencidas nunca são usadas', () => {
  const t0 = COLETA + 5 * H;
  const anterior = { collectedAt: new Date(COLETA + 4 * H).toISOString(), data: SNAP_INMET.data };
  const c = (cop) => [{ origin: 'consulta anterior', copy: cop }, { origin: 'cópia salva', copy: SNAP_INMET, always: true }];
  const r = M.pickInmetCopy(c(anterior), at(t0), { today: '2026-11-14' });
  assert.strictEqual(r.ok, true); assert.strictEqual(r.origin, 'consulta anterior'); assert.strictEqual(r.copy, anterior);
  assert.strictEqual(M.pickInmetCopy(c(null), at(t0), { today: '2026-11-14' }).origin, 'cópia salva');
  const tarde = M.pickInmetCopy(c(anterior), at(COLETA + 30 * H));
  assert.strictEqual(tarde.ok, false); assert.strictEqual(tarde.reason, 'vencida');
  assert.strictEqual(M.pickInmetCopy([{ origin: 'cópia salva', copy: null, always: true }], at(t0)).reason, 'ausente');
});

test('B1-14a selo: hora quando a coleta é de hoje; data e hora quando é de outro dia (fuso local)', () => {
  const original = process.env.TZ;
  try {
    for (const tz of ['America/Sao_Paulo', 'UTC', 'Asia/Tokyo']) {
      process.env.TZ = tz;
      const coleta = new Date(2026, 10, 14, 23, 50).toISOString();
      const hoje = M.collectionStamp(coleta, new Date(2026, 10, 14, 23, 59));
      assert.deepStrictEqual([hoje.day, hoje.time, hoje.sameDay], ['14/11', '23:50', true], tz);
      const amanha = M.collectionStamp(coleta, new Date(2026, 10, 15, 0, 20));
      assert.deepStrictEqual([amanha.day, amanha.time, amanha.sameDay], ['14/11', '23:50', false], tz);
    }
  } finally {
    if (original === undefined) delete process.env.TZ; else process.env.TZ = original;
  }
});

testAsync('B1-01 inicialização normal: consulta ao vivo, um intervalo de 30 min e um listener', async () => {
  const net = rede('ok'), t0 = COLETA + 2 * H;
  const { app, state, el, intervals, listeners } = abrir(t0, net);
  await app.fetchInmetForecast();
  app.startInmetAutoRefresh();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(intervals.length, 1); assert.strictEqual(intervals[0].ms, 30 * MIN);
  assert.strictEqual(listeners.visibilitychange.length, 1);
  const t = M.collectionStamp(new Date(t0).toISOString(), new Date(t0));
  assert.ok(badge(el).includes(`Ao vivo · ${t.time}`) && !badge(el).includes('/'), badge(el));
});

testAsync('B1-02 relógio avança 25 h: a previsão ao vivo deixa de valer e a rede falha', async () => {
  const net = rede('ok'), t0 = COLETA + 2 * H;
  const { app, state, el, setNow, tick } = abrir(t0, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  net.modo = 'falha'; setNow(t0 + 25 * H); tick(); await settle();
  assert.strictEqual(net.chamadas, 2);
  assert.strictEqual(state.inmet.status, 'error'); assert.strictEqual(state.inmet.days, null);
  assert.strictEqual(climaAlertas(state).length, 0);
  assert.ok(badge(el).includes('Indisponível'));
  assert.ok(el('weather-body').innerHTML.includes('Previsão indisponível agora'));
});

testAsync('B1-03/13 cópia válida que vence durante a sessão: sai da tela e da RULE-03', async () => {
  const net = rede('falha');
  const { app, state, el, setNow, tick } = abrir(COLETA + 2 * H, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(climaAlertas(state).length, 1);
  assert.ok(el('automations-grid').innerHTML.includes('dia(s) favorável(is)'));
  setNow(COLETA + 24 * H + MIN); tick(); await settle();
  assert.strictEqual(state.inmet.status, 'error');
  assert.strictEqual(state.inmet.fallback.reason, 'vencida');
  assert.strictEqual(climaAlertas(state).length, 0);
  assert.ok(el('automations-grid').innerHTML.includes('Aguardando previsão'));
  assert.ok(el('weather-body').innerHTML.includes('não foi usada'));
});

testAsync('B1-04 retorno à aba depois de mais de 24 h: reavalia oculta e consulta ao voltar', async () => {
  const net = rede('falha');
  const { app, state, setNow, setVisibility, fire, tick } = abrir(COLETA + 2 * H, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  setVisibility('hidden'); setNow(COLETA + 30 * H); tick(); await settle();
  assert.strictEqual(net.chamadas, 1);                          // oculta: só reavalia, sem consultar
  assert.strictEqual(state.inmet.status, 'error');               // mas a cópia vencida já saiu
  assert.strictEqual(climaAlertas(state).length, 0);
  setVisibility('visible'); fire('visibilitychange'); await settle();
  assert.strictEqual(net.chamadas, 2);                          // ao voltar, consulta de novo
  assert.strictEqual(state.inmet.status, 'error');
});

testAsync('B1-05 retorno à aba com dados válidos: sem consulta desnecessária', async () => {
  const net = rede('ok'), t0 = COLETA + 2 * H;
  const { app, state, setNow, fire } = abrir(t0, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  const antes = state.inmet.collectedAt;
  setNow(t0 + 10 * MIN); fire('visibilitychange'); await settle();
  assert.strictEqual(net.chamadas, 1); assert.strictEqual(state.inmet.collectedAt, antes);
  setNow(t0 + 31 * MIN); fire('visibilitychange'); await settle();
  assert.strictEqual(net.chamadas, 2); assert.notStrictEqual(state.inmet.collectedAt, antes);   // passou de 30 min
});

testAsync('B1-06 consulta ao vivo bem-sucedida na reavaliação substitui a cópia', async () => {
  const net = rede('falha');
  const { app, state, el, setNow, tick } = abrir(COLETA + 2 * H, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.strictEqual(state.inmet.status, 'snapshot');
  net.modo = 'ok'; setNow(COLETA + 2 * H + 30 * MIN); tick(); await settle();
  assert.strictEqual(state.inmet.status, 'live');
  assert.ok(badge(el).includes('Ao vivo'));
  assert.strictEqual(state.alerts.filter(a => a.id.startsWith('dq-inmet')).length, 0);
});

testAsync('B1-07 consulta ao vivo indisponível: usa a última consulta ao vivo válida, com data', async () => {
  const net = rede('ok'), t0 = COLETA + 2 * H;
  const { app, state, el, setNow, tick } = abrir(t0, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  net.modo = 'falha'; setNow(t0 + 40 * MIN); tick(); await settle();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(state.inmet.fallback.origin, 'consulta anterior');
  assert.strictEqual(state.inmet.collectedAt, new Date(t0).toISOString());
  const t = M.collectionStamp(new Date(t0).toISOString(), new Date(t0));
  assert.ok(badge(el).includes(`Cópia de ${t.day} · ${t.time}`), badge(el));
  assert.ok(el('weather-body').innerHTML.includes('última previsão oficial salva'));
});

testAsync('B1-08/14b consulta ao vivo de ontem: na virada do dia o dia passado sai e o selo mostra a data', async () => {
  const net = rede('ok'), t0 = new Date(2026, 10, 14, 23, 50).getTime();   // horário local
  const { app, state, el, setNow, tick } = abrir(t0, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.ok(badge(el).includes('Ao vivo · 23:50') && !badge(el).includes('/'), badge(el));
  net.modo = 'lenta'; setNow(new Date(2026, 10, 15, 0, 20).getTime()); tick(); await settle();
  assert.strictEqual(net.pendentes.length, 1);                   // nova consulta em andamento
  assert.strictEqual(state.inmet.days[0].date, '15/11/2026');    // enquanto isso, o 14/11 já saiu
  assert.ok(badge(el).includes('Ao vivo · 14/11 · 23:50'), badge(el));
  assert.ok(climaAlertas(state)[0].desc.startsWith('16/11, 17/11:'));
  net.pendentes[0].falha(); await settle();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(state.inmet.fallback.origin, 'consulta anterior');
  assert.strictEqual(state.inmet.days[0].date, '15/11/2026');
  assert.ok(badge(el).includes('Cópia de 14/11 · 23:50'), badge(el));
});

testAsync('B1-09 falha de rede depois de a cópia vencer: a cópia vencida não é reutilizada', async () => {
  const net = rede('falha');
  const { app, state, setNow } = abrir(COLETA + 2 * H, net);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'snapshot');
  setNow(COLETA + 25 * H);
  await app.fetchInmetForecast();                                // "Tentar novamente"
  assert.strictEqual(state.inmet.status, 'error');
  assert.strictEqual(state.inmet.days, null);
  assert.strictEqual(state.inmet.fallback.reason, 'vencida');
  assert.strictEqual(state.inmet.fallback.collectedAt, SNAP_INMET.collectedAt);
});

testAsync('B1-10 atualizações simultâneas: uma única consulta', async () => {
  const net = rede('lenta');
  const { app, state, fire, tick } = abrir(COLETA + 2 * H, net);
  app.startInmetAutoRefresh();
  const p1 = app.fetchInmetForecast(), p2 = app.fetchInmetForecast();
  app.checkInmetForecast('teste'); fire('visibilitychange'); tick(); await settle();
  assert.strictEqual(p1, p2);
  assert.strictEqual(net.chamadas, 1);
  net.pendentes[0].ok(); await p1; await settle();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(state.integrationLog.filter(l => l.message.startsWith('Previsão recebida')).length, 1);
});

testAsync('B1-11 resposta atrasada: avaliada no momento em que chega, sem reaproveitar cópia vencida', async () => {
  const net = rede('falha');
  const { app, state, setNow, tick } = abrir(COLETA + 23 * H, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.strictEqual(state.inmet.status, 'snapshot');           // cópia com 23 h
  net.modo = 'lenta'; setNow(COLETA + 23 * H + 59 * MIN); tick(); await settle();
  assert.strictEqual(net.chamadas, 2);                           // consulta lenta em andamento
  setNow(COLETA + 24 * H + 2 * MIN); tick(); await settle();     // nova verificação durante a espera
  assert.strictEqual(net.chamadas, 2);                           // não abre outra consulta
  assert.strictEqual(state.inmet.status, 'error');               // a cópia já venceu e saiu
  net.pendentes[0].falha(); await settle();                      // a resposta atrasada chega (falha)
  assert.strictEqual(state.inmet.status, 'error');
  assert.strictEqual(state.inmet.fallback.reason, 'vencida');
  assert.strictEqual(climaAlertas(state).length, 0);
});

testAsync('B1-12 verificação periódica sem intervalos nem listeners duplicados', async () => {
  const net = rede('ok');
  const { app, intervals, listeners, tick } = abrir(COLETA + 2 * H, net);
  await app.fetchInmetForecast();
  app.startInmetAutoRefresh(); app.startInmetAutoRefresh(); app.startInmetAutoRefresh();
  tick(); tick(); await settle();
  assert.strictEqual(intervals.length, 1);
  assert.strictEqual(listeners.visibilitychange.length, 1);
});

testAsync('B1-15 sem alertas duplicados ao longo de várias reavaliações', async () => {
  const net = rede('falha');
  const { app, state, setNow, fire, tick } = abrir(COLETA + 2 * H, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  for (const h of [2.5, 3, 10, 20, 26]) {
    setNow(COLETA + h * H); tick(); fire('visibilitychange'); await settle();
    assert.ok(unicos(state), `ids repetidos em +${h} h`);
    assert.ok(climaAlertas(state).length <= 1);
    assert.ok(state.alerts.filter(a => a.id.startsWith('dq-inmet')).length <= 1);
  }
  assert.strictEqual(state.inmet.status, 'error');
});

/* ==========================================================================
 * Correção pós-B1: a resposta ao vivo passa pela mesma regra de data da M1
 * (só dias de hoje em diante, no calendário local). Relógio e rede simulados.
 * ========================================================================== */
const datas = (state) => (state.inmet.days || []).map(d => d.date.slice(0, 5)).join(',');
/** Resposta da API com os registros reais da cópia, sob as datas pedidas: [[data nova, dia de origem], ...] */
const respostaCom = (pares) => ({ '3148103': Object.fromEntries(pares.map(([nova, origem]) => [nova, dia(origem)])) });
const respondeCom = (net, dados) => { net.dados = () => copy(dados); };

testAsync('PB1-01 dia passado, hoje e dois futuros: o passado sai do card e da RULE-03', async () => {
  const net = rede('ok');
  // 14/11 recebe o registro de 16/11 (favorável à ferrugem): se o dia passado vazasse, apareceria no alerta
  respondeCom(net, respostaCom([['14/11/2026', '16/11/2026'], ['15/11/2026', '15/11/2026'], ['16/11/2026', '16/11/2026'], ['17/11/2026', '17/11/2026']]));
  const { app, state, el } = abrir(new Date(2026, 10, 15, 10, 0).getTime(), net);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(datas(state), '15/11,16/11,17/11');
  assert.ok(climaAlertas(state)[0].desc.startsWith('16/11, 17/11:'), climaAlertas(state)[0].desc);
  assert.ok(!climaAlertas(state)[0].desc.includes('14/11'));
  assert.ok(el('automations-grid').innerHTML.includes('2 dia(s) favorável(is)'));
  assert.ok(!el('weather-body').innerHTML.includes('14/11'));
});

testAsync('PB1-02a só dias passados, com reserva válida: a resposta é recusada e a reserva assume', async () => {
  const net = rede('ok');
  respondeCom(net, respostaCom([['20/11/2026', '16/11/2026'], ['21/11/2026', '17/11/2026'], ['22/11/2026', '18/11/2026']]));
  const t0 = new Date(2026, 10, 20, 8, 0).getTime();
  const { app, state, el, setNow } = abrir(t0, net, { inmetSnapshot: null });
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'live');
  respondeCom(net, SNAP_INMET.data);                             // agora a API devolve só 14 a 18/11
  setNow(t0 + 60 * MIN);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(state.inmet.fallback.origin, 'consulta anterior');
  assert.ok(/só trouxe dias que já passaram/.test(state.inmet.message), state.inmet.message);
  assert.strictEqual(datas(state), '20/11,21/11,22/11');
  assert.ok(!badge(el).startsWith('Ao vivo'));
});

testAsync('PB1-02b só dias passados, sem reserva válida: previsão indisponível, nada passado na tela', async () => {
  const net = rede('ok');                                        // API devolve 14 a 18/11
  const { app, state, el } = abrir(new Date(2026, 10, 20, 9, 0).getTime(), net);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'error');               // a cópia salva (14/11) também já venceu
  assert.strictEqual(state.inmet.days, null);
  assert.strictEqual(state.inmet.fallback.reason, 'vencida');
  assert.ok(/só trouxe dias que já passaram/.test(state.inmet.message));
  assert.strictEqual(climaAlertas(state).length, 0);
  assert.ok(badge(el).includes('Indisponível'));
  assert.ok(el('weather-body').innerHTML.includes('Previsão indisponível agora'));
});

testAsync('PB1-03 reavaliação com a mesma resposta: os dias passados não voltam', async () => {
  const net = rede('ok');                                        // API sempre devolve 14 a 18/11
  const { app, state, el, setNow, tick } = abrir(new Date(2026, 10, 17, 0, 10).getTime(), net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.strictEqual(datas(state), '17/11,18/11');
  for (const [h, m] of [[0, 45], [1, 20], [9, 0]]) {
    setNow(new Date(2026, 10, 17, h, m).getTime()); tick(); await settle();
    assert.strictEqual(state.inmet.status, 'live');
    assert.strictEqual(datas(state), '17/11,18/11', `${h}:${m}`);
    assert.ok(climaAlertas(state)[0].desc.startsWith('17/11:'), climaAlertas(state)[0].desc);
    assert.ok(!el('weather-body').innerHTML.includes('16/11'));
  }
  assert.ok(net.chamadas >= 3);                                  // consultou de novo e recebeu os mesmos dias
});

testAsync('PB1-04 virada do dia com a página aberta: a nova data local vale para o card e os alertas', async () => {
  const net = rede('ok');
  const { app, state, el, setNow, tick } = abrir(new Date(2026, 10, 14, 23, 50).getTime(), net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.strictEqual(datas(state), '14/11,15/11,16/11,17/11,18/11');
  setNow(new Date(2026, 10, 15, 0, 20).getTime()); tick(); await settle();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(datas(state), '15/11,16/11,17/11,18/11');
  assert.ok(el('weather-body').innerHTML.includes('15/11'));
  assert.ok(climaAlertas(state)[0].desc.startsWith('16/11, 17/11:'));
  setNow(new Date(2026, 10, 17, 0, 5).getTime()); tick(); await settle();
  assert.strictEqual(datas(state), '17/11,18/11');
  assert.ok(climaAlertas(state)[0].desc.startsWith('17/11:'));
});

testAsync('PB1-05 reserva válida: coleta original preservada e sujeita às 24 h da M1', async () => {
  const net = rede('ok');
  const t0 = new Date(2026, 10, 15, 10, 0).getTime();
  const { app, state, el, setNow, tick } = abrir(t0, net, { inmetSnapshot: null });
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  net.modo = 'falha'; setNow(t0 + 40 * MIN); tick(); await settle();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(state.inmet.fallback.origin, 'consulta anterior');
  assert.strictEqual(state.inmet.collectedAt, new Date(t0).toISOString());
  assert.strictEqual(datas(state), '15/11,16/11,17/11,18/11');
  assert.ok(badge(el).startsWith('Cópia de 15/11 · 10:00'), badge(el));
  setNow(t0 + 24 * H + MIN); tick(); await settle();
  assert.strictEqual(state.inmet.status, 'error');
  assert.strictEqual(state.inmet.fallback.reason, 'vencida');
  assert.strictEqual(climaAlertas(state).length, 0);
});

testAsync('PB1-06 reserva incompleta é recusada: usa a cópia salva válida ou fica indisponível', async () => {
  const incompleta = copy(SNAP_INMET.data);
  delete incompleta['3148103']['18/11/2026'].umidade_min;       // a M1 aceita assim ao vivo, mas não como cópia
  for (const comCopia of [true, false]) {
    const net = rede('ok'); respondeCom(net, incompleta);
    const t0 = new Date(2026, 10, 15, 10, 0).getTime();
    const { app, state, setNow } = abrir(t0, net, comCopia ? {} : { inmetSnapshot: null });
    await app.fetchInmetForecast();
    assert.strictEqual(state.inmet.status, 'live');
    net.modo = 'falha'; setNow(t0 + 40 * MIN);
    await app.fetchInmetForecast();
    if (comCopia) {
      assert.strictEqual(state.inmet.status, 'snapshot');
      assert.strictEqual(state.inmet.fallback.origin, 'cópia salva');
      assert.strictEqual(state.inmet.collectedAt, SNAP_INMET.collectedAt);
    } else {
      assert.strictEqual(state.inmet.status, 'error');
      assert.strictEqual(state.inmet.days, null);
      assert.strictEqual(state.inmet.fallback.ok, false);        // reserva incompleta não foi usada
      assert.strictEqual(climaAlertas(state).length, 0);
      // a reserva incompleta, sozinha, é recusada por estrutura (regra da M1)
      assert.strictEqual(M.pickInmetCopy([{ origin: 'consulta anterior', copy: { collectedAt: new Date(t0).toISOString(), data: incompleta } }], at(t0 + 40 * MIN)).reason, 'estrutura');
    }
  }
});

/* ==========================================================================
 * B2 (Etapa 5B / M2): selo do IBGE. A conferência rápida só compara o rendimento de Patrocínio;
 * nunca troca parte da cópia. Rede simulada: nenhuma consulta real ao IBGE nem ao INMET.
 * ========================================================================== */
const PATRO = '3148103';
const SIDRA_FIX = loadSnapshots().sidra;
const Y_PATRO = SIDRA_FIX.municipios.find(m => m.c === PATRO).y;
const ANOS_PATRO = Object.keys(Y_PATRO).sort();
const sidraRapida = (pares) => [{ D3C: 'Ano (Código)', V: 'Valor' }, ...pares.map(([ano, V]) => ({ D3C: ano, V }))];
const respostaIgual = () => sidraRapida(ANOS_PATRO.map(a => [a, String(Y_PATRO[a][0])]));
const respostaIbgeCom = (trocas) => sidraRapida(ANOS_PATRO.map(a => [a, a in trocas ? trocas[a] : String(Y_PATRO[a][0])]));
const seloIbge = (el) => el('regional-status-badge').innerHTML.replace(/<[^>]*>/g, '').trim();

/** Rede: SIDRA rápido e completo respondem conforme o cenário; INMET sempre falha (fora do escopo do B2). */
function redeIbge({ rapida, completa } = {}) {
  const r = { urls: [] };
  const responder = (x) => {
    if (x === 'falha') return Promise.reject(new Error('Failed to fetch'));
    if (x === 'http500') return Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
    return Promise.resolve({ ok: true, json: async () => copy(x) });
  };
  r.fetch = (url) => {
    r.urls.push(String(url));
    if (!String(url).includes('apisidra.ibge.gov.br')) return Promise.reject(new Error('Failed to fetch'));
    return responder(String(url).includes('/n6/3148103/') ? rapida : completa);
  };
  return r;
}
async function abrirPagina(cenario) {
  const net = redeIbge(cenario);
  const ctx = loadApp({ nowMs: COLETA + H, fetchImpl: (...a) => net.fetch(...a) });
  ctx.fire('DOMContentLoaded');                                  // init(): inclui refreshIbge(false)
  await settle();
  ctx.net = net;
  return ctx;
}
const coerente = (t) => t[0] === null || Math.round(t[2] * 1000 / t[1]) === t[0];    // rendimento = produção / área
const JSON_FIX = JSON.stringify(SIDRA_FIX);
function semAlteracao(state, snap) {
  assert.strictEqual(JSON.stringify(state.ibge.municipios), JSON.stringify(SIDRA_FIX.municipios), 'dados do IBGE em tela foram alterados');
  assert.strictEqual(JSON.stringify(state.ibge.mg), JSON.stringify(SIDRA_FIX.mg), 'média de MG foi alterada');
  assert.strictEqual(JSON.stringify(snap.sidra), JSON_FIX, 'a cópia salva em memória foi alterada');
}

test('B2-00 fixture: em Patrocínio o rendimento da cópia é coerente com produção / área em todos os anos', () => {
  assert.strictEqual(ANOS_PATRO.length, 10);
  assert.deepStrictEqual([...Y_PATRO['2025']], [1110, 43850, 48669]);
  ANOS_PATRO.forEach(a => assert.ok(coerente(Y_PATRO[a]), a));
});

testAsync('B2-01 concordância: selo "conferidos ao vivo", nenhum dado muda, sem aviso', async () => {
  const { state, el, snap, net } = await abrirPagina({ rapida: respostaIgual() });
  assert.ok(net.urls.some(u => u.includes('/n6/3148103/')), 'a conferência rápida não foi feita');
  assert.strictEqual(state.ibge.status, 'checked');
  assert.ok(seloIbge(el).startsWith('Dados de Patrocínio conferidos ao vivo · '), seloIbge(el));
  assert.ok(el('regional-status-badge').className.includes('success'));
  assert.strictEqual(state.ibge.conferencia.anos.length, 10);
  assert.strictEqual(state.ibge.conferencia.divergencias.length, 0);
  assert.strictEqual(state.ibge.conferencia.faltantes.length, 0);                       // ajuste de cobertura
  assert.ok(el('regional-status-badge').title.includes('Conferidos 10 de 10 anos esperados'), el('regional-status-badge').title);
  semAlteracao(state, snap);
  assert.ok(el('data-sources-grid').innerHTML.includes('Dados de Patrocínio conferidos ao vivo'));
  assert.ok(!el('data-sources-grid').innerHTML.includes('Cópia conferida ao vivo'));
  assert.ok(!state.alerts.some(a => a.id.startsWith('dq-ibge')));
});

testAsync('B2-02 divergência (2025: 1110 → 1200): aviso, nada é trocado, nenhum selo positivo', async () => {
  const { state, el, snap } = await abrirPagina({ rapida: respostaIbgeCom({ '2025': '1200' }) });
  assert.strictEqual(state.ibge.status, 'divergent');
  const selo = seloIbge(el);
  assert.ok(selo.startsWith('Dados de Patrocínio divergem do IBGE · '), selo);
  assert.ok(!/conferid/i.test(selo), selo);
  assert.ok(el('regional-status-badge').className.includes('warning'));
  assert.ok(!el('regional-status-badge').className.includes('success'));
  const t = el('regional-status-badge').title;
  assert.ok(t.includes('2025') && t.includes('1.110') && t.includes('1.200') && t.includes('Nenhum valor foi trocado'), t);
  assert.deepStrictEqual(copy(state.ibge.conferencia.divergencias), [{ ano: '2025', copia: 1110, aoVivo: 1200 }]);
  const p = state.ibge.municipios.find(m => m.c === PATRO).y;
  assert.deepStrictEqual([...p['2025']], [1110, 43850, 48669]);
  ANOS_PATRO.forEach(a => assert.ok(coerente(p[a]), a));
  semAlteracao(state, snap);
  const fontes = el('data-sources-grid').innerHTML;
  assert.ok(fontes.includes('Dados de Patrocínio divergem do IBGE'));
  assert.ok(!fontes.includes('conferidos ao vivo') && !fontes.includes('Cópia conferida ao vivo'));
  const av = state.alerts.find(a => a.id === 'dq-ibge-divergente');
  assert.ok(av, 'divergência não virou aviso');
  assert.strictEqual(av.priority, 'media');
  assert.ok(av.desc.includes('2025') && av.desc.includes('Nenhum valor foi trocado'), av.desc);
  assert.ok(state.integrationLog.some(l => l.source === 'IBGE' && l.message.includes('1 de 10 ano(s) divergem')));
});

testAsync('B2-03 ausência de dados (vazia, só cabeçalho, anos sem número): nada de "conferidos"', async () => {
  const semNumero = sidraRapida(ANOS_PATRO.map((a, i) => [a, ['...', '-', '', 'X'][i % 4]]));
  for (const [rapida, msg] of [[[], 'resposta vazia'], [sidraRapida([]), 'resposta vazia'], [semNumero, 'sem valores'], [{ erro: 1 }, 'resposta vazia']]) {
    const { state, el, snap } = await abrirPagina({ rapida });
    assert.strictEqual(state.ibge.status, 'snapshot', msg);
    assert.ok(state.ibge.message.includes(msg), state.ibge.message);
    assert.ok(seloIbge(el).startsWith('Cópia oficial de'), seloIbge(el));
    assert.ok(!/conferid|divergem/i.test(seloIbge(el)));
    assert.ok(state.alerts.some(a => a.id === 'dq-ibge-snapshot'));
    assert.ok(!state.alerts.some(a => a.id === 'dq-ibge-divergente'));
    semAlteracao(state, snap);
  }
});

testAsync('B2-04 falha de consulta (rede e HTTP 500): cópia oficial datada, sem validação positiva', async () => {
  for (const rapida of ['falha', 'http500']) {
    const { state, el, snap } = await abrirPagina({ rapida });
    assert.strictEqual(state.ibge.status, 'snapshot', rapida);
    assert.ok(state.ibge.message, 'motivo da falha não registrado');
    assert.ok(seloIbge(el).startsWith('Cópia oficial de'), seloIbge(el));
    assert.ok(el('regional-status-badge').title.startsWith('Última falha:'));
    assert.ok(el('data-sources-grid').innerHTML.includes('Cópia oficial (API falhou)'));
    assert.ok(!/conferidos ao vivo|divergem do IBGE/.test(el('data-sources-grid').innerHTML));
    semAlteracao(state, snap);
  }
});

testAsync('B2-05 resposta parcial: só os anos recebidos são comparados; um divergente basta para o aviso', async () => {
  // Ajustado no ajuste de cobertura do B2: antes exigia 'checked' para resposta parcial (selo exagerava a cobertura)
  const parcialIgual = sidraRapida([['2024', String(Y_PATRO['2024'][0])], ['2025', '1110']]);
  let ctx = await abrirPagina({ rapida: parcialIgual });
  assert.strictEqual(ctx.state.ibge.status, 'partial');
  assert.deepStrictEqual([...ctx.state.ibge.conferencia.anos], ['2024', '2025']);
  semAlteracao(ctx.state, ctx.snap);
  const parcialDiverg = sidraRapida([['2023', String(Y_PATRO['2023'][0])], ['2025', '1200'], ['2025', '1110']]);   // ano repetido: vale o primeiro
  ctx = await abrirPagina({ rapida: parcialDiverg });
  assert.strictEqual(ctx.state.ibge.status, 'divergent');
  assert.strictEqual(ctx.state.ibge.conferencia.anos.length, 2);
  semAlteracao(ctx.state, ctx.snap);
});

testAsync('B2-06 atualização completa continua como antes ("Atualizado ao vivo"), sem alterar a cópia salva', async () => {
  const completa = [{ D1C: 'Município', D2C: 'Variável', D3C: 'Ano', V: 'Valor' }];
  const novo = { '112': '1200', '216': '40000', '214': '48000' };                    // trio coerente: 48000 t / 40000 ha = 1200 kg/ha
  Object.entries(novo).forEach(([v, V]) => completa.push({ D1C: PATRO, D2C: v, D3C: '2025', V }));
  const { app, state, el, snap } = await abrirPagina({ rapida: respostaIbgeCom({ '2025': '1200' }), completa });
  assert.strictEqual(state.ibge.status, 'divergent');
  await app.refreshIbge(true); await settle();
  assert.strictEqual(state.ibge.status, 'live');
  assert.ok(seloIbge(el).startsWith('Atualizado ao vivo · '), seloIbge(el));
  const p = state.ibge.municipios.find(m => m.c === PATRO).y['2025'];
  assert.deepStrictEqual([...p], [1200, 40000, 48000]);
  assert.ok(coerente(p));
  assert.ok(!state.alerts.some(a => a.id === 'dq-ibge-divergente'));
  assert.strictEqual(JSON.stringify(snap.sidra), JSON_FIX, 'a atualização completa alterou a cópia salva em memória');
});

testAsync('B2-07 demais indicadores: concordância, divergência e falha mostram os mesmos números do IBGE', async () => {
  const telas = ['ov-kpis', 'bench-insights', 'bench-rank-title', 'bench-rank-sub', 'bench-series-title', 'historical-table-body', 'bench-source-line'];
  const foto = async (rapida) => { const { el } = await abrirPagina({ rapida }); return telas.map(id => el(id).innerHTML + '|' + el(id).textContent); };
  const igual = await foto(respostaIgual());
  assert.ok(igual.some(h => h.length > 10), 'telas não renderizadas');
  assert.deepStrictEqual(await foto(respostaIbgeCom({ '2025': '1200', '2019': '900' })), igual);
  assert.deepStrictEqual(await foto('falha'), igual);
});

testAsync('B2-08 falha da atualização completa depois de uma divergência: o aviso continua', async () => {
  const { app, state, el, snap } = await abrirPagina({ rapida: respostaIbgeCom({ '2025': '1200' }), completa: 'falha' });
  await app.refreshIbge(true); await settle();
  assert.strictEqual(state.ibge.status, 'divergent');
  assert.ok(seloIbge(el).startsWith('Dados de Patrocínio divergem do IBGE · '), seloIbge(el));
  assert.ok(el('regional-status-badge').title.includes('Última falha:'));
  assert.ok(state.alerts.some(a => a.id === 'dq-ibge-divergente'));
  semAlteracao(state, snap);
});

/* Ajuste de cobertura do B2: completa, parcial, divergente e insuficiente. */
const ESPERADOS = ['2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025'];
const respostaAnos = (anos, trocas = {}) => sidraRapida(anos.map(a => [a, a in trocas ? trocas[a] : String(Y_PATRO[a][0])]));

testAsync('B2-09 cobertura completa: os 10 anos pedidos voltaram iguais → "conferidos ao vivo"', async () => {
  const { state, el, snap, net } = await abrirPagina({ rapida: respostaAnos(ESPERADOS) });
  const url = net.urls.find(u => u.includes('/n6/3148103/'));
  assert.ok(url.includes('/p/2016-2025/'), url);                                           // anos esperados = anos pedidos
  assert.deepStrictEqual([...ANOS_PATRO], ESPERADOS);
  assert.strictEqual(state.ibge.status, 'checked');
  assert.deepStrictEqual([...state.ibge.conferencia.esperados], ESPERADOS);
  assert.strictEqual(state.ibge.conferencia.anos.length, 10);
  assert.ok(seloIbge(el).startsWith('Dados de Patrocínio conferidos ao vivo · '), seloIbge(el));
  assert.ok(state.integrationLog.some(l => l.message.includes('10 de 10 anos esperados comparados')));
  semAlteracao(state, snap);
  // anos fora do período pedido não contam nem atrapalham
  const extra = await abrirPagina({ rapida: [...respostaAnos(ESPERADOS), { D3C: '2015', V: '999' }, { D3C: '2026', V: '1' }] });
  assert.strictEqual(extra.state.ibge.status, 'checked');
  assert.strictEqual(extra.state.ibge.conferencia.anos.length, 10);
});

testAsync('B2-10 cobertura parcial sem divergência → "parcialmente conferidos", nunca "conferidos ao vivo"', async () => {
  for (const anos of [ESPERADOS.slice(0, 9), ['2025'], ['2016', '2020', '2025']]) {
    const { state, el, snap } = await abrirPagina({ rapida: respostaAnos(anos) });
    assert.strictEqual(state.ibge.status, 'partial', anos.join());
    const selo = seloIbge(el);
    assert.ok(selo.startsWith('Dados de Patrocínio parcialmente conferidos · '), selo);
    assert.ok(!selo.includes('conferidos ao vivo'), selo);
    assert.ok(!el('regional-status-badge').className.includes('success'), 'parcial não pode ter cor de conferência completa');
    const c = state.ibge.conferencia;
    assert.strictEqual(c.esperados.length, 10);
    assert.strictEqual(c.anos.length, anos.length);
    assert.deepStrictEqual([...c.faltantes], ESPERADOS.filter(a => !anos.includes(a)));
    const t = el('regional-status-badge').title;
    assert.ok(t.includes(`Conferidos ${anos.length} de 10 anos esperados`) && t.includes(c.faltantes[0]), t);
    assert.ok(state.integrationLog.some(l => l.message.includes(`${anos.length} de 10 anos esperados comparados`)));
    const fontes = el('data-sources-grid').innerHTML;
    assert.ok(fontes.includes('Dados de Patrocínio parcialmente conferidos'));
    assert.ok(!fontes.includes('Dados de Patrocínio conferidos ao vivo'));
    assert.ok(!state.alerts.some(a => a.id.startsWith('dq-ibge')));
    semAlteracao(state, snap);
  }
});

testAsync('B2-11 divergência em resposta parcial ou completa → "divergem", aviso mantido, cobertura registrada', async () => {
  for (const anos of [ESPERADOS, ['2024', '2025']]) {
    const { state, el, snap } = await abrirPagina({ rapida: respostaAnos(anos, { '2025': '1200' }) });
    assert.strictEqual(state.ibge.status, 'divergent');
    assert.ok(seloIbge(el).startsWith('Dados de Patrocínio divergem do IBGE · '), seloIbge(el));
    assert.ok(!/conferid/i.test(seloIbge(el)));
    const av = state.alerts.find(a => a.id === 'dq-ibge-divergente');
    assert.ok(av && av.priority === 'media');
    assert.ok(av.desc.includes(`Conferidos ${anos.length} de 10 anos esperados`), av.desc);
    assert.ok(state.integrationLog.some(l => l.message.includes(`${anos.length} de 10 anos esperados comparados`)));
    assert.deepStrictEqual([...state.ibge.municipios.find(m => m.c === PATRO).y['2025']], [1110, 43850, 48669]);
    semAlteracao(state, snap);
  }
});

testAsync('B2-12 dados insuficientes (nenhum ano esperado com número): sem conferência positiva', async () => {
  const casos = [
    sidraRapida(ESPERADOS.map(a => [a, '...'])),
    sidraRapida([['2015', '1110'], ['2026', '1110']]),                                     // só anos fora do período pedido
    sidraRapida([])
  ];
  for (const rapida of casos) {
    const { state, el, snap } = await abrirPagina({ rapida });
    assert.strictEqual(state.ibge.status, 'snapshot');
    assert.ok(state.ibge.message, 'motivo não registrado');
    assert.ok(seloIbge(el).startsWith('Cópia oficial de'), seloIbge(el));
    assert.ok(!/conferid|divergem/i.test(seloIbge(el) + el('data-sources-grid').innerHTML.replace(/Cópia oficial \(API falhou\)/g, '')));
    assert.ok(state.alerts.some(a => a.id === 'dq-ibge-snapshot'));
    semAlteracao(state, snap);
  }
});

testAsync('B2-13 falha da atualização completa depois de conferência parcial: o estado parcial continua', async () => {
  const { app, state, el } = await abrirPagina({ rapida: respostaAnos(['2025']), completa: 'falha' });
  await app.refreshIbge(true); await settle();
  assert.strictEqual(state.ibge.status, 'partial');
  assert.ok(seloIbge(el).startsWith('Dados de Patrocínio parcialmente conferidos'));
  assert.ok(el('regional-status-badge').title.includes('Última falha:'));
});

/* ==========================================================================
 * B3 (Etapa 5B / M2): textos e datas. P7 (texto da média de MG), N2 (data dos alertas de
 * qualidade = data da consulta ou avaliação, no fuso local), N3 (aba Fontes com a cópia do INMET
 * recusada) e V3 (selo "Oficial · transcrito" do Cepea). Rede simulada: nada de consulta real.
 * ========================================================================== */
const isoLocal = (ms) => M.localDateISO(new Date(ms));
const horaLocal = (y, m, d, h, mi) => new Date(y, m - 1, d, h, mi).getTime();          // horário local do fuso do teste
const visivelB3 = (html) => String(html).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const dataHoraBR = (ms) => { const d = new Date(ms); return `${d.toLocaleDateString('pt-BR')} ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`; };
const alertaB3 = (state, prefixo) => state.alerts.find(a => a.id.startsWith(prefixo));
const cartaoFonte = (el, id) => {
  const nome = D.dataSources.find(s => s.id === id).name;
  return el('data-sources-grid').innerHTML.split('<article').find(c => c.includes(nome)) || '';
};
const NOME_SRC = (id) => D.dataSources.find(s => s.id === id).name;

/** Página no instante nowMs; INMET sempre falha; IBGE responde conforme o cenário. */
async function abrirB3(nowMs, { ibge = 'falha', inmetSnapshot, inmetAoVivo } = {}) {
  const rapida = ibge === 'igual' ? respostaIgual() : ibge === 'divergente' ? respostaIbgeCom({ '2025': '1200' }) : 'falha';
  const net = redeIbge({ rapida });
  const fetchImpl = (url) => (inmetAoVivo && !String(url).includes('apisidra'))
    ? Promise.resolve({ ok: true, json: async () => copy(inmetAoVivo) }) : net.fetch(url);
  const opts = { nowMs, fetchImpl };
  if (inmetSnapshot !== undefined) opts.inmetSnapshot = inmetSnapshot;
  const ctx = loadApp(opts);
  ctx.fire('DOMContentLoaded');
  await settle();
  ctx.net = net;
  return ctx;
}
/** O harness não emula <select>: escolhe ano, região e município como o navegador faria e renderiza o benchmark. */
function renderBench(ctx) {
  ctx.el('bench-year').value = '2025'; ctx.el('bench-meso').value = ''; ctx.el('bench-muni').value = PATRO;
  ctx.app.renderBenchmark();
}

testAsync('B3-01 P7: linha de fonte do benchmark sem o número fixo de municípios da média de MG', async () => {
  const ctx = await abrirB3(COLETA + 2 * H, { ibge: 'igual' });
  renderBench(ctx);
  const t = ctx.el('bench-source-line').textContent;
  assert.ok(t.includes('média de MG com os municípios com produção em cada ano'), t);
  assert.ok(!t.includes('calculada sobre os') && !t.includes('533'), t);
  assert.ok(t.startsWith('IBGE · Produção Agrícola Municipal (SIDRA 1613) · café arábica · 60 maiores municípios produtores de MG'), t);
});

testAsync('B3-02 N2: alertas do INMET com a data local da tentativa de consulta, nunca a data fixa de 14/11', async () => {
  // cópia ainda válida (20 h): previsão vinda de cópia
  const t1 = COLETA + 20 * H;
  let ctx = await abrirB3(t1, { ibge: 'igual' });
  let a = alertaB3(ctx.state, 'dq-inmet-');
  assert.strictEqual(ctx.state.inmet.status, 'snapshot');
  assert.strictEqual(a.id, 'dq-inmet-snapshot');
  assert.strictEqual(a.refDate, isoLocal(t1));
  assert.notStrictEqual(a.refDate, D.meta.referenceDate);
  // cópia vencida (40 h): previsão indisponível
  const t2 = COLETA + 40 * H;
  ctx = await abrirB3(t2, { ibge: 'igual' });
  a = alertaB3(ctx.state, 'dq-inmet-');
  assert.strictEqual(a.id, 'dq-inmet-error');
  assert.strictEqual(a.refDate, isoLocal(t2));
  const lista = ctx.el('overview-alerts-list').innerHTML;
  if (lista.includes(a.title)) assert.ok(lista.includes(`ref. ${isoLocal(t2).split('-').reverse().join('/')}`), 'data exibida diferente');
  // a mesma sessão: a cópia vence com a página aberta e a reavaliação usa a data da nova consulta
  ctx = await abrirB3(t1, { ibge: 'igual' });
  ctx.setNow(t2); ctx.fire('visibilitychange'); await settle();
  a = alertaB3(ctx.state, 'dq-inmet-');
  assert.strictEqual(a.id, 'dq-inmet-error');
  assert.strictEqual(a.refDate, isoLocal(t2));
});

testAsync('B3-03 N2: alertas do IBGE com a data da tentativa (cópia) ou da conferência (divergência)', async () => {
  const t = COLETA + 40 * H;
  let ctx = await abrirB3(t, { ibge: 'falha' });
  let a = alertaB3(ctx.state, 'dq-ibge-snapshot');
  assert.ok(a, 'aviso de cópia do IBGE ausente');
  assert.strictEqual(a.refDate, isoLocal(t));
  ctx = await abrirB3(t, { ibge: 'divergente' });
  a = alertaB3(ctx.state, 'dq-ibge-divergente');
  assert.ok(a, 'aviso de divergência ausente');
  assert.strictEqual(a.refDate, isoLocal(Date.parse(ctx.state.ibge.conferencia.checkedAt)));
  assert.strictEqual(a.refDate, isoLocal(t));
  assert.notStrictEqual(a.refDate, D.meta.referenceDate);
});

testAsync('B3-04 N2: perto da meia-noite vale o dia local, não o dia em UTC', async () => {
  for (const [ms, dia] of [[horaLocal(2026, 11, 16, 23, 50), '2026-11-16'], [horaLocal(2026, 11, 17, 0, 20), '2026-11-17']]) {
    const { state } = await abrirB3(ms, { ibge: 'falha' });
    for (const id of ['dq-inmet-', 'dq-ibge-snapshot', 'dq-conab']) {
      const a = alertaB3(state, id);
      assert.ok(a, id + ' ausente');
      assert.strictEqual(a.refDate, dia, `${id} às ${new Date(ms).toTimeString().slice(0, 5)} locais: ${a.refDate}`);
    }
  }
});

testAsync('B3-05 N2: alerta da Conab com a data local da avaliação na página', async () => {
  const t = COLETA + 40 * H;
  const ctx = await abrirB3(t, { ibge: 'igual' });
  assert.strictEqual(alertaB3(ctx.state, 'dq-conab').refDate, isoLocal(t));
  ctx.setNow(t + 24 * H); ctx.fire('visibilitychange'); await settle();                  // reavaliação no dia seguinte
  assert.strictEqual(alertaB3(ctx.state, 'dq-conab').refDate, isoLocal(t + 24 * H));
});

testAsync('B3-06 identificadores dos alertas preservados e sem duplicidade nas reavaliações', async () => {
  const t = COLETA + 40 * H;
  const ctx = await abrirB3(t, { ibge: 'falha' });
  const ids = ctx.state.alerts.map(a => a.id).sort();
  assert.ok(['dq-inmet-error', 'dq-ibge-snapshot', 'dq-conab'].every(id => ids.includes(id)), ids.join());
  for (let i = 1; i <= 3; i++) {
    ctx.setNow(t + i * 31 * MIN); ctx.intervals.forEach(x => x.fn()); ctx.fire('visibilitychange');
    await ctx.app.refreshIbge(false); await settle();
    assert.ok(unicos(ctx.state), 'alerta duplicado');
    assert.deepStrictEqual(ctx.state.alerts.map(a => a.id).sort(), ids);
  }
});

testAsync('B3-07 N3: aba Fontes mostra a data e o motivo da cópia do INMET recusada', async () => {
  // a) cópia vencida: data da cópia e motivo, nada de "—"
  let ctx = await abrirB3(COLETA + 40 * H, { ibge: 'igual' });
  let c = cartaoFonte(ctx.el, 'SRC-INMET');
  assert.ok(c.includes('Indisponível'), c);
  assert.ok(c.includes(`A última cópia salva, de ${dataHoraBR(COLETA)}, não foi usada:`), c);
  assert.ok(c.includes('acima do limite de 24 h'), c);
  assert.ok(!c.includes('<dd>—</dd>'), 'status mostra "—" com cópia recusada');
  // b) cópia sem data válida: motivo, sem data inventada
  ctx = await abrirB3(COLETA + 2 * H, { ibge: 'igual', inmetSnapshot: snapCom('ontem', {}) });
  c = cartaoFonte(ctx.el, 'SRC-INMET');
  assert.ok(c.includes('Indisponível') && c.includes('A cópia salva não foi usada:'), c);
  // c) sem cópia: como antes
  ctx = await abrirB3(COLETA + 2 * H, { ibge: 'igual', inmetSnapshot: null });
  c = cartaoFonte(ctx.el, 'SRC-INMET');
  assert.ok(c.includes('Indisponível') && c.includes('<dd>—</dd>') && !c.includes('não foi usada'), c);
  // d) cópia válida usada: como antes
  ctx = await abrirB3(COLETA + 2 * H, { ibge: 'igual' });
  c = cartaoFonte(ctx.el, 'SRC-INMET');
  assert.ok(c.includes('Cópia oficial (API falhou)') && c.includes(`<dd>${dataHoraBR(COLETA)}</dd>`), c);
  // e) ao vivo: como antes
  const agora = new Date(2026, 10, 14, 12, 0).getTime();
  ctx = await abrirB3(agora, { ibge: 'igual', inmetAoVivo: SNAP_INMET.data });
  assert.strictEqual(ctx.state.inmet.status, 'live');
  c = cartaoFonte(ctx.el, 'SRC-INMET');
  assert.ok(c.includes('Ao vivo') && !c.includes('não foi usada'), c);
});

testAsync('B3-08 V3: "Oficial · transcrito" no KPI, no detalhe e na fonte do Cepea; IBGE e INMET continuam "Oficial"', async () => {
  const ctx = await abrirB3(COLETA + 2 * H, { ibge: 'igual' });
  const SELO_T = '<span class="dtag dtag-oficial">Oficial · transcrito</span>', SELO = '<span class="dtag dtag-oficial">Oficial</span>';
  const cards = ctx.el('ov-kpis').innerHTML.split('<button').filter(x => x.includes('kpi-card'));
  const cepea = cards.find(x => x.includes('Preço do café (Cepea)'));
  assert.ok(cepea && cepea.includes(SELO_T), 'KPI do Cepea sem o selo');
  cards.filter(x => x !== cepea).forEach(x => assert.ok(!x.includes('transcrito'), 'selo "transcrito" fora do Cepea'));
  const idx = Number(/openKpi\((\d+)\)/.exec(cepea)[1]);
  ctx.app.openKpi(idx);
  assert.ok(ctx.el('drawer-eyebrow').innerHTML.includes(SELO_T), ctx.el('drawer-eyebrow').innerHTML);
  assert.ok(cartaoFonte(ctx.el, 'SRC-CEPEA').includes(SELO_T), 'fonte do Cepea sem o selo');
  for (const id of ['SRC-IBGE', 'SRC-INMET']) {
    const c = cartaoFonte(ctx.el, id);
    assert.ok(c.includes(SELO) && !c.includes('transcrito</span>'), `${id} deveria continuar "Oficial"`);
  }
  const outro = cards.find(x => x !== cepea && x.includes('dtag-oficial'));
  if (outro) assert.ok(outro.includes(SELO), 'outro KPI oficial mudou de selo');
  ctx.app.openKpi(idx === 0 ? 1 : 0);
  assert.ok(!ctx.el('drawer-eyebrow').innerHTML.includes('transcrito'), 'detalhe de outro KPI com o selo do Cepea');
});

/* Linha de base gravada com o js/app.js aprovado antes da B3 (200dde80…), por fuso: números de KPIs,
 * benchmark e histórico, e identificadores, categoria e prioridade dos alertas (sem a data). */
const LINHA_DE_BASE_B3_09 = { "America/Sao_Paulo": "654c241fc9b897febc74ca7a8bde0e7fa59a01bd23d2f739c9e374c2235d3c3d", "UTC": "654c241fc9b897febc74ca7a8bde0e7fa59a01bd23d2f739c9e374c2235d3c3d", "Asia/Tokyo": "adbd9d7a82745e833d0debb4c9b8cd766dcb8e51df9e748f9a35e4e93af15953" };
async function fotoB3() {
  const cenas = [];
  for (const [now, ibge] of [[COLETA + 2 * H, 'igual'], [COLETA + 40 * H, 'falha'], [COLETA + 2 * H, 'divergente']]) {
    const ctx = await abrirB3(now, { ibge });
    renderBench(ctx);
    const nums = (id) => (visivelB3(ctx.el(id).innerHTML || ctx.el(id).textContent).match(/\d[\d.,]*/g) || []).join(' ');
    cenas.push({
      kpis: nums('ov-kpis'), bench: ['bench-insights', 'bench-rank-title', 'bench-rank-sub', 'bench-series-title'].map(nums),
      historico: nums('historical-table-body'), alertas: ctx.state.alerts.map(a => `${a.id}|${a.cat}|${a.priority}`)
    });
  }
  return cenas;
}
testAsync('B3-09 números, benchmark, histórico e alertas (identificadores e quantidade) iguais aos de antes da B3', async () => {
  const cenas = await fotoB3();
  cenas.forEach(c => { assert.ok(c.kpis.length > 20 && c.historico.length > 20 && c.bench.every(b => b.length), 'telas não renderizadas'); });
  const hash = require('crypto').createHash('sha256').update(JSON.stringify(cenas)).digest('hex');
  const fuso = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (process.env.RIGORA_FOTO_B3) console.log(`    [linha de base B3-09] ${fuso}: ${hash}`);
  if (LINHA_DE_BASE_B3_09[fuso]) assert.strictEqual(hash, LINHA_DE_BASE_B3_09[fuso], 'números ou alertas mudaram em relação ao app.js anterior');
  else console.log(`    (B3-09: sem linha de base gravada para o fuso ${fuso}; conferidas só a renderização e a unicidade)`);
  cenas.forEach(c => assert.strictEqual(new Set(c.alertas).size, c.alertas.length));
});

/* ==========================================================================
 * B7 (Etapa 5B / M2, item I5): procedência na aba Fontes, lida de exportacao.
 * Valores de EXPORTACAO_B5 copiados da exportação controlada do B5 (execução 3 do IBGE,
 * execução 1 do INMET recusada por idade). Nada de consulta real: a rede é simulada.
 * ========================================================================== */
const SEM_EXPORTACAO = Symbol('sem exportacao');
const EXPORTACAO_B5 = {
  geradoEm: '2026-11-15T19:31:50+00:00', geradoPor: 'python -m pipeline.export_snapshots', banco: 'rigora.db',
  sidra: {
    modo: 'sqlite', fonte: 'IBGE/SIDRA PAM 1613 (café arábica, MG)', execucaoId: 3, execucoesNosDadosExportados: [3],
    dataColeta: '2026-11-14T17:46:15+00:00', arquivoBruto: 'sidra_pam_1613_mg_arabica_20261114T174615Z.json',
    hashSha256: '035139b5a1f06e566def3eacdcf7740079dcc6d311774bc417d9684307380946', statusExecucao: 'sucesso',
    validacao: { resultado: 'aprovado', erros_impeditivos: 0, avisos: 6, registros_com_aviso: 4330, registros_analisados: 8520 },
    municipiosExportados: 60, anos: [2016, 2025], registrosExportados: 600, municipiosNaMediaMG: 533
  },
  inmet: {
    modo: 'indisponivel', fonte: 'INMET/Previsão do tempo (Patrocínio, 3148103)', execucaoId: 1,
    dataColeta: '2026-11-14T17:32:11+00:00', arquivoBruto: 'inmet_previsao_3148103_20261114T173211Z.json',
    hashSha256: '684e0119973201652dc326b674a88b83cc5182ff0d2eafc9959a2866d8a2bebc', statusExecucao: 'sucesso',
    idadeHorasNaGeracao: 26.0, motivo: 'previsão mais recente coletada há 26 h (limite 24 h); não é exibida como atual'
  },
  aviso: 'dataColeta = quando a fonte respondeu; geradoEm = quando este arquivo foi escrito. Este arquivo não indica coleta ao vivo: é sempre uma cópia salva.'
};
const MANUAL = 'Cópia manual', INCOMPLETO = 'Registro de procedência incompleto';
const textoIbgeB5 = () => `Arquivo gerado em ${dataHoraBR(Date.parse(EXPORTACAO_B5.geradoEm))} a partir da execução 3 (coleta de ${dataHoraBR(Date.parse(EXPORTACAO_B5.sidra.dataColeta))}; bruto SHA-256 035139b5a1f0…).`;
const textoInmetB5 = () => `Arquivo gerado em ${dataHoraBR(Date.parse(EXPORTACAO_B5.geradoEm))} sem previsão: a execução 1 não foi exportada. Motivo: previsão mais recente coletada há 26 h (limite 24 h); não é exibida como atual.`;

/** Página com exportacao injetada no snapshot carregado (o app a lê na hora de renderizar). */
async function abrirB7(exportacao, { nowMs = COLETA + 2 * H, inmetSnapshot, ibge = 'igual' } = {}) {
  const rapida = ibge === 'igual' ? respostaIgual() : ibge === 'divergente' ? respostaIbgeCom({ '2025': '1200' }) : 'falha';
  const net = redeIbge({ rapida });
  const opts = { nowMs, fetchImpl: (...a) => net.fetch(...a) };
  if (inmetSnapshot !== undefined) opts.inmetSnapshot = inmetSnapshot;
  const ctx = loadApp(opts);
  if (exportacao !== SEM_EXPORTACAO) ctx.snap.exportacao = exportacao;
  ctx.fire('DOMContentLoaded');
  await settle();
  return ctx;
}
/** Texto da linha "Procedência" de um cartão (null se a linha não existir). */
const procedencia = (el, id) => {
  const m = /<dt>Procedência<\/dt><dd>([\s\S]*?)<\/dd>/.exec(cartaoFonte(el, id));
  return m ? m[1] : null;
};
const linhaColeta = (el, id) => (/<dt>Coleta \/ atualização<\/dt><dd>([\s\S]*?)<\/dd>/.exec(cartaoFonte(el, id)) || [])[1];
function procedenciaSegura(el) {                                  // regras de B2/B3 valem para qualquer cenário
  for (const id of ['SRC-IBGE', 'SRC-INMET']) {
    const t = procedencia(el, id);
    assert.ok(t && t.trim() && t.trim() !== '—', `${id}: procedência vazia ou "—"`);
    assert.ok(!/conferid|divergem/i.test(t), `${id}: procedência com termo de conferência: ${t}`);
  }
  for (const id of ['SRC-CEPEA', 'SRC-CONAB', 'SRC-LEDGER']) assert.strictEqual(procedencia(el, id), null, `${id} ganhou procedência`);
}

testAsync('B7-01 sem exportacao: "Cópia manual" nos cartões do IBGE e do INMET; leitura na hora de renderizar', async () => {
  const ctx = await abrirB7(SEM_EXPORTACAO);
  assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), MANUAL);
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), MANUAL);
  procedenciaSegura(ctx.el);
  ctx.snap.exportacao = copy(EXPORTACAO_B5);                       // o arquivo muda; a próxima renderização mostra
  await ctx.app.refreshIbge(false); await settle();
  assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), textoIbgeB5());
});

testAsync('B7-02 exportação real do B5: execução, hash curto, geração e coleta separadas; INMET sem previsão', async () => {
  const ctx = await abrirB7(copy(EXPORTACAO_B5), { inmetSnapshot: null });
  const ibge = procedencia(ctx.el, 'SRC-IBGE');
  assert.strictEqual(ibge, textoIbgeB5());
  assert.ok(ibge.includes('035139b5a1f0…') && !ibge.includes('035139b5a1f06'), 'hash não truncado em 12');
  assert.notStrictEqual(dataHoraBR(Date.parse(EXPORTACAO_B5.geradoEm)), dataHoraBR(Date.parse(EXPORTACAO_B5.sidra.dataColeta)));
  const inmet = procedencia(ctx.el, 'SRC-INMET');
  assert.strictEqual(inmet, textoInmetB5());
  assert.ok(!inmet.includes('a partir da execução') && !inmet.includes('coleta de'), 'INMET sugere coleta exportada');
  procedenciaSegura(ctx.el);
  // INMET com previsão exportada: mesma forma do IBGE
  const comPrevisao = copy(EXPORTACAO_B5);
  comPrevisao.inmet = { modo: 'sqlite', execucaoId: 1, dataColeta: SNAP_INMET.collectedAt, hashSha256: EXPORTACAO_B5.inmet.hashSha256 };
  const c2 = await abrirB7(comPrevisao);
  assert.strictEqual(procedencia(c2.el, 'SRC-INMET'),
    `Arquivo gerado em ${dataHoraBR(Date.parse(EXPORTACAO_B5.geradoEm))} a partir da execução 1 (coleta de ${dataHoraBR(COLETA)}; bruto SHA-256 684e01199732…).`);
});

testAsync('B7-03 modos "mantido do arquivo anterior" e "indisponível"', async () => {
  const mantido = copy(EXPORTACAO_B5);
  mantido.sidra = { modo: 'mantido_do_arquivo_anterior', motivo: 'tabela pam_municipio_ano vazia', dataColeta: '2026-11-14T16:11:55Z' };
  mantido.inmet = { modo: 'mantido_do_arquivo_anterior', motivo: 'nenhuma execução do INMET com sucesso no banco.', dataColeta: SNAP_INMET.collectedAt };
  let ctx = await abrirB7(mantido);
  assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), 'Dados mantidos do arquivo anterior. Motivo: tabela pam_municipio_ano vazia.');
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), 'Dados mantidos do arquivo anterior. Motivo: nenhuma execução do INMET com sucesso no banco.');
  procedenciaSegura(ctx.el);
  const semExecucao = copy(EXPORTACAO_B5);
  semExecucao.inmet = { modo: 'indisponivel', motivo: 'nenhuma execução do INMET com sucesso no banco' };
  ctx = await abrirB7(semExecucao, { inmetSnapshot: null });
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'),
    `Arquivo gerado em ${dataHoraBR(Date.parse(EXPORTACAO_B5.geradoEm))} sem previsão. Motivo: nenhuma execução do INMET com sucesso no banco.`);
  // incoerências entre a seção e os dados do arquivo não viram procedência válida
  ctx = await abrirB7(copy(EXPORTACAO_B5));                            // "indisponível", mas o arquivo tem previsão
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), INCOMPLETO);
  const sqliteSemDados = copy(EXPORTACAO_B5); sqliteSemDados.inmet = { modo: 'sqlite', execucaoId: 1, dataColeta: SNAP_INMET.collectedAt, hashSha256: EXPORTACAO_B5.inmet.hashSha256 };
  ctx = await abrirB7(sqliteSemDados, { inmetSnapshot: null });
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), INCOMPLETO);
});

testAsync('B7-04 registros malformados e HTML injetado: sem exceção, "Registro de procedência incompleto", texto escapado', async () => {
  const quebrada = new Proxy({}, { get() { throw new Error('acesso quebrado'); } });
  const casos = [null, 'texto', 42, [], {}, { geradoEm: 'ontem', sidra: EXPORTACAO_B5.sidra },
    Object.assign(copy(EXPORTACAO_B5), { sidra: 'x' }), Object.assign(copy(EXPORTACAO_B5), { sidra: { modo: 'sqlite', execucaoId: 3 } }),
    Object.assign(copy(EXPORTACAO_B5), { sidra: Object.assign(copy(EXPORTACAO_B5.sidra), { hashSha256: 'abc' }) }),
    Object.assign(copy(EXPORTACAO_B5), { sidra: Object.assign(copy(EXPORTACAO_B5.sidra), { execucaoId: '3' }) }),
    Object.assign(copy(EXPORTACAO_B5), { sidra: Object.assign(copy(EXPORTACAO_B5.sidra), { dataColeta: 'ontem' }) }),
    Object.assign(copy(EXPORTACAO_B5), { sidra: Object.assign(copy(EXPORTACAO_B5.sidra), { modo: 'desconhecido' }) }),
    Object.assign(copy(EXPORTACAO_B5), { sidra: Object.assign(copy(EXPORTACAO_B5.sidra), { modo: 'indisponivel', motivo: 42 }) }),   // ajuste B7: motivo de tipo inválido
    Object.assign(copy(EXPORTACAO_B5), { sidra: { modo: 'mantido_do_arquivo_anterior', motivo: '   ' } }),
    quebrada];
  for (const ex of casos) {
    const ctx = await abrirB7(ex);
    assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), INCOMPLETO, `IBGE com ${JSON.stringify(ex === quebrada ? 'proxy' : ex)}`);
    assert.strictEqual(ctx.state.ibge.status, 'checked');                // o fluxo do IBGE continua
    assert.strictEqual(ctx.state.inmet.status, 'snapshot');              // e o do INMET também
    assert.ok(!ctx.el('weather-status-badge').innerHTML.includes('Consultando'));
    procedenciaSegura(ctx.el);
  }
  const injetado = copy(EXPORTACAO_B5);
  injetado.inmet = { modo: 'mantido_do_arquivo_anterior', motivo: '<img src=x onerror="alert(1)"> & <b>' };
  const ctx = await abrirB7(injetado);
  const html = cartaoFonte(ctx.el, 'SRC-INMET');
  assert.ok(!html.includes('<img') && !html.includes('<b>'), 'HTML do snapshot chegou à tela');
  assert.ok(html.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &lt;b&gt;'), html);
});

testAsync('B7-05 geração e coleta em horário local, inclusive na virada do dia', async () => {
  const g = horaLocal(2026, 11, 16, 23, 50), c = horaLocal(2026, 11, 17, 0, 20);
  const ex = copy(EXPORTACAO_B5);
  ex.geradoEm = new Date(c + 5 * MIN).toISOString();
  ex.sidra.dataColeta = new Date(g).toISOString();
  ex.inmet = { modo: 'indisponivel', execucaoId: 1, motivo: 'previsão vencida' };
  const ctx = await abrirB7(ex, { nowMs: c + 10 * MIN, inmetSnapshot: null });
  assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), 'Arquivo gerado em 17/11/2026 00:25 a partir da execução 3 (coleta de 16/11/2026 23:50; bruto SHA-256 035139b5a1f0…).');
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), 'Arquivo gerado em 17/11/2026 00:25 sem previsão: a execução 1 não foi exportada. Motivo: previsão vencida.');
});

testAsync('B7-06 regras do B2 e da B3 preservadas: status, coleta, selo "Oficial" e cópia recusada do INMET', async () => {
  // cópia do INMET vencida: a linha de coleta mostra a cópia recusada e não há "—" no cartão
  for (const ex of [SEM_EXPORTACAO, copy(EXPORTACAO_B5)]) {
    const ctx = await abrirB7(ex, { nowMs: COLETA + 40 * H });
    const inmet = cartaoFonte(ctx.el, 'SRC-INMET');
    assert.ok(inmet.includes(`A última cópia salva, de ${dataHoraBR(COLETA)}, não foi usada:`), inmet);
    assert.ok(!inmet.includes('<dd>—</dd>'), 'traço indevido no cartão do INMET');
    assert.ok(inmet.includes('<span class="dtag dtag-oficial">Oficial</span>') && inmet.includes('Indisponível'));
    const ibge = cartaoFonte(ctx.el, 'SRC-IBGE');
    assert.ok(ibge.includes('Dados de Patrocínio conferidos ao vivo') && ibge.includes('<span class="dtag dtag-oficial">Oficial</span>'));
    procedenciaSegura(ctx.el);
  }
  // a linha "Coleta / atualização" e o status são os mesmos com e sem exportação
  for (const [nowMs, inmetSnapshot, ibge] of [[COLETA + 2 * H, undefined, 'igual'], [COLETA + 40 * H, undefined, 'falha'], [COLETA + 2 * H, null, 'divergente']]) {
    const a = await abrirB7(SEM_EXPORTACAO, { nowMs, inmetSnapshot, ibge });
    const b = await abrirB7(copy(EXPORTACAO_B5), { nowMs, inmetSnapshot, ibge });
    for (const id of ['SRC-IBGE', 'SRC-INMET', 'SRC-CEPEA', 'SRC-CONAB', 'SRC-LEDGER']) {
      assert.strictEqual(linhaColeta(b.el, id), linhaColeta(a.el, id), `${id}: linha de coleta mudou`);
      const sem = (h) => h.replace(/\s*<dt>Procedência<\/dt><dd>[\s\S]*?<\/dd>/, '');
      assert.strictEqual(sem(cartaoFonte(b.el, id)), sem(cartaoFonte(a.el, id)), `${id}: cartão mudou além da procedência`);
    }
  }
});

testAsync('B7-07 números, benchmark, histórico e alertas iguais à linha de base B3-09, com e sem exportação', async () => {
  const fuso = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const base = await fotoB3();
  if (LINHA_DE_BASE_B3_09[fuso]) {
    assert.strictEqual(require('crypto').createHash('sha256').update(JSON.stringify(base)).digest('hex'), LINHA_DE_BASE_B3_09[fuso]);
  }
  let i = 0;
  for (const [now, ibge] of [[COLETA + 2 * H, 'igual'], [COLETA + 40 * H, 'falha'], [COLETA + 2 * H, 'divergente']]) {
    const ctx = await abrirB7(copy(EXPORTACAO_B5), { nowMs: now, ibge });
    renderBench(ctx);
    const nums = (id) => (visivelB3(ctx.el(id).innerHTML || ctx.el(id).textContent).match(/\d[\d.,]*/g) || []).join(' ');
    const cena = {
      kpis: nums('ov-kpis'), bench: ['bench-insights', 'bench-rank-title', 'bench-rank-sub', 'bench-series-title'].map(nums),
      historico: nums('historical-table-body'), alertas: ctx.state.alerts.map(a => `${a.id}|${a.cat}|${a.priority}`)
    };
    // JSON: os arrays vêm de contextos vm diferentes (deepStrictEqual compara também o protótipo)
    assert.strictEqual(JSON.stringify(cena), JSON.stringify(base[i++]), 'a exportação mudou números ou alertas');
  }
});

testAsync('B7-08 campos desconhecidos (qualidade.pla05 e outros) são ignorados sem erro', async () => {
  const ex = copy(EXPORTACAO_B5);
  ex.qualidade = { pla05: { grupos: 275, membros: 726 } };
  ex.versao = 2;
  ex.sidra.campoNovo = { qualquer: [1, 2, 3] };
  ex.inmet.outro = 'x';
  const ctx = await abrirB7(ex, { inmetSnapshot: null });
  ctx.snap.qualidade = { pla05: { grupos: 275 } };                    // seção futura do snapshot
  await ctx.app.refreshIbge(false); await settle();
  assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), textoIbgeB5());
  assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), textoInmetB5());
  procedenciaSegura(ctx.el);
});

/* Ajuste B7: IBGE "indisponível" válido não é registro incompleto nem cópia manual. */
const textoGeradoB5 = () => dataHoraBR(Date.parse(EXPORTACAO_B5.geradoEm));
const comSidra = (sidra) => Object.assign(copy(EXPORTACAO_B5), { sidra });

testAsync('B7-09 IBGE com modo "indisponível" válido: "Fonte indisponível", com o motivo quando houver', async () => {
  const MOTIVO = 'nenhuma execução do IBGE/SIDRA com sucesso no banco';
  const casos = [
    [{ modo: 'indisponivel', motivo: MOTIVO }, `Fonte indisponível. Motivo: ${MOTIVO}.`],
    [{ modo: 'indisponivel', motivo: MOTIVO + '.  ' }, `Fonte indisponível. Motivo: ${MOTIVO}.`],
    [{ modo: 'indisponivel', motivo: MOTIVO, candidatoDoBanco: { modo: 'sqlite', execucaoId: 9 } }, `Fonte indisponível. Motivo: ${MOTIVO}.`],
    [{ modo: 'indisponivel' }, 'Fonte indisponível.'],
    [{ modo: 'indisponivel', motivo: '   ' }, 'Fonte indisponível.'],
    [{ modo: 'indisponivel', motivo: null }, 'Fonte indisponível.'],
    [Object.assign(copy(EXPORTACAO_B5.sidra), { modo: 'indisponivel' }), 'Fonte indisponível.']
  ];
  for (const [sidra, esperado] of casos) {
    const ctx = await abrirB7(comSidra(sidra), { inmetSnapshot: null });
    const t = procedencia(ctx.el, 'SRC-IBGE');
    assert.strictEqual(t, esperado, JSON.stringify(sidra));
    assert.notStrictEqual(t, MANUAL); assert.notStrictEqual(t, INCOMPLETO);
    assert.ok(!t.includes('Arquivo gerado') && !t.includes('a partir da execução'), 'indisponível sugere arquivo gerado com dados do IBGE');
    assert.strictEqual(procedencia(ctx.el, 'SRC-INMET'), textoInmetB5(), 'o INMET mudou');     // INMET preservado
    procedenciaSegura(ctx.el);
  }
  const ctx = await abrirB7(comSidra({ modo: 'indisponivel', motivo: '<script>x</script> & "y"' }));
  assert.ok(cartaoFonte(ctx.el, 'SRC-IBGE').includes('Fonte indisponível. Motivo: &lt;script&gt;x&lt;/script&gt; &amp; &quot;y&quot;.'));
  assert.ok(!cartaoFonte(ctx.el, 'SRC-IBGE').includes('<script>'));
});

testAsync('B7-10 distinção: indisponível ≠ incompleto ≠ cópia manual; status, coleta e INMET inalterados', async () => {
  for (const sidra of [{ modo: 'indisponivel', motivo: 42 }, { modo: 'indisponivel', motivo: {} }, { modo: 'indisponivel', motivo: ['x'] },
    { modo: 'INDISPONIVEL', motivo: 'x' }, { modo: '' }, { motivo: 'x' }]) {
    const ctx = await abrirB7(comSidra(sidra));
    assert.strictEqual(procedencia(ctx.el, 'SRC-IBGE'), INCOMPLETO, JSON.stringify(sidra));
  }
  const semData = comSidra({ modo: 'indisponivel', motivo: 'x' }); semData.geradoEm = 'ontem';
  assert.strictEqual(procedencia((await abrirB7(semData)).el, 'SRC-IBGE'), INCOMPLETO);
  assert.strictEqual(procedencia((await abrirB7(SEM_EXPORTACAO)).el, 'SRC-IBGE'), MANUAL);
  // INMET: as regras de antes continuam (indisponível com previsão no arquivo é incoerente)
  const inmetComDados = await abrirB7(comSidra({ modo: 'indisponivel', motivo: 'x' }));
  assert.strictEqual(procedencia(inmetComDados.el, 'SRC-INMET'), INCOMPLETO);
  // status, linha de coleta e o resto do cartão do IBGE são os mesmos de sem exportação
  for (const ibge of ['igual', 'falha', 'divergente']) {
    const a = await abrirB7(SEM_EXPORTACAO, { ibge });
    const b = await abrirB7(comSidra({ modo: 'indisponivel', motivo: 'x' }), { ibge });
    const sem = (h) => h.replace(/\s*<dt>Procedência<\/dt><dd>[\s\S]*?<\/dd>/, '');
    assert.strictEqual(sem(cartaoFonte(b.el, 'SRC-IBGE')), sem(cartaoFonte(a.el, 'SRC-IBGE')), `IBGE (${ibge}) mudou além da procedência`);
    assert.strictEqual(b.state.ibge.status, a.state.ibge.status);
    assert.strictEqual(JSON.stringify(b.state.alerts.map(x => x.id)), JSON.stringify(a.state.alerts.map(x => x.id)));
  }
});

testAsync('T0-02 nenhum teste lê o js/snapshots.js de produção; a página simulada carrega a fixture', async () => {
  assert.ok(!lidos.has(SNAPSHOT_PRODUCAO), 'um teste leu ' + SNAPSHOT_PRODUCAO);
  assert.ok(lidos.has(SNAPSHOT_FIXTURE));
  const { state } = loadApp({ nowMs: COLETA + H, fetchImpl: falhaAoVivo });
  assert.strictEqual(state.ibge.municipios.length, 60);
  assert.ok(!lidos.has(SNAPSHOT_PRODUCAO));
});

/* ==========================================================================
 * Bloco 2: umidade do INMET (T2), livro-caixa salvo no navegador (T3) e área total (T9).
 * Rede, relógio e armazenamento simulados em memória. Nada é gravado em disco.
 * ========================================================================== */
/** Dia real da cópia com a umidade trocada em todos os períodos. APAGAR remove o campo. */
const APAGAR = Symbol('apagar');
function comUmidade(origem, min, max) {
  const d = dia(origem);
  const alvos = ['manha', 'tarde', 'noite'].filter(k => d[k] && typeof d[k] === 'object').map(k => d[k]);
  for (const p of (alvos.length ? alvos : [d])) {
    if (min === APAGAR) delete p.umidade_min; else p.umidade_min = min;
    if (max === APAGAR) delete p.umidade_max; else p.umidade_max = max;
  }
  return d;
}
const respostaDias = (dias) => ({ '3148103': dias });
const CINCO = ['14/11/2026', '15/11/2026', '16/11/2026', '17/11/2026', '18/11/2026'];
const T15 = new Date(2026, 10, 15, 10, 0).getTime();            // 15/11 10:00 local: dentro das 24 h da cópia nos três fusos

/** Procura sinais de valor inválido no texto exibido: undefined, NaN, null e faixa pela metade. */
function problemasVisiveis(texto) {
  const achados = [];
  const regras = [
    ['undefined', /\bundefined\b/], ['NaN', /\bNaN\b/], ['null', /\bnull\b/],
    ['faixa sem fim', /\d\s*–\s*%/], ['faixa sem início', /(^|[^\d\s])\s*–\s*\d+\s*%/], ['UR vazia', /\bUR\s*%/]
  ];
  for (const [nome, re] of regras) if (re.test(texto)) achados.push(nome);
  return achados;
}
function varrer(els) {
  const out = [];
  for (const [id, e] of Object.entries(els)) {
    const t = `${e.innerHTML || ''}\n${e.textContent || ''}`;
    const p = problemasVisiveis(t);
    if (p.length) out.push(`${id}: ${p.join(', ')}`);
  }
  return out;
}

test('B2-SCAN-00 o detector acusa valores inválidos e aceita faixas normais (conferência do próprio teste)', () => {
  for (const ruim of ['Umidade undefined–80%', 'UR NaN%', 'T média null', 'Umidade 40–%', 'Umidade –80%', 'UR %', 'UR média NaN%'])
    assert.ok(problemasVisiveis(ruim).length > 0, ruim);
  for (const bom of ['Umidade 40–100%', 'UR 50–90%', 'T média entre 22 e 26 °C', 'UR indisponível', 'set/2026 a ago/2027', '22–26 °C'])
    assert.deepStrictEqual(problemasVisiveis(bom), [], bom);
});

test('B2-T2-U1 faixa de umidade: só dois números finitos com 0 ≤ mín ≤ máx ≤ 100', () => {
  assert.strictEqual(M.inmetHumidityOk({ umidade_min: 40, umidade_max: 100 }), true);
  assert.strictEqual(M.inmetHumidityOk({ umidade_min: 0, umidade_max: 0 }), true);
  for (const [a, b] of [[undefined, 80], [40, undefined], [null, 80], [40, null], ['40', 80], [40, '80'], [NaN, 80], [40, Infinity], [90, 80], [-1, 80], [40, 101], [true, 80]])
    assert.strictEqual(M.inmetHumidityOk({ umidade_min: a, umidade_max: b }), false, `${a}–${b}`);
  assert.strictEqual(M.inmetHumidityOk(null), false);
  const r = M.rustFavorability({ temp_min: 20, temp_max: 28 });
  assert.strictEqual(r.urMean, null);
  assert.strictEqual(r.favorable, false);
  assert.strictEqual(r.tMean, 24);                               // temperatura continua calculada
  assert.deepStrictEqual(M.rustFavorability({ temp_min: 20, temp_max: 28, umidade_min: 60, umidade_max: 100 }), { tMean: 24, urMean: 80, favorable: true });
});

test('B2-T2-U2 separação dos dias com e sem umidade completa, sem alterar a resposta', () => {
  const dados = respostaDias({ '15/11/2026': dia('15/11/2026'), '16/11/2026': comUmidade('16/11/2026', APAGAR, APAGAR) });
  const antes = JSON.stringify(dados);
  const u = M.inmetUsableDays(M.parseInmetForecast(dados));
  assert.deepStrictEqual(u.days.map(d => d.date), ['15/11/2026']);
  assert.deepStrictEqual(u.omitted, ['16/11/2026']);
  assert.strictEqual(JSON.stringify(dados), antes);
  assert.deepStrictEqual(u.days[0], M.parseInmetForecast(dados)[0]);
  assert.strictEqual(JSON.stringify(M.inmetUsableDays(null)), JSON.stringify({ days: [], omitted: [] }));
});

testAsync('B2-T2-01 resposta completa: todos os dias, sem aviso, cópia guardada igual à resposta', async () => {
  const net = rede('ok');
  const { app, state, el } = abrir(T15, net);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(datas(state), '15/11,16/11,17/11,18/11');
  assert.strictEqual(JSON.stringify(state.inmet.omitted), JSON.stringify([]));
  assert.strictEqual(JSON.stringify(state.inmet.raw.data), JSON.stringify(SNAP_INMET.data));
  assert.ok(!el('weather-body').innerHTML.includes('sem umidade completa'));
  assert.ok(el('weather-body').innerHTML.includes('Umidade 50–90%'));
});

testAsync('B2-T2-02 um dia sem umidade: dia omitido, aviso claro, alerta e automação sem esse dia', async () => {
  const net = rede('ok');
  const dias = Object.fromEntries(CINCO.map(d => [d, dia(d)]));
  dias['16/11/2026'] = comUmidade('16/11/2026', APAGAR, APAGAR);  // 16/11 é favorável à ferrugem na cópia real
  respondeCom(net, respostaDias(dias));
  const { app, state, el } = abrir(T15, net);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(datas(state), '15/11,17/11,18/11');
  assert.strictEqual(JSON.stringify(state.inmet.omitted), JSON.stringify(['16/11/2026']));
  const body = el('weather-body').innerHTML;
  assert.ok(body.includes('1 dia(s) da resposta do INMET sem umidade completa (16/11)'), body);
  assert.deepStrictEqual(problemasVisiveis(body), []);
  assert.ok(climaAlertas(state)[0].desc.startsWith('17/11:'), climaAlertas(state)[0].desc);
  assert.ok(el('automations-grid').innerHTML.includes('1 dia(s) favorável(is)'));
  assert.ok(state.integrationLog[0].message.includes('omitido(s): 16/11'), state.integrationLog[0].message);
  assert.strictEqual(JSON.stringify(state.inmet.raw.data), JSON.stringify(respostaDias(dias)));   // cópia = resposta recebida
});

testAsync('B2-T2-03 umidade nula, tipo inválido ou faixa incompleta: o dia sai, os outros ficam', async () => {
  const casos = [[null, 90], [50, null], ['50', 90], [50, '90'], [true, 90], [50, APAGAR], [APAGAR, 90], [95, 60], [-5, 90], [50, 120]];
  for (const [a, b] of casos) {
    const net = rede('ok');
    const dias = Object.fromEntries(CINCO.map(d => [d, dia(d)]));
    dias['15/11/2026'] = comUmidade('15/11/2026', a, b);
    respondeCom(net, respostaDias(dias));
    const { app, state, el } = abrir(T15, net);
    await app.fetchInmetForecast();
    const caso = `${String(a)}–${String(b)}`;
    assert.strictEqual(state.inmet.status, 'live', caso);
    assert.strictEqual(datas(state), '16/11,17/11,18/11', caso);
    assert.ok(el('weather-body').innerHTML.includes('(15/11)'), caso);
    assert.deepStrictEqual(varrer({ body: el('weather-body') }), [], caso);
  }
});

testAsync('B2-T2-04 nenhum dia com umidade completa: a resposta é recusada e a reserva da M1 assume', async () => {
  const net = rede('ok');
  respondeCom(net, respostaDias(Object.fromEntries(CINCO.map(d => [d, comUmidade(d, APAGAR, APAGAR)]))));
  const { app, state, el } = abrir(T15, net);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.ok(/nenhum dia da resposta ao vivo tem umidade completa/.test(state.inmet.message), state.inmet.message);
  assert.strictEqual(datas(state), '15/11,16/11,17/11,18/11');
  assert.deepStrictEqual(problemasVisiveis(el('weather-body').innerHTML), []);
});

testAsync('B2-T2-05 variáveis válidas não mudam: temperaturas, resumo, vento e risco dos dias mantidos', async () => {
  const net = rede('ok');
  const dias = Object.fromEntries(CINCO.map(d => [d, dia(d)]));
  dias['18/11/2026'] = comUmidade('18/11/2026', 50, APAGAR);
  respondeCom(net, respostaDias(dias));
  const { app, state } = abrir(T15, net);
  await app.fetchInmetForecast();
  const esperado = M.parseInmetForecast(SNAP_INMET.data).filter(d => d.date !== '18/11/2026' && M.brDateToISO(d.date) >= '2026-11-15');
  assert.strictEqual(JSON.stringify(state.inmet.days), JSON.stringify(esperado));
});

testAsync('B2-T2-06 regras da M1 e do B1 mantidas: dia passado sai antes; resposta incompleta não vira reserva', async () => {
  const net = rede('ok');
  const dias = Object.fromEntries(CINCO.map(d => [d, dia(d)]));
  dias['14/11/2026'] = comUmidade('14/11/2026', APAGAR, APAGAR);   // dia passado em 15/11: sai pela regra de data
  dias['17/11/2026'] = comUmidade('17/11/2026', APAGAR, 90);
  respondeCom(net, respostaDias(dias));
  const t0 = new Date(2026, 10, 15, 10, 0).getTime();
  const { app, state, el, setNow, tick } = abrir(t0, net);
  await app.fetchInmetForecast(); app.startInmetAutoRefresh();
  assert.strictEqual(datas(state), '15/11,16/11,18/11');
  assert.strictEqual(JSON.stringify(state.inmet.omitted), JSON.stringify(['17/11/2026']));   // o passado não conta como omitido
  setNow(t0 + 31 * MIN); tick(); await settle();                 // reavaliação do B1: consulta de novo
  assert.strictEqual(state.inmet.status, 'live');
  assert.strictEqual(datas(state), '15/11,16/11,18/11');
  assert.deepStrictEqual(problemasVisiveis(el('weather-body').innerHTML), []);
  net.modo = 'falha'; setNow(t0 + 80 * MIN);
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'snapshot');
  assert.strictEqual(state.inmet.fallback.origin, 'cópia salva');  // M1: a resposta incompleta não serve de reserva
  assert.deepStrictEqual(problemasVisiveis(el('weather-body').innerHTML), []);
  const r = M.inmetSnapshotForecast(snapCom(SNAP_INMET.collectedAt, dias), at(COLETA + H), { today: '2026-11-14' });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.reason, 'estrutura');
});

testAsync('B2-T2-07 cópia salva com umidade fora da faixa (aceita pela M1): "UR indisponível", sem estimativa e sem NaN', async () => {
  const dias = Object.fromEntries(CINCO.map(d => [d, dia(d)]));
  dias['16/11/2026'] = comUmidade('16/11/2026', 60, 140);
  const { app, state, el } = loadApp({ nowMs: COLETA + H, inmetSnapshot: snapCom(SNAP_INMET.collectedAt, dias), fetchImpl: falhaAoVivo });
  await app.fetchInmetForecast();
  assert.strictEqual(state.inmet.status, 'snapshot');
  const d16 = state.inmet.days.find(d => d.date === '16/11/2026');
  assert.strictEqual(d16.risk.urMean, null);
  assert.strictEqual(d16.risk.favorable, false);
  const body = el('weather-body').innerHTML;
  assert.ok(body.includes('UR indisponível'));
  assert.ok(body.includes('sem estimativa de ferrugem (umidade incompleta)'));
  assert.deepStrictEqual(problemasVisiveis(body), []);
  assert.ok(climaAlertas(state)[0].desc.startsWith('17/11:'), climaAlertas(state)[0].desc);
});

/* ------------------------------ livro-caixa ------------------------------ */
const CHAVE = 'rigora.lancamentos.v1';
class FakeStorage {
  constructor(inicial = {}, opts = {}) {
    this.m = new Map(Object.entries(inicial)); this.recusar = opts.recusar || null; this.gravacoes = 0;
  }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) {
    if (this.recusar && this.recusar.test(k)) { const e = new Error('QuotaExceededError'); e.name = 'QuotaExceededError'; throw e; }
    this.gravacoes++; this.m.set(k, String(v));
  }
  removeItem(k) { this.m.delete(k); }
  chaves() { return [...this.m.keys()].sort(); }
  json(k = CHAVE) { return JSON.parse(this.getItem(k)); }
}

class FakeElB12 extends FakeEl { focus() {} }                  // o formulário pede foco ao abrir
/** Página com armazenamento controlado: FakeStorage, 'bloqueado' (acesso lança erro) ou 'ausente'. */
function loadAppB12({ nowMs = COLETA + H, storage = new FakeStorage() } = {}) {
  const els = {};
  const listeners = {};
  const sandbox = {
    console: { info() {}, warn() {}, log() {}, error() {} },
    document: {
      getElementById: (id) => (els[id] = els[id] || new FakeElB12()),
      createElement: () => new FakeElB12(), querySelector: () => new FakeElB12(), querySelectorAll: () => [],
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
      visibilityState: 'visible', body: new FakeEl()
    },
    location: { hash: '' }, history: { replaceState() {} },
    innerWidth: 1280, innerHeight: 800,
    fetch: falhaAoVivo, AbortController,
    setTimeout: (fn, ms) => (ms >= 1000 ? 0 : setTimeout(fn, ms)), clearTimeout,  // avisos e prazos longos não seguram o processo
    setInterval: () => 0, clearInterval() {}, addEventListener() {},
    __now: nowMs
  };
  if (storage === 'bloqueado') Object.defineProperty(sandbox, 'localStorage', { get() { throw new Error('SecurityError: acesso ao armazenamento negado'); } });
  else if (storage !== 'ausente') sandbox.localStorage = storage;
  sandbox.window = sandbox; sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext('(() => { const R = Date; globalThis.Date = class extends R { constructor(...a) { if (a.length) super(...a); else super(globalThis.__now); } static now() { return globalThis.__now; } }; })();', sandbox);
  for (const f of ['js/data.js', SNAPSHOT_FIXTURE, 'js/metrics.js', 'js/charts.js']) vm.runInContext(read(f), sandbox, { filename: f });
  vm.runInContext(read('js/app.js'), sandbox, { filename: 'js/app.js' });
  const app = vm.runInContext('RigoraApp', sandbox);
  (listeners.DOMContentLoaded || []).forEach(fn => fn());        // init(), como na página real
  return { app, state: app._state, el: (id) => (els[id] = els[id] || new FakeElB12()), els, storage, D: sandbox.RIGORA_DATA };
}

function lancar(ctx, { talhao = 'T01', categoria = 'Defensivos', data = '2026-11-10', desc = 'Fungicida para ferrugem', valor = '1850' } = {}) {
  ctx.app.openNewActivityModal();
  ctx.el('act-talhao').value = talhao; ctx.el('act-category').value = categoria; ctx.el('act-date').value = data;
  ctx.el('act-desc').value = desc; ctx.el('act-cost').value = valor; ctx.el('act-error').textContent = '';
  ctx.app.submitNewActivity();
}
const novos = (state) => state.entries.filter(e => e.isNew);
const cartaoFonteB12 = (el) => el('data-sources-grid').innerHTML.split('<article class="source-card">').find(p => p.includes('Livro-caixa da propriedade')) || '';
const registroValido = (id, extra = {}) => Object.assign({ id, date: '2026-10-05', safra: '2027', talhaoId: 'T02', category: 'Fertilizantes e Corretivos', description: 'Adubação de cobertura', value: 4200 }, extra);
const guardado = (lancamentos, extra = {}) => JSON.stringify(Object.assign({ versao: 1, salvoEm: '2026-11-14T10:00:00.000Z', lancamentos }, extra));

testAsync('B2-T3-01 lançamento novo é salvo neste navegador, numa chave versionada, sem a demonstração', async () => {
  const ctx = loadAppB12();
  const demo = ctx.state.entries.length;
  lancar(ctx);
  assert.strictEqual(ctx.el('act-error').textContent, '');
  assert.strictEqual(ctx.state.entries.length, demo + 1);
  assert.deepStrictEqual(ctx.storage.chaves(), [CHAVE]);
  const s = ctx.storage.json();
  assert.strictEqual(s.versao, 1);
  assert.ok(Number.isFinite(Date.parse(s.salvoEm)));
  assert.strictEqual(s.lancamentos.length, 1);
  const r = s.lancamentos[0];
  assert.deepStrictEqual(Object.keys(r).sort(), ['category', 'date', 'description', 'id', 'safra', 'talhaoId', 'value']);
  assert.ok(/^L\d{6}$/.test(r.id), r.id);
  assert.deepStrictEqual([r.date, r.safra, r.talhaoId, r.category, r.description, r.value], ['2026-11-10', '2027', 'T01', 'Defensivos', 'Fungicida para ferrugem', 1850]);
  assert.ok(!ctx.storage.getItem(CHAVE).includes('"L001"'));
  assert.strictEqual(novos(ctx.state)[0].stored, true);
  assert.ok(ctx.el('activities-table-body').innerHTML.includes('salvo neste navegador'));
  assert.ok(ctx.el('ledger-summary').innerHTML.includes('Lançamentos novos ficam salvos só neste navegador.'));
  assert.ok(!ctx.state.integrationLog.some(l => l.source === 'Livro-caixa'));
});

testAsync('B2-T3-02 o lançamento sobrevive ao reinício da página e entra nos indicadores do mesmo jeito', async () => {
  const storage = new FakeStorage();
  const a = loadAppB12({ storage });
  lancar(a, { valor: '2500.50', talhao: 'ALL' });
  const b = loadAppB12({ storage });
  assert.strictEqual(b.state.entries.length, a.state.entries.length);
  assert.strictEqual(JSON.stringify(novos(b.state)), JSON.stringify(novos(a.state)));
  assert.strictEqual(M.sum(b.state.entries, e => e.value), M.sum(a.state.entries, e => e.value));
  assert.strictEqual(novos(b.state)[0].value, 2500.5);
  lancar(b, { desc: 'Segunda aplicação' });
  const c = loadAppB12({ storage });
  assert.strictEqual(novos(c.state).length, 2);
  assert.strictEqual(new Set(c.state.entries.map(e => e.id)).size, c.state.entries.length);   // ids únicos
  assert.strictEqual(storage.json().lancamentos.length, 2);
  assert.strictEqual(c.state.entries.length, c.D.entries.length + 2);   // demonstração intacta, sem duplicar
});

testAsync('B2-T3-03 sem duplicação: registro repetido não entra duas vezes e continua guardado', async () => {
  const rep = registroValido('L123456');
  const storage = new FakeStorage({ [CHAVE]: guardado([rep, rep, registroValido('L123457', { value: 10 })]) });
  const ctx = loadAppB12({ storage });
  assert.strictEqual(JSON.stringify(novos(ctx.state).map(e => e.id)), JSON.stringify(['L123456', 'L123457']));
  assert.strictEqual(storage.gravacoes, 0);                      // abrir a página não grava
  assert.ok(cartaoFonteB12(ctx.el).includes('1 registro(s) salvo(s) inválido(s) ou repetido(s)'));
  lancar(ctx);
  const s = storage.json();
  assert.strictEqual(s.lancamentos.length, 3);
  assert.strictEqual(new Set(s.lancamentos.map(e => e.id)).size, 3);
  assert.strictEqual(JSON.stringify(s.ignorados), JSON.stringify([rep]));   // preservado, não apagado
  const de = loadAppB12({ storage });
  assert.strictEqual(novos(de.state).length, 3);
});

testAsync('B2-T3-04 registros inválidos dentro de um conteúdo válido: nenhum entra nos cálculos, todos ficam guardados', async () => {
  const ruins = [
    registroValido('L200001', { category: 'Categoria inventada' }), registroValido('L200002', { value: -5 }),
    registroValido('L200003', { value: '100' }), registroValido('L200004', { date: '2026-08-31' }),
    registroValido('L200005', { date: '2027-02-30' }), registroValido('L200006', { talhaoId: 'T99' }),
    registroValido('L200007', { description: 'x'.repeat(91) }), registroValido('L200008', { description: '   ' }),
    registroValido('L001'), registroValido('L200009', { safra: '2026' }), null, 'texto', 42, registroValido('L200010', { value: null })
  ];
  const storage = new FakeStorage({ [CHAVE]: guardado(ruins) });
  const ctx = loadAppB12({ storage });
  assert.strictEqual(novos(ctx.state).length, 0);
  assert.strictEqual(ctx.state.entries.length, ctx.D.entries.length);
  assert.ok(cartaoFonteB12(ctx.el).includes(`${ruins.length} registro(s) salvo(s) inválido(s)`));
  lancar(ctx);
  assert.strictEqual(JSON.stringify(storage.json().ignorados), JSON.stringify(ruins));
  assert.strictEqual(storage.json().lancamentos.length, 1);
});

testAsync('B2-T3-05 conteúdo ilegível ou de outra versão: preservado, copiado à parte antes de gravar, nunca apagado', async () => {
  for (const raw of ['{isto não é json', JSON.stringify({ versao: 2, lancamentos: [] }), JSON.stringify({ versao: '1', lancamentos: [] }), JSON.stringify({ versao: 1 }), JSON.stringify([1, 2]), 'null']) {
    const storage = new FakeStorage({ [CHAVE]: raw });
    const ctx = loadAppB12({ storage });
    assert.strictEqual(novos(ctx.state).length, 0, raw);
    assert.strictEqual(storage.getItem(CHAVE), raw, raw);        // abrir não altera nada
    assert.strictEqual(storage.gravacoes, 0, raw);
    assert.ok(cartaoFonteB12(ctx.el).includes('não puderam ser lidos'), raw);
    ctx.app.openNewActivityModal();
    assert.ok(ctx.el('act-storage-hint').textContent.startsWith('Atenção: os lançamentos salvos antes não puderam ser lidos'), raw);
    lancar(ctx);
    const copia = storage.chaves().find(k => k.startsWith(`${CHAVE}.corrompido.`));
    assert.ok(copia, raw);
    assert.strictEqual(storage.getItem(copia), raw, raw);        // cópia idêntica ao conteúdo anterior
    assert.strictEqual(storage.json().versao, 1, raw);
    assert.strictEqual(storage.json().lancamentos.length, 1, raw);
    assert.ok(cartaoFonteB12(ctx.el).includes(copia), raw);
  }
});

testAsync('B2-T3-06 se a cópia à parte falhar, nada é gravado e o usuário é avisado', async () => {
  const raw = '{ilegível';
  const storage = new FakeStorage({ [CHAVE]: raw }, { recusar: /corrompido/ });
  const ctx = loadAppB12({ storage });
  lancar(ctx);
  assert.strictEqual(storage.getItem(CHAVE), raw);
  assert.deepStrictEqual(storage.chaves(), [CHAVE]);
  assert.strictEqual(novos(ctx.state).length, 1);                // vale na sessão
  assert.strictEqual(novos(ctx.state)[0].stored, false);
  const log = ctx.state.integrationLog.find(l => l.source === 'Livro-caixa');
  assert.ok(log && log.message.includes('nada foi gravado'), log && log.message);
  assert.ok(ctx.el('activities-table-body').innerHTML.includes('só nesta sessão'));
  assert.ok(ctx.el('ledger-summary').innerHTML.includes('Atenção:'));
});

testAsync('B2-T3-07 armazenamento indisponível ou bloqueado: a página funciona, o lançamento vale na sessão e há aviso', async () => {
  for (const storage of ['bloqueado', 'ausente']) {
    const ctx = loadAppB12({ storage });
    const demo = ctx.state.entries.length;
    assert.strictEqual(demo, ctx.D.entries.length, storage);
    ctx.app.openNewActivityModal();
    assert.ok(ctx.el('act-storage-hint').textContent.startsWith('Atenção: o armazenamento deste navegador não está disponível'), storage);
    lancar(ctx);
    assert.strictEqual(ctx.state.entries.length, demo + 1, storage);
    assert.strictEqual(novos(ctx.state)[0].stored, false, storage);
    assert.ok(ctx.state.integrationLog.some(l => l.source === 'Livro-caixa' && l.level === 'error'), storage);
    const card = cartaoFonteB12(ctx.el);
    assert.ok(card.includes('aviso de armazenamento') && card.includes('Armazenamento deste navegador indisponível'), storage);
    assert.ok(ctx.el('activities-table-body').innerHTML.includes('só nesta sessão'), storage);
  }
});

testAsync('B2-T3-08 navegador recusa a gravação (cheio): aviso e lançamento mantido na sessão', async () => {
  const storage = new FakeStorage({}, { recusar: /^rigora\.lancamentos/ });
  const ctx = loadAppB12({ storage });
  lancar(ctx);
  assert.deepStrictEqual(storage.chaves(), []);
  assert.strictEqual(novos(ctx.state).length, 1);
  assert.ok(cartaoFonteB12(ctx.el).includes('A última gravação falhou (o navegador recusou a gravação'));
  storage.recusar = null;                                        // o armazenamento volta: o próximo salvamento grava os dois
  lancar(ctx, { desc: 'Outro' });
  assert.strictEqual(storage.json().lancamentos.length, 2);
  assert.ok(novos(ctx.state).every(e => e.stored));
  assert.ok(!cartaoFonteB12(ctx.el).includes('falhou'));
});

testAsync('B2-T3-09 formulário: validação de antes mantida e nada gravado quando o lançamento é recusado', async () => {
  const ctx = loadAppB12();
  lancar(ctx, { data: '2026-08-31' });
  assert.ok(ctx.el('act-error').textContent.includes('A data deve estar no ciclo da safra 2027'));
  lancar(ctx, { valor: '0' });
  assert.ok(ctx.el('act-error').textContent.includes('Informe um valor maior que zero.'));
  lancar(ctx, { talhao: 'T99' });
  assert.ok(ctx.el('act-error').textContent.includes('Confira talhão, categoria e descrição'));
  assert.strictEqual(novos(ctx.state).length, 0);
  assert.strictEqual(ctx.storage.gravacoes, 0);
});

testAsync('B2-T3-10 a interface informa que o armazenamento é local a este navegador', async () => {
  const ctx = loadAppB12();
  ctx.app.openNewActivityModal();
  assert.ok(ctx.el('act-storage-hint').textContent.includes('só neste navegador'));
  const html = read('index.html');
  assert.ok(/id="act-storage-hint"[^>]*>O lançamento fica salvo só neste navegador/.test(html));
  const fonte = ctx.D.dataSources.find(s => s.id === 'SRC-LEDGER');
  assert.ok(fonte.limitations.includes('só neste navegador') && fonte.limitations.includes('não há conta, servidor nem sincronização'));
  lancar(ctx);
  assert.ok(cartaoFonteB12(ctx.el).includes('Demonstrativo · salvo neste navegador'));
});

test('B2-T9-01 área total da propriedade = soma dos talhões (12 ha), sem o 14 divergente', () => {
  const soma = D.talhoes.reduce((s, t) => s + t.areaHa, 0);
  assert.strictEqual(soma, 12);
  assert.strictEqual(D.property.totalAreaHa, soma);
  const html = read('index.html');
  assert.ok(!html.includes('14,0 ha'));
  assert.ok(html.includes('id="prop-context-sub">Patrocínio (MG) · Cerrado Mineiro · 12,0 ha em 3 talhões'));
});

testAsync('B2-T9-02 indicadores e textos da tela usam a mesma área', async () => {
  const ctx = loadAppB12();
  lancar(ctx);                                                   // renderAll: visão geral, propriedades, simulador
  assert.ok(ctx.el('ov-context-sub').textContent.includes('12,0 ha de café arábica'), ctx.el('ov-context-sub').textContent);
  assert.strictEqual(ctx.el('prop-context-sub').textContent, 'Patrocínio (MG) · Cerrado Mineiro · 12,0 ha em 3 talhões de café arábica · sequeiro');
  assert.strictEqual(ctx.state.scenarios.base.areaHa, ctx.D.property.totalAreaHa);
  assert.ok(!JSON.stringify(Object.values(ctx.els).map(e => e.innerHTML + e.textContent)).includes('14,0 ha'));
});

testAsync('B2-SCAN-01 varredura da tela inteira: nenhum undefined, NaN, null ou faixa incompleta', async () => {
  // 1) Página completa com armazenamento problemático e lançamento novo
  for (const storage of [new FakeStorage(), new FakeStorage({ [CHAVE]: '{x' }), 'bloqueado']) {
    const ctx = loadAppB12({ storage });
    lancar(ctx);
    await ctx.app.fetchInmetForecast();
    assert.deepStrictEqual(varrer(ctx.els), [], String(storage));
  }
  // 2) Previsão ao vivo com vários defeitos de umidade, com reavaliação
  const net = rede('ok');
  const dias = Object.fromEntries(CINCO.map(d => [d, dia(d)]));
  dias['15/11/2026'] = comUmidade('15/11/2026', null, 90);
  dias['17/11/2026'] = comUmidade('17/11/2026', 80, APAGAR);
  respondeCom(net, respostaDias(dias));
  const v = abrir(T15, net);
  await v.app.fetchInmetForecast(); v.app.startInmetAutoRefresh();
  v.setNow(T15 + 31 * MIN); v.tick(); await settle();
  for (const id of ['weather-body', 'automations-grid', 'data-sources-grid', 'integration-log', 'notif-list'])
    assert.deepStrictEqual(varrer({ [id]: v.el(id) }), [], id);
  assert.ok(v.state.alerts.every(a => problemasVisiveis(`${a.title} ${a.desc} ${a.impact || ''}`).length === 0));
});

(async () => {
  let atual = null;
  try {
    for (const [name, fn] of pendentes) { atual = name; await fn(); passed++; console.log('  ok -', name); }
    console.log(`\n${passed} testes aprovados, 0 reprovados.`);
  } catch (e) {
    console.error(`  FALHOU - ${atual}\n${e && e.stack ? e.stack : e}`);
    console.log(`\n${passed} testes aprovados, 1 reprovado.`);
    process.exitCode = 1;
  }
})();
