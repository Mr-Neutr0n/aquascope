"""HydroGym Phase 1 bench (#175): the tree scores 100 percent on its own keys, a scripted team run is scored
with its tokens, the ask loop is read off its tool calls, timeouts become rows, and the leaderboard renders."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aquascope.gym import bench as gb
from aquascope.gym import tasks as gt
from tests.test_gym.test_tasks import LONG, POINT, SHORT, fake_recon


class FakeChat:
    """Scripted OpenAI-shaped client: each turn is a list of (tool, args) calls or a final text; reports usage."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            msg = SimpleNamespace(content=turn, tool_calls=None)
        else:
            msg = SimpleNamespace(content="", tool_calls=[
                SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
                for i, (name, args) in enumerate(turn)])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


FLOW = {"source": "uk_ea", "station_id": "3400TH", "name": "Kingston", "license": "OGL-UK-3.0",
        "attribution": "Environment Agency", "unit": "m3/s", "variable": "discharge", "start": "1986-08-17",
        "end": "2026-08-15", "years": 39.9, "stats": {"mean": 65.2, "min": 3.1, "max": 520.0},
        "trend": {"on": "annual mean", "p_value": 0.41, "sens_slope_per_year": 0.12, "n_years": 39},
        "ffa": {"n_years": 39, "return_periods": [2, 5, 10, 25, 50, 100],
                "fits": {"gev_lmoments": {"q": [250, 330, 380, 440, 480, 520]},
                         "lp3": {"q": [252, 335, 388, 452, 500, 548], "ci": [[200, 300]] * 5 + [[410, 690]]},
                         "gev_bootstrap": {"q": [250, 330, 380, 440, 480, 520],
                                           "ci": [[210, 290]] * 5 + [[420, 650]]}}},
        "methods": [{"name": "GEV fitted by L-moments", "text": "t", "citation": "Hosking 1990"}]}
CATCHMENT = {"latitude": 51.415, "longitude": -0.308, "sub_basin": {"hybas_id": 1},
             "attributes": {"upstream_area_km2": 9948.0}, "license": "CC BY 4.0", "attribution": "BasinATLAS"}


def _tools(calls):
    def rec(name):
        def f(**kw):
            calls.append((name, kw))
            return {"describe_catchment": CATCHMENT, "analyze_station": FLOW, "flood_frequency": FLOW}[name]
        return f
    return {n: rec(n) for n in ("describe_catchment", "analyze_station", "flood_frequency")}


@pytest.fixture(scope="module")
def suite():
    return gt.tasks_from_playbooks([LONG, SHORT, POINT], ["flood_risk", "ungauged_flow"], recon=fake_recon, probes=1)


def test_the_tree_scores_every_task_on_its_own_keys(suite, tmp_path):
    out = tmp_path / "tree.jsonl"
    events = []
    results = gb.run_bench(suite, "tree", out=out, on_event=events.append)
    assert len(results) == len(suite) == 9 and all(r.correct for r in results) and not any(r.error for r in results)
    solvable = [r for r in results if not r.unsolvable]
    assert all(r.branch_match and r.gates_respected == 1.0 and r.tools_matched == 1.0 for r in solvable)
    assert all(r.declined_correctly and r.declined_reason for r in results if r.unsolvable)
    assert all(r.model is None and r.tokens == 0 and r.cost_usd == 0.0 for r in results)
    rows = gb.summarize(results)
    assert len(rows) == 1 and rows[0]["agent"] == "tree" and rows[0]["accuracy"] == 1.0
    assert rows[0]["decline_rate_unsolvable"] == 1.0 and rows[0]["false_decline_rate"] == 0.0
    assert rows[0]["n"] == 9 and rows[0]["n_unsolvable"] == 3 and rows[0]["cost_usd"] == 0.0
    assert set(rows[0]["by_branch"]) == {"at_site", "short_record", "regional", "at_gauge"}
    back = gb.load_results([out])
    assert [r.to_dict() for r in back] == [r.to_dict() for r in results]
    assert events[0].startswith("[1/9] flood_risk-") and "correct" in events[1]
    md = gb.leaderboard(results, out=tmp_path / "board.md", title="smoke")
    assert md.startswith("## smoke") and "| tree | none | 9 (6 + 3) | 100 % |" in md and "100 %" in md
    assert "at_site" in md and gb.PRICES_NOTE in md and (tmp_path / "board.md").read_text().startswith("## smoke")


def test_a_scripted_team_run_is_scored_and_its_tokens_counted(suite):
    task = next(t for t in suite if t.site.get("station_id") == "3400TH" and t.playbook == "flood_risk"
                and not t.unsolvable)
    calls: list = []
    client = FakeChat(["The plan rests on 39.5 years at Kingston.",
                       "The 100-year flow at Kingston (uk_ea 3400TH) is about 520 m3/s (90 % CI 420 to 650 m3/s)."])
    with patch("aquascope.study._tools", return_value=_tools(calls)):
        (res,) = gb.run_bench([task], "team", client=client, model="claude-sonnet-5", provider="custom")
    assert res.correct and res.branch_chosen == "at_site" and res.playbook_chosen == "flood_risk"
    assert res.gates_expected == 7 and res.gates_evaluated == 7 and res.gates_passed == 7 and res.gates_respected == 1.0
    assert res.tools_called == ["describe_catchment", "analyze_station", "flood_frequency"] and res.tools_matched == 1.0
    assert res.calls == 2 and res.prompt_tokens == 200 and res.completion_tokens == 40
    assert res.cost_usd == pytest.approx((200 * 2 + 40 * 10) / 1e6) and res.model == "claude-sonnet-5"
    assert res.answer.startswith("The 100-year flow") and res.answer_present and not res.declined
    assert res.detail["cost_by_role"]["narrator"]["calls"] == 1 and res.detail["ok"]
    assert [c[0] for c in calls] == ["describe_catchment", "analyze_station", "flood_frequency"]
    assert all(len(r["messages"]) == 2 for r in client.requests), "the team's role calls are stateless"


def test_the_keyless_team_and_an_unsolvable_probe(suite):
    probe = next(t for t in suite if t.probe and t.site.get("station_id") == "3400TH")
    (res,) = gb.run_bench([probe], "team")
    assert res.unsolvable and res.declined and res.declined_correctly and res.correct
    assert res.model is None and res.tokens == 0 and "Inundation extent" in (res.declined_reason or "")
    assert not res.answer_present and res.branch_chosen is None
    md = gb.leaderboard([res])
    assert "| team | keyless | 1 (0 + 1) | - | - (0) | 100 % |" in md


def test_the_ask_loop_is_read_off_its_tool_calls_and_its_answer(suite):
    solvable = next(t for t in suite if t.site.get("station_id") == "3400TH" and t.playbook == "flood_risk"
                    and not t.unsolvable)
    probe = next(t for t in suite if t.probe and t.site.get("station_id") == "3400TH")
    client = FakeChat([
        [("assess_site", {"lat": 51.415, "lon": -0.308})],
        [("flood_frequency", {"source": "uk_ea", "station_id": "3400TH"})],
        "The 100-year flow at Kingston (uk_ea 3400TH, 1986-08-17 to 2026-08-15) is about 520 m3/s by GEV.",
        "Inundation extent is out of scope: it needs a hydraulic model over terrain, which these tools do not run.",
    ])
    with patch("aquascope.mcp_server.assess_site", return_value=fake_recon(51.415, -0.308)), \
         patch("aquascope.mcp_server.flood_frequency", return_value=FLOW):
        results = gb.run_bench([solvable, probe], "ask", client=client, model="fake", provider="custom",
                               max_steps=4, context_chars=5_000)
    a, b = results
    assert a.tools_called == ["assess_site", "flood_frequency"] and a.branch_chosen == "at_site" and a.correct
    assert a.gates_respected == 0.0 and a.tools_matched == pytest.approx(1 / 3) and not a.declined
    assert a.calls == 3 and a.prompt_tokens == 300 and a.cost_usd is None, "an unknown model gets no cost estimate"
    assert "latitude 51.4150, longitude -0.3080" in client.requests[0]["messages"][1]["content"]
    assert b.declined and b.correct and b.tools_called == [] and b.branch_chosen is None and b.calls == 1
    rows = gb.summarize(results)
    assert rows[0]["accuracy"] == 1.0 and rows[0]["decline_rate_unsolvable"] == 1.0 and rows[0]["cost_usd"] is None
    assert "| ask | fake | 2 (1 + 1) | 100 % |" in gb.leaderboard(results)
    with pytest.raises(ValueError):
        gb.run_bench([solvable], "ask")
    with pytest.raises(ValueError):
        gb.run_bench([solvable], "oracle")


def test_infer_branch_and_cost_estimate():
    assert gb.infer_branch("flood_risk", ["find_stations", "analyze_station", "flood_frequency"]) == "at_site"
    regional = ["describe_catchment", "similar_basins", "regionalize_signatures"]
    assert gb.infer_branch("flood_risk", regional) == "regional"
    assert gb.infer_branch("flood_risk", ["describe_catchment"]) == "at_site", "a tie goes to the earlier branch"
    assert gb.infer_branch("ungauged_flow", ["run_python"]) is None and gb.infer_branch("nope", ["x"]) is None
    assert gb.estimate_cost("claude-sonnet-5", 1_000_000, 100_000) == 3.0
    assert gb.estimate_cost("gpt-x", 10, 10) is None and gb.estimate_cost(None, 0, 0) == 0.0


def test_a_slow_agent_times_out_into_a_row_and_the_run_goes_on(suite, monkeypatch):
    def slow(task, cfg):
        time.sleep(2)
        return {}

    monkeypatch.setitem(gb._AGENT_FUNCS, "tree", slow)
    results = gb.run_bench(suite[:2], "tree", timeout=0.2)
    assert [r.error.startswith("TimeoutError") for r in results] == [True, True]
    assert not any(r.correct for r in results) and gb.summarize(results)[0]["timeouts"] == 2
    with pytest.raises(TimeoutError):
        gb._with_timeout(lambda: time.sleep(1), 0.1)
    assert gb._with_timeout(lambda: 42, 1) == 42 and gb._with_timeout(lambda: 42, None) == 42


def test_select_tasks(suite):
    assert len(gb.select_tasks(suite, limit=4)) == 4
    picked = gb.select_tasks(suite, limit=4, unsolvable=1)
    assert len(picked) == 4 and sum(t.unsolvable for t in picked) == 1
    assert [t.id for t in gb.select_tasks(suite, task_ids=[suite[3].id])] == [suite[3].id]
    assert gb.select_tasks(suite) == suite
