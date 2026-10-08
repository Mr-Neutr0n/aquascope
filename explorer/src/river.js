// The River tab (#516): a click (or a gauge) snapped to its GEOGLOWS v2 river
// reach, the reach's 86 years of simulated daily flow analysed like a gauge,
// and the trace to the sea. Every number comes from aquascope.rivers in the
// worker; this module asks for it and lays it out.

import { $, actions, escapeHtml, fmt, sourceStyle, state, stationKey } from "./core.js?v=__BUILD__";
import { addTableDownload, plot } from "./charts.js?v=__BUILD__";
import { addMethodOnce } from "./methods.js?v=__BUILD__";
import { setPointMarker } from "./map.js?v=__BUILD__";
import { clearRiverTrace, drawRiverTrace, setRiversVisible } from "./river-map.js?v=__BUILD__";
import { RECORD_CREDIT, snapLine } from "./river-core.js?v=__BUILD__";
import { setCard, setTab } from "./shell.js?v=__BUILD__";
import { call, ensureCatalogInWorker } from "./worker-client.js?v=__BUILD__";

const TARGETS = {
  pt: { panel: "panel-point", methods: "pt-methods" },
  st: { panel: "panel-station", methods: "methods" },
};
const runs = { pt: { run: 0 }, st: { run: 0 } };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const panel = (t) => $(TARGETS[t].panel);
const box = (t) => $(`${t}-river`);
const part = (t, cls) => box(t) && box(t).querySelector(`.${cls}`);

function skeleton(t) {
  const el = box(t);
  if (!el) return;
  el.innerHTML = `
    <p class="river-snap" role="status"></p>
    <div class="card river-record" hidden>
      <div class="card-title">
        <h3>Simulated river flow <span class="muted">GEOGLOWS v2, modelled, not a gauge</span></h3>
        <span class="card-tools river-tools"></span>
      </div>
      <div class="card-body">
        <div class="kpis river-kpis"></div>
        <div id="plot-${t}-river" class="plot"></div>
        <h4>Return periods <span class="muted river-ffa-n"></span></h4>
        <table class="ffa river-ffa"></table>
        <h4>Flow duration and the year</h4>
        <div id="plot-${t}-river-fdc" class="plot"></div>
        <div id="plot-${t}-river-regime" class="plot"></div>
        <p class="basin-foot muted river-foot"></p>
      </div>
    </div>
    <div class="card river-trace" hidden>
      <div class="card-title"><h3>To the sea</h3></div>
      <div class="card-body">
        <button type="button" class="btn river-trace-btn">Trace to the sea</button>
        <div class="river-trace-out"></div>
      </div>
    </div>`;
  part(t, "river-trace-btn").addEventListener("click", () => traceToSea(t));
}

// Snap the place (a click, or a gauge's position) to its reach. Returns the snap promise, which the
// catchment card waits on so a hillside is not described as the river below it.
export function startRiver(t, lat, lon, { gauge = false } = {}) {
  const r = runs[t];
  const my = ++r.run;
  Object.assign(r, { lat, lon, gauge, snap: null, reach: null, record: null, recordFor: null });
  clearRiverTrace();
  skeleton(t);
  const head = t === "pt" ? $("pt-snap") : null;
  if (head) { head.hidden = true; head.textContent = ""; }
  setTab(panel(t), "river", { enabled: false, reason: "Finding the river…" });
  const promise = call("river", { op: "snap", args: { lat, lon } }).then((snap) => {
    if (my !== r.run) return snap;
    r.snap = snap;
    const line = snapLine(snap, { gauge });
    const p = part(t, "river-snap");
    // A point's snap is said once, under its title; the tab repeats it only when there is a choice to make.
    if (p && (!head || !snap.snapped)) p.textContent = line;
    if (head) { head.textContent = line; head.hidden = !line; }
    if (snap.snapped) {
      useReach(t, { river_id: snap.river_id, lat: snap.snap_lat, lon: snap.snap_lon });
    } else if (snap.nearest && p) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn small";
      b.textContent = "Use the nearest reach";
      b.addEventListener("click", () => {
        b.remove();
        useReach(t, { river_id: snap.nearest.river_id, lat: snap.nearest.lat, lon: snap.nearest.lon, chosen: true });
      });
      p.append(" ", b);
    }
    setTab(panel(t), "river", { enabled: true });
    return snap;
  }).catch((err) => {
    if (my === r.run) {
      setTab(panel(t), "river", { enabled: false, reason: `Could not read the river network (${err.message}).` });
    }
    return null;
  });
  return promise;
}

function useReach(t, reach) {
  const r = runs[t];
  r.reach = reach;
  // The marker moves to the river it now stands for; the address keeps the click.
  if (t === "pt" && Number.isFinite(reach.lat) && Number.isFinite(reach.lon)) setPointMarker(reach.lat, reach.lon);
  if (reach.chosen) {
    const p = part(t, "river-snap");
    if (p) p.textContent = `Using river reach ${reach.river_id}, the nearest one mapped.`;
  }
  const trace = part(t, "river-trace");
  if (trace) { trace.hidden = false; trace.dataset.state = "ready"; }
  if (isRiverTabShown(t) || reach.chosen) loadRecord(t);
}

function isRiverTabShown(t) {
  const tab = panel(t) && panel(t).querySelector('[role="tab"][data-tab="river"]');
  return Boolean(tab && tab.getAttribute("aria-selected") === "true");
}

async function loadRecord(t) {
  const r = runs[t];
  const reach = r.reach;
  if (!reach || r.recordFor === reach.river_id) return;
  r.recordFor = reach.river_id;
  const my = r.run;
  const card = part(t, "river-record");
  setCard(card, "loading", { message: "Asking GEOGLOWS for 86 years of simulated daily flow (10 to 20 s)…" });
  try {
    const res = await call("river", { op: "record", args: { river_id: reach.river_id } });
    if (my !== r.run) return;
    if (res.error) {
      r.recordFor = null;
      setCard(card, "empty", { message: res.error });
      return;
    }
    r.record = res;
    renderRecord(t, res);
  } catch (err) {
    if (my !== r.run) return;
    r.recordFor = null;
    console.warn("GEOGLOWS record:", err && err.message);
    setCard(card, "error", { message: "GEOGLOWS did not answer this time; it is slow now and then.", retry: () => loadRecord(t) });
  }
}

function renderRecord(t, res) {
  const card = part(t, "river-record");
  const st = res.stats || {};
  const fdc = res.fdc || {};
  part(t, "river-kpis").innerHTML = [
    ["mean", `${fmt(st.mean)} m³/s`, `${res.start} to ${res.end}`],
    ["Q95 (low)", `${fmt(fdc.q95)} m³/s`, "exceeded 95 % of days"],
    ["Q10 (high)", `${fmt(fdc.q10)} m³/s`, "exceeded 10 % of days"],
    ["record max", `${fmt(st.max)} m³/s`, `${fmt(res.years, 0)} simulated years`],
  ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${escapeHtml(s)}</div></div>`).join("");
  setCard(card, "ready");

  const traces = [];
  if (res.series && res.series.t) {
    traces.push({ x: res.series.t, y: res.series.v, type: "scatter", mode: "lines", name: "daily (simulated)",
      line: { width: 1, color: "#1e88e5" }, hovertemplate: "%{x}<br>%{y:.3~f} m³/s<extra></extra>" });
  }
  if (res.annual_max && res.annual_max.year && res.annual_max.year.length > 1) {
    traces.push({ x: res.annual_max.year.map((y) => `${y}-07-01`), y: res.annual_max.v, type: "scatter", mode: "markers",
      name: "annual maximum", marker: { size: 4, color: "#0d47a1" }, hovertemplate: "%{x|%Y}: %{y:.3~f} m³/s<extra></extra>" });
  }
  plot(`plot-${t}-river`, traces, { height: 230, yaxis: { title: { text: "m³/s" } }, legend: { orientation: "h", y: 1.15 } },
    `river-${res.river_id}-simulated`);

  const table = part(t, "river-ffa");
  const ffa = res.ffa;
  if (ffa && ffa.fits) {
    const rps = ffa.return_periods, gv = ffa.fits.gev_lmoments || {}, lp = ffa.fits.lp3 || {};
    part(t, "river-ffa-n").textContent = `${ffa.n_years} simulated annual maxima, m³/s`;
    table.innerHTML = `<thead><tr><th>T (yr)</th><th>GEV L-moments</th><th>LP3 (90 % CI)</th></tr></thead><tbody>` +
      rps.map((rp, i) => `<tr><td>${rp}</td><td>${gv.q ? fmt(gv.q[i]) : "—"}</td>` +
        `<td>${lp.q ? `${fmt(lp.q[i])} <span class="ci">[${fmt(lp.ci[i][0])}, ${fmt(lp.ci[i][1])}]</span>` : "—"}</td></tr>`).join("") +
      "</tbody>";
    const tools = part(t, "river-tools");
    tools.replaceChildren();
    addTableDownload(tools, table, `river-${res.river_id}-return-periods.csv`);
  } else {
    part(t, "river-ffa-n").textContent = "";
    table.innerHTML = `<tbody><tr><td class="muted">Too few complete years for a frequency fit.</td></tr></tbody>`;
  }

  if (fdc.exceedance && fdc.q) {
    plot(`plot-${t}-river-fdc`, [{ x: fdc.exceedance, y: fdc.q, mode: "lines", line: { color: "#1e88e5", width: 2 },
      hovertemplate: "%{x:.1f} % of days<br>%{y:.3~f} m³/s<extra></extra>" }], {
      height: 200, xaxis: { title: { text: "% of days exceeded" }, range: [0, 100] },
      yaxis: { title: { text: "m³/s" }, type: "log" }, showlegend: false,
    }, `river-${res.river_id}-flow-duration`);
  }
  const reg = res.monthly_regime;
  if (reg && reg.mean) {
    plot(`plot-${t}-river-regime`, [
      { x: MONTHS, y: reg.p90, type: "scatter", mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { x: MONTHS, y: reg.p10, type: "scatter", mode: "lines", line: { width: 0 }, fill: "tonexty",
        fillcolor: "rgba(30,136,229,0.18)", name: "10th to 90th percentile", hoverinfo: "skip" },
      { x: MONTHS, y: reg.mean, type: "scatter", mode: "lines+markers", name: "monthly mean", line: { color: "#0d47a1" },
        hovertemplate: "%{x}: %{y:.3~f} m³/s<extra></extra>" },
    ], { height: 200, yaxis: { title: { text: "m³/s" } }, legend: { orientation: "h", y: 1.2 } },
    `river-${res.river_id}-regime`);
  }
  part(t, "river-foot").textContent = `${(res.notes || [])[0] || ""} Data: ${RECORD_CREDIT}.`;
  for (const m of res.methods || []) if (m && m.name) addMethodOnce(TARGETS[t].methods, m);
}

async function traceToSea(t) {
  const r = runs[t];
  const reach = r.reach;
  if (!reach) return;
  const my = r.run;
  const btn = part(t, "river-trace-btn");
  const out = part(t, "river-trace-out");
  btn.disabled = true;
  out.innerHTML = `<p class="muted"><span class="spinner" aria-hidden="true"></span> Following the river down the network ` +
    `(reads the basin's routing tables, a few MB)…</p>`;
  try {
    await ensureCatalogInWorker();
    const res = await call("river", { op: "trace", args: { river_id: reach.river_id, lat: reach.lat, lon: reach.lon } });
    if (my !== r.run) return;
    btn.disabled = false;
    const coords = (res.geometry && res.geometry.coordinates) || [];
    if (coords.length >= 2) {
      if (!state.riversOn) setRiversVisible(true);
      drawRiverTrace(coords);
    }
    renderTrace(t, res);
  } catch (err) {
    if (my !== r.run) return;
    btn.disabled = false;
    out.innerHTML = `<p class="status error">Could not trace this river: ${escapeHtml(err.message)}</p>`;
  }
}

function renderTrace(t, res) {
  const out = part(t, "river-trace-out");
  const area = res.upstream_area_km2;
  const gauges = (res.gauges || []).filter((g) => state.byKey.has(`${g.source}/${g.station_id}`));
  const shown = gauges.slice(0, 12);
  const list = shown.map((g, i) => {
    const s = sourceStyle(g.source);
    return `<li><button type="button" class="nearest-open" data-i="${i}">` +
      `<span class="nearest-name">${escapeHtml(g.name || g.station_id)}</span><span class="muted">${escapeHtml(s.label)}</span>` +
      `<span class="dist">km ${fmt(g.along_km, 0)}</span></button></li>`;
  }).join("");
  out.innerHTML =
    `<p>${escapeHtml(res.message || "")}</p>` +
    (area !== null && area !== undefined ? `<p class="muted">About ${fmt(area, 0)} km² drain to the starting reach.</p>` : "") +
    (shown.length ? `<h4>Gauges on the way</h4><ol class="nearest river-gauges">${list}</ol>` : "") +
    (gauges.length > shown.length ? `<p class="muted">and ${gauges.length - shown.length} more further down.</p>` : "") +
    ((res.notes || []).length ? `<details><summary class="muted">Notes</summary><ul class="muted">${
      res.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul></details>` : "") +
    `<p class="basin-foot muted">Path: TDX-Hydro (NGA) via GEOGLOWS v2, CC BY-SA 4.0, drawn here and not republished. ` +
    `Dams on the path come later.</p>`;
  out.querySelectorAll(".river-gauges button").forEach((b) => {
    const g = shown[Number(b.dataset.i)];
    b.addEventListener("click", () => actions.selectStation(stationKey(g), { fly: true }));
  });
}

export function clearRiver() {
  for (const t of Object.keys(runs)) runs[t].run++;
  clearRiverTrace();
}

export function initRiver() {
  for (const t of Object.keys(TARGETS)) {
    const root = panel(t);
    if (!root) continue;
    root.addEventListener("tabchange", (e) => {
      if (e.detail.tab !== "river") return;
      loadRecord(t);
      for (const id of [`plot-${t}-river`, `plot-${t}-river-fdc`, `plot-${t}-river-regime`]) {
        const el = $(id);
        if (el && el.offsetParent !== null && el.data) Plotly.Plots.resize(el);
      }
    });
  }
  const toggle = $("toggle-rivers");
  if (toggle) toggle.checked = Boolean(state.riversOn);
}
