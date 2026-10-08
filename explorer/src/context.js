// Place context (#520): the Context tab of a clicked point and the Context
// lines of a drawn box. The engine is aquascope.context in the worker, asked
// one layer at a time so the card fills line by line; this module only lays
// the answers out, draws one small chart and puts flood events on the map.

import { $, EMPTY_FC, escapeHtml, state } from "./core.js?v=__BUILD__";
import { plot } from "./charts.js?v=__BUILD__";
import { map } from "./map.js?v=__BUILD__";
import { setCard } from "./shell.js?v=__BUILD__";
import { call } from "./worker-client.js?v=__BUILD__";
import {
  CONTEXT_LAYERS, boxProblem, contextLine, credits, floodPoints, normaliseBox, pickChart,
} from "./context-view.js?v=__BUILD__";

const SOURCE = "context-floods";
const LAYER = "context-floods";
let pointKey = null;   // the point whose context is loaded (or loading)
let areaRun = 0;

// ── flood events on the map ─────────────────────────────────────────────────

let shown = EMPTY_FC;
let watching = false;

function drawFloods(fc) {
  shown = fc || EMPTY_FC;
  if (!state.mapOk || !map) return;
  if (!map.getSource(SOURCE)) {
    if (!shown.features.length) return;
    map.addSource(SOURCE, { type: "geojson", data: shown });
    map.addLayer({
      id: LAYER, type: "circle", source: SOURCE,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2.5, 10, 6],
        "circle-color": "#ef6c00", "circle-opacity": 0.75,
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 0.8,
      },
    });
  } else {
    map.getSource(SOURCE).setData(shown);
  }
  if (!watching) {
    watching = true;
    // A basemap change replaces the style; put the events back.
    map.on("styledata", () => {
      if (shown.features.length && !map.getSource(SOURCE)) {
        try { drawFloods(shown); } catch (err) { console.info("context floods:", err && err.message); }
      }
    });
  }
}

export function clearContextFloods() { drawFloods(EMPTY_FC); }

// ── shared rendering ────────────────────────────────────────────────────────

function lineHtml(line) {
  const cls = `ctx-line ${line.state}`;
  return `<li class="${cls}"><span class="ctx-label">${escapeHtml(line.label)}</span>` +
    `<span class="ctx-text">${line.state === "loading" ? '<span class="spinner" aria-hidden="true"></span>' : ""}` +
    `${escapeHtml(line.text)}</span></li>`;
}

// Short names on the line, the full attribution one tap away (and in the tooltip).
function creditHtml(results) {
  const list = credits(results);
  if (!list.length) return "";
  const names = list.map((c) => `<a href="${escapeHtml(c.homepage || "#")}" target="_blank" rel="noopener" ` +
    `title="${escapeHtml(c.attribution)}">${escapeHtml(c.short)}</a> (${escapeHtml(c.licence)})`).join(" · ");
  const full = list.map((c) => `<li>${escapeHtml(c.attribution)}, ${escapeHtml(c.licence)}</li>`).join("");
  return `Data: ${names}<details class="ctx-attrib"><summary>Attribution</summary><ul>${full}</ul></details>`;
}

// Ask for every layer in turn and redraw after each. `stillWanted()` false
// means the reader moved on: stop asking and drop what arrives.
async function fill(results, ask, render, stillWanted) {
  for (const { id } of CONTEXT_LAYERS) {
    if (!stillWanted()) return;
    try {
      results[id] = await ask(id);
    } catch (err) {
      results[id] = { error: err && err.message ? err.message : String(err) };
    }
    if (!stillWanted()) return;
    render();
  }
}

// ── the point's Context tab ─────────────────────────────────────────────────

export function resetPointContext() {
  pointKey = null;
  const card = $("pt-context");
  if (card) card.hidden = true;
  clearContextFloods();
}

export async function loadPointContext(lat, lon) {
  const key = `${lat},${lon}`;
  if (pointKey === key) return;
  pointKey = key;
  const card = $("pt-context");
  const list = $("pt-context-lines");
  const foot = $("pt-context-credits");
  const chartWrap = $("pt-context-chart");
  const results = {};
  const render = () => {
    list.innerHTML = CONTEXT_LAYERS.map(({ id }) => lineHtml(contextLine(id, results[id]))).join("");
    foot.innerHTML = creditHtml(results);
    const chart = pickChart(results);
    chartWrap.hidden = !chart;
    if (chart) {
      $("pt-context-chart-title").textContent = chart.what[0].toUpperCase() + chart.what.slice(1);
      plot("plot-context", [{ x: chart.x, y: chart.y, type: "bar", name: chart.unit, marker: { color: "#1565c0" } }],
        { height: 150, margin: { l: 36, r: 8, t: 4, b: 28 }, yaxis: { title: { text: chart.unit } }, bargap: 0.25 },
        `context-${lat}-${lon}`);
    }
    if (results.flood_history) drawFloods(floodPoints(results.flood_history));
  };
  setCard(card, "ready");
  render();
  await fill(results, (id) => call("context", { op: "point", name: id, lat, lon }), render,
    () => pointKey === key && state.point && state.point.lat === lat && state.point.lon === lon);
}

// ── a drawn box ─────────────────────────────────────────────────────────────

export async function openAreaContext(drawn, host) {
  const my = ++areaRun;
  const bbox = normaliseBox(drawn);
  const box = document.createElement("div");
  box.className = "area-context";
  host.querySelectorAll(".area-context").forEach((el) => el.remove());
  host.appendChild(box);
  const problem = boxProblem(bbox);
  if (problem) {
    box.innerHTML = `<p class="muted">${escapeHtml(problem)}</p>`;
    return;
  }
  const results = {};
  const render = () => {
    box.innerHTML = `<ul class="ctx-lines">${CONTEXT_LAYERS.map(({ id }) => lineHtml(contextLine(id, results[id]))).join("")}</ul>` +
      `<div class="ctx-credits muted">${creditHtml(results)}</div>`;
    if (results.flood_history) drawFloods(floodPoints(results.flood_history));
  };
  render();
  const b = [bbox.west, bbox.south, bbox.east, bbox.north];
  await fill(results, (id) => call("context", { op: "area", name: id, bbox: b }), render,
    () => my === areaRun && box.isConnected);
}

export function cancelAreaContext() {
  areaRun++;
  clearContextFloods();
}
