/**
 * Verificador independente do snapshot exportado (etapa B5). Não altera nenhum arquivo.
 *
 * Uso:
 *   node tests/check_exported_snapshot.js <gerado.js> <referencia.js> [--json relatorio.json]
 *   node tests/check_exported_snapshot.js --autoteste <gerado.js> <referencia.js>
 *
 * Confere: contrato, dados (60 municípios, 10 anos, rendimento = produção / área, média de MG,
 * execução de origem), diferenças contra a cópia de referência, carregamento da aplicação com a
 * rede sempre falhando e as regras da M1 para a previsão do INMET.
 *
 * Os testes M1/B1/B2 de tests/metrics.test.js continuam rodando contra a fixture do T0. Aqui as
 * regras da M1 são aplicadas ao arquivo gerado com o relógio relativo às datas registradas nele
 * (geração e coleta), nunca ao cenário fixo de 14/11. Nenhuma consulta real: o fetch é simulado.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const M = require('../js/metrics.js');

const ROOT = path.join(__dirname, '..');
const H = 3600 * 1000;
const MIN = 60 * 1000;
const ANOS = ['2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025'];
const PATRO = '3148103';
const TOL_RENDIMENTO = 0.5;            // kg/ha: o rendimento é produção / área arredondado
const LIMITE_M1_H = 24;                // mesmo limite da M1 (js/metrics.js) e do exportador
const copy = (o) => JSON.parse(JSON.stringify(o));
const isObj = (o) => o !== null && typeof o === 'object' && !Array.isArray(o);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const isIso = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s) && Number.isFinite(Date.parse(s));

/* ---------------------------------------------------------------- leitura */
function lerSnapshot(texto, nome) {
  const sandbox = { window: {} };
  vm.runInNewContext(texto, sandbox, { filename: nome });
  const s = sandbox.window.RIGORA_SNAPSHOTS;
  if (!isObj(s)) throw new Error(`${nome}: não define window.RIGORA_SNAPSHOTS`);
  return copy(s);                                               // sai do contexto isolado
}
function carregar(arquivo) {
  const texto = fs.readFileSync(arquivo, 'utf8');
  return { arquivo, texto, snap: lerSnapshot(texto, arquivo) };
}
const textoDe = (snap) => `window.RIGORA_SNAPSHOTS = ${JSON.stringify(snap)};\n`;

/* -------------------------------------------------------------- contrato */
function verificarContrato(texto, s) {
  const p = [];
  if (texto !== null) {
    if (!texto.startsWith('/**')) p.push('arquivo não começa com o comentário /**');
    if (!texto.includes('window.RIGORA_SNAPSHOTS = {')) p.push('falta "window.RIGORA_SNAPSHOTS = {"');
    if (!texto.endsWith('};\n')) p.push('arquivo não termina com "};"');
  }
  const chaves = Object.keys(s);
  if (chaves.join() !== 'sidra,inmet,exportacao') p.push(`seções esperadas sidra, inmet, exportacao; encontradas: ${chaves.join(', ')}`);
  const sd = s.sidra;
  if (!isObj(sd)) p.push('sidra ausente');
  else {
    for (const [k, ok] of [['source', typeof sd.source === 'string'], ['query', typeof sd.query === 'string'],
      ['collectedAt', isIso(sd.collectedAt)], ['municipiosMG', Number.isInteger(sd.municipiosMG) && sd.municipiosMG > 0],
      ['fields', Array.isArray(sd.fields) && sd.fields.length === 3 && sd.fields.every(f => typeof f === 'string')],
      ['mg', isObj(sd.mg)], ['municipios', Array.isArray(sd.municipios)]]) if (!ok) p.push(`sidra.${k} ausente ou do tipo errado`);
    const trio = (v) => Array.isArray(v) && v.length === 3 && v.every(x => x === null || isNum(x));
    Object.entries(isObj(sd.mg) ? sd.mg : {}).forEach(([y, v]) => { if (!/^\d{4}$/.test(y) || !trio(v)) p.push(`sidra.mg[${y}] inválido`); });
    (Array.isArray(sd.municipios) ? sd.municipios : []).forEach((m, i) => {
      if (!isObj(m) || Object.keys(m).join() !== 'c,n,meso,y' || !/^\d{7}$/.test(String(m.c)) || typeof m.n !== 'string' || !m.n
        || !(m.meso === null || typeof m.meso === 'string') || !isObj(m.y)) { p.push(`município #${i} fora do formato {c, n, meso, y}`); return; }
      Object.entries(m.y).forEach(([y, v]) => { if (!/^\d{4}$/.test(y) || !trio(v)) p.push(`município ${m.c} ano ${y} inválido`); });
    });
  }
  const im = s.inmet;
  if (im === undefined) p.push('chave inmet ausente');
  else if (im !== null) {
    if (!isIso(im.collectedAt) || !isObj(im.data) || !Object.keys(im.data).length) p.push('inmet sem collectedAt ISO ou sem data');
    Object.entries(isObj(im.data) ? im.data : {}).forEach(([cod, dias]) => {
      if (!isObj(dias) || !Object.keys(dias).length) { p.push(`inmet.data[${cod}] vazio`); return; }
      Object.entries(dias).forEach(([d, e]) => {
        if (!/^\d{2}\/\d{2}\/\d{4}$/.test(d)) p.push(`inmet: data fora do formato dd/mm/aaaa: ${d}`);
        const regs = isObj(e) ? ['manha', 'tarde', 'noite'].filter(k => k in e).map(k => e[k]) : [];
        (regs.length ? regs : [e]).forEach(r => {
          if (!isObj(r)) { p.push(`inmet ${d}: registro inválido`); return; }
          ['temp_min', 'temp_max', 'umidade_min', 'umidade_max'].forEach(k => { if (!isNum(r[k])) p.push(`inmet ${d}: ${k} precisa ser número`); });
          if (typeof r.dia_semana !== 'string') p.push(`inmet ${d}: dia_semana ausente`);
        });
      });
    });
  }
  const ex = s.exportacao;
  if (!isObj(ex)) p.push('exportacao ausente');
  else {
    if (!isIso(ex.geradoEm)) p.push('exportacao.geradoEm ausente ou não ISO');
    for (const k of ['geradoPor', 'banco', 'aviso']) if (typeof ex[k] !== 'string') p.push(`exportacao.${k} ausente`);
    for (const k of ['sidra', 'inmet']) if (!isObj(ex[k]) || typeof ex[k].modo !== 'string') p.push(`exportacao.${k}.modo ausente`);
  }
  return p;
}

/* ----------------------------------------------------------------- dados */
function verificarDados(s) {
  const p = [];
  const sd = s.sidra || {}, mun = Array.isArray(sd.municipios) ? sd.municipios : [];
  if (mun.length !== 60) p.push(`esperados 60 municípios; o arquivo tem ${mun.length}`);
  const cods = mun.map(m => m && m.c);
  if (new Set(cods).size !== cods.length) p.push('códigos de município repetidos');
  if (!cods.includes(PATRO)) p.push('Patrocínio (3148103) ausente');
  const coerente = (t) => t[1] > 0 && t[2] > 0 && Math.abs(t[0] - t[2] * 1000 / t[1]) <= TOL_RENDIMENTO;
  mun.forEach(m => {
    if (!m || !isObj(m.y)) return;
    const anos = Object.keys(m.y).sort();
    if (anos.join() !== ANOS.join()) p.push(`município ${m.c}: esperados os 10 anos 2016–2025; tem ${anos.join(', ') || 'nenhum'}`);
    Object.entries(m.y).forEach(([y, t]) => {
      if (!Array.isArray(t)) return;
      if (t.every(v => v === null)) return;                                   // ano sem dado no IBGE: null, nunca zero
      if (t.some(v => v === null)) p.push(`município ${m.c} ${y}: trio incompleto ${JSON.stringify(t)}`);
      else if (!coerente(t)) p.push(`município ${m.c} ${y}: rendimento ${t[0]} ≠ produção / área (${(t[2] * 1000 / t[1]).toFixed(1)})`);
    });
  });
  const mg = isObj(sd.mg) ? sd.mg : {};
  if (Object.keys(mg).sort().join() !== ANOS.join()) p.push(`média de MG: esperados os 10 anos; tem ${Object.keys(mg).sort().join(', ')}`);
  Object.entries(mg).forEach(([y, t]) => {
    if (!Array.isArray(t) || t.some(v => !isNum(v))) { p.push(`média de MG ${y}: valores ausentes`); return; }
    if (!coerente(t)) p.push(`média de MG ${y}: rendimento ${t[0]} ≠ produção / área (${(t[2] * 1000 / t[1]).toFixed(1)})`);
    const soma = mun.reduce((a, m) => a + ((m && m.y && m.y[y] && m.y[y][1]) || 0), 0);
    if (soma > t[1] + 0.5) p.push(`média de MG ${y}: área de MG (${t[1]}) menor que a soma dos 60 municípios (${soma})`);
  });
  const ex = s.exportacao || {}, es = ex.sidra || {};
  if (es.modo !== 'sqlite') p.push(`seção sidra não veio do banco: modo "${es.modo}"`);
  if (!Number.isInteger(es.execucaoId)) p.push('exportacao.sidra.execucaoId ausente');
  if (JSON.stringify(es.execucoesNosDadosExportados) !== JSON.stringify([es.execucaoId])) p.push('dados de mais de uma execução ou execução não identificada');
  if (es.statusExecucao !== 'sucesso') p.push(`execução de origem com status "${es.statusExecucao}"`);
  if (!es.validacao || es.validacao.resultado !== 'aprovado') p.push('validação da execução de origem não aprovada');
  if (es.municipiosExportados !== mun.length || es.registrosExportados !== mun.length * ANOS.length) p.push('contagens de exportacao.sidra não batem com o arquivo');
  if (es.municipiosNaMediaMG !== sd.municipiosMG) p.push('municipiosNaMediaMG diferente de sidra.municipiosMG');
  if (Date.parse(es.dataColeta) !== Date.parse(sd.collectedAt)) p.push('sidra.collectedAt diferente da data de coleta da execução');
  if (Date.parse(ex.geradoEm) < Date.parse(es.dataColeta)) p.push('arquivo gerado antes da coleta que diz conter');
  const ei = ex.inmet || {};
  if (s.inmet === null && ei.modo !== 'indisponivel') p.push('inmet null sem modo "indisponivel" em exportacao.inmet');
  if (s.inmet === null && typeof ei.motivo !== 'string') p.push('inmet null sem motivo registrado');
  if (s.inmet !== null && ei.modo === 'indisponivel') p.push('exportacao.inmet diz indisponível, mas há previsão no arquivo');
  return p;
}

/* -------------------------------------------- diferenças contra a referência */
function compararReferencia(g, r) {
  const dif = [], esperadas = [];
  const gs = g.sidra || {}, rs = r.sidra || {};
  for (const k of ['source', 'query', 'fields', 'municipiosMG']) {
    if (JSON.stringify(gs[k]) !== JSON.stringify(rs[k])) dif.push({ onde: `sidra.${k}`, referencia: rs[k], gerado: gs[k] });
  }
  ANOS.forEach(y => {
    const a = (rs.mg || {})[y], b = (gs.mg || {})[y];
    if (JSON.stringify(a) !== JSON.stringify(b)) dif.push({ onde: `sidra.mg[${y}]`, referencia: a, gerado: b });
  });
  const rm = new Map((rs.municipios || []).map(m => [m.c, m])), gm = new Map((gs.municipios || []).map(m => [m.c, m]));
  for (const c of rm.keys()) if (!gm.has(c)) dif.push({ onde: `município ${c}`, referencia: rm.get(c).n, gerado: 'ausente' });
  for (const c of gm.keys()) if (!rm.has(c)) dif.push({ onde: `município ${c}`, referencia: 'ausente', gerado: gm.get(c).n });
  for (const [c, a] of rm) {
    const b = gm.get(c); if (!b) continue;
    if (a.n !== b.n) dif.push({ onde: `município ${c}.n`, referencia: a.n, gerado: b.n });
    if (a.meso !== b.meso) dif.push({ onde: `município ${c}.meso`, referencia: a.meso, gerado: b.meso });
    const anos = new Set([...Object.keys(a.y || {}), ...Object.keys(b.y || {})]);
    [...anos].sort().forEach(y => {
      if (JSON.stringify((a.y || {})[y]) !== JSON.stringify((b.y || {})[y])) dif.push({ onde: `município ${c} ${y}`, referencia: (a.y || {})[y], gerado: (b.y || {})[y] });
    });
  }
  const ordemR = (rs.municipios || []).map(m => m.c).join(), ordemG = (gs.municipios || []).map(m => m.c).join();
  if (ordemR !== ordemG) esperadas.push({ onde: 'ordem dos municípios', motivo: 'a interface ordena por conta própria', referencia: 'ordem da cópia manual', gerado: 'ordem do exportador' });
  if (gs.collectedAt !== rs.collectedAt) {
    esperadas.push({ onde: 'sidra.collectedAt', motivo: 'a referência foi montada à mão a partir da consulta da interface; o arquivo gerado usa a data da execução do banco', referencia: rs.collectedAt, gerado: gs.collectedAt });
  }
  if (JSON.stringify(g.inmet) !== JSON.stringify(r.inmet)) {
    const ei = (g.exportacao || {}).inmet || {};
    esperadas.push({ onde: 'inmet', motivo: g.inmet === null ? `o exportador não exporta previsão vencida (${ei.motivo || 'sem motivo'})` : 'previsão de outra coleta',
      referencia: r.inmet ? `coleta de ${r.inmet.collectedAt}` : null, gerado: g.inmet ? `coleta de ${g.inmet.collectedAt}` : null });
  }
  if (!('exportacao' in r) && 'exportacao' in g) esperadas.push({ onde: 'exportacao', motivo: 'seção nova da etapa 3e; a referência manual não a tem', referencia: 'ausente', gerado: 'presente' });
  return { diferencasDeDados: dif, diferencasEsperadas: esperadas };
}

/* ------------------------------------------------------- aplicação simulada */
class FakeEl {
  constructor() {
    this._html = ''; this.textContent = ''; this.className = ''; this.value = ''; this.disabled = false; this.title = '';
    this.style = {}; this.dataset = {}; this.options = [];
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }
  get innerHTML() { return this._html; }
  /** Como um <select> do navegador: ao receber <option>s, passa a valer a primeira opção. */
  set innerHTML(html) {
    this._html = String(html);
    const opts = [...this._html.matchAll(/<option value="([^"]*)"/g)].map(m => m[1]);
    if (opts.length) { this.options = opts.map(v => ({ value: v })); if (!opts.includes(this.value)) this.value = opts[0]; }
  }
  setAttribute() {} getAttribute() { return null; } addEventListener() {} appendChild() {} remove() {} scrollIntoView() {}
  querySelector() { return new FakeEl(); } querySelectorAll() { return []; } closest() { return null; } contains() { return false; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; }
}
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise(r => setImmediate(r)); };
const visivel = (html) => String(html).replace(/<[^>]*>/g, '').trim();

/** Carrega a página com o snapshot dado (texto), relógio fixo em nowMs e rede que sempre falha. */
async function abrirAplicacao(textoSnapshot, nowMs) {
  const els = {}, listeners = {}, erros = [], urls = [];
  const sandbox = {
    console: { info() {}, warn() {}, log() {}, error: (...a) => erros.push('console.error: ' + a.join(' ')) },
    document: {
      getElementById: (id) => (els[id] = els[id] || new FakeEl()),
      createElement: () => new FakeEl(), querySelector: () => new FakeEl(), querySelectorAll: () => [],
      addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); }, visibilityState: 'visible', body: new FakeEl()
    },
    localStorage: { getItem: () => null, setItem() {} }, location: { hash: '' }, history: { replaceState() {} },
    innerWidth: 1280, innerHeight: 800, AbortController, setTimeout, clearTimeout,
    setInterval: () => 1, clearInterval() {}, addEventListener() {},
    fetch: (url) => { urls.push(String(url)); return Promise.reject(new Error('Failed to fetch (rede simulada indisponível)')); },
    __now: nowMs
  };
  sandbox.window = sandbox; sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext('(() => { const R = Date; globalThis.Date = class extends R { constructor(...a) { if (a.length) super(...a); else super(globalThis.__now); } static now() { return globalThis.__now; } }; })();', sandbox);
  const rejeicoes = [];
  const onRej = (e) => rejeicoes.push('promessa rejeitada sem tratamento: ' + (e && e.message ? e.message : e));
  process.on('unhandledRejection', onRej);
  try {
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'js/data.js'), 'utf8'), sandbox, { filename: 'js/data.js' });
    vm.runInContext(textoSnapshot, sandbox, { filename: 'snapshot-gerado.js' });
    for (const f of ['js/metrics.js', 'js/charts.js', 'js/app.js']) vm.runInContext(fs.readFileSync(path.join(ROOT, f), 'utf8'), sandbox, { filename: f });
    (listeners.DOMContentLoaded || []).forEach(fn => fn());
    await settle();
  } catch (e) {
    erros.push('erro ao carregar: ' + (e && e.stack ? e.stack.split('\n')[0] : e));
  } finally {
    process.removeListener('unhandledRejection', onRej);
  }
  let state = null;
  try { state = vm.runInContext('RigoraApp', sandbox)._state; } catch (e) { erros.push('RigoraApp indisponível'); }
  return { state, el: (id) => els[id] || new FakeEl(), erros: erros.concat(rejeicoes), urls, snap: sandbox.RIGORA_SNAPSHOTS };
}
const dataBR = (iso) => new Date(iso).toLocaleDateString('pt-BR');

async function verificarAplicacao(snap, nowMs, esperadoInmet) {
  const p = [], info = {};
  const app = await abrirAplicacao(textoDe(snap), nowMs);
  p.push(...app.erros);
  const { state, el } = app;
  if (!state) return { problemas: p.concat('aplicação não iniciou'), info };
  for (const id of ['ov-kpis', 'bench-insights', 'bench-rank-title', 'bench-series-title', 'historical-table-body', 'bench-source-line']) {
    if (visivel(el(id).innerHTML || el(id).textContent).length < 5) p.push(`tela ${id} não foi renderizada`);
  }
  info.kpis = (el('ov-kpis').innerHTML.match(/class="kpi-card/g) || []).length;
  if (!app.urls.length) p.push('a aplicação não tentou nenhuma consulta (rede simulada não foi exercitada)');
  info.consultasSimuladas = app.urls.length;
  // IBGE: a cópia é a base exibida; com a rede falhando, selo de cópia oficial datada
  const selo = visivel(el('regional-status-badge').innerHTML);
  info.seloIbge = selo;
  if (state.ibge.status !== 'snapshot') p.push(`IBGE: status "${state.ibge.status}" (esperado "snapshot" com a rede falhando)`);
  if (!selo.startsWith('Cópia oficial de ' + dataBR(snap.sidra.collectedAt))) p.push(`IBGE: selo "${selo}" não mostra a cópia oficial com a data da coleta`);
  if (/conferid|divergem|ao vivo/i.test(selo)) p.push(`IBGE: selo afirma conferência sem consulta: "${selo}"`);
  if (!el('regional-status-badge').className.includes('warning')) p.push('IBGE: selo sem a cor de aviso');
  if (!state.alerts.some(a => a.id === 'dq-ibge-snapshot')) p.push('IBGE: falta o aviso de cópia oficial datada');
  if (JSON.stringify(state.ibge.municipios) !== JSON.stringify(snap.sidra.municipios)) p.push('IBGE: dados em tela diferentes do arquivo');
  if (JSON.stringify(app.snap.sidra) !== JSON.stringify(snap.sidra)) p.push('IBGE: a aplicação alterou o snapshot em memória');
  // INMET
  const seloInmet = visivel(el('weather-status-badge').innerHTML);
  const clima = state.alerts.filter(a => a.cat === 'clima');
  info.seloInmet = seloInmet; info.inmetStatus = state.inmet.status; info.inmetMotivo = state.inmet.fallback && state.inmet.fallback.reason;
  info.alertasClima = clima.length;
  if (esperadoInmet.ok) {
    if (state.inmet.status !== 'snapshot') p.push(`INMET: status "${state.inmet.status}" (esperado "snapshot")`);
    if (!seloInmet.startsWith('Cópia de')) p.push(`INMET: selo "${seloInmet}" (esperado "Cópia de …")`);
    if (!state.inmet.days || state.inmet.days.length !== esperadoInmet.days.length) p.push('INMET: dias exibidos diferentes dos dias válidos pela M1');
    const hoje = M.localDateISO(new Date(nowMs));
    if ((state.inmet.days || []).some(d => M.brDateToISO(d.date) < hoje)) p.push('INMET: dia que já passou na tela');
  } else {
    if (state.inmet.status !== 'error' || state.inmet.days !== null) p.push(`INMET: status "${state.inmet.status}" (esperado "error", sem dias)`);
    if (!state.inmet.fallback || state.inmet.fallback.reason !== esperadoInmet.reason) p.push(`INMET: motivo "${state.inmet.fallback && state.inmet.fallback.reason}" (esperado "${esperadoInmet.reason}")`);
    if (!seloInmet.includes('Indisponível')) p.push(`INMET: selo "${seloInmet}" (esperado "Indisponível")`);
    if (!el('weather-body').innerHTML.includes('Previsão indisponível agora')) p.push('INMET: card não diz "Previsão indisponível agora"');
    if (clima.length) p.push(`INMET: ${clima.length} alerta(s) climático(s) sem previsão válida`);
    if (!state.alerts.some(a => a.id === 'dq-inmet-error')) p.push('INMET: falta o aviso de previsão indisponível');
  }
  return { problemas: p, info };
}

/* ------------------------------------------------------- regras M1 (INMET) */
/**
 * Arquivo gerado: aplica a M1 com o relógio no instante da geração (exportacao.geradoEm).
 * Cenários 2–5: derivados em memória da previsão real da referência, com o relógio relativo à
 * coleta registrada nela (C). Valem para qualquer data de coleta, não só para 14/11.
 */
async function verificarM1(g, r) {
  const casos = [], p = [];
  const caso = (nome, ok, detalhe) => { casos.push({ nome, ok, detalhe }); if (!ok) p.push(`M1: ${nome} — ${detalhe}`); };
  const em = (ms) => new Date(ms);
  const geradoEm = Date.parse(g.exportacao.geradoEm);
  // 1. o próprio arquivo gerado
  const rg = M.inmetSnapshotForecast(g.inmet, em(geradoEm));
  if (g.inmet === null) {
    caso('1. inmet null → previsão indisponível', rg.ok === false && rg.reason === 'ausente', `motivo "${rg.reason}"`);
    const ei = g.exportacao.inmet || {};
    if (ei.dataColeta) {
      const idade = (geradoEm - Date.parse(ei.dataColeta)) / H;
      const ref = r && r.inmet ? M.inmetSnapshotForecast({ collectedAt: ei.dataColeta, data: r.inmet.data }, em(geradoEm)) : null;
      caso('1b. a coleta registrada no banco está fora das 24 h (por isso null)', idade > LIMITE_M1_H && (!ref || ref.reason === 'vencida'),
        `idade ${idade.toFixed(1)} h na geração${ref ? `; a M1 recusaria essa coleta por "${ref.reason}"` : ''}`);
    }
  } else {
    const idade = (geradoEm - Date.parse(g.inmet.collectedAt)) / H;
    caso('1. previsão exportada é válida pela M1 no instante da geração', rg.ok === true, `idade ${idade.toFixed(1)} h, motivo "${rg.reason}"`);
    const hoje = M.localDateISO(em(geradoEm));
    if (rg.ok) caso('1c. só dias de hoje em diante', rg.days.every(d => M.brDateToISO(d.date) >= hoje), `${rg.days.length} dia(s)`);
  }
  if (!r || !r.inmet) { caso('2–5. cenários sintéticos', false, 'a referência não tem previsão para derivar os cenários'); return { casos, problemas: p }; }
  const C = Date.parse(r.inmet.collectedAt), base = r.inmet, total = Object.keys(base.data[PATRO] || {}).length;
  // 2. válida (até 24 h): só hoje e futuros
  for (const h of [2, 23, 24]) {
    const now = C + h * H, res = M.inmetSnapshotForecast(base, em(now)), hoje = M.localDateISO(em(now));
    caso(`2. coleta com ${h} h → usada, só com dias de hoje em diante`, res.ok === true && res.days.every(d => M.brDateToISO(d.date) >= hoje)
      && res.days.length + res.removedPast === total, res.ok ? `${res.days.length} dia(s), ${res.removedPast} passado(s) removido(s)` : `motivo "${res.reason}"`);
  }
  // 3. mais de 24 h: vencida
  for (const extra of [MIN, 2 * H]) {
    const res = M.inmetSnapshotForecast(base, em(C + 24 * H + extra));
    caso(`3. coleta com 24 h + ${extra / MIN} min → recusada como vencida`, res.ok === false && res.reason === 'vencida', `motivo "${res.reason}"`);
  }
  // 4. datas inválidas ou futuras além da tolerância
  const agora = C + 2 * H;
  const futuro = (min) => M.inmetSnapshotForecast(Object.assign(copy(base), { collectedAt: new Date(agora + min * MIN).toISOString() }), em(agora));
  caso('4a. coleta 10 min no futuro (além da tolerância de 5 min) → recusada', futuro(10).reason === 'data_invalida', `motivo "${futuro(10).reason}"`);
  caso('4b. coleta 4 min no futuro (dentro da tolerância) → aceita', futuro(4).ok === true, `motivo "${futuro(4).reason}"`);
  for (const ruim of ['ontem', '2026-13-40T99:00:00Z', '']) {
    const res = M.inmetSnapshotForecast(Object.assign(copy(base), { collectedAt: ruim }), em(agora));
    caso(`4c. collectedAt "${ruim}" → recusada`, res.ok === false && res.reason === 'data_invalida', `motivo "${res.reason}"`);
  }
  const diaRuim = copy(base); const k0 = Object.keys(diaRuim.data[PATRO])[0];
  diaRuim.data[PATRO]['31/02/2026'] = diaRuim.data[PATRO][k0];
  const rd = M.inmetSnapshotForecast(diaRuim, em(agora));
  caso('4d. dia com data inexistente (31/02) → cópia recusada', rd.ok === false, `motivo "${rd.reason}"`);
  // 5. os mesmos cenários pela aplicação: arquivo gerado com a seção inmet trocada em memória (sintético)
  for (const [nome, now] of [['coleta válida (2 h)', C + 2 * H], ['coleta vencida (24 h + 1 min)', C + 24 * H + MIN]]) {
    const sint = copy(g); sint.inmet = copy(base);
    const esperado = M.inmetSnapshotForecast(base, em(now));
    const res = await verificarAplicacao(sint, now, esperado);
    caso(`5. aplicação, ${nome} [cenário sintético]`, res.problemas.length === 0,
      res.problemas.join('; ') || `${res.info.inmetStatus}${res.info.inmetMotivo ? ' (' + res.info.inmetMotivo + ')' : ''}, selo "${res.info.seloInmet}", alertas de clima ${res.info.alertasClima}`);
  }
  return { casos, problemas: p };
}

/* ---------------------------------------------------------------- relatório */
async function verificar(gerado, referencia) {
  const contrato = verificarContrato(gerado.texto, gerado.snap);
  const dados = contrato.length ? ['dados não conferidos: contrato inválido'] : verificarDados(gerado.snap);
  const comp = compararReferencia(gerado.snap, referencia.snap);
  let app = { problemas: ['aplicação não conferida: contrato inválido'], info: {} };
  let m1 = { casos: [], problemas: ['M1 não conferida: contrato inválido'] };
  if (!contrato.length) {
    const now = Date.parse(gerado.snap.exportacao.geradoEm);
    app = await verificarAplicacao(gerado.snap, now, M.inmetSnapshotForecast(gerado.snap.inmet, new Date(now)));
    m1 = await verificarM1(gerado.snap, referencia.snap);
  }
  const reprovacoes = [...contrato, ...dados, ...comp.diferencasDeDados.map(d => `diferença de dado em ${d.onde}`), ...app.problemas, ...m1.problemas];
  return { aprovado: reprovacoes.length === 0, contrato, dados, ...comp, aplicacao: app, m1, reprovacoes };
}

function imprimir(r, g) {
  const s = g.snap, ex = s.exportacao || {};
  const linha = (ok, t) => console.log(`  ${ok ? 'ok   ' : 'FALHA'} ${t}`);
  console.log(`Verificador do snapshot exportado (B5)\n  arquivo: ${path.basename(g.arquivo)} · gerado em ${ex.geradoEm} · execução IBGE ${ex.sidra && ex.sidra.execucaoId}`);
  console.log('\nContrato'); linha(!r.contrato.length, r.contrato.length ? r.contrato.join('; ') : 'seções sidra, inmet e exportacao; tipos e formatos conferidos');
  console.log('\nDados');
  linha(!r.dados.length, r.dados.length ? r.dados.join('; ')
    : `${s.sidra.municipios.length} municípios × 10 anos; rendimento = produção / área (±${TOL_RENDIMENTO} kg/ha) em todos os trios; média de MG coerente nos 10 anos; execução ${ex.sidra.execucaoId} (${ex.sidra.modo}, ${ex.sidra.statusExecucao})`);
  console.log('\nComparação com a referência');
  linha(!r.diferencasDeDados.length, `${r.diferencasDeDados.length} diferença(s) de dados (valores, municípios, média de MG)`);
  r.diferencasDeDados.forEach(d => console.log(`        ${d.onde}: referência ${JSON.stringify(d.referencia)} → gerado ${JSON.stringify(d.gerado)}`));
  console.log(`  info  ${r.diferencasEsperadas.length} diferença(s) esperada(s), que não são de dados:`);
  r.diferencasEsperadas.forEach(d => console.log(`        ${d.onde}: ${d.motivo} (referência ${JSON.stringify(d.referencia)} → gerado ${JSON.stringify(d.gerado)})`));
  console.log('\nAplicação (rede sempre falhando, relógio no instante da geração)');
  linha(!r.aplicacao.problemas.length, r.aplicacao.problemas.length ? r.aplicacao.problemas.join('; ')
    : `carregou sem erros; ${r.aplicacao.info.kpis} KPIs e benchmark renderizados; ${r.aplicacao.info.consultasSimuladas} consultas simuladas, todas falharam`);
  if (r.aplicacao.info.seloIbge !== undefined) {
    console.log(`        selo IBGE: "${r.aplicacao.info.seloIbge}"`);
    console.log(`        INMET: ${r.aplicacao.info.inmetStatus} (${r.aplicacao.info.inmetMotivo}), selo "${r.aplicacao.info.seloInmet}", alertas de clima: ${r.aplicacao.info.alertasClima}`);
  }
  console.log('\nRegras da M1 (relógio relativo às datas registradas)');
  r.m1.casos.forEach(c => linha(c.ok, `${c.nome} (${c.detalhe})`));
  console.log(`\nResultado: ${r.aprovado ? 'APROVADO' : `REPROVADO (${r.reprovacoes.length} problema(s))`}`);
}

/* ------------------------------------------------------- autoteste do verificador */
async function autoteste(gerado, referencia) {
  const casos = [];
  const espera = async (nome, mutar, onde) => {
    const g = copy(gerado.snap); mutar(g);
    const r = await verificar({ arquivo: 'mutado', texto: gerado.texto, snap: g }, referencia);
    const alvo = r[onde];
    const achou = Array.isArray(alvo) ? alvo.length > 0 : !!(alvo && alvo.problemas && alvo.problemas.length);
    casos.push([nome + ` (detectado em ${onde})`, !r.aprovado && achou]);
  };
  const r0 = await verificar(gerado, referencia);
  casos.push(['arquivo gerado sem alteração: aprovado', r0.aprovado]);
  const refSoma = compararReferencia(referencia.snap, referencia.snap);
  casos.push(['referência contra ela mesma: zero diferenças', !refSoma.diferencasDeDados.length && !refSoma.diferencasEsperadas.length]);
  const textoRuim = await verificar({ arquivo: 'mutado', texto: gerado.texto.replace(/^\/\*\*/, '/*'), snap: copy(gerado.snap) }, referencia);
  casos.push(['arquivo sem o comentário /** (detectado em contrato)', !textoRuim.aprovado && textoRuim.contrato.length > 0]);
  const patro = (g) => g.sidra.municipios.find(m => m.c === PATRO);
  await espera('seção exportacao ausente', g => { delete g.exportacao; }, 'contrato');
  await espera('seção inmet ausente', g => { delete g.inmet; }, 'contrato');
  await espera('município fora do formato', g => { g.sidra.municipios[0].c = '31'; }, 'contrato');
  await espera('inmet com data de coleta inválida', g => { g.inmet = { collectedAt: 'ontem', data: { [PATRO]: {} } }; }, 'contrato');
  await espera('59 municípios', g => { g.sidra.municipios.pop(); }, 'dados');
  await espera('ano 2019 faltando em Patrocínio', g => { delete patro(g).y['2019']; }, 'dados');
  await espera('rendimento incoerente (troca parcial)', g => { patro(g).y['2025'][0] = 1200; }, 'dados');
  await espera('trio incompleto', g => { patro(g).y['2024'][2] = null; }, 'dados');
  await espera('média de MG incoerente', g => { g.sidra.mg['2020'][1] *= 2; }, 'dados');
  await espera('seção sidra mantida do arquivo anterior', g => { g.exportacao.sidra.modo = 'mantido_do_arquivo_anterior'; }, 'dados');
  await espera('execução de origem sem identificação', g => { delete g.exportacao.sidra.execucaoId; }, 'dados');
  await espera('valor diferente da referência, mas coerente', g => { const t = patro(g).y['2016']; t[1] += 10; t[0] = Math.round(t[2] * 1000 / t[1]); }, 'diferencasDeDados');
  await espera('município trocado', g => { g.sidra.municipios[59] = Object.assign(copy(g.sidra.municipios[59]), { c: '3100000', n: 'Teste' }); }, 'diferencasDeDados');
  await espera('aplicação recebe sidra sem municípios', g => { g.sidra.municipios = []; }, 'aplicacao');
  await espera('arquivo exporta previsão vencida', g => { g.inmet = copy(referencia.snap.inmet); g.exportacao.inmet = { modo: 'sqlite' }; }, 'm1');
  let ok = 0;
  casos.forEach(([n, passou]) => { console.log(`  ${passou ? 'ok' : 'FALHOU'} - autoteste: ${n}`); if (passou) ok++; });
  console.log(`\n${ok} de ${casos.length} autotestes aprovados, ${casos.length - ok} reprovados.`);
  return ok === casos.length;
}

/* -------------------------------------------------------------------- main */
async function main(argv) {
  const auto = argv.includes('--autoteste');
  const ij = argv.indexOf('--json');
  const jsonOut = ij >= 0 ? argv[ij + 1] : null;
  const pos = argv.filter((a, i) => !a.startsWith('--') && (ij < 0 || i !== ij + 1));
  if (pos.length !== 2) { console.error('uso: node tests/check_exported_snapshot.js [--autoteste] <gerado.js> <referencia.js> [--json saida.json]'); return 2; }
  const gerado = carregar(pos[0]), referencia = carregar(pos[1]);
  if (auto) return (await autoteste(gerado, referencia)) ? 0 : 1;
  const r = await verificar(gerado, referencia);
  imprimir(r, gerado);
  if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(r, null, 2));
  return r.aprovado ? 0 : 1;
}
if (require.main === module) main(process.argv.slice(2)).then(c => { process.exitCode = c; }, e => { console.error(e); process.exitCode = 2; });
module.exports = { verificar, verificarContrato, verificarDados, compararReferencia, verificarM1, abrirAplicacao };
