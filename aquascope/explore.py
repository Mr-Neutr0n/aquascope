"""``(source, station) -> answer``: the entry point behind the Explorer and the MCP server.

:func:`analyze_station` fetches one station's observed record through
aquascope's own collectors and computes the Phase-0 analytics: hydrograph and
annual maxima, flood frequency (GEV by L-moments, Log-Pearson III with
confidence limits, an on-demand bootstrap GEV band), the flow-duration curve,
and a Mann-Kendall trend, plus the method citations. It runs unchanged in
CPython (tests, CLI, MCP) and inside Pyodide in the browser (the Explorer's
worker imports it from the wheel), so what any surface shows is exactly what
``pip install aquascope`` computes.

Everything returned is plain JSON: lists, dicts, numbers, strings, ``None``.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from aquascope.registry import SOURCES, build_collector

logger = logging.getLogger(__name__)

RETURN_PERIODS = [2, 5, 10, 25, 50, 100]
MIN_YEARS_FOR_FFA = 10

METHODS: dict[str, dict[str, str]] = {
    "gev_lmoments": {
        "name": "GEV fitted by L-moments",
        "text": "Annual maxima (calendar years) fitted to a Generalized Extreme Value distribution with "
        "L-moment estimators (Hosking 1990); return levels from the fitted quantile function.",
        "citation": "Hosking, J. R. M. (1990). L-moments: analysis and estimation of distributions using linear "
        "combinations of order statistics. J. R. Stat. Soc. B, 52(1), 105-124.",
    },
    "lp3": {
        "name": "Log-Pearson III (Bulletin 17C style)",
        "text": "Log-transformed annual maxima fitted to a Pearson type III distribution; station skew, "
        "frequency factors and analytical confidence limits after Bulletin 17C.",
        "citation": "England, J. F. Jr. et al. (2018). Guidelines for determining flood flow frequency, "
        "Bulletin 17C. USGS Techniques and Methods 4-B5.",
    },
    "gev_bootstrap": {
        "name": "GEV (MLE, L-moment seeded) with bootstrap CI",
        "text": "Maximum-likelihood GEV seeded from L-moments with the shape bounded to |k| <= 0.5; "
        "90 % confidence bands from 1,000 bootstrap resamples of the annual maxima.",
        "citation": "Coles, S. (2001). An Introduction to Statistical Modeling of Extreme Values. Springer.",
    },
    "fdc": {
        "name": "Flow-duration curve",
        "text": "Empirical exceedance probabilities of daily flow (Weibull plotting positions); "
        "Q95 and Q10 read from the curve.",
        "citation": "Vogel, R. M., & Fennessey, N. M. (1994). Flow-duration curves I: new interpretation and "
        "confidence intervals. J. Water Resour. Plann. Manage., 120(4), 485-504.",
    },
    "era5": {
        "name": "ERA5 reanalysis via Open-Meteo",
        "text": "Daily precipitation, 2 m temperature and FAO-56 reference evapotranspiration for the grid cell "
        "(about 9 km) containing the point, from ECMWF's ERA5 reanalysis served by Open-Meteo.",
        "citation": "Hersbach, H. et al. (2020). The ERA5 global reanalysis. Q. J. R. Meteorol. Soc., 146, "
        "1999-2049; Open-Meteo.com (CC BY 4.0).",
    },
    "glofas": {
        "name": "GloFAS modelled discharge via Open-Meteo",
        "text": "Daily river discharge simulated by the Global Flood Awareness System (LISFLOOD, ~5 km grid) "
        "for the cell containing the point. Modelled, not observed: use it for context, not for design values.",
        "citation": "Harrigan, S. et al. (2020). GloFAS-ERA5 operational global river discharge reanalysis "
        "1979-present. Earth Syst. Sci. Data, 12, 2043-2060.",
    },
    "fao56": {
        "name": "FAO-56 reference evapotranspiration and aridity index",
        "text": "ET0 after Allen et al. (1998); aridity index = mean annual precipitation / mean annual ET0 "
        "(UNEP classes: hyper-arid < 0.05, arid < 0.2, semi-arid < 0.5, dry sub-humid < 0.65, humid otherwise).",
        "citation": "Allen, R. G., Pereira, L. S., Raes, D., & Smith, M. (1998). Crop evapotranspiration. "
        "FAO Irrigation and Drainage Paper 56.",
    },
    "trend": {
        "name": "Mann-Kendall trend on annual means",
        "text": "Non-parametric Mann-Kendall test with Sen's slope on the annual mean series.",
        "citation": "Mann, H. B. (1945). Nonparametric tests against trend. Econometrica, 13, 245-259; "
        "Sen, P. K. (1968). J. Am. Stat. Assoc., 63, 1379-1389.",
    },
}


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    if isinstance(d, (datetime, pd.Timestamp)):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _clean(x: Any) -> Any:
    """Make numbers JSON-safe (NaN/inf -> None)."""
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else round(x, 4)
    return x


# ── fetching ────────────────────────────────────────────────────────────────


def _records_to_series(records: list, prefer: str | None = None) -> tuple[pd.Series | None, str, str]:
    """Turn a list of aquascope readings into (series, variable, unit).

    Handles StreamflowReading, WaterLevelReading, ClimateReading and
    WaterQualitySample (discharge / gage height / rainfall parameters).
    """
    if not records:
        return None, "", ""
    rows: list[tuple[datetime, float]] = []
    variable, unit = "", ""
    for r in records:
        name = type(r).__name__
        if name == "StreamflowReading":
            variable, unit = "discharge", getattr(r, "unit", "m3/s")
            rows.append((r.reading_datetime, float(r.discharge_cms)))
        elif name == "WaterLevelReading":
            variable, unit = "water_level", getattr(r, "unit", "m")
            rows.append((r.reading_datetime, float(r.water_level)))
        elif name == "ClimateReading":
            if r.parameter != "rainfall_mm":
                continue
            variable, unit = "precipitation", getattr(r, "unit", "mm")
            rows.append((r.sample_datetime, float(r.value)))
        elif name == "WaterQualitySample":
            p = (r.parameter or "").lower()
            if "discharge" in p or p == "q":
                var = "discharge"
            elif "gage" in p or "level" in p or p == "h":
                var = "water_level"
            elif "rain" in p or "precip" in p:
                var = "precipitation"
            else:
                continue
            if prefer and var != prefer:
                continue
            variable, unit = var, getattr(r, "unit", "")
            rows.append((r.sample_datetime, float(r.value)))
    if not rows:
        return None, "", ""
    s = pd.Series({t: v for t, v in rows}).sort_index()
    s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
    s = s[~s.index.duplicated(keep="last")]
    # SI in, SI out: USGS gage height arrives in feet.
    if variable == "water_level" and str(unit).lower() in ("ft", "feet", "foot"):
        s = s * 0.3048
        unit = "m"
    return s, variable, unit


# Which agency parameter serves which archive variable.
_USGS_CODES = {"discharge": "00060", "water_level": "00065"}


def fetch_series(
    source: str,
    station_id: str,
    *,
    years: int = 40,
    prefer_archive: bool = True,
    variable: str | None = None,
) -> dict[str, Any]:
    """Fetch the observed record for one station.

    The Archive is tried first (a harvested daily file, one HTTPS GET, no
    agency load) when ``prefer_archive`` is set and the source is one the
    harvest mirrors; otherwise, or when the archive has no file for the
    station, the record comes straight from the agency through aquascope's
    collector. ``variable`` asks for one variable (``discharge``,
    ``water_level``, ``precipitation``, ``groundwater_level``); by default the
    source's variables are tried in its preferred order (discharge first).
    Returns ``{"series": pd.Series | None, "variable": str, "unit": str,
    "note": str}``; ``note`` says where the data came from and any
    record-length limit of the source.
    """
    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(years * 365.25))
    note = ""

    if prefer_archive:
        from aquascope.archive.observations import ARCHIVE_UNITS, fetch_archived_series, harvestable_variables

        candidates = (variable,) if variable else harvestable_variables(source)
        for var in candidates:
            if var not in harvestable_variables(source):
                continue
            archived = fetch_archived_series(source, station_id, var)
            if archived is not None and not archived.empty:
                archived = archived[archived.index >= pd.Timestamp(start)]
                return {
                    "series": archived,
                    "variable": var,
                    "unit": ARCHIVE_UNITS.get(var, ""),
                    "note": (
                        f"From the AquaScope archive (daily {var.replace('_', ' ')} harvested from "
                        f"{SOURCES[source].agency}; {archived.index.min().date()} to {archived.index.max().date()})."
                    ),
                }

    if source == "usgs":
        # Pass the catalog id as-is ("USGS-01646500" or another agency's "CA574-09527500");
        # the collector maps it onto NWIS (number + agencyCd) or the OGC monitoring_location_id.
        c = build_collector("usgs")
        span = int(years * 365.25)
        s, var, unit = None, "", ""
        for want in (variable,) if variable else ("discharge", "water_level"):
            code = _USGS_CODES.get(want or "")
            if code is None:
                continue
            recs = c.collect(station_id=station_id, days=span, collection="daily", parameter=code, max_items=None)
            s, var, unit = _records_to_series(recs)
            if s is not None:
                break
        note = "USGS daily values (NWIS), full period requested."
    elif source == "uk_ea":
        c = build_collector("uk_ea")
        measure, measure_var = _uk_ea_pick_measure(c, station_id, variable=variable)
        s = None
        var = unit = ""
        if measure:
            recs = c.collect(measure=measure, min_date=start.isoformat(), max_date=end.isoformat(), max_items=None)
            s, var, unit = _records_to_series(recs)
            if s is not None and measure_var == "groundwater_level":
                var = "groundwater_level"  # WaterLevelReading, but the measure is a borehole / tubewell
        note = f"Environment Agency Hydrology API, measure {measure or 'n/a'}."
    elif source == "hubeau_hydrometrie":
        c = build_collector("hubeau_hydrometrie")
        s, var, unit = None, "", ""
        if variable in (None, "discharge"):
            # Long record first: obs_elab QmnJ is the daily mean discharge (multi-decade where the
            # station computes it); fall back to the real-time feed (last 30 days) for H-only stations.
            recs = c.collect(
                code_station=station_id, elaborated="QmnJ", date_debut_obs=start.isoformat(),
                date_fin_obs=end.isoformat(), size=20_000, max_items=None,
            )
            s, var, unit = _records_to_series(recs)
            note = "Hub'Eau elaborated daily mean discharge (obs_elab QmnJ), full period requested."
        if s is None and variable in (None, "discharge"):
            recs = c.collect(code_station=station_id, grandeur_hydro="Q", days=30)
            s, var, unit = _records_to_series(recs)
            note = "Hub'Eau real-time observations (last 30 days); this station has no elaborated daily discharge."
        if s is None and variable in (None, "water_level"):
            recs = c.collect(code_station=station_id, grandeur_hydro="H", days=30)
            s, var, unit = _records_to_series(recs)
            note = "Hub'Eau real-time water level (last 30 days)."
    elif source == "pegelonline":
        c = build_collector("pegelonline")
        recs = c.collect(station_id=station_id, timeseries=("Q", "W"), days=31)
        s, var, unit = _records_to_series(recs, prefer=variable or "discharge")
        if s is None and not variable:
            s, var, unit = _records_to_series(recs)
        note = "PEGELONLINE serves the last 31 days only."
    elif source == "ireland_opw":
        c = build_collector("ireland_opw")
        recs = c.collect(stations=[{"properties": {"ref": station_id}}])
        s, var, unit = _records_to_series(recs)
        note = "waterlevel.ie month file (15-minute levels, last month)."
    elif source == "taiwan_cwa":
        # CODIS answers one calendar year per request and each takes several
        # seconds at the source; ten years keeps the click-to-chart wait tolerable.
        cwa_years = min(years, 10)
        cwa_start = end - timedelta(days=int(cwa_years * 365.25))
        c = build_collector("taiwan_cwa")
        recs = c.collect(station_ids=[station_id], start=cwa_start.isoformat(), end=end.isoformat())
        s, var, unit = _records_to_series(recs)
        note = (
            f"CWA CODIS daily rainfall, last {cwa_years} years "
            "(one request per year at the source, a few seconds each)."
        )
    else:
        raise ValueError(f"{source} has no Explorer fetch path yet")

    if variable and s is not None and var != variable:
        s, var, unit = None, "", ""  # the station has no record of the variable asked for
    return {"series": s, "variable": var, "unit": unit, "note": note}


# EA stations publish several measures per property (daily min / mean / max,
# 15-minute instantaneous, and for boreholes the manual "dipped" readings).
# One series at a time: per variable, the daily statistic that best stands
# for the day comes first; the 15-minute series is the last resort.
_UK_EA_VARIABLE_ORDER = ("discharge", "water_level", "precipitation", "groundwater_level")
_UK_EA_STAT_ORDER = (
    (86400, "mean"), (86400, "total"), (86400, "maximum"), (0, "instantaneous"), (900, "instantaneous"),
)


def _uk_ea_measure_variable(m: dict) -> str | None:
    """Archive variable served by an EA measure, from its parameter, unit and notation."""
    parameter = str(m.get("parameter") or "")
    unit = str(m.get("unitName") or m.get("unit") or "")
    mid = str(m.get("@id") or "")
    if parameter == "flow":
        return "discharge"
    if parameter == "rainfall":
        return "precipitation"
    if parameter == "groundwaterLevel" or "-gw-" in mid or unit.startswith("mAOD"):
        return "groundwater_level"
    if parameter == "level":
        return "water_level"
    return None


def _uk_ea_pick_measure(collector, station_id: str, *, variable: str | None = None) -> tuple[str | None, str | None]:
    """Return ``(measure notation, variable)`` to fetch for a station, or ``(None, None)``.

    ``variable`` restricts the choice to measures serving that archive variable;
    otherwise variables are tried in ``_UK_EA_VARIABLE_ORDER`` (flow first).
    """
    try:
        data = collector.client.get_json(f"id/stations/{station_id}.json")
    except Exception as exc:  # noqa: BLE001
        logger.info("uk_ea station lookup failed for %s: %s", station_id, exc)
        return None, None
    items = data.get("items") or []
    station = items[0] if isinstance(items, list) and items else items
    measures = station.get("measures") or []
    if isinstance(measures, dict):
        measures = [measures]

    def stat(m: dict) -> str:
        v = m.get("valueStatistic")
        v = v.get("@id", "") if isinstance(v, dict) else str(v or "")
        return v.rsplit("/", 1)[-1]

    def notation(m: dict) -> str | None:
        mid = m.get("@id")
        return str(mid).rsplit("/", 1)[-1] if mid else None  # the collector wants the notation, not the URL

    wanted = (variable,) if variable else _UK_EA_VARIABLE_ORDER
    for want in wanted:
        mine = [m for m in measures if _uk_ea_measure_variable(m) == want and m.get("@id")]
        for period, statistic in _UK_EA_STAT_ORDER:
            for m in mine:
                if int(m.get("period") or 0) == period and stat(m) == statistic:
                    return notation(m), want
        if mine:
            daily = [m for m in mine if int(m.get("period") or 0) == 86400]
            return notation((daily or mine)[0]), want
    if variable or not measures:
        return None, None
    return notation(measures[0]), _uk_ea_measure_variable(measures[0])


# ── analytics ───────────────────────────────────────────────────────────────


def _annual_max(s: pd.Series) -> pd.Series:
    daily = s.resample("D").mean()
    counts = daily.resample("YS").count()
    am = daily.resample("YS").max()
    # keep years with at least ~80 % coverage so a partial first/last year does not fake a low maximum
    return am[counts >= 292].dropna()


def analyze_series(s: pd.Series, variable: str, unit: str) -> dict[str, Any]:
    """Compute Phase-0 analytics for a series. Pure function, JSON-safe output."""
    from aquascope.hydrology.flood_frequency import fit_gev_lmoments, fit_lp3
    from aquascope.hydrology.flow_duration import flow_duration_curve

    s = s.dropna()
    out: dict[str, Any] = {
        "variable": variable,
        "unit": unit,
        "n": int(len(s)),
        "start": _iso(s.index.min()) if len(s) else None,
        "end": _iso(s.index.max()) if len(s) else None,
        "years": round((s.index.max() - s.index.min()).days / 365.25, 1) if len(s) > 1 else 0.0,
        "stats": {
            "mean": _clean(float(s.mean())) if len(s) else None,
            "median": _clean(float(s.median())) if len(s) else None,
            "min": _clean(float(s.min())) if len(s) else None,
            "max": _clean(float(s.max())) if len(s) else None,
        },
        "methods": [],
        "notes": [],
    }
    if not len(s):
        return out

    # hydrograph: daily means, capped at ~25k points for the browser
    daily = s.resample("D").mean().dropna()
    if len(daily) > 25_000:
        daily = daily.iloc[:: int(math.ceil(len(daily) / 25_000))]
    out["series"] = {"t": [d.strftime("%Y-%m-%d") for d in daily.index], "v": [_clean(float(v)) for v in daily.values]}

    am = _annual_max(s)
    out["annual_max"] = {"year": [int(y) for y in am.index.year], "v": [_clean(float(v)) for v in am.values]}

    if variable == "discharge":
        fdc = flow_duration_curve(daily)
        step = max(1, len(fdc.exceedance) // 200)
        out["fdc"] = {
            "exceedance": [_clean(float(x)) for x in fdc.exceedance[::step]],
            "q": [_clean(float(x)) for x in fdc.discharge[::step]],
            "q95": _clean(float(fdc.percentiles.get(95, float("nan")))),
            "q50": _clean(float(fdc.percentiles.get(50, float("nan")))),
            "q10": _clean(float(fdc.percentiles.get(10, float("nan")))),
        }
        out["methods"].append(METHODS["fdc"])

        if len(am) >= MIN_YEARS_FOR_FFA:
            ffa: dict[str, Any] = {"n_years": int(len(am)), "return_periods": RETURN_PERIODS, "fits": {}}
            try:
                g = fit_gev_lmoments(am, return_periods=RETURN_PERIODS)
                ffa["fits"]["gev_lmoments"] = {
                    "q": [_clean(float(g.return_periods[rp])) for rp in RETURN_PERIODS],
                    "params": [_clean(float(p)) for p in g.params],
                }
                out["methods"].append(METHODS["gev_lmoments"])
            except Exception as exc:  # noqa: BLE001
                ffa["fits"]["gev_lmoments"] = {"error": str(exc)}
            try:
                lp3 = fit_lp3(am, return_periods=RETURN_PERIODS, ci_level=0.90)
                ffa["fits"]["lp3"] = {
                    "q": [_clean(float(lp3.return_periods[rp])) for rp in RETURN_PERIODS],
                    "ci": [[_clean(float(a)), _clean(float(b))] for a, b in
                           (lp3.confidence_intervals.get(rp, (float("nan"), float("nan"))) for rp in RETURN_PERIODS)],
                    "params": [_clean(float(p)) for p in lp3.params],
                }
                out["methods"].append(METHODS["lp3"])
            except Exception as exc:  # noqa: BLE001
                ffa["fits"]["lp3"] = {"error": str(exc)}
            out["ffa"] = ffa
        else:
            out["notes"].append(
                f"Flood frequency needs at least {MIN_YEARS_FOR_FFA} complete years of daily flow; this record has "
                f"{len(am)}."
            )

    # Trend on annual means of the well-covered years. "Well covered" is relative
    # to the record's own sampling (daily gauges vs monthly borehole dips), so a
    # 40-year manual groundwater record still gets a trend.
    counts = s.resample("YS").count()
    typical = float(counts[counts > 0].median()) if (counts > 0).any() else 0.0
    covered = counts[counts >= 0.8 * typical].index if typical else counts.index[:0]
    if len(covered) >= 8:
        try:
            from aquascope.analysis.trends import mann_kendall, sens_slope

            annual_mean = s.resample("YS").mean().reindex(covered).dropna()
            mk = mann_kendall(annual_mean.values)
            slope = sens_slope(annual_mean.values)
            out["trend"] = {
                "on": "annual mean",
                "p_value": _clean(float(mk.p_value)),
                "tau": _clean(float(mk.tau)),
                "trend": str(mk.trend),
                "sens_slope_per_year": _clean(float(slope.slope)),
                "n_years": int(mk.n_samples),
            }
            out["methods"].append(METHODS["trend"])
        except Exception as exc:  # noqa: BLE001
            logger.info("trend skipped: %s", exc)
    return out


def flood_ci(s: pd.Series) -> dict[str, Any]:
    """The slow part: bootstrap GEV confidence bands (called on demand)."""
    from aquascope.hydrology.flood_frequency import fit_gev

    am = _annual_max(s.dropna())
    r = fit_gev(am, return_periods=RETURN_PERIODS, ci_level=0.90)
    return {
        "q": [_clean(float(r.return_periods[rp])) for rp in RETURN_PERIODS],
        "ci": [[_clean(float(a)), _clean(float(b))] for a, b in
               (r.confidence_intervals.get(rp, (float("nan"), float("nan"))) for rp in RETURN_PERIODS)],
        "params": [_clean(float(p)) for p in r.params],
        "method": METHODS["gev_bootstrap"],
    }


def analyze_station(
    source: str,
    station_id: str,
    *,
    years: int = 40,
    store: dict[str, Any] | None = None,
    variable: str | None = None,
) -> dict[str, Any]:
    """Fetch + analyse one station. The entry point the browser worker calls.

    Pass ``store`` (any dict) to keep the fetched pandas Series under
    ``store["series"]`` for follow-up calls such as :func:`flood_ci` and
    :func:`to_csv` without a second fetch. ``variable`` picks one of the
    station's variables (default: the source's preferred one, discharge first).
    """
    meta = SOURCES[source]
    fetched = fetch_series(source, station_id, years=years, variable=variable)
    if store is not None:
        store["series"] = fetched["series"]
        store["source"], store["station_id"] = source, station_id
    result: dict[str, Any] = {
        "source": source,
        "station_id": station_id,
        "agency": meta.agency,
        "license": meta.license,
        "attribution": meta.attribution,
        "fetch_note": fetched["note"],
    }
    s = fetched["series"]
    if s is None or s.empty:
        result.update({"n": 0, "error": "The source returned no observations for this station."})
        return result
    result.update(analyze_series(s, fetched["variable"], fetched["unit"]))
    return result


def _aridity_class(index: float | None) -> str | None:
    if index is None:
        return None
    if index < 0.05:
        return "hyper-arid"
    if index < 0.2:
        return "arid"
    if index < 0.5:
        return "semi-arid"
    if index < 0.65:
        return "dry sub-humid"
    return "humid"


def anywhere(lat: float, lon: float, *, years: int = 10) -> dict[str, Any]:
    """The "hydrology of anywhere" card: ERA5 climate + FAO-56 ET0 + GloFAS modelled discharge for a point.

    Uses Open-Meteo (keyless, CORS-enabled) through the OpenMeteo collector,
    so it works from the browser worker too. Returns JSON only.
    """
    end = datetime.now(timezone.utc).date() - timedelta(days=7)  # ERA5 lags a few days
    start = end - timedelta(days=int(years * 365.25))
    out: dict[str, Any] = {
        "latitude": round(float(lat), 5),
        "longitude": round(float(lon), 5),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "years": years,
        "methods": [],
        "notes": [],
    }

    weather = build_collector("openmeteo", mode="weather")
    try:
        raw = weather.fetch_raw(
            latitude=lat, longitude=lon, start_date=start.isoformat(), end_date=end.isoformat(),
            daily=["precipitation_sum", "temperature_2m_mean", "et0_fao_evapotranspiration"],
        )
        daily = raw.get("daily", {})
        idx = pd.to_datetime(daily.get("time", []))
        p = pd.Series(daily.get("precipitation_sum", []), index=idx, dtype="float64")
        t = pd.Series(daily.get("temperature_2m_mean", []), index=idx, dtype="float64")
        et0 = pd.Series(daily.get("et0_fao_evapotranspiration", []), index=idx, dtype="float64")
        annual_p = p.resample("YS").sum(min_count=300)
        annual_et0 = et0.resample("YS").sum(min_count=300)
        monthly_p = p.groupby(p.index.month).mean() * 30.44
        monthly_et0 = et0.groupby(et0.index.month).mean() * 30.44
        mean_p = float(annual_p.dropna().mean()) if annual_p.notna().any() else None
        mean_et0 = float(annual_et0.dropna().mean()) if annual_et0.notna().any() else None
        aridity = (mean_p / mean_et0) if (mean_p is not None and mean_et0) else None
        out["climate"] = {
            "source": "ERA5 via Open-Meteo",
            "precipitation_mm_per_year": _clean(mean_p),
            "et0_mm_per_year": _clean(mean_et0),
            "temperature_mean_c": _clean(float(t.mean())) if len(t) else None,
            "aridity_index": _clean(aridity),
            "aridity_class": _aridity_class(aridity),
            "monthly_precipitation_mm": [_clean(float(monthly_p.get(m, float("nan")))) for m in range(1, 13)],
            "monthly_et0_mm": [_clean(float(monthly_et0.get(m, float("nan")))) for m in range(1, 13)],
            "annual_precipitation": {
                "year": [int(y) for y in annual_p.dropna().index.year],
                "mm": [_clean(float(v)) for v in annual_p.dropna().values],
            },
            "wettest_day_mm": _clean(float(p.max())) if len(p) else None,
        }
        out["methods"].extend([METHODS["era5"], METHODS["fao56"]])
    except Exception as exc:  # noqa: BLE001
        out["notes"].append(f"ERA5 climate unavailable: {exc}")

    flood = build_collector("openmeteo", mode="flood")
    try:
        # GloFAS goes back to 1984 and is cheap: ask for at least 20 years so
        # the (indicative) flood frequency has enough complete years.
        flood_start = end - timedelta(days=int(max(years, 20) * 365.25))
        raw = flood.fetch_raw(latitude=lat, longitude=lon, start_date=flood_start.isoformat(),
                              end_date=end.isoformat(), daily=["river_discharge"])
        daily = raw.get("daily", {})
        idx = pd.to_datetime(daily.get("time", []))
        q = pd.Series(daily.get("river_discharge", []), index=idx, dtype="float64").dropna()
        if len(q):
            summary = analyze_series(q, "discharge", "m3/s")
            summary.pop("series", None)
            summary.pop("methods", None)
            summary["source"] = "GloFAS v4 (modelled) via Open-Meteo"
            summary["modelled"] = True
            out["glofas"] = summary
            out["notes"].append(
                "GloFAS discharge is a model output for the grid cell (about 5 km), not a gauge reading; "
                "return levels from it are indicative only."
            )
            out["methods"].append(METHODS["glofas"])
            if "ffa" in summary:
                out["methods"].extend([METHODS["gev_lmoments"], METHODS["lp3"]])
    except Exception as exc:  # noqa: BLE001
        out["notes"].append(f"GloFAS discharge unavailable: {exc}")

    out["attribution"] = (
        "Open-Meteo.com (CC BY 4.0); ERA5 and GloFAS: Copernicus Climate Change and Emergency Management Services."
    )
    return out


def to_csv(result: dict[str, Any]) -> str:
    """CSV of the daily series in a result dict (for the download button)."""
    series = result.get("series") or {"t": [], "v": []}
    unit = result.get("unit", "")
    lines = [f"date,{result.get('variable', 'value')}_{unit}".replace("/", "_per_")]
    lines += [f"{t},{'' if v is None else v}" for t, v in zip(series["t"], series["v"])]
    return "\n".join(lines) + "\n"
