// Rivers as objects (#516), the pure part: no DOM, no map, so node can test it.
// What a river reach is, its record and its path to the sea all come from
// Python (aquascope.rivers); this module only decides how they are drawn and
// said: the stream-order styling, the snap sentence, and the growing line of
// the trace animation.

// The GEOGLOWS v2 global stream network, read in place by range requests
// (2.4 GB, CORS open). TDX-Hydro geometry, CC BY-SA 4.0: shown, never republished.
export const STREAMS_PMTILES = "https://geoglows-v2.s3.us-west-2.amazonaws.com/hydrography-global/streams.pmtiles";
export const RIVERS_CREDIT = {
  label: "Rivers",
  attribution: 'GEOGLOWS v2 stream network from <a href="https://registry.opendata.aws/geoglows-v2/">TDX-Hydro</a> (NGA)',
  licence: "CC BY-SA 4.0, shown and not republished",
};
export const RECORD_CREDIT = "GEOGLOWS v2 retrospective simulation (GEOGloWS ECMWF Streamflow Service), CC BY 4.0";

// Line width by Strahler order, growing with zoom: big rivers read at a
// continent's scale, the headwater streams only once you are close.
export function riverWidth() {
  return ["interpolate", ["linear"], ["zoom"],
    3, ["interpolate", ["linear"], ["coalesce", ["get", "strahlerOrder"], 1], 1, 0.2, 6, 0.8, 10, 2.4],
    8, ["interpolate", ["linear"], ["coalesce", ["get", "strahlerOrder"], 1], 1, 0.4, 6, 1.6, 10, 4],
    12, ["interpolate", ["linear"], ["coalesce", ["get", "strahlerOrder"], 1], 1, 1, 6, 3, 10, 6],
  ];
}

// The tiles hold orders 6 and up at every zoom, 4 and up from zoom 6 and all
// from zoom 8; the layer starts at 3 so the world view is not a mesh of lines.
export const RIVERS_MINZOOM = 3;

function distanceText(m) {
  const n = Number(m);
  if (!Number.isFinite(n)) return "";
  return n < 1000 ? `${Math.round(n)} m` : `${Number((n / 1000).toFixed(1))} km`;
}

// One plain sentence for the snap, whichever way it went.
export function snapLine(snap, { gauge = false } = {}) {
  if (!snap) return "";
  if (snap.snapped) {
    const order = snap.strahler_order ? `, stream order ${snap.strahler_order}` : "";
    return gauge
      ? `This gauge is ${distanceText(snap.distance_m)} from river reach ${snap.river_id}${order}.`
      : `Snapped ${distanceText(snap.distance_m)} to river reach ${snap.river_id}${order}.`;
  }
  const tol = distanceText(snap.max_distance_m);
  if (snap.nearest) return `No stream within ${tol}. The nearest mapped reach is ${distanceText(snap.nearest.distance_m)} away.`;
  return `No stream within ${distanceText(snap.searched_m || snap.max_distance_m)} of this point.`;
}

function segKm(a, b) {
  const d2r = Math.PI / 180;
  const dLat = (b[1] - a[1]) * d2r, dLon = (b[0] - a[0]) * d2r;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * d2r) * Math.cos(b[1] * d2r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(Math.min(1, s)));
}

// Cumulative distance along a line, km, one entry per vertex.
export function cumulativeKm(coords) {
  const out = [0];
  for (let i = 1; i < (coords || []).length; i++) out.push(out[i - 1] + segKm(coords[i - 1], coords[i]));
  return out;
}

// The part of a line from its start to `fraction` of its length, with the
// last point interpolated: the trace grows at an even speed along the river,
// not vertex by vertex (vertices are dense in bends and sparse on the plains).
export function lineUpTo(coords, fraction, cum = null) {
  if (!coords || coords.length < 2) return coords ? coords.slice() : [];
  const f = Math.max(0, Math.min(1, Number(fraction) || 0));
  if (f >= 1) return coords.slice();
  const c = cum || cumulativeKm(coords);
  const target = c[c.length - 1] * f;
  let i = 1;
  while (i < c.length && c[i] < target) i++;
  if (i >= c.length) return coords.slice();
  const span = c[i] - c[i - 1];
  const t = span > 0 ? (target - c[i - 1]) / span : 0;
  const a = coords[i - 1], b = coords[i];
  return [...coords.slice(0, i), [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]];
}

// [west, south, east, north] of a line, for fitting the map to it.
export function lineBounds(coords) {
  if (!coords || !coords.length) return null;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [x, y] of coords) {
    if (x < w) w = x;
    if (x > e) e = x;
    if (y < s) s = y;
    if (y > n) n = y;
  }
  return [w, s, e, n];
}
