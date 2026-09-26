'use strict';
/* GIS map: parcel polygons coloured by acquisition status, project markers coloured by delay risk. */

async function viewMap(main) {
  const states = S.meta.states;
  main.innerHTML = `<div class="gap mb"><h2>Land map</h2><span class="muted small" id="map-count"></span><div class="spacer"></div>
      <div><select id="m-state"><option value="">All visible states</option>${states.map((s) => `<option>${esc(s)}</option>`).join('')}</select></div></div>
    <div class="legend mb" id="legend">
      ${Object.entries(STATUS_COLORS).map(([k, c]) => `<label><input type="checkbox" data-layer="${k}" checked><span class="dot" style="background:${c}"></span>${title(k)}</label>`).join('')}
      <label><span class="dot" style="background:#fff;border:2px dashed #d03b3b"></span>Disputed</label>
      <span class="muted">|</span>
      <label><input type="checkbox" id="toggle-val-layer"> <b>Validation &amp; Mismatch Layer:</b></label>
      <span class="conf-badge green" style="padding:2px 8px;font-size:11px"><span class="dot" style="background:#0ca30c;margin-right:2px"></span>Verified</span>
      <span class="conf-badge yellow" style="padding:2px 8px;font-size:11px"><span class="dot" style="background:#fab219;margin-right:2px"></span>In Review</span>
      <span class="conf-badge red" style="padding:2px 8px;font-size:11px"><span class="dot" style="background:#d03b3b;margin-right:2px"></span>Failed / Mismatch</span>
      <span class="muted">|</span>
      <label><input type="checkbox" data-layer="__projects" checked>Project markers (delay risk):</label>
      ${Object.entries(RISK_COLORS).map(([k, c]) => `<span><span class="dot" style="background:${c}"></span>${k}</span>`).join('')}
    </div>
    <div id="map"></div>
    <p class="small muted mt">Parcels and coordinates are synthetic. Zoom in to see individual parcels; toggle the Validation Layer to inspect cadastral area mismatches and rule compliance.</p>`;

  const map = (S.map = L.map('map', { preferCanvas: true }).setView([16.0, 78.5], 7));
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);

  const focus = S.focusProject || null;
  S.focusProject = null;
  S.currentFocus = focus;
  await loadMapData(map, $('#m-state').value, focus);

  $('#m-state').addEventListener('change', (e) => loadMapData(map, e.target.value, null));
  $('#toggle-val-layer').addEventListener('change', () => loadMapData(map, $('#m-state').value, S.currentFocus));
  $('#legend').addEventListener('change', (e) => {
    if (e.target.id === 'toggle-val-layer') return;
    const key = e.target.dataset.layer; const layer = S.mapLayers && S.mapLayers[key];
    if (layer) (e.target.checked ? map.addLayer(layer) : map.removeLayer(layer));
  });
}

async function loadMapData(map, state, focus) {
  const q = state ? '?state=' + encodeURIComponent(state) : '';
  const [parcels, projects] = await Promise.all([api('/api/gis/parcels' + q), api('/api/gis/projects')]);
  (S.mapLayers ? Object.values(S.mapLayers) : []).forEach((l) => map.removeLayer(l));
  S.mapLayers = {};

  const valMode = $('#toggle-val-layer') && $('#toggle-val-layer').checked;
  const VAL_COLORS = {
    passed: '#0ca30c',
    warning: '#fab219',
    failed: '#d03b3b',
  };

  const getValStatus = (p) => {
    if (p.validation_status) return p.validation_status;
    if (p.area_mismatch_pct != null) {
      if (p.area_mismatch_pct > 25) return 'failed';
      if (p.area_mismatch_pct > 10) return 'warning';
      return 'passed';
    }
    return 'passed';
  };

  const byStatus = {};
  parcels.features.forEach((f) => { (byStatus[f.properties.status] = byStatus[f.properties.status] || []).push(f); });
  Object.keys(STATUS_COLORS).forEach((status) => {
    const layer = L.geoJSON({ type: 'FeatureCollection', features: byStatus[status] || [] }, {
      style: (f) => {
        const hot = focus && f.properties.project_id === focus;
        const valSt = getValStatus(f.properties);
        const fillColor = valMode ? VAL_COLORS[valSt] : STATUS_COLORS[status];
        const strokeColor = valMode
          ? (f.properties.disputed ? '#d03b3b' : VAL_COLORS[valSt])
          : (f.properties.disputed ? '#d03b3b' : hot ? '#0f1b2d' : STATUS_COLORS[status]);
        return {
          fillColor: fillColor,
          fillOpacity: valMode ? 0.72 : 0.60,
          color: strokeColor,
          weight: f.properties.disputed || hot || valMode ? 3 : 1.2,
          dashArray: f.properties.disputed ? '4 3' : null
        };
      },
      onEachFeature: (f, lyr) => lyr.bindPopup(() => parcelPopup(f.properties), { minWidth: 260 }),
    });
    S.mapLayers[status] = layer;
    const chk = $(`#legend input[data-layer="${status}"]`);
    if (!chk || chk.checked) layer.addTo(map);
  });

  // Bubble map: colour = delay risk, radius = proposed area
  const maxArea = Math.max(1, ...projects.features.map((f) => f.properties.proposed_area_ha || 0));
  const markers = L.geoJSON(projects, {
    pointToLayer: (f, ll) => {
      const p = f.properties, color = p.status === 'completed' ? '#898781' : RISK_COLORS[p.risk_category] || '#898781';
      const radius = 6 + 12 * Math.sqrt((p.proposed_area_ha || 0) / maxArea);
      return L.circleMarker(ll, { radius, fillColor: color, fillOpacity: 0.85, color: p.status === 'stalled' ? '#0b0b0b' : '#fff', weight: p.status === 'stalled' ? 3 : 2 });
    },
    onEachFeature: (f, lyr) => {
      const p = f.properties;
      lyr.bindPopup(`<h4>${esc(p.code)}</h4>${esc(p.name)}<br>${esc(p.district)}, ${esc(p.state)}<br>Stage: ${esc(p.stage_label)}${p.status === 'stalled' ? ' <b class="fail">(stalled)</b>' : ''}<br>
        ${p.status === 'completed' ? 'Completed' : `Delay risk: ${riskBadge(p.risk_category, p.risk_score)}`}<br><button class="btn sm primary" style="margin-top:6px" data-act="open-project" data-id="${p.id}">Open project</button>`);
    },
  });
  S.mapLayers.__projects = markers;
  const pChk = $('#legend input[data-layer="__projects"]');
  if (!pChk || pChk.checked) markers.addTo(map);

  $('#map-count').textContent = `${parcels.features.length} parcels, ${projects.features.length} projects`;

  const pts = [];
  const relevant = focus ? parcels.features.filter((f) => f.properties.project_id === focus) : parcels.features;
  relevant.forEach((f) => f.geometry.coordinates[0].forEach((c) => pts.push([c[1], c[0]])));
  if (!pts.length) projects.features.forEach((f) => pts.push([f.geometry.coordinates[1], f.geometry.coordinates[0]]));
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: focus ? 15 : 9 });
}

function parcelPopup(p) {
  const valStatus = p.validation_status || (p.area_mismatch_pct > 25 ? 'failed' : p.area_mismatch_pct > 10 ? 'warning' : 'passed');
  const valBadgeCls = valStatus === 'passed' ? 'low' : valStatus === 'warning' ? 'medium' : 'critical';
  const valIcon = valStatus === 'passed' ? '&#10003;' : valStatus === 'warning' ? '&#9888;' : '&#10007;';

  const mismatchHtml = p.area_mismatch_pct != null
    ? `<br>Doc Area: <b>${num(p.doc_area_ha, 2)} ha</b> &middot; Mismatch: <span class="mismatch-pill ${p.area_mismatch_pct > 25 ? 'critical' : p.area_mismatch_pct > 10 ? 'warn' : 'ok'}">${p.area_mismatch_pct}%</span>`
    : '';

  return `<div><h4>Survey ${esc(p.survey_no)} <span class="muted">(${esc(p.village)})</span></h4>
    ${esc(p.project_code)} &middot; GIS Area: <b>${num(p.area_ha, 2)} ha</b>${mismatchHtml}<br>
    Status: <b>${esc(p.status)}</b>${p.disputed ? ' <span class="badge critical">disputed</span>' : ''} &middot;
    Validation: <span class="badge ${valBadgeCls}">${valIcon} ${title(valStatus)}</span><br>
    Owner: ${p.owner_name == null ? '<span class="muted">hidden for your role</span>' : esc(p.owner_name)}<br>
    <div class="gap mt" style="gap:6px">
      <button class="btn sm" data-act="verify-popup" data-id="${p.id}">Verify records</button>
      <button class="btn sm primary" data-act="rules-popup" data-id="${p.id}">Check Rules</button>
    </div>
    <div id="pv-${p.id}" style="margin-top:6px"></div>
    <div id="pr-pop-${p.id}" style="margin-top:6px"></div></div>`;
}
ACT['verify-popup'] = async (el) => {
  const target = $(`#pv-${el.dataset.id}`);
  el.disabled = true;
  await renderVerification(el.dataset.id, target);
  el.disabled = false;
};
ACT['rules-popup'] = async (el) => {
  const target = $(`#pr-pop-${el.dataset.id}`);
  el.disabled = true;
  await renderParcelRules(el.dataset.id, target);
  el.disabled = false;
};
