"""`aquascope context LAT LON` (and `--bbox`): one line per layer and the attribution, or JSON (#520)."""

from __future__ import annotations

import json
import sys

import pytest

from aquascope import cli

POINT = {
    "lat": 51.415, "lon": -0.308,
    "layers": {
        "flood_history": {"summary": "3 flood events in the news within 25 km, latest 2024-01-05."},
        "dams": {"summary": "No dams in Global Dam Watch within 50 km."},
    },
    "summary": ["3 flood events in the news within 25 km, latest 2024-01-05.",
                "No dams in Global Dam Watch within 50 km."],
    "attribution": ["Global Dam Watch database v1.0 (Lehner et al. 2024), CC BY 4.0"],
}


def test_context_prints_one_line_per_layer(monkeypatch, capsys):
    seen = {}

    def fake(lat, lon, layers=None):
        seen.update(lat=lat, lon=lon, layers=layers)
        return POINT

    monkeypatch.setattr("aquascope.context.place_context", fake)
    monkeypatch.setattr(sys, "argv", ["aquascope", "context", "51.415", "-0.308", "--layers", "flood_history,dams"])
    cli.main()
    out = capsys.readouterr().out
    assert seen == {"lat": 51.415, "lon": -0.308, "layers": "flood_history,dams"}
    assert out.startswith("Context of 51.4150, -0.3080")
    assert "Flood history" in out and "3 flood events in the news" in out
    assert "Data: Global Dam Watch" in out


def test_context_json_and_a_box(monkeypatch, capsys):
    seen = {}

    def fake_area(w, s, e, n, layers=None):
        seen["bbox"] = (w, s, e, n)
        return {**POINT, "bbox": [w, s, e, n]}

    monkeypatch.setattr("aquascope.context.area_context", fake_area)
    monkeypatch.setattr(sys, "argv", ["aquascope", "context", "--bbox=-1,51,0,52", "--json"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["bbox"] == [-1.0, 51.0, 0.0, 52.0]
    assert seen["bbox"] == (-1.0, 51.0, 0.0, 52.0)


def test_context_needs_a_place(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aquascope", "context"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
