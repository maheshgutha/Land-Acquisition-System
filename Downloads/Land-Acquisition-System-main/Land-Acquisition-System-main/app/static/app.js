'use strict';
/* Core: state, API client, helpers, and the main list views (dashboard, projects, risk, alerts, reports, audit). */

const S = { token: null, user: null, meta: null, view: null, filters: { q: '', state: '', stage: '', status: '', risk: '' } };
const ACT = {};    // click handlers keyed by data-act
const FORMS = {};  // submit handlers keyed by data-form

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n, d = 1) => (n == null || n === '' ? '-' : Number(n).toLocaleString('en-IN', { maximumFractionDigits: d }));
const cr = (n) => '₹' + num(n, 1) + ' Cr';
const pct = (n) => (n == null ? '-' : num(n, 0) + '%');
const parseDate = (iso) => new Date(iso.length === 10 || iso.endsWith('Z') ? iso : iso + 'Z');
const fdate = (iso) => (iso ? parseDate(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '-');
const title = (s) => String(s || '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const titleAll = (s) => String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
const fsize = (n) => (n < 1024 ? n + ' B' : num(n / 1024, 1) + ' KB');
const can = (...roles) => !!S.user && roles.includes(S.user.role);

// Validated palette (dataviz skill references/palette.md): status colors reserved for risk state,
// categorical slots 1/3/7/2 for the five acquisition-status stages. Never reused as generic UI color.
const RISK_COLORS = { low: '#0ca30c', medium: '#fab219', high: '#ec835a', critical: '#d03b3b' };
const STATUS_COLORS = { proposed: '#898781', notified: '#2a78d6', awarded: '#4a3aa7', compensated: '#eda100', possessed: '#1baf7a' };
const DOC_CATEGORIES_EXTRA = ['notification_copy', 'award_copy', 'survey_report', 'court_order', 'payment_proof', 'other'];

const riskBadge = (cat, score) => (cat ? `<span class="badge ${cat}">${esc(cat)} ${score != null ? score + '%' : ''}</span>` : '<span class="muted">-</span>');
const statusBadge = (s) => `<span class="badge ${esc(s)}">${esc(s)}</span>`;
const bar = (p, color = 'var(--brand)') => `<div class="bar"><i style="width:${Math.max(0, Math.min(100, p || 0))}%;background:${color}"></i></div>`;

// ---------------------------------------------------------------- API
class ApiError extends Error {
  constructor(status, detail) {
    let message = 'Request failed';
    let unmet = [];
    if (typeof detail === 'string') message = detail;
    else if (Array.isArray(detail)) message = detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ');
    else if (detail && typeof detail === 'object') { message = detail.message || message; unmet = detail.unmet || []; }
    super(message);
    this.status = status;
    this.unmet = unmet;
  }
}

async function api(path, { method = 'GET', json, form, raw = false } = {}) {
  const headers = {};
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  let body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  else if (form) body = form;
  const r = await fetch(path, { method, headers, body });
  if (r.status === 401 && S.token) { logout(); throw new ApiError(401, 'Session expired, please sign in again'); }
  if (!r.ok) {
    let d = {};
    try { d = (await r.json()).detail; } catch (_) { /* not json */ }
    throw new ApiError(r.status, d);
  }
  return raw ? r : r.json();
}

async function downloadBlob(path, filename) {
  const r = await api(path, { raw: true });
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function toast(message, kind = '', unmet = []) {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = esc(message) + (unmet.length ? '<ul>' + unmet.map((u) => `<li>${esc(u)}</li>`).join('') + '</ul>' : '');
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 7000 : 3500);
}
const fail = (e) => toast(e.message || String(e), 'bad', e.unmet || []);

// run an async action; show errors as toasts
async function attempt(fn, okMessage) {
  try { const out = await fn(); if (okMessage) toast(okMessage, 'ok'); return out; } catch (e) { fail(e); return undefined; }
}

// ---------------------------------------------------------------- events
// Capture phase: Leaflet popups stop click propagation, which would otherwise hide clicks inside them.
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]');
  if (el && ACT[el.dataset.act]) { if (el.tagName === 'BUTTON' || el.tagName === 'A') e.preventDefault(); ACT[el.dataset.act](el, e); }
}, true);
document.addEventListener('submit', (e) => {
  const f = e.target.closest('form[data-form]');
  if (f && FORMS[f.dataset.form]) { e.preventDefault(); FORMS[f.dataset.form](f, e); }
});
const formData = (f) => Object.fromEntries(new FormData(f).entries());

// ---------------------------------------------------------------- auth & shell
function saveSession() {
  try { sessionStorage.setItem('landscan', JSON.stringify({ token: S.token, user: S.user })); } catch (_) { /* storage unavailable */ }
}
function loadSession() {
  try { const s = JSON.parse(sessionStorage.getItem('landscan') || 'null'); if (s && s.token) { S.token = s.token; S.user = s.user; return true; } } catch (_) { /* ignore */ }
  return false;
}
function logout() {
  S.token = S.user = null;
  try { sessionStorage.removeItem('landscan'); } catch (_) { /* ignore */ }
  closeDrawer && closeDrawer();
  showLogin();
}

async function showLogin() {
  $('#app').classList.add('hidden');
  $('#login').classList.remove('hidden');
  try {
    const users = await (await fetch('/api/auth/demo-users')).json();
    if (Array.isArray(users)) {
      $('#demo-users').classList.remove('hidden');
      $('#demo-chips').innerHTML = users.map((u) => `<button type="button" class="chip" data-act="fill-login" data-u="${esc(u.username)}" title="${esc(u.full_name)}">${esc(u.username)} <span class="muted">(${esc(u.role)})</span></button>`).join('');
    }
  } catch (_) { /* demo list disabled */ }
}
ACT['fill-login'] = (el) => { $('#lu').value = el.dataset.u; $('#lp').value = 'demo1234'; };
FORMS.login = async (f) => {
  const err = $('#login-error'); err.classList.add('hidden');
  try {
    const d = formData(f);
    const r = await api('/api/auth/login', { method: 'POST', form: new URLSearchParams(d) });
    S.token = r.access_token; S.user = r.user; saveSession();
    await startApp();
  } catch (e) { err.textContent = e.message; err.classList.remove('hidden'); }
};
ACT.logout = () => logout();

const TABS = [
  { key: 'dashboard', label: 'Dashboard' }, { key: 'map', label: 'Map' }, { key: 'projects', label: 'Projects' },
  { key: 'risk', label: 'Delay risk' }, { key: 'alerts', label: 'Alerts' }, { key: 'reports', label: 'Reports' },
  { key: 'audit', label: 'Audit', roles: ['auditor', 'central'] },
];

async function startApp() {
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
  S.meta = await api('/api/meta');
  const scope = S.user.district ? `${S.user.district}, ${S.user.state}` : S.user.state || S.user.agency || 'All India';
  $('#who').innerHTML = `<b>${esc(S.user.full_name)}</b> &middot; ${esc(S.user.role)} &middot; ${esc(scope)}`;
  $('#nav').innerHTML = TABS.filter((t) => !t.roles || t.roles.includes(S.user.role))
    .map((t) => `<a href="#/${t.key}" data-tab="${t.key}">${t.label}</a>`).join('');
  route();
}

function route() {
  if (!S.user) return;
  const key = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/')[0];
  const tab = TABS.find((t) => t.key === key && (!t.roles || t.roles.includes(S.user.role))) || TABS[0];
  S.view = tab.key;
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.tab === tab.key));
  if (S.map) { S.map.remove(); S.map = null; }
  const main = $('#main');
  main.innerHTML = '<div class="grid g4">' + '<div class="card skeleton skel-card"></div>'.repeat(8) + '</div>';
  const view = VIEWS[tab.key];
  view(main).catch((e) => { main.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; });
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------------- dashboard
async function viewDashboard(main) {
  const [s, states, trends, early, late] = await Promise.all([
    api('/api/dashboard/summary'), api('/api/dashboard/by-state'), api('/api/dashboard/trends'), api('/api/risk/portfolio?limit=6&delayed=false'), api('/api/risk/portfolio?limit=6&delayed=true'),
  ]);
  const a = s.area_ha, r = s.risk, maxStage = Math.max(1, ...s.by_stage.map((x) => x.count));
  const areaRow = (label, v, color) => `<div class="mini"><span>${label}</span>${bar(a.proposed ? (100 * v) / a.proposed : 0, color)}<span class="v">${num(v)} ha</span></div>`;

  main.innerHTML = `
    <div class="gap mb"><h2>National overview</h2><span class="muted small">${esc(S.user.role === 'central' || S.user.role === 'auditor' ? 'All states' : (S.user.district ? S.user.district + ', ' : '') + (S.user.state || S.user.agency || ''))}</span><div class="spacer"></div><button class="btn sm" data-act="print-page">Print executive summary</button></div>
    <div class="insight mb">${buildInsight(s)}</div>
    <div class="grid g4">
      <div class="card"><h4>Projects</h4><div class="kpi">${s.projects.total}</div>
        <div class="sub">${s.projects.active} active &middot; <span class="fail">${s.projects.stalled} stalled</span> &middot; ${s.projects.completed} completed</div></div>
      <div class="card"><h4>Land acquisition</h4>${areaRow('Proposed', a.proposed, 'var(--proposed)')}${areaRow('Notified', a.notified, 'var(--notified)')}${areaRow('Awarded', a.awarded, 'var(--awarded)')}${areaRow('Possessed', a.acquired, 'var(--possessed)')}</div>
      <div class="card"><h4>Notifications and awards</h4><div class="kpi">${s.notifications}<small>notifications</small></div>
        <div class="sub">${s.awards.count} awards declared, ${cr(s.awards.amount_cr)}</div></div>
      <div class="card"><h4>Compensation</h4><div class="kpi">${pct(s.compensation.disbursed_pct)}<small>disbursed</small></div>
        ${bar(s.compensation.disbursed_pct, 'var(--compensated)')}<div class="sub">${cr(s.compensation.disbursed_cr)} of ${cr(s.compensation.assessed_cr)} assessed</div></div>
      <div class="card"><h4>Families and R&amp;R</h4><div class="kpi">${num(s.families.displaced, 0)}<small>displaced of ${num(s.families.affected, 0)} affected</small></div>
        ${bar(s.families.rr_progress_pct, 'var(--notified)')}<div class="sub">R&amp;R progress ${pct(s.families.rr_progress_pct)} &middot; ${s.families.rr_status.resettled} resettled, ${s.families.rr_status.allotted} allotted</div></div>
      <div class="card"><h4>Possession</h4><div class="kpi">${pct(s.possession.pct)}<small>of parcels</small></div>
        ${bar(s.possession.pct, 'var(--possessed)')}<div class="sub">${s.possession.parcels_possessed} of ${s.possession.parcels_total} parcels in possession</div></div>
      <div class="card"><h4>Schedule</h4><div class="kpi">${pct(s.timeline.active_projects_on_schedule_pct)}<small>on schedule</small></div>
        <div class="sub">${s.timeline.overdue_projects} overdue now &middot; ${s.timeline.completed_stages_on_time_pct == null ? '-' : pct(s.timeline.completed_stages_on_time_pct)} of finished stages on time</div></div>
      <div class="card"><h4>Delay risk (active projects)</h4>${CHART.statusBar([
    { key: 'low', label: 'Low', value: r.low, color: RISK_COLORS.low },
    { key: 'medium', label: 'Medium', value: r.medium, color: RISK_COLORS.medium },
    { key: 'high', label: 'High', value: r.high, color: RISK_COLORS.high },
    { key: 'critical', label: 'Critical', value: r.critical, color: RISK_COLORS.critical },
  ])}</div>
    </div>

    <div class="grid g2 mt">
      <div class="card"><h4>Projects by lifecycle stage</h4>
        ${s.by_stage.map((x) => `<div class="mini" style="grid-template-columns:170px 1fr 30px"><span>${esc(x.label)}</span>${bar((100 * x.count) / maxStage, 'var(--brand)')}<span class="v">${x.count}</span></div>`).join('')}</div>
      <div class="card"><h4>Early warnings: not late yet, highest predicted delay risk</h4>
        <div class="table-wrap"><table><thead><tr><th>Project</th><th>Stage</th><th>Risk</th><th>Main driver</th></tr></thead><tbody>
        ${early.items.map((p) => `<tr class="click" data-act="open-project" data-id="${p.id}"><td><b>${esc(p.code)}</b><div class="small muted">${esc(p.district)}, ${esc(p.state)}</div></td><td>${esc(p.stage_label)}<div class="small muted">${num(p.days_in_stage, 0)} of ${p.planned_days} d</div></td><td>${riskBadge(p.risk_category, p.risk_score)}</td><td class="small">${esc(p.top_driver || '-')}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No open early warnings.</td></tr>'}
        </tbody></table></div>
        <h4 class="mt">Already late (past 125% of plan)</h4>
        <div class="table-wrap"><table><tbody>
        ${late.items.map((p) => `<tr class="click" data-act="open-project" data-id="${p.id}"><td><b>${esc(p.code)}</b><div class="small muted">${esc(p.district)}</div></td><td>${esc(p.stage_label)}</td><td class="num fail">${num(p.overrun_ratio * 100, 0)}% of plan</td><td>${p.status === 'stalled' ? statusBadge('stalled') : ''}</td></tr>`).join('') || '<tr><td class="muted">None.</td></tr>'}
        </tbody></table></div></div>
    </div>

    <div class="grid g2 mt">
      <div class="card"><h4>State-wise progress</h4><div class="table-wrap"><table><thead><tr><th>State</th><th class="num">Projects</th><th>Avg progress</th><th class="num">Acquired / proposed (ha)</th><th class="num">Disbursed %</th><th class="num">Stalled</th><th class="num">High risk</th></tr></thead><tbody>
        ${states.map((g) => `<tr><td><b>${esc(g.state)}</b></td><td class="num">${g.projects}</td><td class="progress">${bar(g.avg_progress_pct)}<span class="small muted">${pct(g.avg_progress_pct)}</span></td><td class="num">${num(g.acquired_ha)} / ${num(g.proposed_ha)}</td><td class="num">${g.assessed_cr ? pct((100 * g.disbursed_cr) / g.assessed_cr) : '-'}</td><td class="num">${g.stalled}</td><td class="num">${g.high_risk}</td></tr>`).join('')}
        </tbody></table></div></div>
      <div class="card"><h4>Where stages slip (finished stages)</h4><div class="table-wrap"><table><thead><tr><th>Stage</th><th class="num">Planned d</th><th class="num">Actual d</th><th>Delayed</th></tr></thead><tbody>
        ${trends.stage_performance.map((x) => `<tr><td>${esc(x.label)}</td><td class="num">${num(x.avg_planned_days, 0)}</td><td class="num">${num(x.avg_actual_days, 0)}</td><td class="progress">${CHART.meter(x.delayed_pct, { goodMax: 25, badMin: 45 })}<span class="small muted">${pct(x.delayed_pct)} of ${x.n}</span></td></tr>`).join('')}
        </tbody></table></div></div>
    </div>
    <div class="card mt"><h4>Delay-risk trend, national average score by month</h4>
      ${trends.risk_over_time.length > 1 ? CHART.lineChart(trends.risk_over_time.map((x) => ({ x: x.month, y: x.avg_score, n: x.n })), { valueLabel: 'avg score', fmt: (v) => v + '%' }) : '<span class="muted small">Not enough scoring history yet — score more projects over time to see a trend.</span>'}
    </div>
    <p class="muted small mt">Demo data is synthetic. "Delayed" means a stage took more than 125% of its planned duration.</p>`;
}

// One or two plain-English sentences synthesizing the KPIs above — the "decision support" the PS asks for,
// not a chart. Built from numbers already on screen; nothing here is inferred beyond simple arithmetic.
function buildInsight(s) {
  const bits = [];
  bits.push(`${s.projects.active} of ${s.projects.total} projects are active${s.projects.stalled ? `, ${s.projects.stalled} stalled` : ''}.`);
  const crit = s.risk.critical, high = s.risk.high;
  if (crit + high > 0) bits.push(`${crit + high} project${crit + high === 1 ? '' : 's'} (${crit} critical, ${high} high) are at elevated delay risk and worth reviewing first.`);
  if (s.timeline.overdue_projects) bits.push(s.timeline.overdue_projects === 1 ? '1 project is already past its planned stage duration.' : `${s.timeline.overdue_projects} projects are already past their planned stage duration.`);
  if (s.compensation.assessed_cr) bits.push(`Compensation is ${pct(s.compensation.disbursed_pct)} disbursed (${cr(s.compensation.disbursed_cr)} of ${cr(s.compensation.assessed_cr)} assessed).`);
  if (s.families.displaced) bits.push(`R&amp;R is ${pct(s.families.rr_progress_pct)} complete for ${s.families.displaced} displaced families.`);
  return bits.join(' ');
}

// ---------------------------------------------------------------- projects
const projectRow = (p) => `<tr class="click" data-act="open-project" data-id="${p.id}">
  <td><b>${esc(p.code)}</b><div class="small muted">${esc(p.name)}</div></td>
  <td>${esc(p.district)}<div class="small muted">${esc(p.state)}</div></td>
  <td>${esc(title(p.project_type))}<div class="small muted">${esc(p.agency)}</div></td>
  <td>${esc(p.stage_label)}<div class="progress">${bar(p.progress_pct)}</div></td>
  <td>${statusBadge(p.status)}${p.status === 'stalled' ? `<div class="small muted">${esc(S.meta.stall_reasons[p.stall_reason] || '')}</div>` : ''}</td>
  <td class="num nowrap ${p.overdue ? 'fail' : ''}">${p.status === 'completed' ? '-' : num(p.days_in_stage, 0) + ' / ' + p.planned_days + ' d'}</td>
  <td class="num">${num(p.proposed_area_ha)} ha</td>
  <td>${p.status === 'completed' ? '<span class="muted">-</span>' : riskBadge(p.risk_category, p.risk_score)}</td></tr>`;

async function viewProjects(main) {
  const f = S.filters;
  const params = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  const data = await api('/api/projects?' + params);
  const opt = (arr, cur, label = (x) => x) => arr.map((x) => `<option value="${esc(x.v ?? x)}" ${(x.v ?? x) === cur ? 'selected' : ''}>${esc(label(x.l ?? x))}</option>`).join('');
  main.innerHTML = `
    <div class="gap mb"><h2>Projects</h2><span class="muted">${data.total} shown</span><div class="spacer"></div>
      ${can('agency', 'state') ? '<button class="btn primary" data-act="new-project">New project</button>' : ''}</div>
    <div class="filters">
      <div class="grow"><label>Search</label><input id="f-q" value="${esc(f.q)}" placeholder="Name or code"></div>
      <div><label>State</label><select id="f-state"><option value="">All</option>${opt(S.meta.states, f.state)}</select></div>
      <div><label>Stage</label><select id="f-stage"><option value="">All</option>${opt(S.meta.stages.map((s) => ({ v: s.key, l: s.label })), f.stage)}</select></div>
      <div><label>Status</label><select id="f-status"><option value="">All</option>${opt(['active', 'stalled', 'completed'], f.status)}</select></div>
      <div><label>Risk</label><select id="f-risk"><option value="">All</option>${opt(['critical', 'high', 'medium', 'low'], f.risk)}</select></div>
    </div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Project</th><th>Location</th><th>Type / agency</th><th>Stage</th><th>Status</th><th class="num">In stage / plan</th><th class="num">Area</th><th>Delay risk</th></tr></thead>
    <tbody>${data.items.map(projectRow).join('') || '<tr><td colspan="8" class="muted">No projects match.</td></tr>'}</tbody></table></div></div>`;
  const bind = (id, key, ev = 'change') => $(id).addEventListener(ev, (e) => { S.filters[key] = e.target.value; viewProjects(main); });
  bind('#f-state', 'state'); bind('#f-stage', 'stage'); bind('#f-status', 'status'); bind('#f-risk', 'risk');
  let t; $('#f-q').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { S.filters.q = e.target.value; viewProjects(main).then(() => { const i = $('#f-q'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 350); });
}

ACT['open-project'] = (el) => openProject(Number(el.dataset.id));

ACT['new-project'] = () => {
  const states = S.meta.states.map((s) => `<option ${S.user.state === s ? 'selected' : ''}>${esc(s)}</option>`).join('');
  const types = ['highway', 'railway', 'solar_park', 'irrigation', 'urban', 'industrial_corridor'].map((t) => `<option value="${t}">${title(t)}</option>`).join('');
  openModal(`<h3>New land acquisition proposal</h3><form data-form="new-project" class="mt">
    <div class="field"><label>Project name</label><input name="name" required minlength="3"></div>
    <div class="row"><div class="field"><label>Type</label><select name="project_type">${types}</select></div>
      <div class="field"><label>State</label><select name="state">${states}</select></div>
      <div class="field"><label>District</label><input name="district" required></div></div>
    ${S.user.role === 'state' ? '<div class="field"><label>Requesting agency</label><input name="agency" required></div>' : ''}
    <div class="row"><div class="field"><label>Proposed area (ha)</label><input name="proposed_area_ha" type="number" step="0.01" min="0.01" required></div>
      <div class="field"><label>Estimated cost (Rs Cr)</label><input name="estimated_cost_cr" type="number" step="0.1" min="0" required></div>
      <div class="field"><label>Planned completion</label><input name="planned_completion" type="date"></div></div>
    <div class="gap"><button class="btn primary" type="submit">Create proposal</button><button class="btn" type="button" data-act="close-modal">Cancel</button></div></form>`);
};
FORMS['new-project'] = async (f) => {
  const d = formData(f);
  ['proposed_area_ha', 'estimated_cost_cr'].forEach((k) => { d[k] = Number(d[k]); });
  if (!d.planned_completion) delete d.planned_completion;
  const p = await attempt(() => api('/api/projects', { method: 'POST', json: d }), 'Proposal created');
  if (p) { closeModal(); route(); openProject(p.id); }
};

// ---------------------------------------------------------------- delay risk
async function viewRisk(main) {
  const [pf, model] = await Promise.all([api('/api/risk/portfolio?limit=100'), api('/api/risk/model')]);
  const v = model.versions.find((x) => x.active) || model.versions[0];
  main.innerHTML = `
    <div class="gap mb"><h2>Delay risk</h2><div class="spacer"></div>
      ${can('central', 'state') ? '<button class="btn" data-act="rescore-all">Rescore all projects</button>' : ''}
      ${can('central') ? '<button class="btn primary" data-act="retrain">Retrain from recorded outcomes</button>' : ''}</div>
    <div class="banner warn">${esc(model.training_data_note)}</div>
    <div id="retrain-result"></div>
    <div class="grid g2 mb">
      <div class="card"><h4>Active model</h4>${v ? `<div class="kv">
        <div><b style="font-size:12px;word-break:break-all">${esc(v.version)}</b><span>version</span></div>
        <div><b>${v.metrics.auc ?? '-'}</b><span>AUC (held-out)</span></div>
        <div><b>${v.metrics.top_decile_lift ?? '-'}x</b><span>top-decile lift</span></div>
        <div><b>${v.metrics.brier ?? '-'}</b><span>Brier score</span></div>
        <div><b>${v.n_train}</b><span>training rows</span></div>
        <div><b>${v.n_real}</b><span>recorded outcomes used</span></div></div>` : '<span class="muted">No model yet</span>'}
        <p class="small muted mt">LightGBM classifier with probability calibration; explanations are TreeSHAP values. The target is "this stage will run more than 25% over its plan".</p></div>
      <div class="card"><h4>Model versions</h4><div class="table-wrap"><table><thead><tr><th>Version</th><th>Trained</th><th class="num">Real rows</th><th class="num">AUC</th><th></th></tr></thead><tbody>
        ${model.versions.map((x) => `<tr><td class="small">${esc(x.version.slice(0, 15))}</td><td class="small">${fdate(x.trained_at)}</td><td class="num">${x.n_real}</td><td class="num">${x.metrics.auc ?? '-'}</td><td>${x.active ? '<span class="badge active">active</span>' : ''}</td></tr>`).join('')}
        </tbody></table></div></div>
    </div>
    <div class="card"><h4>Active projects ranked by delay probability</h4>
      <p class="small muted">Projects already past 125% of a stage's plan are delayed by definition, so their probability is near 100%; the overrun column ranks them.</p>
      <div class="table-wrap"><table><thead><tr><th>Project</th><th>Stage</th><th class="num">Overrun</th><th>Risk</th><th>Main driver</th></tr></thead><tbody>
      ${pf.items.map((p) => `<tr class="click" data-act="open-project" data-id="${p.id}"><td><b>${esc(p.code)}</b><div class="small muted">${esc(p.name)}</div></td><td>${esc(p.stage_label)}</td><td class="num ${p.overrun_ratio > 1.25 ? 'fail' : ''}">${num(p.overrun_ratio * 100, 0)}%</td><td>${riskBadge(p.risk_category, p.risk_score)}</td><td class="small">${esc(p.top_driver || '-')}</td></tr>`).join('')}
      </tbody></table></div></div>`;
}
ACT['rescore-all'] = async () => { const r = await attempt(() => api('/api/risk/rescore', { method: 'POST' })); if (r) { toast(`Rescored ${r.rescored} projects`, 'ok'); route(); } };
ACT.retrain = async () => {
  const r = await attempt(() => api('/api/risk/retrain', { method: 'POST' }));
  if (!r) return;
  $('#retrain-result').innerHTML = r.challenger
    ? `<div class="banner ${r.promoted ? 'ok' : 'info'}"><b>${r.promoted ? 'New model promoted' : 'Challenger not promoted'}.</b> ${esc(r.reason)}. Used ${r.n_real} recorded outcome(s). AUC challenger ${r.challenger.auc} vs champion ${r.champion.auc} on the same holdout.</div>`
    : `<div class="banner info">${esc(r.reason)}</div>`;
};

// ---------------------------------------------------------------- alerts
async function viewAlerts(main) {
  const alerts = await api('/api/alerts?limit=200');
  main.innerHTML = `<div class="gap mb"><h2>Alerts</h2><span class="muted">${alerts.length} open</span></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Severity</th><th>Project</th><th>Alert</th><th>Source</th><th>Raised</th><th></th></tr></thead><tbody>
    ${alerts.map((a) => `<tr><td><span class="badge ${esc(a.severity)}">${esc(a.severity)}</span></td>
      <td class="click" data-act="open-project" data-id="${a.project_id}"><b>${esc(a.project_code)}</b><div class="small muted">${esc(a.project_name)}</div></td>
      <td>${esc(a.message)}<div class="small muted">${esc(title(a.kind))}</div></td><td><span class="badge ${esc(a.source)}">${esc(a.source)}</span></td>
      <td class="small nowrap">${fdate(a.created_at)}</td>
      <td>${can('central', 'state', 'district', 'field', 'agency') ? `<button class="btn sm" data-act="ack-alert" data-id="${a.id}">Acknowledge</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No open alerts.</td></tr>'}
    </tbody></table></div></div>
    <p class="small muted mt">Rule alerts come from recorded facts (overdue stages, stalls, disputes, payment lag). Model alerts come from the delay-risk model.</p>`;
}
ACT['ack-alert'] = async (el) => { if (await attempt(() => api(`/api/alerts/${el.dataset.id}/ack`, { method: 'POST' }), 'Acknowledged')) route(); };

// ---------------------------------------------------------------- reports
async function viewReports(main) {
  if (!can('central', 'state', 'district', 'auditor', 'agency')) { main.innerHTML = '<div class="banner warn">Reports are not available for your role.</div>'; return; }
  main.innerHTML = `<div class="gap mb"><h2>MIS reports</h2></div>
    <div class="filters"><div><label>Group by</label><select id="r-group"><option value="">None (one row per project)</option>
      ${['state', 'district', 'project_type', 'agency', 'stage'].map((g) => `<option value="${g}">${title(g)}</option>`).join('')}</select></div>
      <div><button class="btn" data-act="run-report">Preview</button></div>
      <div><button class="btn primary" data-act="csv-report">Download CSV</button></div>
      <div><button class="btn" data-act="print-page">Print</button></div></div>
    <div class="card"><div class="table-wrap" id="report-out"><span class="muted">Choose grouping and preview.</span></div></div>`;
  ACT['run-report']();
}
ACT['run-report'] = async () => {
  const g = $('#r-group').value;
  const d = await attempt(() => api('/api/reports/mis' + (g ? '?group_by=' + g : '')));
  if (!d) return;
  $('#report-out').innerHTML = `<table><thead><tr>${d.columns.map((c) => `<th>${esc(title(c))}</th>`).join('')}</tr></thead><tbody>${d.rows.slice(0, 200).map((r) => `<tr>${d.columns.map((c) => `<td>${esc(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
};
ACT['csv-report'] = () => { const g = $('#r-group').value; attempt(() => downloadBlob('/api/reports/mis?format=csv' + (g ? '&group_by=' + g : ''), 'mis_report.csv')); };

// ---------------------------------------------------------------- audit
async function viewAudit(main, offset = 0) {
  const a = await api(`/api/audit?limit=50&offset=${offset}`);
  main.innerHTML = `<div class="gap mb"><h2>Audit trail</h2><span class="muted">${a.total} entries</span><div class="spacer"></div><button class="btn primary" data-act="verify-audit">Verify integrity</button></div>
    <div id="audit-verify"></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th><th>Hash</th></tr></thead><tbody>
    ${a.items.map((r) => `<tr><td>${r.id}</td><td class="small nowrap">${parseDate(r.ts).toLocaleString('en-IN')}</td><td>${esc(r.actor)}<div class="small muted">${esc(r.role)}</div></td><td>${esc(r.action)}</td><td>${esc(r.entity_type)} ${esc(r.entity_id)}</td><td class="small">${esc(JSON.stringify(r.detail)).slice(0, 110)}</td><td class="small muted">${esc(r.hash)}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="gap mt"><button class="btn sm" ${offset === 0 ? 'disabled' : ''} data-act="audit-page" data-o="${Math.max(0, offset - 50)}">Newer</button><button class="btn sm" ${offset + 50 >= a.total ? 'disabled' : ''} data-act="audit-page" data-o="${offset + 50}">Older</button></div></div>
    <p class="small muted mt">Each entry stores a hash of its content and the previous entry's hash, so editing or deleting history breaks the chain.</p>`;
}
ACT['audit-page'] = (el) => viewAudit($('#main'), Number(el.dataset.o));
ACT['verify-audit'] = async () => {
  const r = await attempt(() => api('/api/audit/verify'));
  if (r) $('#audit-verify').innerHTML = r.valid
    ? `<div class="banner ok">Chain intact: ${r.checked} entries verified.</div>`
    : `<div class="banner bad">Chain BROKEN at entry #${r.broken_at} after ${r.checked} valid entries. History has been altered.</div>`;
};

// ---------------------------------------------------------------- modal
function openModal(html) { $('#modal-root').innerHTML = `<div class="modal-wrap" data-act="modal-bg"><div class="modal">${html}</div></div>`; }
function closeModal() { $('#modal-root').innerHTML = ''; }
ACT['print-page'] = () => window.print();
ACT['close-modal'] = closeModal;
ACT['modal-bg'] = (el, e) => { if (e.target === el) closeModal(); };

const VIEWS = { dashboard: viewDashboard, map: (m) => viewMap(m), projects: viewProjects, risk: viewRisk, alerts: viewAlerts, reports: viewReports, audit: (m) => viewAudit(m, 0) };
