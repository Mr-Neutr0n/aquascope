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


def test_the_reach_the_site_snaps_to_is_listed_as_modelled(monkeypatch):
    from aquascope import rivers

    monkeypatch.setattr(rivers, "snap_to_river", lambda lat, lon, max_distance_m=1000.0: {
        "snapped": True, "river_id": 230399750, "distance_m": 18.0, "snap_lat": 51.4151, "snap_lon": -0.3085,
        "message": "Snapped 18 m to river reach 230399750, Strahler order 6."})
    ws = _ws()
    with patched(UNGAUGED):
        inv = scout.scout(ws)
    reach = inv.dataset("geoglows_reach")
    assert reach.kind == "modelled" and reach.variable == "discharge" and reach.station_id == "230399750"
    assert reach.start == "1940-01-01" and reach.years > 80 and reach.distance_km == 0.018
    assert reach.note.startswith("MODELLED, not measured") and reach.quality["licence"] == "CC BY 4.0"
    assert [d.kind for d in inv.datasets] == ["catchment", "donors", "reanalysis", "modelled"]


def test_no_stream_near_the_site_is_a_note_and_no_network_is_silent(monkeypatch):
    from aquascope import rivers

    monkeypatch.setattr(rivers, "snap_to_river", lambda lat, lon, max_distance_m=1000.0: {
        "snapped": False, "message": "No stream within 1,000 m of this point."})
    ws = _ws()
    with patched(UNGAUGED):
        inv = scout.scout(ws)
    assert "geoglows_reach" not in [d.id for d in inv.datasets]
    assert "GEOGLOWS river network: No stream within 1,000 m of this point." in inv.notes


def test_an_unreadable_network_leaves_the_reach_out_quietly():
    # tests/conftest.py takes GEOGLOWS off the network, as an outage would
    assert scout.reach_dataset(51.4, -0.3) == (None, None)
