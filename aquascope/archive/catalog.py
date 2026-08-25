"""Read the published station catalog (the Archive) without any agency call.

``load_stations()`` downloads ``stations.parquet`` from the Hugging Face
dataset once a day into a local cache and returns plain dicts, so the MCP
server, the CLI and notebooks can answer "which gauges are near X" in
milliseconds. Falls back to ``stations.geojson`` when ``pyarrow`` is missing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from aquascope.schemas.station import in_bbox

logger = logging.getLogger(__name__)

DEFAULT_REPO_ID = "Rekin226/aquascope-gauges"
CACHE_TTL_SECONDS = 24 * 3600

_OVERRIDE: list[dict[str, Any]] | None = None


def set_catalog(rows: list[dict[str, Any]] | None) -> None:
    """Make :func:`load_stations` return ``rows`` instead of downloading the catalog.

    Used by the Explorer's browser worker, which already holds the catalog in
    DuckDB-WASM and cannot use httpx or pyarrow; also handy in tests. Pass
    ``None`` to go back to the Hub.
    """
    global _OVERRIDE
    _OVERRIDE = list(rows) if rows is not None else None


def catalog_url(repo_id: str = DEFAULT_REPO_ID, filename: str = "stations.parquet") -> str:
    return f"https://huggingface.co/datasets/{repo_id}/resolve/main/{filename}"


def cache_dir() -> Path:
    root = os.environ.get("AQUASCOPE_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "aquascope")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path, refresh: bool) -> Path:
    if dest.exists() and not refresh and time.time() - dest.stat().st_mtime < CACHE_TTL_SECONDS:
        return dest
    logger.info("Downloading %s", url)
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


def load_stations(
    *, repo_id: str = DEFAULT_REPO_ID, refresh: bool = False, path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Return every station in the published catalog as a list of dicts.

    Keys: ``source, station_id, name, latitude, longitude, variables (list),
    period_start, period_end, url, river, country, agency, license,
    redistributable, extra (dict)``. ``path`` reads a local ``stations.parquet``
    (a fresh harvest) instead of the Hub.
    """
    if _OVERRIDE is not None and path is None:
        return _OVERRIDE
    try:
        import pyarrow.parquet as pq  # noqa: F401
    except ImportError:
        pq = None
    if path is not None and pq is None:
        raise ImportError("reading a local stations.parquet needs pyarrow (pip install 'aquascope[archive]')")
    if pq is not None:
        if path is not None:
            dest = Path(path)
        else:
            local = cache_dir() / f"{repo_id.replace('/', '__')}.parquet"
            dest = _download(catalog_url(repo_id, "stations.parquet"), local, refresh)
        table = pq.read_table(dest, columns=[c for c in [
            "source", "station_id", "name", "latitude", "longitude", "variables", "period_start", "period_end",
            "url", "river", "country", "agency", "license", "redistributable", "extra",
        ]])
        rows = table.to_pylist()
        for r in rows:
            for k in ("period_start", "period_end"):
                if r.get(k) is not None:
                    r[k] = r[k].isoformat()
            r["variables"] = list(r.get("variables") or [])
            r["extra"] = json.loads(r["extra"]) if r.get("extra") else {}
        return rows
    local = cache_dir() / f"{repo_id.replace('/', '__')}.geojson"
    dest = _download(catalog_url(repo_id, "stations.geojson"), local, refresh)
    gj = json.loads(dest.read_text(encoding="utf-8"))
    rows = []
    for f in gj.get("features", []):
        p = dict(f.get("properties") or {})
        lon, lat = f["geometry"]["coordinates"]
        p.update({"latitude": lat, "longitude": lon, "variables": list(p.get("variables") or [])})
        rows.append(p)
    return rows


def search_stations(
    rows: list[dict[str, Any]],
    *,
    bbox: tuple[float, float, float, float] | None = None,
    variable: str | None = None,
    sources: list[str] | None = None,
    query: str | None = None,
    near: tuple[float, float] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Filter the catalog: bbox, variable, sources, name/id substring, and optional nearest-first ordering."""
    q = (query or "").strip().lower()
    src = set(sources or [])
    out = []
    for r in rows:
        if src and r["source"] not in src:
            continue
        if variable and variable not in (r.get("variables") or []):
            continue
        if bbox and not in_bbox(r["latitude"], r["longitude"], bbox):
            continue
        if q and q not in (r.get("name") or "").lower() and q not in r["station_id"].lower():
            continue
        out.append(r)
    if near:
        lat0, lon0 = near
        out.sort(key=lambda r: (r["latitude"] - lat0) ** 2 + ((r["longitude"] - lon0) * 0.7) ** 2)
    return out[: max(0, limit)]
