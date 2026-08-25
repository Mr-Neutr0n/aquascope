// GR4J (Perrin, Michel, Andreassian 2003) and a small differential-evolution
// calibrator, in plain JavaScript so the Explorer can calibrate a rainfall-runoff
// model on 40 years of daily data in a couple of seconds, in the page.
//
// Same arithmetic as aquascope.models.rainfall_runoff.GR4J (checked to round-off
// by tests/test_models/test_gr4j_js.py when node is available): production store
// (initial 0.3 X1), percolation, unit hydrographs from the S-curves (exponent 2.5,
// UH1 over ceil(X4) days for 90 % of Pr, UH2 over ceil(2 X4) days for 10 %),
// groundwater exchange X2 (R/X3)^3.5, routing store (initial 0.3 X3).
// Units: precip and pet in mm/d, streamflow in mm/d.
//
// Loads as a classic script (window.GR4J) and as an ES/CommonJS module.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.GR4J = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const BOUNDS = { X1: [1, 1500], X2: [-10, 5], X3: [1, 500], X4: [0.5, 10] };
  const NAMES = ["X1", "X2", "X3", "X4"];

  function unitHydrographs(x4) {
    const s1 = (t) => (t <= 0 ? 0 : t >= x4 ? 1 : Math.pow(t / x4, 2.5));
    const s2 = (t) => (t <= 0 ? 0 : t >= 2 * x4 ? 1 : t <= x4 ? 0.5 * Math.pow(t / x4, 2.5) : 1 - 0.5 * Math.pow(2 - t / x4, 2.5));
    const n1 = Math.ceil(x4), n2 = Math.ceil(2 * x4);
    const uh1 = new Float64Array(n1), uh2 = new Float64Array(n2);
    let sum1 = 0, sum2 = 0;
    for (let t = 1; t <= n1; t++) { uh1[t - 1] = Math.max(0, s1(t) - s1(t - 1)); sum1 += uh1[t - 1]; }
    for (let t = 1; t <= n2; t++) { uh2[t - 1] = Math.max(0, s2(t) - s2(t - 1)); sum2 += uh2[t - 1]; }
    if (sum1 > 0) for (let i = 0; i < n1; i++) uh1[i] /= sum1;
    if (sum2 > 0) for (let i = 0; i < n2; i++) uh2[i] /= sum2;
    return [uh1, uh2];
  }

  // precip, pet: arrays (mm/d). params: {X1,X2,X3,X4} or [X1,X2,X3,X4]. Returns Float64Array of streamflow (mm/d).
  function simulate(precip, pet, params) {
    const x1 = Array.isArray(params) ? params[0] : params.X1;
    const x2 = Array.isArray(params) ? params[1] : params.X2;
    const x3 = Array.isArray(params) ? params[2] : params.X3;
    const x4 = Array.isArray(params) ? params[3] : params.X4;
    const n = precip.length;
    const [uh1, uh2] = unitHydrographs(x4);
    const n1 = uh1.length, n2 = uh2.length;
    let s = 0.3 * x1, r = 0.3 * x3;
    const pr = new Float64Array(n);
    for (let t = 0; t < n; t++) {
      const p = precip[t], e = pet[t];
      let percInput;
      if (p >= e) {
        const pn = p - e, ratio = s / x1, tw = Math.tanh(pn / x1);
        const ps = (x1 * (1 - ratio * ratio) * tw) / (1 + ratio * tw);
        s += ps; percInput = pn - ps;
      } else {
        const en = e - p, ratio = s / x1, tw = Math.tanh(en / x1);
        s -= (s * (2 - ratio) * tw) / (1 + (1 - ratio) * tw); percInput = 0;
      }
      const perc = s * (1 - Math.pow(1 + Math.pow((4 / 9) * s / x1, 4), -0.25));
      s -= perc;
      pr[t] = perc + percInput;
    }
    const q = new Float64Array(n);
    // causal convolutions with ring buffers, then the routing store
    const b1 = new Float64Array(n1), b2 = new Float64Array(n2);
    let h1 = 0, h2 = 0;
    for (let t = 0; t < n; t++) {
      const v = pr[t];
      // add v * uh to the next n1 / n2 slots (buffer index (h + k) mod n)
      for (let k = 0; k < n1; k++) b1[(h1 + k) % n1] += 0.9 * v * uh1[k];
      for (let k = 0; k < n2; k++) b2[(h2 + k) % n2] += 0.1 * v * uh2[k];
      const q9 = b1[h1], q1 = b2[h2];
      b1[h1] = 0; b2[h2] = 0;
      h1 = (h1 + 1) % n1; h2 = (h2 + 1) % n2;
      const exch = x2 * Math.pow(r / x3, 3.5);
      r = r + q9 + exch; if (r < 0) r = 0;
      const qr = r * (1 - Math.pow(1 + Math.pow(r / x3, 4), -0.25));
      r -= qr;
      let qd = q1 + exch; if (qd < 0) qd = 0;
      q[t] = qr + qd;
    }
    return q;
  }

  // metrics over indices where obs is finite (mask: Uint8Array or null)
  function metrics(obs, sim, from, to) {
    let n = 0, so = 0, ss = 0;
    for (let i = from; i < to; i++) { const o = obs[i]; if (Number.isFinite(o)) { n++; so += o; ss += sim[i]; } }
    if (n < 30) return { nse: null, kge: null, log_nse: null, pbias: null, n };
    const mo = so / n, ms = ss / n;
    let sse = 0, sst = 0, cov = 0, vo = 0, vs = 0, lsse = 0, lsst = 0, lmo = 0;
    for (let i = from; i < to; i++) { const o = obs[i]; if (Number.isFinite(o)) lmo += Math.log(o + 1e-6); }
    lmo /= n;
    for (let i = from; i < to; i++) {
      const o = obs[i]; if (!Number.isFinite(o)) continue;
      const s = sim[i];
      sse += (s - o) ** 2; sst += (o - mo) ** 2;
      cov += (o - mo) * (s - ms); vo += (o - mo) ** 2; vs += (s - ms) ** 2;
      const lo = Math.log(o + 1e-6), ls = Math.log(s + 1e-6);
      lsse += (ls - lo) ** 2; lsst += (lo - lmo) ** 2;
    }
    const rr = vo > 0 && vs > 0 ? cov / Math.sqrt(vo * vs) : 0;
    const alpha = vo > 0 ? Math.sqrt(vs / n) / Math.sqrt(vo / n) : 0;
    const beta = mo !== 0 ? ms / mo : 0;
    return {
      nse: sst > 0 ? 1 - sse / sst : null,
      kge: 1 - Math.sqrt((rr - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2),
      log_nse: lsst > 0 ? 1 - lsse / lsst : null,
      pbias: so !== 0 ? 100 * (ss - so) / so : null,
      n,
    };
  }

  // Deterministic PRNG (mulberry32) so a calibration is reproducible for a seed.
  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Differential evolution (best/1/bin, F 0.7, CR 0.9) maximising `objective` (nse|kge|log_nse) over
  // [warmup, calEnd). Async: yields between generations so the page stays responsive; onProgress(gen, best).
  async function calibrate(precip, pet, obs, { objective = "kge", warmup = 365, calEnd = null, popsize = 20, generations = 40,
    seed = 1, onProgress = null, bounds = BOUNDS } = {}) {
    const end = calEnd === null ? obs.length : calEnd;
    const lo = NAMES.map((k) => bounds[k][0]), hi = NAMES.map((k) => bounds[k][1]);
    const rand = rng(seed);
    const score = (x) => { const m = metrics(obs, simulate(precip, pet, x), warmup, end)[objective]; return m === null ? -1e9 : m; };
    let pop = [], fit = [];
    for (let i = 0; i < popsize; i++) {
      const x = lo.map((l, d) => l + rand() * (hi[d] - l));
      pop.push(x); fit.push(score(x));
    }
    let bi = fit.indexOf(Math.max(...fit));
    const history = [{ gen: 0, best: fit[bi], sims: popsize }];
    let sims = popsize;
    for (let g = 1; g <= generations; g++) {
      for (let i = 0; i < popsize; i++) {
        let a, b;
        do { a = Math.floor(rand() * popsize); } while (a === i);
        do { b = Math.floor(rand() * popsize); } while (b === i || b === a);
        const jr = Math.floor(rand() * 4);
        const trial = pop[i].slice();
        for (let d = 0; d < 4; d++) {
          if (rand() < 0.9 || d === jr) {
            let v = pop[bi][d] + 0.7 * (pop[a][d] - pop[b][d]);
            if (v < lo[d]) v = lo[d] + rand() * (pop[i][d] - lo[d]);   // reflect softly into the box
            if (v > hi[d]) v = hi[d] - rand() * (hi[d] - pop[i][d]);
            trial[d] = v;
          }
        }
        const f = score(trial); sims++;
        if (f >= fit[i]) { pop[i] = trial; fit[i] = f; if (f > fit[bi]) bi = i; }
      }
      history.push({ gen: g, best: fit[bi], sims });
      if (onProgress) onProgress(g, fit[bi], generations);
      await new Promise((res) => setTimeout(res, 0));
    }
    const best = pop[bi];
    const sim = simulate(precip, pet, best);
    return {
      params: { X1: best[0], X2: best[1], X3: best[2], X4: best[3] },
      objective, best: fit[bi], simulations: sims, history,
      calibration: metrics(obs, sim, warmup, end),
      validation: end < obs.length ? metrics(obs, sim, end, obs.length) : null,
      sim,
    };
  }

  return { BOUNDS, NAMES, unitHydrographs, simulate, metrics, calibrate, rng };
});
