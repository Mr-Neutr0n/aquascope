"""Playbooks: the shipped trees validate, pick the right branch per site, decline what they should, and
emit a study the runner executes with no model in the loop."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aquascope import playbooks as pbk
from aquascope.gates import evaluate
from aquascope.study import parse_block_yaml, run_study

STATION = {"source": "uk_ea", "station_id": "3400TH", "name": "Kingston", "distance_km": 0.4,
           "variables": ["discharge", "water_level"], "period_start": "1883-10-01", "years": 39.5}


def recon(years=None, stations=None, donors=None, area=None, dams=0, available=("glofas",)):
    years = years or {}
    ctx = {"years_by_variable": dict(years), "resolution_by_variable": {k: "daily" for k in years},
           "area_km2": area, "donors": donors, "ungauged": not years}
    if available is not None:
        ctx["available"] = list(available)
    return {"point": {"lat": 51.415, "lon": -0.308}, "stations": list(stations or []),
            "catchment": {"area_km2": area, "upstream_area_km2": area, "dams": dams},
            "context": ctx, "sufficiency": [], "notes": ["served record starts 1986"]}


LONG = recon({"discharge": 39.5}, [STATION], donors=8, area=9948, dams=1)
SHORT = recon({"discharge": 12}, [dict(STATION, years=12)], donors=8, area=300)
UNGAUGED = recon(None, [], donors=5, area=120)
WELL = recon({"groundwater_level": 15}, [dict(STATION, variables=["groundwater_level"], years=15)])


def test_the_three_playbooks_list_load_and_validate():
    ids = [p["id"] for p in pbk.list_playbooks()]
    assert ids == ["flood_risk", "groundwater_decline", "ungauged_flow"]
    for pid in ids:
        pb = pbk.load(pid)
        assert pbk.validate(pb) == [], pid
        assert pb.branches and pb.caveats and pb.citations and pb.declines
        desc = pbk.describe(pid)
        assert desc["id"] == pid and isinstance(desc["branches"], list)


def test_the_files_stay_within_the_yaml_subset_the_browser_reads():
    yaml = pytest.importorskip("yaml")
    for path in pbk.PLAYBOOK_DIR.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert parse_block_yaml(text) == yaml.safe_load(text), path.name


@pytest.mark.parametrize("pid, site, branch, tools", [
    ("flood_risk", LONG, "at_site", ["describe_catchment", "analyze_station", "flood_frequency"]),
    ("flood_risk", SHORT, "short_record",
     ["describe_catchment", "analyze_station", "similar_basins", "regionalize_signatures", "anywhere"]),
    ("flood_risk", UNGAUGED, "regional",
     ["describe_catchment", "similar_basins", "regionalize_signatures", "anywhere"]),
    ("ungauged_flow", LONG, "at_gauge", ["describe_catchment", "analyze_station", "regionalize_signatures"]),
    ("ungauged_flow", UNGAUGED, "regional",
     ["describe_catchment", "similar_basins", "regionalize_signatures", "anywhere"]),
    ("groundwater_decline", WELL, "well",
     ["analyze_station", "get_timeseries", "sgi_drought", "get_timeseries", "recharge"]),
    ("groundwater_decline", UNGAUGED, "regional", ["anywhere"]),
])
def test_each_playbook_selects_the_branch_the_record_supports(pid, site, branch, tools):
    assert pbk.select_branch(pid, site).id == branch
    study = pbk.plan(pid, site, {"return_period": 100}, problem_text="the problem")
    assert study.version == 2 and study.author == "playbook" and study.question == "the problem"
    assert study.plan["playbook"] == pid and study.plan["branch"] == branch
    assert [s.tool for s in study.steps] == tools
    text = study.to_yaml()
    assert "{{" not in text, "every placeholder is resolved"
    assert all(s.id and s.rationale for s in study.steps)
    assert study.plan["caveats"] and study.plan["citations"]
    assert study.plan["recon_notes"] == ["served record starts 1986"]


def test_placeholders_resolve_to_typed_values_and_prose():
    study = pbk.plan("flood_risk", LONG, {"return_period": 200})
    fetch = study.step_by_id("s3")
    assert fetch.arguments == {"source": "uk_ea", "station_id": "3400TH", "bootstrap_ci": True}
    assert study.step_by_id("s1").arguments == {"lat": 51.415, "lon": -0.308}
    rp = [g for g in fetch.expects if g["check"] == "max_return_period_factor"][0]
    assert rp["return_period"] == 200 and isinstance(rp["return_period"], int)
    assert "T = 200 year" in fetch.rationale and "39.5 years" in study.plan["rationale"]
    assert study.problem["params"] == {"return_period": 200, "decision": "design flow"}
    assert study.plan["station"]["station_id"] == "3400TH"


def test_intake_defaults_and_coercion():
    pb = pbk.load("groundwater_decline")
    filled = pbk.fill_intake(pb, {"horizon": "20", "attribute_cause": "no", "concern": "Supply"})
    assert filled == {"horizon": 20, "concern": "supply", "attribute_cause": False}
    assert pbk.fill_intake(pb, None)["attribute_cause"] is False
    with pytest.raises(pbk.Declined) as exc:
        pbk.fill_intake(pbk.load("flood_risk"), {"decision": "mapping"})
    assert exc.value.kind == "intake" and "decision" in exc.value.reason


def test_declines_print_their_own_sentence():
    with pytest.raises(pbk.Declined) as exc:
        pbk.plan("flood_risk", recon({"discharge": 12}, [dict(STATION, years=12)], donors=1), {"return_period": 100})
    assert exc.value.kind == "declined" and "36 years" in exc.value.reason and "100 years" in exc.value.reason
    with pytest.raises(pbk.Declined) as exc:
        pbk.plan("flood_risk", LONG, {"decision": "inundation extent"})
    assert "out of scope" in exc.value.reason
    with pytest.raises(pbk.Declined) as exc:
        pbk.plan("ungauged_flow", recon(None, [], donors=2))
    assert "three donor" in exc.value.reason and "2 found" in exc.value.reason
    with pytest.raises(pbk.Declined) as exc:
        pbk.plan("groundwater_decline", WELL, {"attribute_cause": True})
    assert "pumping" in exc.value.reason
    # a long record with donors is not declined for T = 100
    assert pbk.plan("flood_risk", LONG, {"return_period": 100}).plan["branch"] == "at_site"
    # and a site with donors unknown is left to the run-time gate
    assert pbk.plan("ungauged_flow", recon(None, [], donors=None)).plan["branch"] == "regional"


def test_caveats_carry_the_evidence_sentences_and_react_to_the_site():
    flood = pbk.plan("flood_risk", LONG, {"return_period": 100}).plan["caveats"]
    assert any("Wasko et al. 2024" in c and "immature" in c for c in flood)
    assert any("upstream dams" in c for c in flood)
    no_dams = pbk.plan("flood_risk", recon({"discharge": 39.5}, [STATION], donors=8, dams=0), {"return_period": 100})
    assert not any("upstream dams" in c for c in no_dams.plan["caveats"])
    gw = pbk.load("groundwater_decline")
    assert any("Jasechko" in c for c in gw.citations)
    regional = pbk.plan(gw, UNGAUGED).plan["caveats"]
    assert any("regional signal" in c for c in regional)


def test_the_273_scenario_is_refused_at_plan_time_and_by_the_gate():
    big = recon({"discharge": 30}, [STATION], area=101033)
    tree = {"id": "calib", "title": "Calibration", "problem": "climate_change",
            "branches": [{"id": "only", "steps": [
                {"id": "s1", "tool": "analyse_table", "method": "gr4j_calibration", "arguments": {}}]}]}
    with pytest.raises(pbk.Declined) as exc:
        pbk.plan(tree, big)
    assert exc.value.kind == "refused" and "101,033" in exc.value.reason and "ceiling" in exc.value.reason
    gate = evaluate([{"check": "max_area_km2", "value": 10000, "path": "attributes.upstream_area_km2"}],
                    {"attributes": {"upstream_area_km2": 101033.0}})
    assert not gate[0]["passed"] and "above the ceiling" in gate[0]["detail"]
    # the same tree on a small catchment plans
    assert pbk.plan(tree, recon({"discharge": 30}, [STATION], area=900, available=["forcing"])).steps


def test_an_optional_step_is_dropped_with_a_note_not_refused():
    study = pbk.plan("flood_risk", recon(None, [], donors=5, available=[]))
    assert [s.tool for s in study.steps] == ["describe_catchment", "similar_basins", "regionalize_signatures"]
    assert study.plan["notes"] and "glofas" in study.plan["notes"][0]


def test_validate_catches_the_authoring_mistakes():
    bad = {"id": "bad", "title": "Bad", "problem": "x",
           "intake": [{"name": "t", "type": "choice"}, {"name": "u", "type": "int", "default": 1}],
           "branches": [{"id": "b", "when": [{"path": "a", "op": "~", "value": 1}], "steps": [
               {"id": "s1", "tool": "teleport", "method": "warp", "arguments": {"x": "{{ intake.nope }}"},
                "expects": [{"check": "nope"}], "depends_on": ["s9"], "fallback": {"foo": 1}},
               {"id": "s1", "tool": "list_sources", "arguments": {"y": "{{ magic.x }}"}}]}],
           "declines": [{"when": [{"path": "a", "op": "in", "value": []}], "say": " "}]}
    errors = pbk.validate(bad)
    text = "\n".join(errors)
    for needle in ("a choice needs options", "unknown operator '~'", "unknown tool 'teleport'",
                   "unknown method 'warp'", "not an earlier step", "unknown check 'nope'", "a fallback is",
                   "intake.nope", "magic", "duplicate id", "says nothing"):
        assert needle in text, needle
    with pytest.raises(pbk.PlaybookError):
        pbk.load("no_such_playbook")
    with pytest.raises(pbk.PlaybookError):
        pbk.load({"id": "x"})


def test_a_gauged_branch_without_its_station_is_an_authoring_error():
    site = recon({"discharge": 30}, [dict(STATION, variables=["water_level"])], donors=5)
    with pytest.raises(pbk.PlaybookError, match="station"):
        pbk.plan("flood_risk", site, branch="at_site")


def test_the_study_a_playbook_emits_runs_with_no_model():
    study = pbk.plan("flood_risk", LONG, {"return_period": 100})
    payload = {"source": "uk_ea", "station_id": "3400TH", "unit": "m3/s", "years": 39.9, "trend": {"p_value": 0.3},
               "ffa": {"return_periods": [2, 5, 10, 25, 50, 100],
                       "fits": {"gev_lmoments": {"q": [1, 2, 3, 4, 5, 6]}, "lp3": {"q": [1, 2, 3, 4, 5, 6.5]},
                                "gev_bootstrap": {"q": [1, 2, 3, 4, 5, 6], "ci": [[5, 7]] * 6}}}}
    tools = {"describe_catchment": lambda **kw: {"sub_basin": {"hybas_id": 1}, "attributes": {}},
             "analyze_station": lambda **kw: payload, "flood_frequency": lambda **kw: payload}
    with patch("aquascope.study._tools", return_value=tools):
        run = run_study(study)
    assert run.ok and all(g["passed"] for g in run.gates) and len(run.gates) == 7
    assert "gate spread_within: passed" in run.to_markdown()


def test_the_explorer_playbook_list_is_the_package_s_own():
    """explorer/playbooks.json is generated from the YAML files; the page draws its chips from it."""
    import json
    from pathlib import Path

    from aquascope.playbooks import as_json

    data = json.loads(as_json())
    ids = [p["id"] for p in data["playbooks"]]
    assert ids == ["flood_risk", "groundwater_decline", "ungauged_flow"]
    flood = data["playbooks"][0]
    assert flood["title"] and flood["problem"] == "flood_risk"
    fields = {f["name"]: f for f in flood["intake"]}
    assert fields["return_period"]["type"] == "int" and fields["return_period"]["default"] == 100
    assert fields["decision"]["type"] == "choice" and "design flow" in fields["decision"]["options"]
    shipped = Path(__file__).resolve().parents[1] / "explorer" / "playbooks.json"
    assert shipped.read_text(encoding="utf-8") == as_json(), (
        "explorer/playbooks.json is stale: run `python -m aquascope.playbooks`"
    )
