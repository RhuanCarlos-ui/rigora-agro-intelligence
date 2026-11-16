/**
 * Rigora Agro Intelligence - Motor de Gráficos SVG (sem dependências externas)
 * v2: tooltips em todos os gráficos, séries com tipo de dado (oficial/estimado/demonstrativo),
 * barras horizontais clicáveis, combinado barra + linha, multilinha, sensibilidade e cenários.
 */
const RigoraCharts = {
  colors: {
    primary: '#163D32',
    secondary: '#248763',
    accent: '#44A08D',
    official: '#0369A1',
    estimated: '#7C3AED',
    demo: '#B45309',
    lightGreen: '#E6F4EA',
    warning: '#D97706',
    danger: '#DC2626',
    text: '#26352F',
    muted: '#64748B',
    grid: '#E2E8F0',
    card: '#FFFFFF'
  },

  formatCurrency(val, decimals = 0) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(val);
  },

  formatNumber(val, decimals = 1) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    return new Intl.NumberFormat('pt-BR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(val);
  },

  formatCompact(val) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    const abs = Math.abs(val);
    if (abs >= 1e6) return `R$ ${this.formatNumber(val / 1e6, 1)} mi`;
    if (abs >= 1e3) return `R$ ${this.formatNumber(val / 1e3, 0)} mil`;
    return this.formatCurrency(val);
  },

  esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  },

  /* ---------------------------------------------------------------------- */
  /* Infraestrutura comum                                                   */
  /* ---------------------------------------------------------------------- */
  _frame(container, height) {
    const width = Math.max(320, Math.floor(container.clientWidth || 640));
    return { width, height };
  },

  _niceMax(v) {
    if (v <= 0) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / exp;
    const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return nice * exp;
  },

  _empty(container, msg) {
    container.innerHTML = `<div class="chart-empty">${this.esc(msg || 'Nenhum dado disponível para os filtros selecionados.')}</div>`;
  },

  _fmt(v, opts) {
    if (opts.isCurrency) return opts.compact ? this.formatCompact(v) : this.formatCurrency(v);
    return `${this.formatNumber(v, opts.decimals ?? 1)}${opts.unit ? ' ' + opts.unit : ''}`;
  },

  /** Tooltip único para a página; elementos SVG/HTML com data-tip exibem o texto. */
  attachTooltips(container) {
    let tip = document.getElementById('rg-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'rg-tooltip';
      tip.className = 'rg-tooltip';
      tip.setAttribute('role', 'tooltip');
      document.body.appendChild(tip);
    }
    if (container.dataset.tipsBound) return;
    container.dataset.tipsBound = '1';
    const show = (e) => {
      const target = e.target.closest('[data-tip]');
      if (!target || !container.contains(target)) { tip.classList.remove('show'); return; }
      tip.innerHTML = target.getAttribute('data-tip');
      tip.classList.add('show');
      const pad = 14;
      let x = e.clientX + pad;
      let y = e.clientY + pad;
      const r = tip.getBoundingClientRect();
      if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
      if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
      tip.style.left = `${x}px`;
      tip.style.top = `${y}px`;
    };
    container.addEventListener('mousemove', show);
    container.addEventListener('mouseleave', () => tip.classList.remove('show'));
    container.addEventListener('focusin', (e) => {
      const target = e.target.closest('[data-tip]');
      if (!target) return;
      const rect = target.getBoundingClientRect();
      show({ target, clientX: rect.left + rect.width / 2, clientY: rect.top });
    });
    container.addEventListener('focusout', () => tip.classList.remove('show'));
  },

  _yAxis(svg, pad, chartW, chartH, maxVal, minVal, opts, ticks = 4) {
    const range = maxVal - minVal || 1;
    for (let i = 0; i <= ticks; i++) {
      const v = minVal + (range * i) / ticks;
      const y = pad.top + chartH - (i / ticks) * chartH;
      svg.push(`<line x1="${pad.left}" y1="${y}" x2="${pad.left + chartW}" y2="${y}" stroke="${this.colors.grid}" stroke-dasharray="${v === 0 ? '0' : '3 4'}"/>`);
      const label = opts.isCurrency ? ((maxVal - minVal) < 20000 ? this.formatCurrency(v) : this.formatCompact(v)) : `${this.formatNumber(v, opts.axisDecimals ?? 0)}`;
      svg.push(`<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="${this.colors.muted}">${label}</text>`);
    }
  },

  /* ---------------------------------------------------------------------- */
  /* 1. Barras mensais + linha acumulada                                    */
  /* ---------------------------------------------------------------------- */
  renderComboBarLine(containerId, data, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || !data.length) return this._empty(container, opts.emptyMessage);
    const { width, height } = this._frame(container, 260);
    const pad = { top: 22, right: 64, bottom: 34, left: 64 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const maxBar = this._niceMax(Math.max(...data.map(d => d.value)) * 1.1);
    const maxCum = this._niceMax(Math.max(...data.map(d => d.cumulative)) * 1.05);
    const slot = chartW / data.length;
    const barW = Math.min(38, slot * 0.6);
    const svg = [];
    this._yAxis(svg, pad, chartW, chartH, maxBar, 0, { isCurrency: true });
    // eixo direito (acumulado)
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + chartH - (i / 4) * chartH;
      svg.push(`<text x="${pad.left + chartW + 8}" y="${y + 4}" font-size="11" fill="${this.colors.primary}" opacity="0.75">${this.formatCompact((maxCum * i) / 4)}</text>`);
    }
    const pts = [];
    data.forEach((d, i) => {
      const cx = pad.left + slot * i + slot / 2;
      const h = (d.value / maxBar) * chartH;
      const y = pad.top + chartH - h;
      const tip = `<strong>${this.esc(d.label)}</strong><br>No mês: ${this.formatCurrency(d.value)}<br>Acumulado: ${this.formatCurrency(d.cumulative)}${d.extra ? '<br>' + d.extra : ''}`;
      svg.push(`<rect class="rg-bar" x="${cx - barW / 2}" y="${y}" width="${barW}" height="${Math.max(h, 1)}" rx="4" fill="${opts.barColor || this.colors.secondary}" data-tip="${this.esc(tip)}" tabindex="0"/>`);
      svg.push(`<text x="${cx}" y="${pad.top + chartH + 18}" text-anchor="middle" font-size="11" fill="${this.colors.muted}">${this.esc(d.short || d.label)}</text>`);
      pts.push([cx, pad.top + chartH - (d.cumulative / maxCum) * chartH, tip]);
    });
    svg.push(`<polyline points="${pts.map(p => p[0] + ',' + p[1]).join(' ')}" fill="none" stroke="${this.colors.primary}" stroke-width="2.4" stroke-linejoin="round"/>`);
    pts.forEach(p => svg.push(`<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="#fff" stroke="${this.colors.primary}" stroke-width="2" data-tip="${this.esc(p[2])}"/>`));
    if (opts.budgetLine) {
      const y = pad.top + chartH - (opts.budgetLine.value / maxCum) * chartH;
      if (y >= pad.top) {
        svg.push(`<line x1="${pad.left}" y1="${y}" x2="${pad.left + chartW}" y2="${y}" stroke="${this.colors.warning}" stroke-dasharray="6 4" stroke-width="1.5"/>`);
        svg.push(`<text x="${pad.left + chartW - 4}" y="${y - 6}" text-anchor="end" font-size="11" fill="${this.colors.warning}" font-weight="600">${this.esc(opts.budgetLine.label)}</text>`);
      }
    }
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${this.esc(opts.ariaLabel || 'Gráfico de custos mensais')}">${svg.join('')}</svg>`;
    this.attachTooltips(container);
  },

  /* ---------------------------------------------------------------------- */
  /* 2. Barras horizontais (ranking, categorias, talhões) — clicáveis        */
  /* ---------------------------------------------------------------------- */
  renderHBar(containerId, data, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || !data.length) return this._empty(container, opts.emptyMessage);
    const rowH = opts.rowHeight || 30;
    const labelW = opts.labelWidth || 170;
    const { width } = this._frame(container, 0);
    const height = data.length * rowH + 16;
    const valueW = 92;
    const chartW = Math.max(60, width - labelW - valueW - 16);
    const maxV = Math.max(...data.map(d => d.value), opts.refValue || 0) || 1;
    const svg = [];
    data.forEach((d, i) => {
      const y = 8 + i * rowH;
      const w = Math.max((d.value / maxV) * chartW, 2);
      const color = d.color || (d.highlight ? this.colors.primary : (opts.color || this.colors.secondary));
      const tip = d.tip || `<strong>${this.esc(d.label)}</strong><br>${this._fmt(d.value, opts)}`;
      const clickable = opts.onClickAttr ? `style="cursor:pointer" onclick="${opts.onClickAttr(d)}"` : '';
      svg.push(`<g class="rg-hrow${d.highlight ? ' is-highlight' : ''}" data-tip="${this.esc(tip)}" ${clickable} tabindex="0">`);
      svg.push(`<rect x="0" y="${y - 3}" width="${width}" height="${rowH - 2}" fill="${d.highlight ? '#F0FDF4' : 'transparent'}" rx="4"/>`);
      svg.push(`<text x="${labelW - 10}" y="${y + rowH / 2}" text-anchor="end" font-size="12" fill="${this.colors.text}" font-weight="${d.highlight ? 700 : 500}">${this.esc(d.label)}</text>`);
      svg.push(`<rect x="${labelW}" y="${y + 4}" width="${w}" height="${rowH - 14}" rx="4" fill="${color}" opacity="${d.muted ? 0.45 : 1}"/>`);
      svg.push(`<text x="${labelW + w + 8}" y="${y + rowH / 2}" font-size="12" font-weight="600" fill="${this.colors.text}">${this._fmt(d.value, opts)}</text>`);
      svg.push('</g>');
    });
    if (opts.refValue) {
      const x = labelW + (opts.refValue / maxV) * chartW;
      svg.push(`<line x1="${x}" y1="2" x2="${x}" y2="${height - 4}" stroke="${this.colors.official}" stroke-width="1.5" stroke-dasharray="5 4"/>`);
      svg.push(`<text x="${x + 4}" y="${height - 4}" font-size="10.5" fill="${this.colors.official}" font-weight="600">${this.esc(opts.refLabel || '')}</text>`);
    }
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${this.esc(opts.ariaLabel || 'Gráfico de barras')}">${svg.join('')}</svg>`;
    this.attachTooltips(container);
  },

  /* ---------------------------------------------------------------------- */
  /* 3. Multilinhas com eixo categórico (lacunas = null)                    */
  /* series: [{ name, color, dash, type, values: [num|null], tips?: [] }]   */
  /* ---------------------------------------------------------------------- */
  renderMultiLine(containerId, categories, series, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const all = series.flatMap(s => s.values).filter(v => v !== null && v !== undefined);
    if (!categories.length || !all.length) return this._empty(container, opts.emptyMessage);
    const { width, height } = this._frame(container, opts.height || 260);
    const pad = { top: 20, right: 24, bottom: 34, left: opts.isCurrency ? 70 : 48 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    let minVal = opts.minZero ? 0 : Math.min(...all) * 0.9;
    let maxVal = Math.max(...all) * 1.08;
    if (opts.refLines) opts.refLines.forEach(r => { maxVal = Math.max(maxVal, r.value * 1.05); minVal = Math.min(minVal, r.value * 0.95); });
    if (!opts.minZero) minVal = Math.max(0, minVal);
    maxVal = opts.minZero ? this._niceMax(maxVal) : maxVal;
    const range = maxVal - minVal || 1;
    const n = categories.length;
    const getX = (i) => pad.left + (n === 1 ? chartW / 2 : (i / (n - 1)) * chartW);
    const getY = (v) => pad.top + chartH - ((v - minVal) / range) * chartH;
    const svg = [];
    this._yAxis(svg, pad, chartW, chartH, maxVal, minVal, opts);
    const every = Math.ceil(n / Math.max(1, Math.floor(chartW / 64)));
    categories.forEach((c, i) => {
      if (i % every === 0 || i === n - 1) svg.push(`<text x="${getX(i)}" y="${pad.top + chartH + 18}" text-anchor="middle" font-size="11" fill="${this.colors.muted}">${this.esc(c)}</text>`);
    });
    (opts.refLines || []).forEach(r => {
      const y = getY(r.value);
      svg.push(`<line x1="${pad.left}" y1="${y}" x2="${pad.left + chartW}" y2="${y}" stroke="${r.color || this.colors.danger}" stroke-dasharray="6 4" stroke-width="1.5"/>`);
      svg.push(`<text x="${pad.left + chartW - 4}" y="${y - 6}" text-anchor="end" font-size="11" fill="${r.color || this.colors.danger}" font-weight="600">${this.esc(r.label)}</text>`);
    });
    series.forEach(s => {
      // segmentos contínuos (lacunas não são interpoladas)
      let seg = [];
      const flush = () => {
        if (seg.length > 1) svg.push(`<polyline points="${seg.join(' ')}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2.4}" stroke-dasharray="${s.dash || '0'}" stroke-linejoin="round" stroke-linecap="round"/>`);
        seg = [];
      };
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) { flush(); return; }
        seg.push(`${getX(i)},${getY(v)}`);
      });
      flush();
      if (s.showPoints !== false) {
        s.values.forEach((v, i) => {
          if (v === null || v === undefined) return;
          const tip = (s.tips && s.tips[i]) || `<strong>${this.esc(s.name)}</strong><br>${this.esc(categories[i])}: ${this._fmt(v, opts)}`;
          svg.push(`<circle cx="${getX(i)}" cy="${getY(v)}" r="${s.pointRadius || 4}" fill="${s.hollow ? '#fff' : s.color}" stroke="${s.color}" stroke-width="2" data-tip="${this.esc(tip)}" tabindex="0"/>`);
        });
      }
    });
    (opts.markers || []).forEach(m => {
      const i = categories.indexOf(m.category);
      if (i < 0 || m.value === null) return;
      const x = getX(i), y = getY(m.value);
      svg.push(`<rect x="${x - 6}" y="${y - 6}" width="12" height="12" transform="rotate(45 ${x} ${y})" fill="${m.color || this.colors.demo}" stroke="#fff" stroke-width="1.5" data-tip="${this.esc(m.tip)}" tabindex="0"/>`);
    });
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${this.esc(opts.ariaLabel || 'Gráfico de linhas')}">${svg.join('')}</svg>`;
    this.attachTooltips(container);
  },

  /* ---------------------------------------------------------------------- */
  /* 4. Sensibilidade: resultado × preço (eixo x numérico)                   */
  /* ---------------------------------------------------------------------- */
  renderSensitivity(containerId, points, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const valid = points.filter(p => p.result !== null);
    if (!valid.length) return this._empty(container, 'Ajuste os parâmetros para ver a curva.');
    const { width, height } = this._frame(container, 240);
    const pad = { top: 18, right: 22, bottom: 38, left: 72 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const xs = valid.map(p => p.price);
    const ys = valid.map(p => p.result);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(0, ...ys), maxY = Math.max(0, ...ys);
    const padY = (maxY - minY) * 0.08 || 1;
    const y0 = minY - padY, y1 = maxY + padY;
    const gx = (v) => pad.left + ((v - minX) / (maxX - minX || 1)) * chartW;
    const gy = (v) => pad.top + chartH - ((v - y0) / (y1 - y0 || 1)) * chartH;
    const svg = [];
    for (let i = 0; i <= 4; i++) {
      const v = y0 + ((y1 - y0) * i) / 4;
      const y = gy(v);
      svg.push(`<line x1="${pad.left}" y1="${y}" x2="${pad.left + chartW}" y2="${y}" stroke="${this.colors.grid}" stroke-dasharray="3 4"/>`);
      svg.push(`<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="${this.colors.muted}">${this.formatCompact(v)}</text>`);
    }
    for (let i = 0; i <= 4; i++) {
      const v = minX + ((maxX - minX) * i) / 4;
      svg.push(`<text x="${gx(v)}" y="${pad.top + chartH + 18}" text-anchor="middle" font-size="11" fill="${this.colors.muted}">${this.formatCurrency(v)}</text>`);
    }
    svg.push(`<text x="${pad.left + chartW / 2}" y="${height - 4}" text-anchor="middle" font-size="11" fill="${this.colors.muted}">Preço da saca (R$)</text>`);
    // zona de prejuízo
    const zy = gy(0);
    svg.push(`<rect x="${pad.left}" y="${zy}" width="${chartW}" height="${Math.max(0, pad.top + chartH - zy)}" fill="#FEF2F2"/>`);
    svg.push(`<line x1="${pad.left}" y1="${zy}" x2="${pad.left + chartW}" y2="${zy}" stroke="${this.colors.danger}" stroke-width="1.2"/>`);
    const path = valid.map(p => `${gx(p.price)},${gy(p.result)}`).join(' ');
    svg.push(`<polyline points="${path}" fill="none" stroke="${this.colors.primary}" stroke-width="2.6"/>`);
    valid.forEach(p => {
      const tip = `Preço ${this.formatCurrency(p.price)}<br>Resultado: <strong>${this.formatCurrency(p.result)}</strong>`;
      svg.push(`<circle cx="${gx(p.price)}" cy="${gy(p.result)}" r="7" fill="transparent" data-tip="${this.esc(tip)}"/>`);
    });
    (opts.markers || []).forEach(m => {
      if (m.price < minX || m.price > maxX) return;
      const x = gx(m.price);
      svg.push(`<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${pad.top + chartH}" stroke="${m.color}" stroke-dasharray="5 4" stroke-width="1.5"/>`);
      svg.push(`<text x="${x + 4}" y="${pad.top + 12 + (m.offset || 0)}" font-size="11" font-weight="600" fill="${m.color}">${this.esc(m.label)}</text>`);
    });
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="Sensibilidade do resultado ao preço da saca">${svg.join('')}</svg>`;
    this.attachTooltips(container);
  },

  /* ---------------------------------------------------------------------- */
  /* 5. Cenários (barras agrupadas: receita, custo, resultado)               */
  /* ---------------------------------------------------------------------- */
  renderScenarioBars(containerId, scenarios) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const valid = scenarios.filter(s => s.r && s.r.valid);
    if (!valid.length) return this._empty(container, 'Premissas inválidas.');
    const { width, height } = this._frame(container, 240);
    const pad = { top: 18, right: 14, bottom: 44, left: 70 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const metrics = [
      { key: 'revenue', label: 'Receita', color: this.colors.accent },
      { key: 'cost', label: 'Custo', color: '#94A3B8' },
      { key: 'result', label: 'Resultado', color: this.colors.primary }
    ];
    const vals = valid.flatMap(s => metrics.map(m => s.r[m.key]));
    const maxV = this._niceMax(Math.max(...vals, 1));
    const minV = Math.min(0, ...vals);
    const range = maxV - minV;
    const gy = (v) => pad.top + chartH - ((v - minV) / range) * chartH;
    const svg = [];
    for (let i = 0; i <= 4; i++) {
      const v = minV + (range * i) / 4;
      const y = gy(v);
      svg.push(`<line x1="${pad.left}" y1="${y}" x2="${pad.left + chartW}" y2="${y}" stroke="${this.colors.grid}" stroke-dasharray="${Math.abs(v) < 1e-6 ? '0' : '3 4'}"/>`);
      svg.push(`<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="${this.colors.muted}">${this.formatCompact(v)}</text>`);
    }
    const groupW = chartW / valid.length;
    const barW = Math.min(34, (groupW * 0.7) / 3);
    valid.forEach((s, gi) => {
      const gx = pad.left + groupW * gi + groupW / 2;
      metrics.forEach((m, mi) => {
        const v = s.r[m.key];
        const x = gx + (mi - 1) * (barW + 6) - barW / 2;
        const yv = gy(Math.max(v, 0)), yz = gy(Math.min(v, 0));
        const color = m.key === 'result' && v < 0 ? this.colors.danger : m.color;
        const tip = `<strong>${this.esc(s.label)}</strong><br>${m.label}: ${this.formatCurrency(v)}`;
        svg.push(`<rect x="${x}" y="${yv}" width="${barW}" height="${Math.max(yz - yv, 1)}" rx="3" fill="${color}" data-tip="${this.esc(tip)}" tabindex="0"/>`);
      });
      svg.push(`<text x="${gx}" y="${pad.top + chartH + 18}" text-anchor="middle" font-size="12" font-weight="600" fill="${this.colors.text}">${this.esc(s.label)}</text>`);
      const mg = s.r.margin;
      svg.push(`<text x="${gx}" y="${pad.top + chartH + 33}" text-anchor="middle" font-size="11" fill="${mg !== null && mg < 0 ? this.colors.danger : this.colors.muted}">margem ${mg === null ? '—' : this.formatNumber(mg, 1) + '%'}</text>`);
    });
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="Comparação de cenários">${svg.join('')}</svg>
      <div class="chart-legend">${metrics.map(m => `<span><i style="background:${m.color}"></i>${m.label}</span>`).join('')}</div>`;
    this.attachTooltips(container);
  }
};

if (typeof window !== 'undefined') {
  window.RigoraCharts = RigoraCharts;
}
