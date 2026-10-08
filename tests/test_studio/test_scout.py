"""The Scout: datasets from the reconnaissance, the ERA5 cell, uploads through the ingest QA, no network."""

from __future__ import annotations

import aquascope.explore
from aquascope.studio.roles import scout
from aquascope.studio.workspace import Workspace
from tests.test_studio.conftest import RICH, SAMPLES_CSV, SERIES_CSV, UNGAUGED, patched


def _ws(**tables) -> Workspace:
    ws = Workspace()
    ws.site = {"lat": 51.415, "lon": -0.308}
    ws.brief.problem, ws.brief.playbook = "drought", "drought_status"
    for k, v in tables.items():
        ws.add_table(k, v)
    return ws


def test_the_inventory_lists_every_station_variable_the_catchment_the_donors_and_era5():
    ws = _ws()
    with patched(RICH):
        inv = scout.scout(ws)
    assert ws.inventory is inv and inv.site == {"lat": 51.415, "lon": -0.308}
    ids = [d.id for d in inv.datasets]
    assert ids[:4] == ["uk_ea:3400TH:discharge", "uk_ea:3400TH:water_level", "uk_ea:R1:precipitation",
                       "uk_ea:W1:groundwater_level"]
    assert "catchment" in ids and "donors" in ids and ids[-1] == "era5"
    gauge = inv.dataset("uk_ea:3400TH:discharge")
    assert gauge.kind == "station" and gauge.years == 39.5 and gauge.distance_km == 0.4 and gauge.resolution == "daily"
    assert inv.dataset("era5").kind == "reanalysis" and inv.dataset("era5").years > 80
    assert inv.dataset("donors").n == 8 and inv.years_by_variable["precipitation"] == 36.0
    assert inv.recon is not None and inv.notes == ["served record starts 1986"]
    assert [e["event"] for e in ws.events if e["role"] == "scout"] == ["recon", "inventory"]


def test_an_ungauged_site_still_has_the_reanalysis_and_survives_a_failed_recon(monkeypatch):
    ws = _ws()
    with patched(UNGAUGED):
        inv = scout.scout(ws)
    assert [d.kind for d in inv.datasets] == ["catchment", "donors", "reanalysis"]

    def boom(*a, **k):
        raise RuntimeError("catalog offline")

    monkeypatch.setattr(aquascope.explore, "assess_site", boom, raising=False)
    ws2 = _ws()
    inv2 = scout.scout(ws2)
    assert [d.kind for d in inv2.datasets] == ["reanalysis"]
    assert any("reconnaissance unavailable" in n for n in inv2.notes)
    assert any(e["event"] == "error" for e in ws2.events)


def test_uploads_go_through_the_ingest_qa():
    ws = _ws(**{"upload:flows.csv": SERIES_CSV, "upload:samples.csv": SAMPLES_CSV})
    with patched(UNGAUGED):
        inv = scout.scout(ws)
    flows = inv.dataset("upload:flows.csv")
    assert flows.kind == "upload" and flows.variable == "discharge" and flows.n > 1000 and flows.years > 10
    assert flows.quality["verdict"] in ("usable", "check") and flows.quality["mapping"]["value_column"] == "flow_m3s"
    assert flows.quality["qa"]["coverage_pct"] > 0 and flows.quality["columns"] == ["date", "flow_m3s"]
    samples = inv.dataset("upload:samples.csv")
    assert samples.kind == "upload" and samples.variable is None and samples.n == 3
    assert samples.quality["verdict"] == "table" and samples.quality["columns"] == ["station", "parameter", "value",
                                                                                      "unit"]
    assert len(inv.uploads()) == 2 and any("upload:flows.csv" in n for n in inv.notes)
    assert ws.to_dict()["inventory"]["datasets"][-1]["id"] == "upload:samples.csv"


def test_the_place_context_layers_are_listed_with_their_licences():
    ws = _ws()
    with patched(RICH):
        inv = scout.scout(ws)
    layers = [c["layer"] for c in inv.context]
    assert layers == ["flood_history", "surface_water", "flood_hazard", "dams", "rain_gauge", "actual_et", "soil"]
    floods = inv.context[0]
    assert floods["sources"] == ["Google Research", "Microsoft AI for Good Lab"]
    assert floods["licences"] == ["CC-BY-4.0", "MIT"] and "summary" not in floods  # a drought question reads none
    # listed beside the datasets, not counted among them
    assert all(d.kind != "context" for d in inv.datasets)
    assert ws.to_dict()["inventory"]["context"][3]["layer"] == "dams"


def test_a_flood_question_reads_the_flood_history(monkeypatch):
    seen = []

    def fake(lat, lon):
        seen.append((lat, lon))
        return {"summary": "3 flood events in the news within 25 km, latest 2024-01-05.",
                "news": {"available": True, "n_events": 3, "latest": "2024-01-05"}}

    monkeypatch.setattr(scout, "_read_flood_history", fake)
    ws = _ws()
    ws.brief.problem, ws.brief.playbook = "flood_risk", "flood_risk"
    with patched(RICH):
        inv = scout.scout(ws)
    assert seen == [(51.415, -0.308)]
    floods = inv.context[0]
    assert floods["events"] == 3 and floods["latest"] == "2024-01-05" and floods["summary"].startswith("3 flood")
    assert "Flood history: 3 flood events in the news within 25 km, latest 2024-01-05." in inv.notes
    assert any(e["event"] == "context" for e in ws.events if e["role"] == "scout")


def test_a_failed_flood_history_read_never_stops_the_scout(monkeypatch):
    def boom(lat, lon):
        raise RuntimeError("Archive offline")

    monkeypatch.setattr(scout, "_read_flood_history", boom)
    ws = _ws()
    ws.brief.playbook = "flood_change"
    with patched(RICH):
        inv = scout.scout(ws)
    assert "summary" not in inv.context[0]
    assert any("flood history unreadable" in e["detail"] for e in ws.events)


def test_the_flood_playbook_caveat_follows_the_events_the_scout_found(monkeypatch):
    from aquascope import playbooks as pbk

    pb = pbk.load("flood_risk")
    intake = pbk.fill_intake(pb, {})
    flagged = "Historical flood events near the site"
    monkeypatch.setattr(scout, "_read_flood_history", lambda lat, lon: {
        "summary": "2 flood events.", "radius_km": 25.0, "news": {"available": True, "n_events": 2,
                                                                   "latest": "2020-01-01"}})
    ws = _ws()
    ws.brief.playbook = "flood_risk"
    with patched(RICH):
        inv = scout.scout(ws)
    assert inv.recon["context"]["flood_history"] == {"events": 2, "latest": "2020-01-01", "radius_km": 25.0}
    with_events = pbk.caveats_for(pb, pbk.evaluation_context(pb, inv.recon, intake))
    assert any(flagged in c for c in with_events)
    # the reconnaissance alone (what HydroGym scores against) never carries it
    without = pbk.caveats_for(pb, pbk.evaluation_context(pb, RICH, intake))
    assert not any(flagged in c for c in without)
