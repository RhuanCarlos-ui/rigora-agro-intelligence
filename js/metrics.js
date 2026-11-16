/**
 * Rigora Agro Intelligence - Motor de Métricas (fonte única de cálculo)
 * ---------------------------------------------------------------------------
 * Todas as telas (cards, gráficos, simulador, alertas e automações) chamam
 * estas funções. Nenhum componente recalcula indicadores por conta própria.
 * Funções puras: sem DOM, testáveis em Node (ver tests/metrics.test.js).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.RigoraMetrics = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const SACA_KG = 60;

  const round = (v, d = 2) => {
    const f = Math.pow(10, d);
    return Math.round((v + Number.EPSILON) * f) / f;
  };

  const sum = (arr, fn) => arr.reduce((acc, x) => acc + (fn ? fn(x) : x), 0);

  /** kg/ha (IBGE) -> sacas de 60 kg por hectare */
  function kgHaToScHa(kgHa) {
    if (kgHa === null || kgHa === undefined || isNaN(kgHa)) return null;
    return kgHa / SACA_KG;
  }

  /** Variação percentual (a vs b). Retorna null quando a base é zero/ausente. */
  function pctChange(value, base) {
    if (base === null || base === undefined || base === 0 || value === null || value === undefined) return null;
    return ((value - base) / Math.abs(base)) * 100;
  }

  /* ------------------------------------------------------------------------
   * Custos a partir do livro-caixa (lançamentos)
   * Lançamentos com talhaoId === 'ALL' são rateados pela área dos talhões.
   * ---------------------------------------------------------------------- */
  function allocateEntry(entry, talhoes) {
    if (entry.talhaoId !== 'ALL') return [{ talhaoId: entry.talhaoId, value: entry.value }];
    const totalArea = sum(talhoes, t => t.areaHa);
    return talhoes.map(t => ({ talhaoId: t.id, value: entry.value * (t.areaHa / totalArea) }));
  }

  function filterEntries(entries, { safra, talhaoId, category, from, to } = {}) {
    return entries.filter(e =>
      (!safra || e.safra === safra) &&
      (!category || e.category === category) &&
      (!from || e.date >= from) &&
      (!to || e.date <= to) &&
      (!talhaoId || talhaoId === 'ALL' || e.talhaoId === talhaoId || e.talhaoId === 'ALL')
    );
  }

  /** Custo atribuído a um talhão (ou à propriedade inteira quando talhaoId vazio/ALL). */
  function costOf(entries, talhoes, talhaoId) {
    if (!talhaoId || talhaoId === 'ALL') return sum(entries, e => e.value);
    return sum(entries, e => {
      const part = allocateEntry(e, talhoes).find(p => p.talhaoId === talhaoId);
      return part ? part.value : 0;
    });
  }

  function costByCategory(entries, talhoes, talhaoId) {
    const map = {};
    entries.forEach(e => {
      const v = (!talhaoId || talhaoId === 'ALL') ? e.value
        : (allocateEntry(e, talhoes).find(p => p.talhaoId === talhaoId) || { value: 0 }).value;
      map[e.category] = (map[e.category] || 0) + v;
    });
    return Object.entries(map).map(([category, value]) => ({ category, value })).sort((a, b) => b.value - a.value);
  }

  /** Custos por mês (AAAA-MM) com acumulado, na ordem cronológica. */
  function costByMonth(entries, talhoes, talhaoId) {
    const map = {};
    entries.forEach(e => {
      const key = e.date.slice(0, 7);
      const v = (!talhaoId || talhaoId === 'ALL') ? e.value
        : (allocateEntry(e, talhoes).find(p => p.talhaoId === talhaoId) || { value: 0 }).value;
      map[key] = (map[key] || 0) + v;
    });
    let acc = 0;
    return Object.keys(map).sort().map(k => {
      acc += map[k];
      return { month: k, value: map[k], cumulative: acc };
    });
  }

  /* ------------------------------------------------------------------------
   * Indicadores de uma safra (fonte única para cards, gráficos e alertas)
   * ---------------------------------------------------------------------- */
  function safraSummary(ctx) {
    const { talhoes, entries, harvest, sales, safra, talhaoId, marketPrice } = ctx;
    const scope = (!talhaoId || talhaoId === 'ALL') ? talhoes : talhoes.filter(t => t.id === talhaoId);
    const areaHa = sum(scope, t => t.areaHa);
    const safraEntries = filterEntries(entries, { safra });
    const cost = costOf(safraEntries, talhoes, talhaoId);

    const harvestRows = (harvest || []).filter(h => h.safra === safra && scope.some(t => t.id === h.talhaoId));
    const hasHarvest = harvestRows.length > 0;
    const productionSc = hasHarvest ? sum(harvestRows, h => h.sacas) : null;
    const totalProductionAll = sum((harvest || []).filter(h => h.safra === safra), h => h.sacas);
    const share = (hasHarvest && totalProductionAll > 0) ? productionSc / totalProductionAll : 0;

    const safraSales = (sales || []).filter(s => s.safra === safra);
    const soldSc = sum(safraSales, s => s.sacas) * share;
    const revenueRealized = sum(safraSales, s => s.sacas * s.price) * share;
    const stockSc = hasHarvest ? Math.max(productionSc - soldSc, 0) : 0;
    const stockValue = (marketPrice && stockSc > 0) ? stockSc * marketPrice : 0;
    const avgSalePrice = soldSc > 0 ? revenueRealized / soldSc : null;

    const productivity = (hasHarvest && areaHa > 0) ? productionSc / areaHa : null;
    const costPerHa = areaHa > 0 ? cost / areaHa : null;
    const costPerSack = (hasHarvest && productionSc > 0) ? cost / productionSc : null;
    const result = hasHarvest ? revenueRealized + stockValue - cost : null;
    const revenueTotal = hasHarvest ? revenueRealized + stockValue : null;
    const margin = (revenueTotal && revenueTotal > 0) ? (result / revenueTotal) * 100 : null;

    return {
      safra, talhaoId: talhaoId || 'ALL', areaHa, cost, costPerHa,
      productionSc, productivity, costPerSack,
      soldSc, revenueRealized, avgSalePrice, stockSc, stockValue,
      revenueTotal, result, margin, hasHarvest, entriesCount: safraEntries.length
    };
  }

  /* ------------------------------------------------------------------------
   * Simulador financeiro
   * ---------------------------------------------------------------------- */
  function validateSimInputs(p) {
    const errors = [];
    const num = (k) => typeof p[k] === 'number' && isFinite(p[k]);
    if (!num('areaHa') || p.areaHa <= 0) errors.push('Área deve ser maior que zero.');
    if (!num('productivity') || p.productivity < 0) errors.push('Produtividade não pode ser negativa.');
    if (!num('price') || p.price < 0) errors.push('Preço não pode ser negativo.');
    if (!num('costPerHa') || p.costPerHa < 0) errors.push('Custo por hectare não pode ser negativo.');
    if (!num('lossPct') || p.lossPct < 0 || p.lossPct >= 100) errors.push('Perda deve estar entre 0% e 99%.');
    return errors;
  }

  function simulate(p) {
    const errors = validateSimInputs(p);
    if (errors.length) return { valid: false, errors };
    const lossFactor = 1 - p.lossPct / 100;
    const grossSc = p.areaHa * p.productivity;
    const netSc = grossSc * lossFactor;
    const lostSc = grossSc - netSc;
    const revenue = netSc * p.price;
    const cost = p.areaHa * p.costPerHa;
    const result = revenue - cost;
    const margin = revenue > 0 ? (result / revenue) * 100 : null;
    const costPerSack = netSc > 0 ? cost / netSc : null;
    const breakEvenPrice = netSc > 0 ? cost / netSc : null;
    const breakEvenProductivity = (p.price > 0 && lossFactor > 0) ? p.costPerHa / (p.price * lossFactor) : null;
    const resultPerHa = result / p.areaHa;
    /** impacto de cada R$ 100 de variação no preço da saca */
    const priceSensitivityPer100 = netSc * 100;
    const safetyMarginPct = (breakEvenPrice && p.price > 0) ? ((p.price - breakEvenPrice) / p.price) * 100 : null;
    return {
      valid: true, errors: [], grossSc, netSc, lostSc, revenue, cost, result, margin,
      costPerSack, breakEvenPrice, breakEvenProductivity, resultPerHa,
      priceSensitivityPer100, safetyMarginPct, lostRevenue: lostSc * p.price
    };
  }

  /** Curva resultado × preço para o gráfico de sensibilidade. */
  function priceSensitivity(p, minPrice, maxPrice, steps = 24) {
    const out = [];
    for (let i = 0; i <= steps; i++) {
      const price = minPrice + (maxPrice - minPrice) * (i / steps);
      const r = simulate(Object.assign({}, p, { price }));
      out.push({ price, result: r.valid ? r.result : null });
    }
    return out;
  }

  /* ------------------------------------------------------------------------
   * Clima: índice estimado de favorabilidade à ferrugem a partir da previsão
   * Critério documentado: UR média estimada >= 80% e T média entre 22 e 26 °C
   * UR média estimada = (UR mín + UR máx) / 2 ; T média = (T mín + T máx) / 2
   * ---------------------------------------------------------------------- */
  function rustFavorability(day) {
    const tMean = (day.temp_min + day.temp_max) / 2;
    // Bloco 2 (T2): sem faixa de umidade completa não há estimativa (urMean null, nunca NaN)
    const urMean = inmetHumidityOk(day) ? (day.umidade_min + day.umidade_max) / 2 : null;
    const favorable = urMean !== null && urMean >= 80 && tMean >= 22 && tMean <= 26;
    return { tMean, urMean, favorable };
  }

  /**
   * Bloco 2 (T2): a umidade de um dia da previsão só é exibida e usada quando a faixa
   * está completa: dois números finitos com 0 <= mínima <= máxima <= 100.
   */
  function inmetHumidityOk(day) {
    if (day === null || typeof day !== 'object') return false;
    const a = day.umidade_min, b = day.umidade_max;
    return typeof a === 'number' && typeof b === 'number' && Number.isFinite(a) && Number.isFinite(b)
      && a >= 0 && b <= 100 && a <= b;
  }

  /** Separa os dias com umidade completa: { days, omitted: [datas dos dias omitidos] }. Nunca lança erro. */
  function inmetUsableDays(days) {
    const list = Array.isArray(days) ? days : [];
    return {
      days: list.filter(inmetHumidityOk),
      omitted: list.filter(d => !inmetHumidityOk(d)).map(d => (d && typeof d.date === 'string' ? d.date : '?'))
    };
  }

  /* ------------------------------------------------------------------------
   * INMET: leitura da previsão e validade da cópia salva (fallback)
   * A cópia salva (js/snapshots.js) só é usada se tiver até 24 h, o mesmo limite
   * do pipeline (config.INMET_MAX_IDADE_HORAS), e só com os dias de hoje em diante.
   * ---------------------------------------------------------------------- */
  const INMET_PERIODS = ['manha', 'tarde', 'noite'];
  const INMET_SNAPSHOT_MAX_AGE_HOURS = 24;
  const CLOCK_TOLERANCE_MS = 5 * 60 * 1000; // diferença aceita entre o relógio do computador e a data de coleta

  const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  const pad2 = (n) => String(n).padStart(2, '0');

  /** Data local (fuso do navegador) em 'aaaa-mm-dd'. Não usa toISOString, que converteria para UTC. */
  function localDateISO(d) {
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  }

  /** 'dd/mm/aaaa' -> 'aaaa-mm-dd' (comparável como texto, sem fuso). Data inexistente -> null. */
  function brDateToISO(s) {
    const m = typeof s === 'string' ? /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s) : null;
    if (!m) return null;
    const [, dd, mm, yyyy] = m;
    const d = new Date(+yyyy, +mm - 1, +dd);
    return d.getFullYear() === +yyyy && d.getMonth() === +mm - 1 && d.getDate() === +dd ? `${yyyy}-${mm}-${dd}` : null;
  }

  /**
   * Resposta da API do INMET (ou a cópia salva) -> dias da previsão.
   * Usa a tarde quando existe, senão a manhã, senão a noite, senão o registro do dia.
   * Não supõe que manhã, tarde e noite existam juntas. Estrutura inesperada -> erro.
   */
  function parseInmetForecast(data, code = '3148103') {
    const city = isObj(data) ? (data[code] || data[Object.keys(data)[0]]) : null;
    if (!isObj(city)) throw new Error('estrutura inesperada na resposta do INMET');
    const days = Object.keys(city).map(date => {
      const raw = city[date];
      if (!isObj(raw)) throw new Error(`dia ${date} sem dados na resposta do INMET`);
      const present = INMET_PERIODS.filter(k => isObj(raw[k]));
      const p = isObj(raw.tarde) ? raw.tarde : (isObj(raw.manha) ? raw.manha : (isObj(raw.noite) ? raw.noite : raw));
      if (typeof p.temp_max !== 'number' || typeof p.temp_min !== 'number') throw new Error('temperaturas ausentes na resposta do INMET');
      return {
        date, weekday: p.dia_semana, resumo: p.resumo, temp_min: p.temp_min, temp_max: p.temp_max,
        umidade_min: p.umidade_min, umidade_max: p.umidade_max, vento: `${p.int_vento || '—'} (${p.dir_vento || '—'})`,
        tendencia: p.temp_max_tende,
        periods: present.length ? Object.fromEntries(present.map(k => [k, raw[k].resumo])) : null,
        risk: rustFavorability(p)
      };
    });
    if (!days.length) throw new Error('previsão sem dias');
    return days;
  }

  /**
   * Decide se a cópia salva do INMET pode ser usada quando a consulta ao vivo falha.
   * Nunca lança erro. Retorna { ok: true, days, collectedAt, ageHours, removedPast } ou
   * { ok: false, reason, message, days: null, collectedAt? }, com reason em
   * 'ausente' | 'data_invalida' | 'vencida' | 'estrutura' | 'sem_dias_atuais'.
   * `now` = instante atual; `opts.today` ('aaaa-mm-dd') substitui a data local de `now` (testes).
   */
  function inmetSnapshotForecast(snap, now, opts = {}) {
    const maxAgeH = opts.maxAgeHours === undefined ? INMET_SNAPSHOT_MAX_AGE_HOURS : opts.maxAgeHours;
    const fail = (reason, message, extra) => Object.assign({ ok: false, reason, message, days: null }, extra || {});
    try {
      if (!isObj(snap)) return fail('ausente', 'não há cópia salva da previsão');
      const collectedAt = snap.collectedAt;
      const t = typeof collectedAt === 'string' ? Date.parse(collectedAt) : NaN;
      if (!Number.isFinite(t)) return fail('data_invalida', 'a cópia salva não tem data de coleta válida');
      const nowMs = now instanceof Date ? now.getTime() : Number(now);
      if (!Number.isFinite(nowMs)) return fail('data_invalida', 'data atual indisponível para conferir a cópia salva');
      if (t - nowMs > CLOCK_TOLERANCE_MS) return fail('data_invalida', 'a data de coleta da cópia salva está no futuro', { collectedAt });
      const ageHours = Math.max(0, (nowMs - t) / 3600000);
      if (ageHours > maxAgeH) {
        return fail('vencida', `a cópia salva tem ${Math.floor(ageHours)} h, acima do limite de ${maxAgeH} h`, { collectedAt, ageHours });
      }
      const days = parseInmetForecast(snap.data, opts.code);
      days.forEach(d => {
        if (brDateToISO(d.date) === null || typeof d.weekday !== 'string'
          || typeof d.umidade_min !== 'number' || typeof d.umidade_max !== 'number') {
          throw new Error(`dia ${d.date} incompleto`);
        }
      });
      const today = opts.today || localDateISO(new Date(nowMs));
      const current = days.filter(d => brDateToISO(d.date) >= today);
      if (!current.length) return fail('sem_dias_atuais', 'a cópia salva só tem dias que já passaram', { collectedAt, ageHours });
      return { ok: true, reason: null, message: '', collectedAt, ageHours, days: current, removedPast: days.length - current.length };
    } catch (e) {
      return fail('estrutura', `a cópia salva não pôde ser lida (${e && e.message ? e.message : String(e)})`);
    }
  }

  /* ------------------------------------------------------------------------
   * INMET durante a sessão (B1): reavaliação ao voltar à aba e a cada 30 min.
   * Usa exatamente as regras da M1 (inmetSnapshotForecast): 24 h, dias passados
   * removidos, coleta no futuro recusada, cópia incompleta ou malformada recusada.
   * ---------------------------------------------------------------------- */
  const INMET_RECHECK_MINUTES = 30;

  /**
   * Entre as cópias candidatas ({ origin, copy: { collectedAt, data } }), devolve a válida mais recente.
   * Sem nenhuma válida, devolve a recusa da cópia datada mais recente (ou a da última candidata).
   * Nunca lança erro. O resultado traz `origin` e `copy` da cópia escolhida.
   */
  function pickInmetCopy(candidates, now, opts = {}) {
    const results = (Array.isArray(candidates) ? candidates : [])
      .filter(c => isObj(c) && (c.copy !== null && c.copy !== undefined || c.always))
      .map(c => Object.assign(inmetSnapshotForecast(c.copy, now, opts), { origin: c.origin, copy: c.copy }));
    const when = (r) => Date.parse(r.collectedAt);
    const valid = results.filter(r => r.ok).sort((a, b) => when(b) - when(a));
    if (valid.length) return valid[0];
    const dated = results.filter(r => Number.isFinite(when(r))).sort((a, b) => when(b) - when(a));
    return dated[0] || results[results.length - 1] || inmetSnapshotForecast(null, now, opts);
  }

  /**
   * Reavalia o estado atual da previsão (`{ status, collectedAt, days, raw, ... }`) no instante `now`.
   * Retorna { state, fetch, reason }:
   *  - state: novo estado quando a idade ou a data mudam o que pode ser exibido (null = sem mudança);
   *  - fetch: se é preciso consultar o INMET de novo.
   * Dado ao vivo vale como atual por 30 min e só no mesmo dia local; cópia ou indisponível: tenta de novo.
   * `opts.candidates`: cópias de reserva (as mesmas do fallback) para quando a previsão exibida deixa de valer.
   */
  function inmetRecheck(current, now, opts = {}) {
    try {
      if (!isObj(current) || current.status === 'loading') return { state: null, fetch: false, reason: 'consulta em andamento' };
      if (current.status === 'error' || !isObj(current.raw)) return { state: null, fetch: true, reason: 'previsão indisponível' };
      const nowMs = now instanceof Date ? now.getTime() : Number(now);
      const r = inmetSnapshotForecast(current.raw, now, opts);
      if (!r.ok) {
        const fb = pickInmetCopy(opts.candidates, now, opts);
        const reason = `a previsão exibida deixou de valer (${r.message})`;
        const state = fb.ok
          ? { status: 'snapshot', collectedAt: fb.collectedAt, days: fb.days, raw: fb.copy, message: reason, fallback: fb }
          : { status: 'error', collectedAt: null, days: null, raw: null, message: reason, fallback: r };
        return { state, fetch: true, reason };
      }
      const state = r.days.length !== (current.days || []).length ? Object.assign({}, current, { days: r.days }) : null;
      if (current.status !== 'live') return { state, fetch: true, reason: 'a consulta ao vivo anterior falhou' };
      const t = Date.parse(current.collectedAt);
      const today = opts.today || localDateISO(new Date(nowMs));
      const otherDay = localDateISO(new Date(t)) !== today;
      const old = nowMs - t >= INMET_RECHECK_MINUTES * 60000;
      return { state, fetch: otherDay || old, reason: otherDay ? 'a consulta ao vivo é de outro dia' : (old ? `a consulta ao vivo tem mais de ${INMET_RECHECK_MINUTES} min` : 'previsão ao vivo atualizada') };
    } catch (e) {
      return { state: null, fetch: true, reason: `erro ao reavaliar a previsão (${e && e.message ? e.message : String(e)})` };
    }
  }

  /** Data e hora locais de uma coleta: { day: 'dd/mm', time: 'hh:mm', sameDay } em relação a `now`. */
  function collectionStamp(iso, now) {
    const d = new Date(iso);
    const n = now instanceof Date ? now : new Date(now);
    return {
      day: `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}`,
      time: `${pad2(d.getHours())}:${pad2(d.getMinutes())}`,
      sameDay: localDateISO(d) === localDateISO(n)
    };
  }

  return {
    SACA_KG, round, sum, kgHaToScHa, pctChange,
    allocateEntry, filterEntries, costOf, costByCategory, costByMonth,
    safraSummary, validateSimInputs, simulate, priceSensitivity, rustFavorability,
    INMET_SNAPSHOT_MAX_AGE_HOURS, localDateISO, brDateToISO, parseInmetForecast, inmetSnapshotForecast,
    INMET_RECHECK_MINUTES, pickInmetCopy, inmetRecheck, collectionStamp,
    inmetHumidityOk, inmetUsableDays
  };
}));
