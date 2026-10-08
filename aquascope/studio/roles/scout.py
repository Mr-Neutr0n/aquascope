"""The Scout: what exists at the site and in the client's hands, as an inventory.

Deterministic, no model: the reconnaissance (``assess_site`` through the
Solve team's ``_scout``, which survives a failed lookup), one
:class:`~aquascope.studio.workspace.Dataset` per station and variable within
reach, the catchment, the donor pool, the ERA5 cell (reachable for any point
on land), and one dataset per upload, run through the ingest mapping and QA
so the report can say what the table holds and how complete it is.

It also lists the place-context layers (#520) that cover any site: flood
history, surface water, flood depth, dams, rain gauges, actual ET and soil,
each with its sources and licences. For a flood question it reads the flood
history (news events and radar detections near the site), so the report can
set the historical events beside the estimate.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from aquascope.studio.workspace import Dataset, Inventory, Workspace

__all__ = ["FLOOD_PLAYBOOKS", "choose_column", "context_layers", "scout", "upload_dataset"]

ERA5_START = "1940-01-01"
#: The playbooks whose questions are about floods: the Scout reads the flood history for them.
FLOOD_PLAYBOOKS = ("flood_risk", "flood_change")


def _say(ws: Workspace):
    def relay(event: dict[str, Any]) -> None:
        ws.event(str(event.get("role") or "scout"), str(event.get("event") or "recon"),
                 str(event.get("detail") or ""), step=event.get("step"))

    return relay


def _station_datasets(recon: dict[str, Any]) -> list[Dataset]:
    ctx = recon.get("context") or {}
    resolution_by = ctx.get("resolution_by_variable") or {}
    out: list[Dataset] = []
    for st in recon.get("stations") or []:
        if not isinstance(st, dict) or not st.get("station_id"):
            continue
        variables = [v for v in (st.get("variables") or []) if v] or [None]
        for var in variables:
            years = st.get("years")
            out.append(Dataset(
                id=f"{st.get('source')}:{st['station_id']}" + (f":{var}" if var else ""),
                kind="station", variable=var, source=st.get("source"), station_id=str(st["station_id"]),
                name=st.get("name") or str(st["station_id"]),
                lat=st.get("latitude"), lon=st.get("longitude"),
                distance_km=st.get("distance_km"), start=st.get("period_start"), end=st.get("period_end"),
                years=float(years) if isinstance(years, (int, float)) else None,
                resolution=resolution_by.get(var) if var else None,
                note=None if years is not None else "the catalog has no record span for it",
            ))
    return out


def _resolution(index: Any) -> str:
    """daily, monthly, or the median spacing in days, from a DatetimeIndex."""
    import pandas as pd

    if len(index) < 3:
        return "unknown"
    days = float(pd.Series(index).diff().median().total_seconds() / 86400.0)
    if days <= 1.5:
        return "daily"
    if 27 <= days <= 32:
        return "monthly"
    return f"{days:.0f} d"


def choose_column(df: Any, mapping: Any, value_column: str | None) -> Any:
    """The ingest mapping with ``value_column`` as the record (the client named it): guessed again over the
    datetime column and that column alone, so the variable and the unit follow the column; the plain
    override when that guess fails."""
    from aquascope import ingest

    if not value_column or value_column not in df.columns or value_column == mapping.value_column:
        return mapping
    try:
        narrowed = ingest.guess_mapping(df[[mapping.datetime_column, value_column]])
        if narrowed.value_column == value_column:
            return narrowed
    except (ValueError, TypeError, KeyError):
        pass
    mapping.value_column = value_column
    return mapping


def upload_dataset(dataset_id: str, csv: str, *, value_column: str | None = None) -> Dataset:
    """One upload as a dataset: the ingest mapping and QA when the table is a datetime/value series, the
    column list and the row count when it is not (sample rows a workbench step can still take).
    ``value_column`` is the column the client named at intake."""
    import pandas as pd

    from aquascope import ingest

    df = pd.read_csv(io.StringIO(csv), dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    columns = list(df.columns)
    try:
        mapping = choose_column(df, ingest.guess_mapping(df), value_column)
        raw = ingest.apply_mapping(df, mapping)
        series, qa = ingest.qa_series(raw, n_rows_in=len(df))
    except (ValueError, TypeError, KeyError) as exc:
        return Dataset(
            id=dataset_id, kind="upload", source="upload", name=dataset_id, n=int(len(df)),
            quality={"verdict": "table", "columns": columns, "reason": str(exc)},
            note="not a datetime/value series; the rows can be analysed as samples",
        )
    verdict = "usable" if qa.coverage_pct >= 80 and not qa.warnings else "check"
    years = round(qa.n_days_span / 365.25, 2) if qa.n_days_span else None
    return Dataset(
        id=dataset_id, kind="upload", variable=mapping.variable, source="upload", station_id=dataset_id,
        name=dataset_id, start=qa.start, end=qa.end, years=years, resolution=_resolution(series.index),
        n=int(qa.n_values),
        quality={"verdict": verdict, "columns": columns, "mapping": mapping.to_dict(), "qa": qa.to_dict(),
                 "unit": mapping.unit or None},
        note=f"{mapping.variable} in {mapping.unit or 'an unknown unit'}; coverage {qa.coverage_pct} %"
             + (f"; {len(qa.warnings)} warning(s)" if qa.warnings else ""),
    )


def context_layers() -> list[dict[str, Any]]:
    """The place-context layers as inventory rows: what each says and whose data it is (no network)."""
    from aquascope.context import LAYER_SOURCES
    from aquascope.registry import CONTEXT_LAYERS

    rows = []
    for layer, keys in LAYER_SOURCES.items():
        metas = [CONTEXT_LAYERS[k] for k in keys]
        rows.append({"layer": layer, "label": " and ".join(m.label for m in metas),
                     "sources": [m.provider for m in metas], "licences": [m.license for m in metas]})
    return rows


def _read_flood_history(lat: float, lon: float) -> dict[str, Any]:
    """The flood_history layer at the site (network; the tests replace it)."""
    from aquascope.context import flood_history

    return flood_history(lat, lon)


def _flood_history_note(ws: Workspace, inv: Inventory) -> None:
    """Read the flood history for a flood question and keep it with the context rows and in the notes."""
    try:
        res = _read_flood_history(inv.site["lat"], inv.site["lon"])
    except Exception as exc:  # noqa: BLE001 - context is never a reason for the study to stop
        ws.event("scout", "context", f"flood history unreadable: {type(exc).__name__}: {exc}")
        return
    if not isinstance(res, dict) or not res.get("summary"):
        return
    for row in inv.context:
        if row["layer"] == "flood_history":
            row["summary"] = str(res["summary"])
            news = res.get("news") or {}
            if news.get("available"):
                row["events"] = news.get("n_events")
                row["latest"] = news.get("latest")
                # where the flood playbook's historical-events caveat looks (context.flood_history.events);
                # copied, never written into the reconnaissance dict the caller may share
                ctx = dict(inv.recon.get("context") or {})
                ctx["flood_history"] = {"events": news.get("n_events"), "latest": news.get("latest"),
                                        "radius_km": res.get("radius_km")}
                inv.recon = {**inv.recon, "context": ctx}
    inv.notes.insert(0, f"Flood history: {res['summary']}")  # first, so the report keeps it
    ws.event("scout", "context", f"flood history: {res['summary']}")


def scout(ws: Workspace) -> Inventory:
    """Build the inventory and write it to ``ws.inventory``. Never raises on the reconnaissance's account."""
    from aquascope.ai_engine.team import _scout

    site = dict(ws.site or {})
    if site.get("lat") is None or site.get("lon") is None:
        raise ValueError("the workspace names no site (lat, lon)")
    recon = _scout({"lat": float(site["lat"]), "lon": float(site["lon"])}, ws.brief.playbook, dict(ws.brief.intake),
                   _say(ws))
    ctx = recon.get("context") or {}
    inv = Inventory(site={"lat": float(site["lat"]), "lon": float(site["lon"])}, recon=recon,
                    catchment=recon.get("catchment") if isinstance(recon.get("catchment"), dict) else None,
                    donors=ctx.get("donors") if isinstance(ctx.get("donors"), int) else None,
                    notes=[str(n) for n in (recon.get("notes") or [])])
    inv.datasets += _station_datasets(recon)
    catchment = inv.catchment or {}
    if catchment and not catchment.get("error"):
        area = catchment.get("upstream_area_km2") or catchment.get("area_km2")
        inv.datasets.append(Dataset(
            id="catchment", kind="catchment", source=str(catchment.get("source") or "BasinATLAS"),
            name=str(catchment.get("sub_basin") or "the catchment of the point"),
            note=f"upstream area {area:,.0f} km2" if isinstance(area, (int, float)) else None,
        ))
    if inv.donors is not None:
        inv.datasets.append(Dataset(id="donors", kind="donors", source="similar_basins", n=int(inv.donors),
                                    name=f"{inv.donors} donor gauges by catchment similarity"))
    today = datetime.now(timezone.utc).date().isoformat()
    inv.datasets.append(Dataset(
        id="era5", kind="reanalysis", variable="climate", source="ERA5 via Open-Meteo", name="ERA5 cell",
        lat=inv.site["lat"], lon=inv.site["lon"], start=ERA5_START, end=today,
        years=round((datetime.now(timezone.utc).date() - datetime(1940, 1, 1).date()).days / 365.25, 1),
        resolution="daily",
        note="precipitation, temperature and FAO-56 ET0 for a 9 km cell, any point on land; GloFAS modelled "
             "discharge for the same point, indicative",
    ))
    inv.context = context_layers()
    if ws.brief.playbook in FLOOD_PLAYBOOKS:
        _flood_history_note(ws, inv)
    chosen = ws.brief.intake.get("value_column")
    for dataset_id, csv in ws.tables.items():
        try:
            ds = upload_dataset(dataset_id, csv, value_column=str(chosen) if chosen else None)
        except Exception as exc:  # noqa: BLE001 - a table that cannot be read is still listed
            ds = Dataset(id=dataset_id, kind="upload", source="upload", name=dataset_id,
                         quality={"verdict": "unreadable", "reason": f"{type(exc).__name__}: {exc}"})
        inv.datasets.append(ds)
        inv.notes.append(f"{dataset_id}: {ds.note or (ds.quality or {}).get('reason') or 'listed'}")
        ws.event("scout", "upload", f"{dataset_id}: {(ds.quality or {}).get('verdict')}, "
                 f"{ds.n or 0} values" + (f", {ds.years} years of {ds.variable}" if ds.years else ""))
    ws.inventory = inv
    ws.event("scout", "inventory", f"{len(inv.datasets)} dataset(s): "
             + ", ".join(f"{d.id} ({d.years:g} y)" if d.years else d.id for d in inv.datasets[:8]))
    return inv
