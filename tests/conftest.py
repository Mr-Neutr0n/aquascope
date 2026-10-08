"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_usgs_area_cache():
    """The USGS drainage-area cache is process-wide (one lookup per station per run); tests start empty."""
    from aquascope.collectors.usgs import USGSCollector

    USGSCollector._shared_area_cache.clear()
    yield
    USGSCollector._shared_area_cache.clear()


@pytest.fixture(autouse=True)
def _fresh_imgw_frames():
    """The IMGW parsed-archive memo is process-wide (one parse per file per run); tests start empty."""
    from aquascope.collectors.poland_imgw import PolandIMGWCollector

    PolandIMGWCollector._shared_frames.clear()
    yield
    PolandIMGWCollector._shared_frames.clear()


@pytest.fixture(autouse=True)
def _no_geoglows_network(monkeypatch):
    """aquascope.rivers reads GEOGLOWS over HTTP; no test reaches it. The Scout's reach lookup then finds no
    network and leaves the reach out, and tests that want a reach patch these seams themselves."""
    from aquascope import rivers

    def offline(*args, **kwargs):
        raise RuntimeError("GEOGLOWS is not reachable in tests")

    for name in ("_fetch_range", "_fetch_json", "_fetch_text"):
        monkeypatch.setattr(rivers, name, offline)


@pytest.fixture(autouse=True)
def _no_flood_history_reads(monkeypatch):
    """The Studio Scout reads the flood history for a flood question (#520); tests never reach the network for it.

    A test that wants a flood history patches ``scout._read_flood_history`` again with what it needs.
    """
    from aquascope.studio.roles import scout

    monkeypatch.setattr(scout, "_read_flood_history", lambda lat, lon: {})
    yield


@pytest.fixture(autouse=True)
def _no_model_skill_reads(monkeypatch):
    """The Studio Scout reads the published model-skill table near the site (#518); tests never reach the network
    for it. A test that wants a model choice patches ``scout._read_model_skill`` itself."""
    from aquascope.studio.roles import scout

    monkeypatch.setattr(scout, "_read_model_skill", lambda lat, lon: {})
    yield
