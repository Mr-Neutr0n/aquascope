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
