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
const parcelValBadge = (p) => {
  if (!p.validation_status) return '';
  const cls = p.validation_status === 'passed' ? 'low' : p.validation_status === 'warning' ? 'medium' : 'critical';
  const icon = p.validation_status === 'passed' ? '&#10003;' : p.validation_status === 'warning' ? '&#9888;' : '&#10007;';
  return ` <span class="badge ${cls}" title="Rule Engine Status">${icon} ${title(p.validation_status)}</span>`;
};

const parcelMismatchPill = (p) => {
  if (p.area_mismatch_pct == null) return '';
  const cls = p.area_mismatch_pct > 25 ? 'critical' : p.area_mismatch_pct > 10 ? 'warn' : 'ok';
  return `<div class="mismatch-pill ${cls}" title="Cadastral Area Mismatch vs Document">${p.area_mismatch_pct}% mismatch</div>`;
};

const parcelRow = (p, d) => `<tr><td><b>${esc(p.survey_no)}</b><div class="small muted">${esc(p.village)}</div></td>
  <td>${p.owner_name == null ? '<span class="muted small">hidden for your role</span>' : esc(p.owner_name)}</td>
  <td class="num">${num(p.area_ha, 2)}${parcelMismatchPill(p)}</td>
  <td>${esc(p.land_type)}</td>
  <td><span class="dot" style="background:${STATUS_COLORS[p.status]}"></span>${esc(p.status)}${p.disputed ? ' <span class="badge critical">disputed</span>' : ''}${parcelValBadge(p)}</td>
  <td class="nowrap">
    <button class="btn sm" data-act="rules-parcel" data-id="${p.id}" title="Run 22 Land Business Rules (J01-J30)">Rules</button>
    <button class="btn sm" data-act="verify-parcel" data-id="${p.id}">Verify</button>
    ${can('field', 'district') ? `<button class="btn sm" data-act="toggle-dispute" data-id="${p.id}" data-val="${!p.disputed}">${p.disputed ? 'Clear dispute' : 'Flag dispute'}</button>` : ''}
    ${can('field', 'district') && ['possession', 'rr'].includes(d.stage) && p.status === 'compensated' ? `<button class="btn sm primary" data-act="possess-parcel" data-id="${p.id}">Mark possessed</button>` : ''}</td></tr>
  <tr id="vr-${p.id}" class="hidden"><td colspan="6"></td></tr>
  <tr id="pr-${p.id}" class="hidden"><td colspan="6"></td></tr>`;

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

async function renderParcelRules(parcelId, into) {
  into.innerHTML = '<div class="loading">Evaluating 22 Land Business Rules (J01–J30)...</div>';
  const r = await attempt(() => api(`/api/parcels/${parcelId}/validate`, { method: 'POST' }));
  if (!r) { into.innerHTML = '<div class="banner bad">Failed to run rule engine.</div>'; return; }
  const stCls = r.validation_status === 'passed' ? 'ok' : r.validation_status === 'warning' ? 'warn' : 'bad';
  const records = r.records || [];
  into.innerHTML = `<div class="banner ${stCls}" style="margin:6px 0">
    <div class="gap" style="justify-content:space-between">
      <b>Land Validation Rule Engine: ${title(r.validation_status)}</b>
      <span class="small">Mismatch: <b>${r.area_mismatch_pct != null ? r.area_mismatch_pct + '%' : 'N/A'}</b></span>
    </div>
    <div class="rule-panel" style="margin-top:8px">
      ${records.map((rec) => `
        <div class="rule-card ${rec.status}">
          <span class="rule-id">${esc(rec.rule_id)}</span>
          <div style="flex:1">
            <div class="gap" style="gap:6px">
              <b>${esc(rec.rule_name)}</b>
              <span class="badge ${rec.status === 'passed' ? 'low' : rec.status === 'warning' ? 'medium' : 'critical'}" style="font-size:10px">${esc(rec.status).toUpperCase()}</span>
              <span class="small muted">(${esc(rec.severity)})</span>
            </div>
            <div style="margin-top:2px">${esc(rec.message)}</div>
          </div>
        </div>
      `).join('')}
    </div>
  </div>`;
}
ACT['rules-parcel'] = async (el) => {
  const row = $(`#pr-${el.dataset.id}`);
  if (!row.classList.contains('hidden')) { row.classList.add('hidden'); return; }
  row.classList.remove('hidden');
  await renderParcelRules(el.dataset.id, row.firstElementChild);
};

// ---- documents
function documentsTab(d) {
  const docs = D.docs;
  const cats = [...d.documents_required, ...DOC_CATEGORIES_EXTRA];
  const canUpload = can('agency', 'state', 'district', 'field');
  return `<div class="card"><h4>Required for scrutiny</h4><div class="gap">${d.documents_required.map((c) => `<span class="badge ${d.documents_uploaded.includes(c) ? 'low' : 'medium'}">${d.documents_uploaded.includes(c) ? '&#10003;' : '&#9675;'} ${esc(title(c))}</span>`).join('')}</div></div>
    <div class="card mt"><h4>Repository (${docs.length})</h4>
      ${docs.map((x) => `<div class="mb" style="padding-bottom:12px;border-bottom:1px solid #f1f5f9">
        <div class="gap" style="justify-content:space-between">
          <div>
            <b>${esc(x.name)}</b> <span class="badge">${esc(title(x.category))}</span> <span class="muted small">v${x.latest_version}</span>
          </div>
          <div class="gap">
            <button class="btn sm primary" data-act="nlp-extract-doc" data-id="${x.id}">&#129302; Extract &amp; Validate NLP</button>
          </div>
        </div>
        <ul class="plain small mt">${[...x.versions].reverse().map((v) => `<li>v${v.version} &middot; ${fdate(v.uploaded_at)} by ${esc(v.uploaded_by)} &middot; ${fsize(v.size)} <span class="muted" title="SHA-256 ${esc(v.sha256)}">sha ${esc(v.sha256.slice(0, 10))}</span>
          <button class="btn sm" data-act="dl-version" data-id="${v.id}" data-name="${esc(v.filename)}">Download</button>${v.note ? ` <span class="muted">${esc(v.note)}</span>` : ''}</li>`).join('')}</ul>
        <div id="doc-nlp-${x.id}" class="hidden mt"></div>
      </div>`).join('') || '<span class="muted small">No documents uploaded.</span>'}
      ${canUpload && d.status !== 'completed' ? `<form data-form="upload-doc" class="mt"><h4>Upload document or new version</h4><div class="row">
        <div><label>File</label><div class="gap"><input type="file" name="file" id="doc-file" required style="flex:1"><button class="btn sm" type="button" data-act="capture-photo" title="Take a photo with this device's camera">&#128247;</button></div>
          <input type="file" id="doc-camera" accept="image/*" capture="environment" class="hidden"></div>
        <div><label>Category</label><select name="category">${cats.map((c) => `<option value="${c}">${esc(title(c))}</option>`).join('')}</select></div>
        <div><label>Name (same name = new version)</label><input name="name" placeholder="e.g. Land plan"></div>
        <div><label>Note</label><input name="note"></div><div style="align-self:end"><button class="btn primary" type="submit">Upload</button></div></div></form>` : ''}</div>`;
}

async function renderDocNlp(docId, into) {
  into.innerHTML = '<div class="loading">Running NLP Extraction &amp; Land Business Rules Engine (H01–H30, I01–I15, J01–J30)...</div>';
  const res = await attempt(() => api(`/api/documents/${docId}/extract`, { method: 'POST', json: { ocr_confidence: 0.92 } }));
  if (!res) {
    into.innerHTML = '<div class="banner bad">Extraction failed or document has no readable content.</div>';
    return;
  }
  const ext = res.extraction || {};
  const val = res.validation || {};
  const fields = ext.fields || {};
  const rules = val.results || [];

  // Color-Coded Field Confidence Badges (Green >=90%, Yellow 70-89%, Orange 40-69%, Red <40%)
  const badgeHtml = (f) => {
    if (!f) return '';
    return `<div class="tooltip-wrap">
      <span class="conf-badge ${f.badge_color}">${f.confidence_pct}% ${esc(f.badge_label || '')}</span>
      <span class="tooltip-text"><b>Confidence Score: ${f.confidence_pct}%</b><br>${esc(f.explanation || 'Formula: OCR (40%) + Pattern Precision (50%) + Gazetteer (10%)')}<br><span style="opacity:0.8">Source: ${esc(f.source || 'regex')}</span></span>
    </div>`;
  };

  const fieldList = [
    { key: 'survey_no', label: 'Survey / Khasra No.' },
    { key: 'khata_no', label: 'Khata / Khatoni No.' },
    { key: 'patta_no', label: 'Patta Number' },
    { key: 'owner_name', label: 'Primary Owner Name' },
    { key: 'area_value', label: 'Recorded Extent/Area' },
    { key: 'area_ha', label: 'Standardized Area (ha)' },
    { key: 'village', label: 'Village (Gazetteer)' },
    { key: 'district', label: 'District' },
    { key: 'state', label: 'State' },
    { key: 'land_classification', label: 'Classification' },
    { key: 'registration_no', label: 'Deed / Reg. No.' },
    { key: 'registration_date', label: 'Registration Date' },
    { key: 'mutation_no', label: 'Mutation No.' },
    { key: 'mutation_status', label: 'Mutation Status' },
    { key: 'encumbrance_status', label: 'Encumbrance' },
  ];

  const overallBadge = `<div class="tooltip-wrap">
    <span class="conf-badge ${ext.badge_color}" style="font-size:13px;padding:4px 12px">Composite: ${ext.confidence_pct}% (${esc(ext.badge_label)})</span>
    <span class="tooltip-text"><b>Weighted Composite Confidence:</b><br>Survey No (25%) + Owner (25%) + Area (25%) + Village (15%) + District (10%)</span>
  </div>`;

  const statusBadge = val.status === 'passed'
    ? '<span class="badge low">&#10003; All Rules Passed</span>'
    : val.status === 'warning'
      ? '<span class="badge medium">&#9888; Warnings Detected</span>'
      : '<span class="badge critical">&#10007; Critical Rule Violations</span>';

  into.innerHTML = `
    <div style="background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;box-shadow:0 2px 8px rgba(0,0,0,0.04)">
      <div class="gap" style="justify-content:space-between;margin-bottom:12px;border-bottom:1px solid #f1f5f9;padding-bottom:10px">
        <div>
          <h4 style="margin:0 0 4px 0">Structured Land Field Extraction (H01–H30, I01–I15)</h4>
          <span class="small muted">Extracted: ${fdate(res.extracted_at)} &middot; ${ext.requires_review ? '<span class="badge medium">Requires Human Review</span>' : '<span class="badge low">Automated Pass</span>'}</span>
        </div>
        <div>${overallBadge}</div>
      </div>

      <div class="table-wrap mb">
        <table style="font-size:12px">
          <thead>
            <tr><th>Field Name</th><th>Extracted Value</th><th>Source</th><th>Confidence Score &amp; Audit Breakdown</th></tr>
          </thead>
          <tbody>
            ${fieldList.map(({ key, label }) => {
              const f = fields[key] || { raw_value: '-', normalized_value: '-', confidence_pct: 0, badge_color: 'red', badge_label: 'Missing', source: 'none' };
              const displayVal = f.normalized_value != null && f.normalized_value !== '' ? String(f.normalized_value) : (f.raw_value || '<span class="muted">-</span>');
              return `<tr>
                <td><b>${esc(label)}</b></td>
                <td>${esc(displayVal)}${f.requires_review ? ' <span class="badge critical" style="font-size:10px">Review</span>' : ''}</td>
                <td><span class="badge" style="font-size:10px">${esc(f.source)}</span></td>
                <td>${badgeHtml(f)}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>

      <div style="margin-top:16px;border-top:1px solid #f1f5f9;padding-top:12px">
        <div class="gap" style="justify-content:space-between">
          <h4 style="margin:0">Land Validation Business Rule Engine Inspection (J01–J30)</h4>
          <div>${statusBadge}</div>
        </div>
        <p class="small muted" style="margin:4px 0 10px 0">Automated verification of 22 rules: syntax formats, duplicate parcel scans, joint-ownership 100% share sums, and GIS cadastral area alignment.</p>
        
        <div class="rule-panel">
          ${rules.map((r) => {
            const icon = r.status === 'passed' ? '&#10003;' : r.status === 'warning' ? '&#9888;' : '&#10007;';
            const statusLabel = r.status === 'passed' ? 'PASSED' : r.status === 'warning' ? 'WARNING' : 'FAILED';
            return `<div class="rule-card ${r.status}">
              <span class="rule-id">${esc(r.rule_id)}</span>
              <div style="flex:1">
                <div class="gap" style="gap:6px">
                  <b>${esc(r.rule_name)}</b>
                  <span class="badge ${r.status === 'passed' ? 'low' : r.status === 'warning' ? 'medium' : 'critical'}" style="font-size:10px">${icon} ${statusLabel}</span>
                  <span class="small muted">(${esc(r.severity)})</span>
                </div>
                <div style="margin-top:3px">${esc(r.message)}</div>
              </div>
            </div>`;
          }).join('')}
        </div>
      </div>
    </div>
  `;
}
ACT['nlp-extract-doc'] = async (el) => {
  const container = $(`#doc-nlp-${el.dataset.id}`);
  if (!container) return;
  if (!container.classList.contains('hidden')) {
    container.classList.add('hidden');
    el.innerHTML = '&#129302; Extract &amp; Validate NLP';
  } else {
    container.classList.remove('hidden');
    el.innerHTML = '&#10006; Hide Extraction &amp; Rules';
    await renderDocNlp(el.dataset.id, container);
  }
};
ACT['capture-photo'] = () => $('#doc-camera').click();
document.addEventListener('change', (e) => {
  if (e.target.id === 'doc-camera' && e.target.files.length) {
    const dt = new DataTransfer();
    dt.items.add(e.target.files[0]);
    $('#doc-file').files = dt.files;
    toast('Photo captured: ' + e.target.files[0].name, 'ok');
  }
});
FORMS['upload-doc'] = async (f) => {
  const fd = new FormData(f);  // the camera input has no `name`, so a captured photo (copied into #doc-file) is the only file present
  if (!fd.get('name')) fd.delete('name');
  await mutate(() => api(`/api/projects/${D.id}/documents`, { method: 'POST', form: fd }), 'Document stored');
};
ACT['dl-version'] = (el) => attempt(() => downloadBlob(`/api/documents/versions/${el.dataset.id}/download`, el.dataset.name));

// ---- timeline
function timelineTab(d) {
  const noteForm = can('central', 'state', 'district', 'field', 'agency') ? `<form data-form="add-note" class="mb"><div class="gap"><input name="text" placeholder="Add a field note or update" required style="flex:1"><button class="btn" type="submit">Add note</button></div></form>` : '';
  return `<div class="card">${noteForm}<ul class="plain timeline">${d.events.map((e) => `<li><div><div class="small">${fdate(e.at)}</div><span class="badge">${esc(e.kind.replace('_', ' '))}</span></div>
    <div>${esc(e.detail)}<div class="who">${esc(e.actor)}${e.stage_label ? ' &middot; ' + esc(e.stage_label) : ''}</div></div></li>`).join('')}</ul></div>`;
}
FORMS['add-note'] = async (f) => { const d = formData(f); await mutate(() => post(`/api/projects/${D.id}/notes`, { text: d.text }), 'Note added'); };
