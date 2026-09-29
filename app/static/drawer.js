'use strict';
/* Project drawer: lifecycle stepper, stage actions, risk explanation, land records, documents, timeline. */

let D = null; // { id, d: detail, tab, land, docs, why, dirty }

async function openProject(id, tab = 'overview') {
  D = { id, tab, d: null, land: null, docs: null, why: null, dirty: false };
  $('#drawer-root').innerHTML = '<div class="overlay"><div class="drawer"><div class="loading">Loading project...</div></div></div>';
  try { D.d = await api(`/api/projects/${id}`); } catch (e) { closeDrawer(); fail(e); return; }
  renderDrawer();
}

function closeDrawer() {
  const dirty = D && D.dirty;
  destroyDrawMap();
  $('#drawer-root').innerHTML = '';
  D = null;
  if (dirty && S.user && S.view !== 'map') route();
}
ACT['close-drawer'] = closeDrawer;
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { if ($('#modal-root').innerHTML) closeModal(); else if (D) closeDrawer(); } });
document.addEventListener('mousedown', (e) => { if (D && e.target.classList && e.target.classList.contains('overlay')) closeDrawer(); });

async function reloadDetail(extra) {
  D.d = await api(`/api/projects/${D.id}`);
  D.dirty = true;
  if (extra) await extra();
  renderDrawer();
}

// run a mutation, then refresh the detail (and the current tab's data)
async function mutate(fn, okMsg) {
  const r = await attempt(fn, okMsg);
  if (r === undefined) return undefined;
  D.land = null; D.docs = null; D.why = null;
  await reloadDetail(async () => { await loadTabData(); });
  return r;
}

const stageIdx = (key) => S.meta.stages.findIndex((s) => s.key === key);

function renderDrawer() {
  if (!D || !D.d) return;
  destroyDrawMap();
  const d = D.d;
  const riskCat = d.risk && d.status !== 'completed' ? d.risk.category : null;
  const tabs = [['overview', 'Overview'], ['land', 'Land and payments'], ['documents', 'Documents'], ['timeline', 'Timeline']];
  $('#drawer-root').innerHTML = `<div class="overlay"><div class="drawer">
    <div class="head">
      <div class="gap"><div><h3>${esc(d.code)}</h3><div>${esc(d.name)}</div></div><div class="spacer"></div>
        <button class="btn sm" data-act="show-on-map">Show on map</button><button class="btn sm" data-act="close-drawer">Close</button></div>
      <div class="gap mt">${statusBadge(d.status)} <span class="badge">${esc(d.stage_label)}</span> ${riskCat ? riskBadge(riskCat, d.risk.score) : ''}
        <span class="muted small">${esc(d.agency)} &middot; ${esc(d.district)}, ${esc(d.state)} &middot; ${esc(title(d.project_type))} &middot; ${esc(titleAll(d.template_key))} template</span></div>
    </div>
    <div class="body">
      ${stepperHtml(d)}
      ${actionHtml(d)}
      <div class="tabs2">${tabs.map(([k, l]) => `<button class="${D.tab === k ? 'active' : ''}" data-act="drawer-tab" data-tab="${k}">${l}</button>`).join('')}</div>
      <div id="tab-body"></div>
    </div></div></div>`;
  renderTab();
}

function stepperHtml(d) {
  return `<div class="stepper">${d.stages.map((s, i) => `<div class="step ${s.state} ${s.delayed ? 'delayed' : ''}">
    <div class="node">${s.state === 'done' ? '&#10003;' : i + 1}</div>${esc(s.label)}
    <span class="d">${s.actual_days != null ? num(s.actual_days, 0) + ' of ' + s.planned_days + ' d' : s.planned_days ? s.planned_days + ' d plan' : ''}</span></div>`).join('')}</div>`;
}

function actionHtml(d) {
  if (d.status === 'completed') return '<div class="banner ok">Project completed: all stages closed out.</div>';
  const allowed = d.advance_roles.includes(S.user.role);
  if (d.status === 'stalled') {
    const reason = S.meta.stall_reasons[d.stall_reason] || 'reason not recorded';
    return `<div class="banner bad"><b>Stalled</b> since ${fdate(d.stalled_since)}: ${esc(reason)}. ${esc(d.stall_note || '')}
      <div class="mt">${can('district', 'state', 'central') ? '<button class="btn sm" data-act="resume-project">Resume project</button>' : '<span class="small">District, state or central officers can resume.</span>'}</div></div>`;
  }
  const gate = d.gate_issues.length ? `<ul class="plain small">${d.gate_issues.map((g) => `<li class="warn">&#9679; ${esc(g)}</li>`).join('')}</ul>` : '';
  const who = allowed ? '' : `<div class="small muted">Completing this stage is done by: ${d.advance_roles.map(esc).join(' or ')}.</div>`;
  return `<div class="card"><div class="gap"><div><b>${esc(d.stage_label)}</b> <span class="muted">day ${num(d.days_in_stage, 0)} of ${d.planned_days} planned</span>${d.overdue ? ' <span class="badge critical">overdue</span>' : ''}</div><div class="spacer"></div>
    ${can('district', 'state', 'field', 'central') ? '<button class="btn sm danger" data-act="stall-project">Mark stalled</button>' : ''}
    <button class="btn primary" data-act="advance-project" ${d.can_advance ? '' : 'disabled'}>Complete stage &rarr;</button></div>${gate}${who}</div>`;
}

ACT['drawer-tab'] = async (el) => { D.tab = el.dataset.tab; renderDrawer(); };
ACT['show-on-map'] = () => { S.focusProject = D.id; closeDrawer(); if (location.hash === '#/map') route(); else location.hash = '#/map'; };

ACT['advance-project'] = async () => { await mutate(() => api(`/api/projects/${D.id}/advance`, { method: 'POST' }), 'Stage completed'); };
ACT['resume-project'] = async () => { await mutate(() => api(`/api/projects/${D.id}/resume`, { method: 'POST', json: { text: 'Resumed from the dashboard' } }), 'Project resumed'); };
ACT['stall-project'] = () => {
  const opts = Object.entries(S.meta.stall_reasons).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  openModal(`<h3>Mark project as stalled</h3><p class="muted small">Record why work stopped so the reason is on file for everyone.</p>
    <form data-form="stall" class="mt"><div class="field"><label>Reason</label><select name="reason">${opts}</select></div>
    <div class="field"><label>Note</label><textarea name="note" rows="3" maxlength="1000" placeholder="What happened, who is following up"></textarea></div>
    <div class="gap"><button class="btn danger" type="submit">Mark stalled</button><button class="btn" type="button" data-act="close-modal">Cancel</button></div></form>`);
};
FORMS.stall = async (f) => { const d = formData(f); closeModal(); await mutate(() => api(`/api/projects/${D.id}/stall`, { method: 'POST', json: d }), 'Project marked stalled'); };

// ---------------------------------------------------------------- tabs
async function loadTabData() {
  if (!D) return;
  if (D.tab === 'land' && !D.land) {
    const p = (x) => api(`/api/projects/${D.id}/${x}`);
    const [parcels, notifications, awards, compensation, families] = await Promise.all([p('parcels'), p('notifications'), p('awards'), p('compensation'), p('families')]);
    D.land = { parcels, notifications, awards, compensation, families };
  }
  if (D.tab === 'documents' && !D.docs) D.docs = await api(`/api/projects/${D.id}/documents`);
}

async function renderTab() {
  const box = $('#tab-body');
  if (!box) return;
  try { await loadTabData(); } catch (e) { box.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; return; }
  if (!D || !$('#tab-body')) return;
  const html = { overview: overviewTab, land: landTab, documents: documentsTab, timeline: timelineTab }[D.tab](D.d);
  $('#tab-body').innerHTML = html;
}

// ---- overview
function driversHtml(drivers) {
  if (!drivers || !drivers.length) return '<span class="muted">No drivers available.</span>';
  const max = Math.max(...drivers.map((x) => Math.abs(x.impact)), 0.001);
  return drivers.map((x) => `<div class="driver ${x.direction === 'increases' ? 'up' : 'down'}"><span>${esc(x.label)} <span class="muted small">(${esc(x.value)})</span></span>
    <div class="track"><i style="width:${(100 * Math.abs(x.impact)) / max}%"></i></div><span class="small ${x.direction === 'increases' ? 'fail' : 'ok'}">${x.impact > 0 ? '+' : ''}${num(x.impact, 2)}</span></div>`).join('');
}

function sparkline(hist) {
  if (!hist || hist.length < 2) return '<span class="muted small">Not enough history yet.</span>';
  const pts = [...hist].reverse().map((p) => ({ x: fdate(p.at), y: p.score }));
  return CHART.lineChart(pts, { compact: true, height: 36, valueLabel: 'risk score' });
}

function overviewTab(d) {
  const risk = d.status !== 'completed' ? d.risk : null;
  const docsHave = d.documents_required.filter((c) => d.documents_uploaded.includes(c)).length;
  const kv = (v, l) => `<div><b>${v}</b><span>${l}</span></div>`;
  const riskHtml = risk ? `<div class="card mt"><h4>Delay risk</h4>
      <div class="gap"><div class="score ${risk.category}"><b>${risk.score}%</b><span class="muted">${esc(risk.category)} probability that "${esc(d.stage_label)}" runs more than 25% over plan</span></div>
        <div class="spacer"></div><div>${sparkline(d.risk_history)}<div class="small muted right">score history</div></div></div>
      ${risk.already_delayed ? '<div class="banner warn mt">This stage is already past 125% of its plan, so it counts as delayed. The drivers below show what else is contributing.</div>' : ''}
      <h4 class="mt">Why the model says this (TreeSHAP, log-odds)</h4>${driversHtml(risk.drivers)}
      <h4 class="mt">Recommended actions</h4>
      ${risk.recommendations.length ? `<ul class="plain">${risk.recommendations.map((r) => `<li>${esc(r.action)} <span class="badge">${esc(r.owner)}</span></li>`).join('')}</ul>` : '<span class="muted">No specific action suggested.</span>'}
      <h4 class="mt">Outlook by stage (early-in-stage snapshot)</h4>
      ${risk.stage_profile.map((s) => `<div class="mini" style="grid-template-columns:190px 1fr 46px"><span>${esc(s.label)}${s.current ? ' (now)' : ''}</span>${bar(s.probability * 100, s.probability > 0.55 ? 'var(--high)' : s.probability > 0.3 ? 'var(--medium)' : 'var(--low)')}<span class="v">${Math.round(s.probability * 100)}%</span></div>`).join('')}
      <p class="small muted mt">Predictions come from a model trained on synthetic data until real outcomes are recorded; they are not recorded facts.</p></div>`
    : '<div class="card mt"><span class="muted">No risk score for a completed project.</span></div>';
  const edit = can('district', 'state', 'field') && d.status !== 'completed' ? `<form data-form="patch-inputs" class="mt"><h4>Update field inputs (re-scores risk)</h4><div class="row">
      <div><label>Open legal disputes</label><input type="number" min="0" name="legal_disputes" value="${d.legal_disputes}"></div>
      <div><label>Approvals pending</label><input type="number" min="0" name="approvals_pending" value="${d.approvals_pending}"></div>
      <div><label>Avg response (days)</label><input type="number" min="0" step="0.5" name="avg_response_days" value="${d.avg_response_days}"></div>
      <div style="align-self:end"><button class="btn" type="submit">Save</button></div></div></form>` : '';
  return `<div class="card"><div class="kv">
      ${kv(num(d.proposed_area_ha) + ' ha', 'Proposed area')}${kv(cr(d.estimated_cost_cr), 'Estimated cost')}${kv(pct(d.progress_pct), 'Lifecycle progress')}
      ${kv(d.legal_disputes, 'Open legal disputes')}${kv(d.approvals_pending, 'Approvals pending')}${kv(num(d.avg_response_days) + ' d', 'Avg stakeholder response')}
      ${kv(d.counts.parcels, 'Parcels')}${kv(`${d.families} / ${d.displaced}`, 'Families / displaced')}${kv(pct(d.comp_pct), 'Compensation paid')}
      ${kv(pct(d.rr_progress_pct), 'R&R progress')}${kv(`${docsHave} / ${d.documents_required.length}`, 'Required documents')}${kv(fdate(d.planned_completion), 'Planned completion')}</div>${edit}</div>
    <div class="card mt"><div class="gap"><h4 style="margin:0">Why is this project stuck or late?</h4><div class="spacer"></div><button class="btn sm" data-act="load-why">Explain</button></div><div id="why-out" class="mt">${D.why ? whyHtml(D.why) : '<span class="muted small">Answers only from recorded facts, rule checks and model drivers, and keeps them separate.</span>'}</div></div>
    ${riskHtml}`;
}
FORMS['patch-inputs'] = async (f) => {
  const d = formData(f);
  const body = { legal_disputes: Number(d.legal_disputes), approvals_pending: Number(d.approvals_pending), avg_response_days: Number(d.avg_response_days) };
  await mutate(() => api(`/api/projects/${D.id}`, { method: 'PATCH', json: body }), 'Inputs updated and risk rescored');
};

function whyHtml(w) {
  const list = (items, cls = '') => (items.length ? `<ul class="plain small ${cls}">${items.map((i) => `<li>${esc(typeof i === 'string' ? i : i.text)}</li>`).join('')}</ul>` : '<div class="small muted">None.</div>');
  return `<p><b>${esc(w.summary)}</b></p>
    <h4>Recorded facts</h4>${list(w.recorded_facts)}
    <h4 class="mt">Rule findings</h4>${list(w.rule_findings)}
    <h4 class="mt">Model inference (prediction, not a record)</h4>${list(w.model_inference)}
    ${w.recommendations.length ? `<h4 class="mt">Suggested next steps</h4>${list(w.recommendations.map((r) => r.action))}` : ''}`;
}
ACT['load-why'] = async () => { const w = await attempt(() => api(`/api/projects/${D.id}/why`)); if (w) { D.why = w; $('#why-out').innerHTML = whyHtml(w); } };

// ---- land and payments
const parcelRow = (p, d) => `<tr><td><b>${esc(p.survey_no)}</b><div class="small muted">${esc(p.village)}</div></td>
  <td>${p.owner_name == null ? '<span class="muted small">hidden for your role</span>' : esc(p.owner_name)}</td><td class="num">${num(p.area_ha, 2)}</td><td>${esc(p.land_type)}</td>
  <td><span class="dot" style="background:${STATUS_COLORS[p.status]}"></span>${esc(p.status)}${p.disputed ? ' <span class="badge critical">disputed</span>' : ''}</td>
  <td class="nowrap"><button class="btn sm" data-act="verify-parcel" data-id="${p.id}">Verify</button>
    ${can('field', 'district') ? `<button class="btn sm" data-act="toggle-dispute" data-id="${p.id}" data-val="${!p.disputed}">${p.disputed ? 'Clear dispute' : 'Flag dispute'}</button>` : ''}
    ${can('field', 'district') && ['possession', 'rr'].includes(d.stage) && p.status === 'compensated' ? `<button class="btn sm primary" data-act="possess-parcel" data-id="${p.id}">Mark possessed</button>` : ''}</td></tr>
  <tr id="vr-${p.id}" class="hidden"><td colspan="6"></td></tr>`;

function landTab(d) {
  const L = D.land;
  const si = stageIdx(d.stage), active = d.status === 'active';
  const comp = L.compensation, assessed = comp.reduce((a, c) => a + c.assessed_cr, 0), paid = comp.reduce((a, c) => a + c.disbursed_cr, 0);
  const parcelForm = active && si < stageIdx('award') && can('agency', 'state', 'district', 'field') ? `<form data-form="add-parcel" class="mt"><h4>Add parcel</h4><div class="row">
      <div><label>Survey no.</label><input name="survey_no" required></div><div><label>Village</label><input name="village"></div><div><label>Owner</label><input name="owner_name"></div>
      <div><label>Area (ha)</label><input name="area_ha" type="number" step="0.01" min="0.01" required></div>
      <div><label>Lat</label><input name="lat" type="number" step="any" id="parcel-lat"></div><div><label>Lon</label><input name="lon" type="number" step="any" id="parcel-lon"></div>
      <div style="align-self:end"><button class="btn" type="button" data-act="use-gps" title="Fill lat/lon from this device's current location">&#128205; Use my location</button></div>
      <div style="align-self:end"><button class="btn" type="button" data-act="toggle-draw">&#9998; Draw boundary on map</button></div>
      <div style="align-self:end"><button class="btn" type="submit">Add</button></div></div>
      <div id="gps-status" class="small muted mt"></div>
      <div id="draw-map-box" class="hidden mt"></div></form>` : '';
  const notifForm = active && si >= stageIdx('notification') && can('district', 'state') ? `<form data-form="add-notification" class="mt"><div class="row">
      <div><label>Reference no.</label><input name="ref_no" required></div><div><label>Issued on</label><input name="issued_on" type="date" required></div>
      <div><label>Area (ha)</label><input name="area_ha" type="number" step="0.01" min="0"></div><div style="align-self:end"><button class="btn" type="submit">Record notification</button></div></div></form>` : '';
  const awardForm = active && d.stage === 'award' && can('district') ? `<form data-form="add-award" class="mt"><div class="row">
      <div><label>Award no.</label><input name="award_no" required></div><div><label>Declared on</label><input name="declared_on" type="date" required></div>
      <div><label>Area (ha)</label><input name="total_area_ha" type="number" step="0.01" min="0"></div><div><label>Amount (Rs Cr)</label><input name="total_amount_cr" type="number" step="0.01" min="0"></div>
      <div style="align-self:end"><button class="btn" type="submit">Declare award</button></div></div></form>` : '';
  const compForm = active && d.stage === 'compensation' && can('district') ? `<div class="gap mt">
      <form data-form="assess" class="gap"><div><label>Rate (Rs Cr per ha)</label><input name="rate_cr_per_ha" type="number" step="0.01" min="0.01" required style="width:130px"></div><button class="btn" type="submit" style="align-self:end">Assess unassessed parcels</button></form>
      <form data-form="disburse" class="gap"><div><label>Pay share of balance</label><select name="fraction" style="width:110px"><option value="0.25">25%</option><option value="0.5">50%</option><option value="1" selected>100%</option></select></div><button class="btn primary" type="submit" style="align-self:end">Disburse</button></form></div>` : '';
  const famForm = active && can('district', 'field') ? `<form data-form="add-family" class="mt"><div class="row">
      <div><label>Head of family</label><input name="head_name" required></div><div><label>Members</label><input name="members" type="number" min="1" value="4"></div>
      <div><label>Displaced?</label><select name="displaced"><option value="false">No</option><option value="true">Yes</option></select></div><div style="align-self:end"><button class="btn" type="submit">Add family</button></div></div></form>` : '';
  const rrSel = (f) => can('district', 'field') && active
    ? `<select data-change="family-status" data-id="${f.id}" style="width:150px">${['pending', 'package_approved', 'allotted', 'resettled'].map((s) => `<option value="${s}" ${s === f.rr_status ? 'selected' : ''}>${title(s)}</option>`).join('')}</select>` : esc(title(f.rr_status));
  return `<div class="card"><h4>Parcels (${L.parcels.length})</h4><div class="table-wrap"><table><thead><tr><th>Survey / village</th><th>Owner</th><th class="num">Area ha</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>${L.parcels.map((p) => parcelRow(p, d)).join('') || '<tr><td colspan="6" class="muted">No parcels yet.</td></tr>'}</tbody></table></div>${parcelForm}</div>
    <div class="grid g2 mt"><div class="card"><h4>Land notifications</h4>${L.notifications.length ? `<ul class="plain small">${L.notifications.map((n) => `<li><b>${esc(n.ref_no)}</b> (${esc(n.kind)}) ${fdate(n.issued_on)}, ${num(n.area_ha)} ha</li>`).join('')}</ul>` : '<span class="muted small">None recorded.</span>'}${notifForm}</div>
      <div class="card"><h4>Awards</h4>${L.awards.length ? `<ul class="plain small">${L.awards.map((a) => `<li><b>${esc(a.award_no)}</b> ${fdate(a.declared_on)}, ${num(a.total_area_ha)} ha, ${cr(a.total_amount_cr)}</li>`).join('')}</ul>` : '<span class="muted small">None declared.</span>'}${awardForm}</div></div>
    <div class="card mt"><h4>Compensation</h4><div class="kpi">${assessed ? pct((100 * paid) / assessed) : '-'}<small>${cr(paid)} of ${cr(assessed)} assessed; 95% is needed to leave this stage</small></div>${bar(assessed ? (100 * paid) / assessed : 0, 'var(--compensated)')}
      ${comp.length ? `<div class="small muted mt">${comp.length} parcel record(s), ${comp.filter((c) => c.disbursed_cr >= c.assessed_cr - 1e-6).length} fully paid</div>` : ''}${compForm}</div>
    <div class="card mt"><h4>Affected families and R&amp;R (${L.families.length})</h4><div class="table-wrap"><table><thead><tr><th>Head of family</th><th class="num">Members</th><th>Displaced</th><th>R&amp;R status</th></tr></thead><tbody>
      ${L.families.map((f) => `<tr><td>${esc(f.head_name)}</td><td class="num">${f.members}</td><td>${f.displaced ? 'Yes' : 'No'}</td><td>${f.displaced ? rrSel(f) : '<span class="muted">not applicable</span>'}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No families recorded.</td></tr>'}</tbody></table></div>${famForm}</div>`;
}

ACT['use-gps'] = (el) => {
  const status = $('#gps-status');
  if (!navigator.geolocation) { status.textContent = 'This device/browser does not support location capture.'; return; }
  status.textContent = 'Getting current location...';
  el.disabled = true;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      $('#parcel-lat').value = pos.coords.latitude.toFixed(6);
      $('#parcel-lon').value = pos.coords.longitude.toFixed(6);
      status.textContent = `Location captured (accuracy ±${Math.round(pos.coords.accuracy)} m). The parcel polygon is generated around this point.`;
      el.disabled = false;
    },
    (err) => { status.textContent = 'Could not get location: ' + err.message + '. Enter latitude/longitude manually.'; el.disabled = false; },
    { enableHighAccuracy: true, timeout: 10000 },
  );
};

// ---------------------------------------------------------------- draw a parcel boundary on the map
// No plugin: plain Leaflet click-to-place-vertex, click-the-start-point-to-close. On close, computes
// area with the same equirectangular-shoelace formula as geo.py's ring_area_ha, so the preview matches
// what the server will store.
let _drawMap = null, _drawLayer = null, _drawPts = [];

function destroyDrawMap() {
  if (_drawMap) { _drawMap.off(); _drawMap.remove(); _drawMap = null; }
  _drawLayer = null; _drawPts = [];
}

const STATE_CENTER = {
  'Andhra Pradesh': [15.9, 79.7], 'Telangana': [17.9, 79.0], 'Karnataka': [15.3, 75.7], 'Maharashtra': [19.5, 76.0],
  'Tamil Nadu': [11.1, 78.6], 'Kerala': [10.5, 76.5], 'Odisha': [20.5, 84.7], 'West Bengal': [23.0, 87.5],
  'Uttar Pradesh': [27.0, 80.5], 'Rajasthan': [26.8, 73.8], 'Madhya Pradesh': [23.5, 78.0], 'Bihar': [25.7, 85.9], 'Gujarat': [22.6, 71.6],
};

function ringAreaHa(ring) {
  if (ring.length < 4) return 0;
  const lat0 = ring.reduce((a, p) => a + p[1], 0) / ring.length;
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110540;
  const pts = ring.map((p) => [p[0] * kx, p[1] * ky]);
  let area = 0;
  for (let i = 0; i < pts.length - 1; i++) area += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1];
  return Math.abs(area) / 2 / 10000;
}

ACT['toggle-draw'] = async (el) => {
  const box = $('#draw-map-box');
  if (!box) return;
  if (_drawMap) {
    destroyDrawMap();
    box.classList.add('hidden');
    box.innerHTML = '';
    el.innerHTML = '&#9998; Draw boundary on map';
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = `<div id="draw-map" style="height:260px;border-radius:8px;overflow:hidden;border:1px solid var(--line)"></div>
    <div class="gap mt small"><span id="draw-status" class="muted">Click the map to place corners (need 3+); click the first (green) point again to close the shape.</span><div class="spacer"></div>
      <button class="btn sm" type="button" data-act="draw-undo">Undo point</button>
      <button class="btn sm" type="button" data-act="draw-clear">Clear</button></div>`;
  el.innerHTML = '&#10007; Hide map';
  D.drawGeometry = null;
  await startDrawMap();
};

async function startDrawMap() {
  let center = [20.5, 78.9], zoom = 5;
  try {
    const gj = await api(`/api/gis/parcels?project_id=${D.id}`);
    const pts = [];
    (gj.features || []).forEach((f) => (f.geometry.coordinates[0] || []).forEach((c) => pts.push([c[1], c[0]])));
    if (pts.length) { center = [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length]; zoom = 16; }
    else if (STATE_CENTER[D.d.state]) { center = STATE_CENTER[D.d.state]; zoom = 9; }
  } catch (e) { /* fall back to the default view */ }
  if (!$('#draw-map')) return; // drawer closed/changed while the GIS lookup was in flight
  _drawMap = L.map('draw-map').setView(center, zoom);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(_drawMap);
  _drawLayer = L.layerGroup().addTo(_drawMap);
  _drawPts = [];
  _drawMap.on('click', onDrawClick);
}

function onDrawClick(e) {
  if (_drawPts.length >= 3) {
    const d = _drawMap.distance(e.latlng, L.latLng(_drawPts[0][0], _drawPts[0][1]));
    if (d < 25) { finishDraw(); return; }
  }
  _drawPts.push([e.latlng.lat, e.latlng.lng]);
  redrawDraw();
}

function redrawDraw() {
  _drawLayer.clearLayers();
  _drawPts.forEach((p, i) => L.circleMarker(p, { radius: 5, color: i === 0 ? '#0ca30c' : '#2a78d6', weight: 2, fillOpacity: 1 }).addTo(_drawLayer));
  if (_drawPts.length > 1) L.polyline(_drawPts, { color: '#2a78d6', weight: 2, dashArray: '4 4' }).addTo(_drawLayer);
  if (_drawPts.length > 2) L.polygon(_drawPts, { color: '#2a78d6', fillOpacity: 0.12 }).addTo(_drawLayer);
  const status = $('#draw-status');
  if (status) status.textContent = _drawPts.length < 3 ? `${_drawPts.length} point(s) placed — need at least 3.` : `${_drawPts.length} points — click the green start point to close the shape.`;
}

ACT['draw-undo'] = () => { if (_drawPts.length) { _drawPts.pop(); redrawDraw(); } };
ACT['draw-clear'] = () => { _drawPts = []; redrawDraw(); };

function finishDraw() {
  const ring = _drawPts.map((p) => [p[1], p[0]]);
  ring.push(ring[0]);
  const areaHa = ringAreaHa(ring);
  D.drawGeometry = { type: 'Polygon', coordinates: [ring] };
  const areaInput = document.querySelector('input[name="area_ha"]');
  if (areaInput) areaInput.value = areaHa.toFixed(2);
  const status = $('#draw-status');
  if (status) status.textContent = `Boundary closed: ${areaHa.toFixed(2)} ha from ${_drawPts.length} points. Area field filled in — adjust it if you need to, then Add.`;
  if (_drawMap) _drawMap.off('click', onDrawClick);
}

const post = (path, json) => api(path, { method: 'POST', json });
FORMS['add-parcel'] = async (f) => {
  const d = formData(f);
  const body = { survey_no: d.survey_no, village: d.village, owner_name: d.owner_name, area_ha: Number(d.area_ha) };
  if (D.drawGeometry) body.geometry = D.drawGeometry;
  else if (d.lat && d.lon) { body.lat = Number(d.lat); body.lon = Number(d.lon); }
  D.drawGeometry = null;
  await mutate(() => post(`/api/projects/${D.id}/parcels`, body), 'Parcel added');
};
FORMS['add-notification'] = async (f) => { const d = formData(f); await mutate(() => post(`/api/projects/${D.id}/notifications`, { kind: 'preliminary', ref_no: d.ref_no, issued_on: d.issued_on, area_ha: Number(d.area_ha || 0) }), 'Notification recorded'); };
FORMS['add-award'] = async (f) => { const d = formData(f); await mutate(() => post(`/api/projects/${D.id}/awards`, { award_no: d.award_no, declared_on: d.declared_on, total_area_ha: Number(d.total_area_ha || 0), total_amount_cr: Number(d.total_amount_cr || 0) }), 'Award declared'); };
FORMS.assess = async (f) => { const d = formData(f); const r = await mutate(() => post(`/api/projects/${D.id}/compensation/assess`, { rate_cr_per_ha: Number(d.rate_cr_per_ha) })); if (r) toast(`Assessed ${r.created} parcel(s): ${cr(r.assessed_cr)}`, 'ok'); };
FORMS.disburse = async (f) => { const d = formData(f); const r = await mutate(() => post(`/api/projects/${D.id}/compensation/disburse-bulk`, { fraction: Number(d.fraction) })); if (r) toast(`Disbursed ${cr(r.amount_cr)} to ${r.records_paid} record(s)`, 'ok'); };
FORMS['add-family'] = async (f) => { const d = formData(f); await mutate(() => post(`/api/projects/${D.id}/families`, { head_name: d.head_name, members: Number(d.members || 4), displaced: d.displaced === 'true' }), 'Family added'); };
ACT['toggle-dispute'] = (el) => mutate(() => api(`/api/parcels/${el.dataset.id}`, { method: 'PATCH', json: { disputed: el.dataset.val === 'true' } }), 'Parcel updated');
ACT['possess-parcel'] = (el) => mutate(() => api(`/api/parcels/${el.dataset.id}`, { method: 'PATCH', json: { status: 'possessed' } }), 'Possession recorded');
document.addEventListener('change', (e) => {
  const el = e.target.closest('[data-change="family-status"]');
  if (el) mutate(() => api(`/api/families/${el.dataset.id}`, { method: 'PATCH', json: { rr_status: el.value } }), 'R&R status updated');
});

async function renderVerification(parcelId, into) {
  const r = await attempt(() => api(`/api/parcels/${parcelId}/verify`, { method: 'POST' }));
  if (!r) return null;
  const icon = { ok: '<span class="ok">&#10003;</span>', fail: '<span class="fail">&#10007;</span>', warn: '<span class="warn">!</span>' };
  into.innerHTML = `<div class="banner ${r.verified ? 'ok' : 'bad'}"><b>${r.verified ? 'Consistent with records' : 'Mismatch found'}</b> <span class="small muted">(${r.sources.map(esc).join(', ')}; mock services)</span>
    <ul class="plain checks">${r.checks.map((c) => `<li>${icon[c.status]} <b>${esc(c.name)}</b> <span class="muted">${esc(c.detail)}</span></li>`).join('')}</ul></div>`;
  return r;
}
ACT['verify-parcel'] = async (el) => {
  const row = $(`#vr-${el.dataset.id}`);
  row.classList.remove('hidden');
  await renderVerification(el.dataset.id, row.firstElementChild);
};

// ---- documents
function documentsTab(d) {
  const docs = D.docs;
  const cats = [...d.documents_required, ...DOC_CATEGORIES_EXTRA];
  const canUpload = can('agency', 'state', 'district', 'field');
  return `<div class="card"><h4>Required for scrutiny</h4><div class="gap">${d.documents_required.map((c) => `<span class="badge ${d.documents_uploaded.includes(c) ? 'low' : 'medium'}">${d.documents_uploaded.includes(c) ? '&#10003;' : '&#9675;'} ${esc(title(c))}</span>`).join('')}</div></div>
    <div class="card mt"><h4>Repository (${docs.length})</h4>
      <div class="banner info small">&#128269; Every PDF or image uploaded here is read automatically (OCR). Open <b>OCR result</b> under a version to review the fields, or use the <a href="#/digitize">Digitize documents</a> page.</div>
      ${docs.map((x) => `<div class="mb"><b>${esc(x.name)}</b> <span class="badge">${esc(title(x.category))}</span> <span class="muted small">v${x.latest_version}</span>
        <ul class="plain small">${[...x.versions].reverse().map((v) => `<li>v${v.version} &middot; ${fdate(v.uploaded_at)} by ${esc(v.uploaded_by)} &middot; ${fsize(v.size)} <span class="muted" title="SHA-256 ${esc(v.sha256)}">sha ${esc(v.sha256.slice(0, 10))}</span>
          <button class="btn sm" data-act="dl-version" data-id="${v.id}" data-name="${esc(v.filename)}">Download</button>
          ${v.ocr ? `<button class="btn sm" data-act="view-extraction" data-id="${v.id}">&#128269; OCR result</button>
            ${v.ocr.status === 'done' ? `<span class="badge ${v.ocr.applied ? 'low' : v.ocr.fields_found ? 'medium' : ''}">${v.ocr.applied ? 'Applied' : v.ocr.fields_found + ' fields read'}</span>` : `<span class="badge">${esc(EXTRACTION_STATUS_LABEL[v.ocr.status] || v.ocr.status)}</span>`}` : ''}
          ${v.note ? ` <span class="muted">${esc(v.note)}</span>` : ''}
          <div id="ext-${v.id}" class="hidden mt"></div></li>`).join('')}</ul></div>`).join('') || '<span class="muted small">No documents uploaded.</span>'}
      ${canUpload && d.status !== 'completed' ? `<form data-form="upload-doc" class="mt" id="upload-doc-form"><h4>Upload document or new version</h4>
        <div class="dropzone" id="doc-dropzone"><div class="row">
        <div><label>File</label><div class="gap"><input type="file" name="file" id="doc-file" required style="flex:1"><button class="btn sm" type="button" data-act="capture-photo" title="Take a photo with this device's camera">&#128247;</button></div>
          <input type="file" id="doc-camera" accept="image/*" capture="environment" class="hidden">
          <div class="small muted mt">or drag a file here &middot; allowed: ${S.meta.upload.allowed_extensions.join(', ')} &middot; up to ${S.meta.upload.max_mb} MB</div></div>
        <div><label>Category</label><select name="category">${cats.map((c) => `<option value="${c}">${esc(title(c))}</option>`).join('')}</select></div>
        <div><label>Name (same name = new version)</label><input name="name" placeholder="e.g. Land plan"></div>
        <div><label>Note</label><input name="note"></div><div style="align-self:end"><button class="btn primary" type="submit">Upload</button></div></div>
        <div id="doc-preview" class="doc-preview hidden"></div>
        <div id="doc-progress" class="doc-progress hidden"><div class="track"><i></i></div><span class="small muted"></span></div>
        </div></form>` : ''}</div>`;
}
ACT['capture-photo'] = () => $('#doc-camera').click();

function docFileChosen(file, previewSel = '#doc-preview') {
  const prev = $(previewSel);
  if (!prev) return;
  if (!file) { prev.classList.add('hidden'); prev.innerHTML = ''; return; }
  const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
  const ok = S.meta.upload.allowed_extensions.includes(ext);
  prev.classList.remove('hidden');
  const sizeOk = file.size <= S.meta.upload.max_mb * 1024 * 1024;
  const warn = !ok ? `<span class="fail">File type ${esc(ext)} is not allowed here (allowed: ${S.meta.upload.allowed_extensions.join(', ')}).</span>`
    : !sizeOk ? `<span class="fail">File is larger than ${S.meta.upload.max_mb} MB.</span>` : '';
  if (file.type.startsWith('image/')) {
    const url = URL.createObjectURL(file);
    prev.innerHTML = `<img src="${url}" alt="preview"><div><b>${esc(file.name)}</b><div class="small muted">${fsize(file.size)}</div>${warn}</div>`;
  } else {
    prev.innerHTML = `<div class="doc-preview-icon">&#128196;</div><div><b>${esc(file.name)}</b><div class="small muted">${fsize(file.size)}</div>${warn}</div>`;
  }
}
document.addEventListener('change', (e) => {
  if (e.target.id === 'doc-camera' && e.target.files.length) {
    const dt = new DataTransfer();
    dt.items.add(e.target.files[0]);
    $('#doc-file').files = dt.files;
    toast('Photo captured: ' + e.target.files[0].name, 'ok');
    docFileChosen(e.target.files[0]);
  }
  if (e.target.id === 'doc-file') docFileChosen(e.target.files[0]);
});
// Drag-and-drop onto the upload card drops straight into the file input (works for one file, same as the picker).
document.addEventListener('dragover', (e) => { if (e.target.closest && e.target.closest('#doc-dropzone')) { e.preventDefault(); $('#doc-dropzone').classList.add('drag'); } });
document.addEventListener('dragleave', (e) => { if (e.target.closest && e.target.closest('#doc-dropzone') && !e.relatedTarget) $('#doc-dropzone').classList.remove('drag'); });
document.addEventListener('drop', (e) => {
  const zone = e.target.closest && e.target.closest('#doc-dropzone');
  if (!zone) return;
  e.preventDefault();
  zone.classList.remove('drag');
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!file) return;
  const dt = new DataTransfer();
  dt.items.add(file);
  $('#doc-file').files = dt.files;
  docFileChosen(file);
});

// Plain fetch() has no upload-progress event, so this one form uses XMLHttpRequest directly instead of api().
function uploadWithProgress(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    if (S.token) xhr.setRequestHeader('Authorization', 'Bearer ' + S.token);
    xhr.upload.addEventListener('progress', (e) => { if (e.lengthComputable) onProgress(Math.round((100 * e.loaded) / e.total)); });
    xhr.onload = () => {
      let body; try { body = JSON.parse(xhr.responseText); } catch (_) { body = null; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, (body && body.detail) || xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'));
    xhr.send(formData);
  });
}
FORMS['upload-doc'] = async (f) => {
  const fd = new FormData(f);  // the camera input has no `name`, so a captured photo (copied into #doc-file) is the only file present
  if (!fd.get('name')) fd.delete('name');
  const bar = $('#doc-progress');
  bar.classList.remove('hidden');
  const fill = bar.querySelector('i'), label = bar.querySelector('span');
  fill.style.width = '0%'; label.textContent = 'Uploading...';
  try {
    const body = await uploadWithProgress(`/api/projects/${D.id}/documents`, fd, (pct) => { fill.style.width = pct + '%'; label.textContent = pct < 100 ? pct + '%' : 'Reading document (OCR)...'; });
    label.textContent = 'Done';
    D.land = null; D.docs = null; D.why = null;
    await reloadDetail(async () => { await loadTabData(); });
    const ext = body && body.latest_extraction;
    toast(ext && ext.status === 'done' ? `Document stored and digitized: ${ext.fields_found} of ${ext.fields_total} fields read` : 'Document stored', 'ok');
    // Show what OCR read straight away, instead of leaving it behind a button.
    if (ext && ext.status !== 'skipped') await ACT['view-extraction']({ dataset: { id: String(ext.document_version_id), force: '1' } });
  } catch (e) { bar.classList.add('hidden'); fail(e); }
};
ACT['dl-version'] = (el) => attempt(() => downloadBlob(`/api/documents/versions/${el.dataset.id}/download`, el.dataset.name));

// ---- OCR / document digitization
const EXTRACTION_FIELD_LABELS = {
  survey_no: 'Survey No.', owner_name: 'Owner name', village: 'Village', area_ha: 'Area (ha)',
  land_type: 'Land type', compensation_amount: 'Compensation (Rs.)', ref_no: 'Reference No.', date: 'Date',
};
const EXTRACTION_STATUS_LABEL = { done: 'Digitized', pending: 'Processing', skipped: 'Not applicable', failed: 'Could not read' };
const PARCEL_LAND_TYPES = ['agricultural', 'residential', 'commercial', 'barren'];
const OCR_ACCEPT = '.pdf,.png,.jpg,.jpeg,.tif,.tiff';

function fmtExtractedValue(k, v) {
  if (k === 'compensation_amount') return '₹' + Number(v).toLocaleString('en-IN');
  if (k === 'area_ha') return `${num(v, 4)} ha`;
  return v;
}

// Shared by the project drawer's Documents tab and the Digitize page.
function extractionPanelHtml(ext, versionId, { bare = false } = {}) {
  const statusBadgeClass = { done: 'low', pending: 'medium', skipped: '', failed: 'critical' }[ext.status] || '';
  const found = ext.fields_found ?? Object.keys(ext.fields || {}).length;
  const total = ext.fields_total || Object.keys(EXTRACTION_FIELD_LABELS).length;
  const head = `<div class="gap"><span class="badge ${statusBadgeClass}">${esc(EXTRACTION_STATUS_LABEL[ext.status] || ext.status)}</span>
    ${ext.status === 'done' ? `<span class="small"><b>${found} of ${total}</b> fields found</span>
      <span class="muted small">${esc(ext.engine)}${ext.ocr_confidence != null ? ' &middot; ' + Math.round(ext.ocr_confidence) + '% OCR confidence' : ''}</span>` : ''}
    ${ext.applied ? `<span class="badge low">&#10003; Applied to parcel by ${esc(ext.applied_by)}</span>` : ''}</div>`;
  const wrap = (html) => (bare ? html : `<div class="card">${html}</div>`);

  if (ext.status !== 'done') {
    return wrap(`${head}${ext.note ? `<div class="small muted mt">${esc(ext.note)}</div>` : ''}`);
  }

  const warnings = (ext.warnings || []).length
    ? `<div class="banner warn small mt">${ext.warnings.map((w) => `<div>&#9888; ${esc(w)}</div>`).join('')}</div>` : '';
  const fieldsTable = `<table class="ocr-fields mt"><tbody>${Object.keys(EXTRACTION_FIELD_LABELS).map((k) => {
    const f = ext.fields[k];
    if (!f) return `<tr class="missing"><td>${EXTRACTION_FIELD_LABELS[k]}</td><td colspan="2">Not found</td></tr>`;
    const high = f.confidence === 'high';
    return `<tr><td>${EXTRACTION_FIELD_LABELS[k]}</td><td><b>${esc(fmtExtractedValue(k, f.value))}</b>
      <div class="ocr-src" title="Text this was read from">“${esc(f.matched_text)}”</div></td>
      <td class="right"><span class="badge ${high ? 'low' : 'medium'}">${high ? 'high confidence' : 'needs review'}</span></td></tr>`;
  }).join('')}</tbody></table>`;

  const fv = (k) => esc(ext.fields[k] ? ext.fields[k].value : '');
  const canApply = can('district', 'state', 'central') && !ext.applied && found;
  const applyForm = canApply ? `<form data-form="apply-extraction" data-version="${versionId}" class="ocr-apply mt">
      <h4>Review and apply to parcel</h4>
      <div class="small muted mb">Correct anything OCR got wrong, then confirm. The parcel is matched by survey number on this document's project.</div>
      <div class="row">
        <div class="field"><label>Survey No. (must match a parcel)</label><input name="survey_no" value="${fv('survey_no')}" required></div>
        <div class="field"><label>Owner name</label><input name="owner_name" value="${fv('owner_name')}"></div>
        <div class="field"><label>Village</label><input name="village" value="${fv('village')}"></div>
        <div class="field"><label>Area (ha)</label><input name="area_ha" type="number" step="0.0001" min="0" value="${fv('area_ha')}"></div>
        <div class="field"><label>Land type</label><select name="land_type"><option value="">(leave unchanged)</option>
          ${PARCEL_LAND_TYPES.map((t) => `<option value="${t}" ${ext.fields.land_type && ext.fields.land_type.value === t ? 'selected' : ''}>${title(t)}</option>`).join('')}</select></div>
      </div>
      <button class="btn primary" type="submit">&#10003; Apply to parcel</button>
    </form>`
    : (!ext.applied && found && !can('district', 'state', 'central')
      ? '<div class="small muted mt">A district, state or central officer reviews these fields and applies them to the parcel.</div>' : '');

  return wrap(`${head}${warnings}${fieldsTable}${applyForm}
    <details class="mt"><summary class="small muted">Raw OCR text</summary><pre class="small ocr-raw">${esc(ext.text)}</pre></details>`);
}

ACT['view-extraction'] = async (el) => {
  const vid = el.dataset.id;
  const box = document.querySelector(`#ext-${vid}`);
  if (!box) return;
  if (!box.classList.contains('hidden') && !el.dataset.force) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  box.classList.remove('hidden');
  box.innerHTML = '<div class="small muted">Loading digitized text...</div>';
  try {
    const ext = await api(`/api/documents/versions/${vid}/extraction`);
    box.innerHTML = extractionPanelHtml(ext, vid);
  } catch (e) {
    box.innerHTML = `<div class="banner bad small">${esc(e.message)}</div>`;
  }
};

FORMS['apply-extraction'] = async (f) => {
  const vid = f.dataset.version;
  const body = formData(f);
  Object.keys(body).forEach((k) => { if (!body[k]) delete body[k]; });
  const out = await attempt(() => api(`/api/documents/versions/${vid}/apply-extraction`, { method: 'POST', json: { fields: body } }), 'Applied to parcel');
  if (out === undefined) return;
  if (f.closest('#ocr-result')) { await openOcrResult(vid); await refreshOcrRecent(); return; }
  D.land = null; D.docs = null;
  await reloadDetail(async () => { await loadTabData(); });
};

// ---- Digitize page (main navigation): upload a scan -> fields read -> officer reviews and applies
const OCR = { projects: [], recent: [], active: null };

async function viewDigitize(main) {
  const canUpload = can('agency', 'state', 'district', 'field');
  const [projects, recent] = await Promise.all([
    canUpload ? api('/api/projects?limit=1000&sort=name') : Promise.resolve({ items: [] }),
    api('/api/documents/extractions?limit=50'),
  ]);
  OCR.projects = projects.items.filter((p) => p.status !== 'completed');
  OCR.recent = recent.items;
  OCR.active = null;

  const uploadCard = canUpload ? `<form data-form="ocr-upload" id="ocr-upload-form">
      <div class="field"><label for="ocr-project">Project</label>
        <select name="project" id="ocr-project" required>${OCR.projects.map((p) => `<option value="${p.id}">${esc(p.code)} &middot; ${esc(p.name)}</option>`).join('')}</select></div>
      <div class="row">
        <div class="field"><label for="ocr-category">Document type</label>
          <select name="category" id="ocr-category">${DOC_CATEGORIES_EXTRA.map((c) => `<option value="${c}" ${c === 'award_copy' ? 'selected' : ''}>${esc(title(c))}</option>`).join('')}</select></div>
        <div class="field"><label for="ocr-name">Name (optional)</label><input name="name" id="ocr-name" placeholder="e.g. Award copy, Sy. No. 245/B"></div>
      </div>
      <label class="ocr-drop" id="ocr-dropzone" for="ocr-file">
        <input type="file" name="file" id="ocr-file" accept="${OCR_ACCEPT}" required>
        <span class="ocr-drop-icon" aria-hidden="true">&#128196;</span>
        <span><b>Choose a scan</b> or drag it here</span>
        <span class="small muted">PDF, JPG, PNG or TIFF &middot; a phone photo works &middot; up to ${S.meta.upload.max_mb} MB</span>
      </label>
      <div id="ocr-preview" class="doc-preview hidden"></div>
      <div id="ocr-progress" class="doc-progress hidden"><div class="track"><i></i></div><span class="small muted"></span></div>
      <div class="gap mt">
        <button class="btn primary" type="submit">&#128269; Digitize document</button>
        <button class="btn" type="button" data-act="ocr-sample" title="Loads a synthetic award notice for a real parcel on the selected project">Try a sample scan</button>
      </div>
    </form>`
    : `<p class="small muted">Your role reviews digitized documents. Scans are uploaded by field, district, state and agency users,
       from here or from a project's Documents tab.</p>`;

  main.innerHTML = `
    <div class="gap mb"><h2>Document digitization</h2><span class="badge model">OCR</span>
      <span class="muted small">Turn scanned land records into structured data</span></div>
    <div class="ocr-steps mb">
      <div class="ocr-step"><b>1</b><div><strong>Upload a scan</strong><span>Award copy, notification, Record of Rights or survey report</span></div></div>
      <div class="ocr-step"><b>2</b><div><strong>Fields are read automatically</strong><span>Survey no., village, owner, area, land type, compensation, reference no., date</span></div></div>
      <div class="ocr-step"><b>3</b><div><strong>An officer reviews and applies</strong><span>Nothing reaches a parcel without sign-off; every step is in the audit log</span></div></div>
    </div>
    <div class="grid g2">
      <div class="card"><h4>Upload and digitize</h4>${uploadCard}</div>
      <div class="card"><h4>Result</h4><div id="ocr-result"><div class="ocr-empty">
        <span aria-hidden="true">&#128269;</span>${canUpload ? 'Upload a scan, or pick one from the list below, to see what was read.' : 'Pick a document from the list below to review what was read.'}</div></div></div>
    </div>
    <div class="card mt"><div class="gap"><h4 style="margin:0">Recently digitized</h4><span class="muted small" id="ocr-recent-count"></span></div>
      <div class="table-wrap mt" id="ocr-recent"></div></div>`;
  renderOcrRecent();
}
VIEWS.digitize = viewDigitize;

function renderOcrRecent() {
  const box = $('#ocr-recent');
  if (!box) return;
  $('#ocr-recent-count').textContent = OCR.recent.length ? `${OCR.recent.length} most recent in your area` : '';
  if (!OCR.recent.length) {
    box.innerHTML = '<div class="small muted">No documents digitized yet. Every PDF or image uploaded to a project is read automatically and will appear here.</div>';
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Uploaded</th><th>Project</th><th>Document</th><th>Status</th><th>Fields found</th><th class="num">OCR conf.</th><th>Survey No.</th><th>Parcel</th></tr></thead><tbody>
    ${OCR.recent.map((r) => `<tr class="click ${String(r.version_id) === String(OCR.active) ? 'active' : ''}" data-act="ocr-open" data-id="${r.version_id}">
      <td class="nowrap">${fdate(r.uploaded_at)}<div class="small muted">${esc(r.uploaded_by)}</div></td>
      <td><b>${esc(r.project_code)}</b></td>
      <td>${esc(r.document_name)} <span class="muted small">v${r.version}</span><div class="small muted">${esc(title(r.category))}</div></td>
      <td><span class="badge ${{ done: 'low', failed: 'critical', pending: 'medium' }[r.status] || ''}">${esc(EXTRACTION_STATUS_LABEL[r.status] || r.status)}</span></td>
      <td class="progress">${r.status === 'done' ? `${bar((100 * r.fields_found) / r.fields_total)}<span class="small muted">${r.fields_found} / ${r.fields_total}</span>` : '<span class="muted small">-</span>'}</td>
      <td class="num">${r.ocr_confidence != null && r.status === 'done' ? Math.round(r.ocr_confidence) + '%' : '-'}</td>
      <td>${esc(r.survey_no || '-')}</td>
      <td>${r.applied ? `<span class="badge low">Applied</span>` : (r.status === 'done' && r.fields_found ? '<span class="badge medium">To review</span>' : '')}</td></tr>`).join('')}
    </tbody></table>`;
}

async function refreshOcrRecent() {
  OCR.recent = (await api('/api/documents/extractions?limit=50')).items;
  renderOcrRecent();
}

async function openOcrResult(versionId, known) {
  const box = $('#ocr-result');
  if (!box) return;
  OCR.active = versionId;
  const r = OCR.recent.find((x) => String(x.version_id) === String(versionId));
  box.innerHTML = '<div class="small muted">Loading...</div>';
  try {
    const ext = known || await api(`/api/documents/versions/${versionId}/extraction`);
    const meta = r ? `<div class="ocr-doc"><b>${esc(r.document_name)}</b> <span class="muted small">v${r.version} &middot; ${esc(r.filename)}</span>
      <div class="small muted">${esc(r.project_code)} &middot; ${esc(r.project_name)}</div></div>` : '';
    box.innerHTML = meta + extractionPanelHtml(ext, versionId, { bare: true });
  } catch (e) { box.innerHTML = `<div class="banner bad small">${esc(e.message)}</div>`; }
  document.querySelectorAll('#ocr-recent tr.click').forEach((tr) => tr.classList.toggle('active', tr.dataset.id === String(versionId)));
}
ACT['ocr-open'] = async (el) => {
  await openOcrResult(el.dataset.id);
  if (window.innerWidth < 900) $('#ocr-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

function ocrFileChosen(file) { docFileChosen(file, '#ocr-preview'); }

ACT['ocr-sample'] = async () => {
  const pid = $('#ocr-project').value;
  if (!pid) { toast('Pick a project first', 'bad'); return; }
  const r = await attempt(() => api(`/api/projects/${pid}/sample-scan`, { raw: true }));
  if (!r) return;
  const p = OCR.projects.find((x) => String(x.id) === String(pid));
  const file = new File([await r.blob()], `sample_award_${p ? p.code : pid}.png`, { type: 'image/png' });
  const dt = new DataTransfer();
  dt.items.add(file);
  $('#ocr-file').files = dt.files;
  $('#ocr-name').value = 'Sample award scan';
  ocrFileChosen(file);
  toast('Sample scan loaded. Press "Digitize document".', 'ok');
};

FORMS['ocr-upload'] = async (f) => {
  const fd = new FormData(f);
  const pid = fd.get('project');
  fd.delete('project');
  if (!fd.get('name')) fd.delete('name');
  const prog = $('#ocr-progress');
  const fill = prog.querySelector('i'), label = prog.querySelector('span');
  prog.classList.remove('hidden');
  fill.style.width = '0%'; label.textContent = 'Uploading...';
  const btn = f.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const body = await uploadWithProgress(`/api/projects/${pid}/documents`, fd, (pct) => {
      fill.style.width = pct + '%';
      label.textContent = pct < 100 ? `Uploading ${pct}%` : 'Reading document (OCR)...';
    });
    label.textContent = 'Done';
    const ext = body.latest_extraction;
    await refreshOcrRecent();
    await openOcrResult(ext.document_version_id, ext);
    toast(ext.status === 'done' ? `Digitized: ${ext.fields_found} of ${ext.fields_total} fields found` : `Stored. ${EXTRACTION_STATUS_LABEL[ext.status] || ext.status}`, ext.status === 'done' ? 'ok' : '');
    f.reset();
    ocrFileChosen(null);
    if (window.innerWidth < 900) $('#ocr-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { fail(e); } finally { btn.disabled = false; setTimeout(() => prog.classList.add('hidden'), 1200); }
};

document.addEventListener('change', (e) => { if (e.target.id === 'ocr-file') ocrFileChosen(e.target.files[0]); });
document.addEventListener('dragover', (e) => { const z = e.target.closest && e.target.closest('#ocr-dropzone'); if (z) { e.preventDefault(); z.classList.add('drag'); } });
document.addEventListener('dragleave', (e) => { const z = e.target.closest && e.target.closest('#ocr-dropzone'); if (z && !e.relatedTarget) z.classList.remove('drag'); });
document.addEventListener('drop', (e) => {
  const zone = e.target.closest && e.target.closest('#ocr-dropzone');
  if (!zone) return;
  e.preventDefault();
  zone.classList.remove('drag');
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!file) return;
  const dt = new DataTransfer();
  dt.items.add(file);
  $('#ocr-file').files = dt.files;
  ocrFileChosen(file);
});

// ---- timeline
function timelineTab(d) {
  const noteForm = can('central', 'state', 'district', 'field', 'agency') ? `<form data-form="add-note" class="mb"><div class="gap"><input name="text" placeholder="Add a field note or update" required style="flex:1"><button class="btn" type="submit">Add note</button></div></form>` : '';
  return `<div class="card">${noteForm}<ul class="plain timeline">${d.events.map((e) => `<li><div><div class="small">${fdate(e.at)}</div><span class="badge">${esc(e.kind.replace('_', ' '))}</span></div>
    <div>${esc(e.detail)}<div class="who">${esc(e.actor)}${e.stage_label ? ' &middot; ' + esc(e.stage_label) : ''}</div></div></li>`).join('')}</ul></div>`;
}
FORMS['add-note'] = async (f) => { const d = formData(f); await mutate(() => post(`/api/projects/${D.id}/notes`, { text: d.text }), 'Note added'); };
