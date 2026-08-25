"""The Explorer's JavaScript GR4J (explorer/gr4j.js) must match the Python model to round-off (needs node)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "explorer" / "gr4j.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run_js(precip, pet, params, extra: str = "") -> dict:
    payload = json.dumps({"p": list(map(float, precip)), "e": list(map(float, pet)), "x": params})
    script = f"""
const G = require({json.dumps(str(JS))});
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const q = Array.from(G.simulate(d.p, d.e, d.x));
{extra}
console.log(JSON.stringify({{q, uh: G.unitHydrographs(d.x[3]).map(a => Array.from(a))}}));
"""
    out = subprocess.run(["node", "-e", script], input=payload, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("params", [[320.0, -0.8, 70.0, 2.1], [1200.0, 3.0, 400.0, 0.6], [50.0, -8.0, 5.0, 9.7]])
def test_js_gr4j_matches_python(params):
    from aquascope.models.rainfall_runoff import GR4J

    rng = np.random.default_rng(3)
    idx = pd.date_range("2001-01-01", periods=1200, freq="D")
    precip = pd.Series(np.where(rng.random(len(idx)) < 0.4, rng.gamma(0.9, 6, len(idx)), 0.0), index=idx)
    pet = pd.Series(np.clip(2 + 1.5 * np.sin(np.arange(len(idx)) / 58.1), 0.1, None), index=idx)
    py = GR4J(*params).simulate(precip, pet, warmup_days=0).streamflow.to_numpy()
    js = _run_js(precip.to_numpy(), pet.to_numpy(), params)
    assert np.max(np.abs(np.array(js["q"]) - py)) < 1e-9 * max(1.0, py.max())
    uh1, uh2 = GR4J(*params)._unit_hydrographs()
    assert np.allclose(js["uh"][0], uh1) and np.allclose(js["uh"][1], uh2)


def test_js_metrics_and_calibration_recover_a_synthetic_basin():
    from aquascope.analysis import metrics as m
    from aquascope.models.rainfall_runoff import GR4J

    rng = np.random.default_rng(5)
    idx = pd.date_range("2001-01-01", periods=365 * 8, freq="D")
    precip = pd.Series(np.where(rng.random(len(idx)) < 0.4, rng.gamma(0.9, 6, len(idx)), 0.0), index=idx)
    pet = pd.Series(np.clip(2 + 1.5 * np.sin(np.arange(len(idx)) / 58.1), 0.1, None), index=idx)
    truth = [300.0, -1.0, 80.0, 2.0]
    q_true = GR4J(*truth).simulate(precip, pet, warmup_days=0).streamflow.to_numpy()
    obs = q_true * np.exp(rng.normal(0, 0.1, len(idx)))
    payload = json.dumps({"p": precip.tolist(), "e": pet.tolist(), "obs": obs.tolist(), "x": truth})
    script = f"""
const G = require({json.dumps(str(JS))});
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const sim = G.simulate(d.p, d.e, d.x);
const met = G.metrics(d.obs, sim, 365, d.obs.length);
G.calibrate(d.p, d.e, d.obs, {{objective: 'nse', warmup: 365, calEnd: 365 * 6, popsize: 16, generations: 25, seed: 3}})
  .then(r => console.log(JSON.stringify({{met, r: {{params: r.params, best: r.best, simulations: r.simulations,
                                                    cal: r.calibration, val: r.validation}}}})));
"""
    out = json.loads(subprocess.run(["node", "-e", script], input=payload, capture_output=True, text=True,
                                    check=True).stdout)
    # metrics agree with aquascope.analysis.metrics on the same window
    o, s = obs[365:], q_true[365:]
    assert out["met"]["nse"] == pytest.approx(m.nse(o, s), abs=1e-9)
    assert out["met"]["kge"] == pytest.approx(m.kge(o, s), abs=1e-9)
    assert out["met"]["pbias"] == pytest.approx(m.pbias(o, s), abs=1e-9)
    # DE recovers a good fit in a few hundred simulations and reports validation separately
    r = out["r"]
    assert r["best"] > 0.9 and r["cal"]["nse"] == pytest.approx(r["best"]) and r["val"]["nse"] > 0.85
    assert r["simulations"] == 16 + 16 * 25 and 100 < r["params"]["X1"] < 900
