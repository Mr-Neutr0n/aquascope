"""Tests that the CLI's source registries stay in sync.

The ``--source`` choices, the collector map, and the ``list-sources`` info table
are three hand-maintained lists that have drifted apart before: a collector
could be registered in ``collectors/__init__.py`` yet be unreachable from the
CLI, and an info entry keyed by anything other than its ``DataSource`` value
silently rendered as a placeholder.
"""

from __future__ import annotations

import argparse
import sys

import pytest

from aquascope.cli import cmd_list_sources, main

# Sources with a working collector reachable from `aquascope collect`.
# GRACE and USGS_GW are declared in DataSource but have no collector yet, and
# India WRIS needs required arguments the collect command does not pass.
CLI_SOURCES = (
    "grdc",
    "hubeau_hydrometrie",
    "japan_mlit",
    "korea_wamis",
    "pegelonline",
    "camels_cl",
    "camels_br",
)


@pytest.mark.parametrize("source", CLI_SOURCES)
def test_source_is_a_valid_collect_choice(source, capsys, monkeypatch):
    """Every registered collector is reachable from `aquascope collect`."""
    monkeypatch.setattr(sys, "argv", ["aquascope", "collect", "--source", "__nonexistent__"])
    with pytest.raises(SystemExit):
        main()
    err = capsys.readouterr().err
    assert source in err, f"'{source}' is not an `aquascope collect --source` choice"


def test_list_sources_renders_metadata_for_recent_sources(capsys):
    """list-sources is driven by the registry: every key, real labels, no placeholders."""
    from aquascope.registry import SOURCES

    cmd_list_sources(argparse.Namespace())
    out = capsys.readouterr().out

    assert "GRDC" in out
    assert "Hub'Eau" in out
    assert "PEGELONLINE" in out
    assert "Germany" in out
    for key, meta in SOURCES.items():
        assert f"  {key}  " in out, f"{key} missing from list-sources"
        assert meta.label in out
    # Region and description are mandatory registry fields, so the placeholder
    # dash used for optional ones must never show up on those two lines.
    assert "Region    : —" not in out
    assert "Data      : —" not in out


def test_grdc_rejects_an_unknown_mode(monkeypatch):
    """GRDC's --mode maps to source_type and only accepts its two values."""
    monkeypatch.setattr(sys, "argv", ["aquascope", "collect", "--source", "grdc", "--mode", "bogus"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_pegelonline_requires_station_uuid(monkeypatch, caplog):
    """PEGELONLINE fails clearly before collection when no station is supplied."""
    monkeypatch.setattr(sys, "argv", ["aquascope", "collect", "--source", "pegelonline"])
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert any("requires --station" in record.message for record in caplog.records)


def test_bom_requires_station(monkeypatch, caplog):
    """BOM fails clearly before collection when no station is supplied."""
    monkeypatch.setattr(sys, "argv", ["aquascope", "collect", "--source", "bom"])
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert any("requires --station" in record.message for record in caplog.records)


def test_bom_passes_parameter_type_through_to_collect(monkeypatch):
    """--parameter-type reaches BOMCollector.collect() as the parameter_type kwarg.

    Regression test: BOM's Water Course Discharge series is unpopulated at
    some stations (e.g. regulated rivers), so --parameter-type "Water Course
    Level" must be wireable from the CLI, not just from the Python API.
    """
    captured_kwargs = {}

    class _FakeCollector:
        def collect(self, **kwargs):
            captured_kwargs.update(kwargs)
            return []

    monkeypatch.setattr("aquascope.collectors.BOMCollector", lambda: _FakeCollector())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aquascope",
            "collect",
            "--source",
            "bom",
            "--station",
            "409001",
            "--parameter-type",
            "Water Course Level",
        ],
    )
    main()
    assert captured_kwargs.get("parameter_type") == "Water Course Level"
    assert captured_kwargs.get("station_id") == "409001"
