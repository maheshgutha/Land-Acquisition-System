'use strict';
/* GIS map: parcel polygons coloured by acquisition status, project markers coloured by delay risk. */

async function viewMap(main) {
  const states = S.meta.states;
  main.innerHTML = `<div class="gap mb"><h2>Land map</h2><span class="muted small" id="map-count"></span>
      <span class="synthetic-badge" title="Parcel boundaries and coordinates in this build are synthetic demo data.">Synthetic coordinates</span>
      <div class="spacer"></div>
      <div><select id="m-state"><option value="">All visible states</option>${states.map((s) => `<option>${esc(s)}</option>`).join('')}</select></div></div>
    <div class="legend mb" id="legend">
      ${Object.entries(STATUS_COLORS).map(([k, c]) => `<label><input type="checkbox" data-layer="${k}" checked><span class="dot" style="background:${c}"></span>${title(k)}</label>`).join('')}
      <label><span class="dot" style="background:#fff;border:2px dashed #d03b3b"></span>Disputed</label>
      <span class="muted">|</span>
      <label><input type="checkbox" data-layer="__projects" checked>Project markers, colour = delay risk, size = proposed area:</label>
      ${Object.entries(RISK_COLORS).map(([k, c]) => `<span><span class="dot" style="background:${c}"></span>${k}</span>`).join('')}
    </div>
    <div id="map"></div>
    <p class="small muted mt">Parcels and coordinates are synthetic. Zoom in to see individual parcels; click one to verify it against the (mock) land-records and cadastral services.</p>`;

  const map = (S.map = L.map('map', { preferCanvas: true }).setView([16.0, 78.5], 7));
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);

  const focus = S.focusProject || null;
  S.focusProject = null;
  await loadMapData(map, $('#m-state').value, focus);

  $('#m-state').addEventListener('change', (e) => loadMapData(map, e.target.value, null));
  $('#legend').addEventListener('change', (e) => {
    const key = e.target.dataset.layer; const layer = S.mapLayers && S.mapLayers[key];
    if (layer) (e.target.checked ? map.addLayer(layer) : map.removeLayer(layer));
  });
}

async function loadMapData(map, state, focus) {
  const q = state ? '?state=' + encodeURIComponent(state) : '';
  const [parcels, projects] = await Promise.all([api('/api/gis/parcels' + q), api('/api/gis/projects')]);
  (S.mapLayers ? Object.values(S.mapLayers) : []).forEach((l) => map.removeLayer(l));
  S.mapLayers = {};

  const byStatus = {};
  parcels.features.forEach((f) => { (byStatus[f.properties.status] = byStatus[f.properties.status] || []).push(f); });
  Object.keys(STATUS_COLORS).forEach((status) => {
    const layer = L.geoJSON({ type: 'FeatureCollection', features: byStatus[status] || [] }, {
      style: (f) => {
        const hot = focus && f.properties.project_id === focus;
        return { fillColor: STATUS_COLORS[status], fillOpacity: 0.6, color: f.properties.disputed ? '#d03b3b' : hot ? '#0f1b2d' : STATUS_COLORS[status], weight: f.properties.disputed || hot ? 3 : 1.2, dashArray: f.properties.disputed ? '4 3' : null };
      },
      onEachFeature: (f, lyr) => lyr.bindPopup(() => parcelPopup(f.properties), { minWidth: 240 }),
    });
    S.mapLayers[status] = layer;
    if ($(`#legend input[data-layer="${status}"]`).checked) layer.addTo(map);
  });

  // Bubble map: colour = delay risk (status palette), radius = proposed area (sqrt scale so the eye reads
  // it as magnitude, not raw area) — two encodings on one mark, each carrying a distinct, legible job.
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
  if ($('#legend input[data-layer="__projects"]').checked) markers.addTo(map);

  $('#map-count').textContent = `${parcels.features.length} parcels, ${projects.features.length} projects`;

  const pts = [];
  const relevant = focus ? parcels.features.filter((f) => f.properties.project_id === focus) : parcels.features;
  relevant.forEach((f) => f.geometry.coordinates[0].forEach((c) => pts.push([c[1], c[0]])));
  if (!pts.length) projects.features.forEach((f) => pts.push([f.geometry.coordinates[1], f.geometry.coordinates[0]]));
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: focus ? 15 : 9 });
}

function parcelPopup(p) {
  return `<div><h4>Survey ${esc(p.survey_no)} <span class="muted">(${esc(p.village)})</span></h4>
    ${esc(p.project_code)} &middot; ${num(p.area_ha, 2)} ha<br>Status: <b>${esc(p.status)}</b>${p.disputed ? ' <span class="badge critical">disputed</span>' : ''}<br>
    Owner: ${p.owner_name == null ? '<span class="muted">hidden for your role</span>' : esc(p.owner_name)}<br>
    <button class="btn sm" style="margin-top:6px" data-act="verify-popup" data-id="${p.id}">Verify against records</button>
    <div id="pv-${p.id}" style="margin-top:6px"></div></div>`;
}
ACT['verify-popup'] = async (el) => {
  const target = $(`#pv-${el.dataset.id}`);
  el.disabled = true;
  await renderVerification(el.dataset.id, target);
  el.disabled = false;
};
