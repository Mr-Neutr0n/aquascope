// node --test explorer/tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  cumulativeKm, damFacts, damName, damsGeoJSON, lineBounds, lineUpTo, notableDams, riverWidth, snapLine, STREAMS_PMTILES,
} from "../src/river-core.js";

test("snapLine says where the click landed, or that no stream is near", () => {
  assert.equal(snapLine({ snapped: true, river_id: 230260670, distance_m: 199.8, strahler_order: 5 }),
    "Snapped 200 m to river reach 230260670, stream order 5.");
  assert.equal(snapLine({ snapped: true, river_id: 1, distance_m: 1500 }, { gauge: true }),
    "This gauge is 1.5 km from river reach 1.");
  assert.equal(snapLine({ snapped: false, max_distance_m: 1000, nearest: { river_id: 2, distance_m: 1432 } }),
    "No stream within 1 km. The nearest mapped reach is 1.4 km away.");
  assert.equal(snapLine({ snapped: false, max_distance_m: 200, searched_m: 3000, nearest: null }),
    "No stream within 3 km of this point.");
  assert.equal(snapLine(null), "");
});

test("snapLine says when the main channel won over a nearer stream, and names a larger river further off", () => {
  const near = { river_id: 9, distance_m: 60, strahler_order: 2 };
  assert.equal(snapLine({ snapped: true, river_id: 3, distance_m: 380, strahler_order: 9, choice: "main_channel", nearer: near }),
    "Snapped 380 m to the main channel (order 9); a smaller stream is 60 m away.");
  assert.equal(snapLine({ snapped: true, river_id: 3, distance_m: 380, strahler_order: 9, choice: "area", nearer: near }, { gauge: true }),
    "This gauge is 380 m from river reach 3, stream order 9, the one whose upstream area matches the catchment (the nearest line is 60 m away).");
  assert.equal(snapLine({ snapped: true, river_id: 9, distance_m: 300, strahler_order: 2, choice: "nearest",
    larger: { river_id: 3, distance_m: 1340, strahler_order: 8 } }),
  "Snapped 300 m to river reach 9, stream order 2. A larger river (order 8) is 1.3 km away.");
  assert.equal(snapLine({ snapped: false, max_distance_m: 1000, nearest: { river_id: 2, distance_m: 1432 },
    larger: { river_id: 3, distance_m: 2100, strahler_order: 8 } }),
  "No stream within 1 km. The nearest mapped reach is 1.4 km away. A larger river (order 8) is 2.1 km away.");
});

test("lineUpTo grows the trace by distance, not by vertex", () => {
  const line = [[0, 0], [0, 1], [0, 1.001], [0, 1.002], [0, 2]];
  assert.deepEqual(lineUpTo(line, 0), [[0, 0], [0, 0]]);
  assert.deepEqual(lineUpTo(line, 1), line);
  const half = lineUpTo(line, 0.5);
  const end = half[half.length - 1];
  assert.ok(Math.abs(end[1] - 1.0) < 0.002, `half way is near lat 1, got ${end[1]}`);
  assert.ok(half.length <= 3);
  assert.deepEqual(lineUpTo([[1, 1]], 0.5), [[1, 1]]);
});

test("cumulativeKm and lineBounds", () => {
  const c = cumulativeKm([[0, 0], [0, 1], [1, 1]]);
  assert.equal(c.length, 3);
  assert.ok(Math.abs(c[1] - 111.19) < 0.1);
  assert.deepEqual(lineBounds([[3, -1], [-2, 4], [0, 0]]), [-2, -1, 3, 4]);
  assert.equal(lineBounds([]), null);
});

test("the stream layer is styled by Strahler order and points at the GEOGLOWS bucket", () => {
  const w = riverWidth();
  assert.equal(w[0], "interpolate");
  assert.ok(JSON.stringify(w).includes("strahlerOrder"));
  assert.match(STREAMS_PMTILES, /^https:\/\/geoglows-v2\.s3\.us-west-2\.amazonaws\.com\/.+streams\.pmtiles$/);
});

test("a dam is named by GDW, else by its reservoir, else plainly", () => {
  assert.equal(damName({ name: "Muehleberg" }), "Muehleberg");
  assert.equal(damName({ name: "unnamed", reservoir: "Luzern" }), "Luzern dam");
  assert.equal(damName({ name: null, reservoir: null }), "Unnamed dam");
  assert.equal(damName(null), "");
});

test("damFacts says the storage and the main use in a few words", () => {
  assert.equal(damFacts({ capacity_mcm: 2550, purpose: "Hydroelectricity" }), "2,550 million m³, hydroelectricity");
  assert.equal(damFacts({ capacity_mcm: 9.62 }), "9.6 million m³");
  assert.equal(damFacts({ capacity_mcm: null, purpose: null }), "");
  assert.equal(damFacts({ capacity_mcm: 0, purpose: "Irrigation" }), "irrigation");
});

test("damsGeoJSON keeps each dam's place in the list and skips the unplaced", () => {
  const fc = damsGeoJSON([{ name: "A", lat: 46.9, lon: 7.4 }, { name: "B", lat: null, lon: 7 }, { name: "C", lat: 47, lon: 8 }]);
  assert.equal(fc.type, "FeatureCollection");
  assert.deepEqual(fc.features.map((f) => f.properties.i), [0, 2]);
  assert.deepEqual(fc.features[1].geometry.coordinates, [8, 47]);
  assert.equal(damsGeoJSON(undefined).features.length, 0);
});

test("notableDams lists the named or storing dams and counts the unnamed weirs", () => {
  const dams = [{ name: "unnamed" }, { name: "Muehleberg", capacity_mcm: 25 }, { name: "unnamed", capacity_mcm: 9.6 },
    { name: "unnamed", reservoir: "Luzern" }, { name: "unnamed", capacity_mcm: null }];
  const { notable, others } = notableDams(dams);
  assert.equal(notable.length, 3);
  assert.equal(others, 2);
  assert.deepEqual(notableDams(undefined), { notable: [], others: 0 });
});
