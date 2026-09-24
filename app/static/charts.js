'use strict';
/* Small chart components built to the dataviz skill's spec: fixed mark sizes, a 2px surface gap
   between touching segments, a 2px surface ring on markers, direct labels used sparingly, a legend
   whenever there are 2+ series, and a hover layer on every line chart. No external chart library. */

const CHART = {};
let _tip; // one shared tooltip element, repositioned per chart

function tipEl() {
  if (!_tip) { _tip = document.createElement('div'); _tip.className = 'chart-tip hidden'; document.body.appendChild(_tip); }
  return _tip;
}
function showTip(x, y, html) { const t = tipEl(); t.innerHTML = html; t.style.left = x + 'px'; t.style.top = y + 'px'; t.classList.remove('hidden'); }
function hideTip() { if (_tip) _tip.classList.add('hidden'); }

// ---------------------------------------------------------------- part-to-whole: status bar + legend
// segments: [{key, label, value, color}]. Renders one horizontal stacked bar (2px surface gaps between
// segments) plus a legend row below with swatch + label + value, since status hues alone aren't
// reliably distinguishable (see palette.md contrast WARN) — labels are never optional here.
CHART.statusBar = function (segments, { height = 22 } = {}) {
  const total = Math.max(1, segments.reduce((a, s) => a + s.value, 0));
  const bar = segments.filter((s) => s.value > 0).map((s) => {
    const pct = (100 * s.value) / total;
    return `<span class="cbar-seg" style="width:${pct}%;background:${s.color}" title="${esc(s.label)}: ${s.value} (${Math.round(pct)}%)"></span>`;
  }).join('');
  const legend = segments.map((s) => `<span class="cbar-key"><i style="background:${s.color}"></i>${esc(s.label)} <b>${s.value}</b></span>`).join('');
  return `<div class="cbar" style="height:${height}px">${bar}</div><div class="cbar-legend">${legend}</div>`;
};

// ---------------------------------------------------------------- trend over time: line chart
// points: [{x: label, y: number, n: count}]. Single series -> one hue, no legend box (title names it).
// Ships a hover crosshair + tooltip and a labeled end-point per spec.
CHART.lineChart = function (points, { color = '#2a78d6', height = 180, valueLabel = 'value', fmt = (v) => v, id, compact = false } = {}) {
  if (!points.length) return '<div class="muted small">Not enough data yet.</div>';
  const w = compact ? 200 : 640, h = height;
  const padL = compact ? 4 : 40, padR = compact ? 4 : 16, padT = compact ? 6 : 16, padB = compact ? 4 : 26;
  const innerW = w - padL - padR, innerH = h - padT - padB;
  const ys = points.map((p) => p.y);
  const yMax = Math.max(1, ...ys) * 1.15, yMin = Math.min(0, ...ys);
  const x = (i) => padL + (points.length > 1 ? (i * innerW) / (points.length - 1) : innerW / 2);
  const y = (v) => padT + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join(' ');
  const area = `${path} L${x(points.length - 1).toFixed(1)},${(padT + innerH).toFixed(1)} L${x(0).toFixed(1)},${(padT + innerH).toFixed(1)} Z`;
  const ticks = 4;
  const gridlines = compact ? '' : Array.from({ length: ticks + 1 }, (_, i) => {
    const v = yMin + ((yMax - yMin) * i) / ticks;
    return `<line x1="${padL}" x2="${w - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="cgrid"/><text x="${padL - 6}" y="${(y(v) + 3).toFixed(1)}" class="caxis" text-anchor="end">${fmt(Math.round(v))}</text>`;
  }).join('');
  const xlabels = compact ? '' : points.map((p, i) => (i % Math.ceil(points.length / 7 || 1) === 0 || i === points.length - 1
    ? `<text x="${x(i).toFixed(1)}" y="${h - 6}" class="caxis" text-anchor="middle">${esc(p.x)}</text>` : '')).join('');
  const last = points[points.length - 1];
  const dots = points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.y).toFixed(1)}" r="${compact ? 2.5 : 4}" fill="${color}" stroke="#fcfcfb" stroke-width="2" data-i="${i}"/>`).join('');
  const cid = id || 'lc' + Math.random().toString(36).slice(2, 8);
  const label = compact ? '' : `<text x="${x(points.length - 1).toFixed(1)}" y="${(y(last.y) - 10).toFixed(1)}" class="cend" text-anchor="end">${fmt(last.y)}</text>`;
  return `<svg ${compact ? `width="${w}" height="${h}"` : 'width="100%"'} viewBox="0 0 ${w} ${h}" class="linechart" id="${cid}" data-color="${color}">
    ${gridlines}${xlabels}
    <path d="${area}" fill="${color}" opacity="0.1"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}${label}
    <rect x="${padL}" y="${padT}" width="${innerW}" height="${innerH}" fill="transparent" class="chit" data-points='${esc(JSON.stringify(points.map((p, i) => ({ x: x(i), y: y(p.y), label: p.x, value: p.y, n: p.n }))))}' data-valuelabel="${esc(valueLabel)}"/>
  </svg>`;
};

// Delegated hover for every .linechart on the page: finds the nearest point to the pointer and shows a tooltip.
document.addEventListener('pointermove', (e) => {
  const hit = e.target.closest('.chit');
  if (!hit) return;
  const svg = hit.closest('svg');
  const pts = JSON.parse(hit.dataset.points);
  const box = svg.getBoundingClientRect();
  const scaleX = box.width / svg.viewBox.baseVal.width;
  const mx = (e.clientX - box.left) / scaleX;
  let nearest = pts[0], best = Infinity;
  for (const p of pts) { const d = Math.abs(p.x - mx); if (d < best) { best = d; nearest = p; } }
  showTip(e.clientX + 12, e.clientY + 12, `<b>${esc(String(nearest.label))}</b><br>${esc(hit.dataset.valuelabel)}: ${nearest.value}${nearest.n != null ? ` <span class="muted">(n=${nearest.n})</span>` : ''}`);
});
document.addEventListener('pointerleave', (e) => { if (e.target.closest && e.target.closest('.chit')) hideTip(); }, true);
document.addEventListener('scroll', hideTip, true);

// ---------------------------------------------------------------- meter: a ratio against a limit
// Fill carries severity (good/warning/critical by threshold); unfilled track is a lighter step of blue.
// Default direction: higher = worse (e.g. "% delayed") — green at/below goodMax, red at/above badMin.
// invert: true reads the opposite way (higher = better, e.g. "% on schedule").
CHART.meter = function (pct, { goodMax = 40, badMin = 70, height = 8, invert = false } = {}) {
  const p = Math.max(0, Math.min(100, pct || 0));
  const color = invert
    ? (p >= badMin ? 'var(--low)' : p <= goodMax ? 'var(--critical)' : 'var(--medium)')
    : (p <= goodMax ? 'var(--low)' : p >= badMin ? 'var(--critical)' : 'var(--medium)');
  return `<div class="meter" style="height:${height}px"><i style="width:${p}%;background:${color}"></i></div>`;
};

window.CHART = CHART;
