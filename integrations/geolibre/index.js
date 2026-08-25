// AquaScope Gauges: a GeoLibre plugin. Self-contained ES module, no build step.
//
// Adds a map control (droplet). Toggle it on and every station in the AquaScope
// Archive (https://huggingface.co/datasets/Rekin226/aquascope-gauges) is drawn as
// a clustered layer, coloured by source. Click a gauge for a popup with its
// name, agency, period, and links to the observed record + flood frequency in
// AquaScope Explorer and to the agency page. A right-sidebar panel holds the
// legend, a name search, and per-source counts. Everything is fetched straight
// from the public dataset (CORS-enabled); nothing is bundled.

const PLUGIN_ID = "aquascope-gauges";
const VERSION = "0.1.0";
const CATALOG_URL = "https://huggingface.co/datasets/Rekin226/aquascope-gauges/resolve/main/stations.geojson";
const EXPLORER_URL = "https://rekin226-aquascope-explorer.static.hf.space/";
const SOURCE_ID = "aquascope-gauges";
const LAYER_IDS = ["aquascope-gauges-clusters", "aquascope-gauges-count", "aquascope-gauges-points"];
const PANEL_ID = "aquascope-gauges-panel";

const SOURCE_STYLE = {
  usgs: { label: "USGS (US)", color: "#1565c0" },
  uk_ea: { label: "Environment Agency (UK)", color: "#2e7d32" },
  hubeau_hydrometrie: { label: "Hub'Eau (FR)", color: "#c62828" },
  pegelonline: { label: "PEGELONLINE (DE)", color: "#ef6c00" },
  ireland_opw: { label: "OPW (IE)", color: "#6a1b9a" },
  taiwan_cwa: { label: "CWA (TW)", color: "#00838f" },
};
const FALLBACK = "#546e7a";

let map = null;
let appApi = null;
let geojson = null;
let counts = {};
let active = false;
let popup = null;
let button = null;
const disposers = [];
const handlers = [];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadCatalog() {
  if (geojson) return geojson;
  const res = await fetch(CATALOG_URL);
  if (!res.ok) throw new Error(`catalog ${res.status}`);
  const gj = await res.json();
  counts = {};
  for (const f of gj.features) {
    const src = f.properties.source;
    counts[src] = (counts[src] || 0) + 1;
    f.properties.color = (SOURCE_STYLE[src] || {}).color || FALLBACK;
    f.properties.key = `${src}/${f.properties.station_id}`;
  }
  geojson = gj;
  return gj;
}

function on(type, layer, fn) {
  map.on(type, layer, fn);
  handlers.push([type, layer, fn]);
}

async function addLayers() {
  const gj = await loadCatalog();
  if (!map || map.getSource(SOURCE_ID)) return;
  map.addSource(SOURCE_ID, { type: "geojson", data: gj, cluster: true, clusterMaxZoom: 9, clusterRadius: 38 });
  map.addLayer({
    id: LAYER_IDS[0], type: "circle", source: SOURCE_ID, filter: ["has", "point_count"],
    paint: { "circle-color": "#1565c0", "circle-opacity": 0.75, "circle-stroke-color": "#fff", "circle-stroke-width": 1.5,
      "circle-radius": ["step", ["get", "point_count"], 14, 50, 18, 250, 24, 1000, 30] },
  });
  map.addLayer({
    id: LAYER_IDS[1], type: "symbol", source: SOURCE_ID, filter: ["has", "point_count"],
    layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 11 },
    paint: { "text-color": "#fff" },
  });
  map.addLayer({
    id: LAYER_IDS[2], type: "circle", source: SOURCE_ID, filter: ["!", ["has", "point_count"]],
    paint: { "circle-color": ["get", "color"], "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 3, 10, 6, 14, 9],
      "circle-stroke-color": "#fff", "circle-stroke-width": 1 },
  });
  on("click", LAYER_IDS[0], async (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: [LAYER_IDS[0]] })[0];
    if (!f) return;
    const zoom = await map.getSource(SOURCE_ID).getClusterExpansionZoom(f.properties.cluster_id);
    map.easeTo({ center: f.geometry.coordinates, zoom });
  });
  on("click", LAYER_IDS[2], (e) => {
    const f = e.features[0];
    const p = f.properties;
    const label = (SOURCE_STYLE[p.source] || {}).label || p.source;
    const period = p.period_start ? `${p.period_start} → ${p.period_end || "present"}` : "";
    const vars = Array.isArray(p.variables) ? p.variables.join(", ") : String(p.variables || "").replace(/[\[\]"]/g, "");
    const html = `<div class="aq-gauges-popup"><strong>${esc(p.name || p.station_id)}</strong>` +
      `<span class="aq-muted">${esc(label)} · ${esc(p.station_id)}<br>${esc(vars)}${period ? " · " + esc(period) : ""}</span><br>` +
      `<a href="${EXPLORER_URL}#s=${encodeURIComponent(p.key)}" target="_blank" rel="noopener">Record + flood frequency ↗</a>` +
      (p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">Agency page ↗</a>` : "") + `</div>`;
    popup?.remove();
    const maplibre = globalThis.maplibregl;
    if (maplibre?.Popup) {
      popup = new maplibre.Popup({ offset: 8, maxWidth: "320px" }).setLngLat(f.geometry.coordinates).setHTML(html).addTo(map);
    } else {
      window.open(`${EXPLORER_URL}#s=${encodeURIComponent(p.key)}`, "_blank", "noopener");
    }
  });
  on("mouseenter", LAYER_IDS[2], () => { map.getCanvas().style.cursor = "pointer"; });
  on("mouseleave", LAYER_IDS[2], () => { map.getCanvas().style.cursor = ""; });
  appApi?.registerExternalNativeLayer?.({
    id: SOURCE_ID, name: "AquaScope gauges", nativeLayerIds: LAYER_IDS, sourceIds: [SOURCE_ID], opacity: 1,
    style: { circleRadius: 5, fillColor: "#1565c0" },
    metadata: { source: CATALOG_URL, count: gj.features.length, licence: "per-source open licences (see dataset card)" },
  });
}

function removeLayers() {
  if (!map) return;
  for (const [type, layer, fn] of handlers.splice(0)) map.off(type, layer, fn);
  popup?.remove(); popup = null;
  for (const id of LAYER_IDS) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
  appApi?.unregisterExternalNativeLayer?.(SOURCE_ID);
}

async function setActive(next) {
  active = next;
  button?.classList.toggle("is-active", active);
  if (active) {
    try { await addLayers(); } catch (err) { console.error("AquaScope gauges: catalog failed", err); active = false; button?.classList.remove("is-active"); }
  } else {
    removeLayers();
  }
  renderPanelBody?.();
}

let renderPanelBody = null;

function panel(container) {
  const wrap = document.createElement("div");
  wrap.className = "aq-gauges-panel";
  container.appendChild(wrap);
  renderPanelBody = () => {
    wrap.textContent = "";
    const h = document.createElement("h2"); h.textContent = "AquaScope gauges"; wrap.appendChild(h);
    const p = document.createElement("p");
    p.innerHTML = active
      ? `Showing <b>${Object.values(counts).reduce((a, b) => a + b, 0).toLocaleString()}</b> stations. Click one for its record.`
      : "Click the droplet control (top-right) or the button below to load every public gauge AquaScope can reach.";
    wrap.appendChild(p);
    const tog = document.createElement("button"); tog.textContent = active ? "Hide gauges" : "Show gauges";
    tog.addEventListener("click", () => setActive(!active)); wrap.appendChild(tog);
    if (active) {
      const ul = document.createElement("ul");
      for (const src of Object.keys(counts).sort((a, b) => counts[b] - counts[a])) {
        const li = document.createElement("li");
        const dot = document.createElement("i"); dot.style.background = (SOURCE_STYLE[src] || {}).color || FALLBACK;
        li.appendChild(dot); li.append(` ${(SOURCE_STYLE[src] || {}).label || src} `);
        const n = document.createElement("span"); n.className = "aq-muted"; n.textContent = counts[src].toLocaleString(); li.appendChild(n);
        ul.appendChild(li);
      }
      wrap.appendChild(ul);
      const input = document.createElement("input"); input.placeholder = "Search station name or id…"; wrap.appendChild(input);
      const hits = document.createElement("div"); hits.className = "aq-hits"; wrap.appendChild(hits);
      input.addEventListener("input", () => {
        const q = input.value.trim().toLowerCase(); hits.textContent = "";
        if (q.length < 2 || !geojson) return;
        let n = 0;
        for (const f of geojson.features) {
          const pr = f.properties;
          if ((pr.name && pr.name.toLowerCase().includes(q)) || pr.station_id.toLowerCase().includes(q)) {
            const d = document.createElement("div"); d.textContent = `${pr.name || pr.station_id} · ${(SOURCE_STYLE[pr.source] || {}).label || pr.source}`;
            d.addEventListener("click", () => map?.flyTo({ center: f.geometry.coordinates, zoom: 11 }));
            hits.appendChild(d);
            if (++n >= 20) break;
          }
        }
      });
    }
    const foot = document.createElement("p"); foot.className = "aq-muted";
    foot.innerHTML = `Data: <a href="https://huggingface.co/datasets/Rekin226/aquascope-gauges" target="_blank" rel="noopener">AquaScope Archive</a> (GeoParquet, weekly). Analyses: <a href="${EXPLORER_URL}" target="_blank" rel="noopener">AquaScope Explorer</a>. Per-source open licences.`;
    wrap.appendChild(foot);
  };
  renderPanelBody();
  return () => { wrap.remove(); renderPanelBody = null; };
}

const control = {
  _container: null,
  onAdd(m) {
    map = m;
    const container = document.createElement("div");
    container.className = "maplibregl-ctrl maplibregl-ctrl-group aq-gauges-control";
    button = document.createElement("button");
    button.type = "button";
    button.title = "AquaScope gauges: toggle every public water gauge";
    button.setAttribute("aria-label", "Toggle AquaScope gauges layer");
    button.textContent = "💧";
    button.addEventListener("click", () => setActive(!active));
    container.appendChild(button);
    this._container = container;
    return container;
  },
  onRemove() {
    removeLayers();
    this._container?.remove();
    this._container = null;
    map = null;
  },
};

export const plugin = {
  id: PLUGIN_ID,
  name: "AquaScope Gauges",
  version: VERSION,
  activate(app) {
    appApi = app;
    app.addMapControl(control, "top-right");
    const dispose = app.registerRightPanel?.({ id: PANEL_ID, title: "AquaScope gauges", defaultWidth: 320, render: panel });
    if (typeof dispose === "function") disposers.push(dispose);
  },
  deactivate(app) {
    for (const d of disposers.splice(0)) { try { d(); } catch (e) { console.error(e); } }
    removeLayers();
    app.removeMapControl(control);
    appApi = null;
  },
};
export default plugin;
