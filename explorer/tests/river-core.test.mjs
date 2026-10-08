// node --test explorer/tests/
import test from "node:test";
import assert from "node:assert/strict";

import { cumulativeKm, lineBounds, lineUpTo, riverWidth, snapLine, STREAMS_PMTILES } from "../src/river-core.js";

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
