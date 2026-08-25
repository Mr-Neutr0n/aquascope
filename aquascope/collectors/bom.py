"""
Collector for the Australian Bureau of Meteorology (BOM) Water Data Online portal.

BOM publishes real-time and historical streamflow, water-level, storage, and
groundwater data through a KISTERS WISKI (KiWIS) endpoint:

    https://www.bom.gov.au/waterdata/services

No authentication key is required. The collector works in two steps, both
against the same ``QueryServices`` endpoint:

1. ``getTimeseriesList`` — resolve the ``ts_id`` for a station/parameter/
   time-series-name combination.
2. ``getTimeseriesValues`` (with ``metadata=true``) — fetch observations for
   that ``ts_id`` over a date range. ``metadata=true`` also returns the
   station's coordinates and the parameter's real unit alongside the data,
   so no separate station lookup is needed.

The timeseries lookup omits ``returnfields`` entirely: BOM's KiWIS instance
returns an HTTP 500 for *any* ``returnfields`` value on a
``getTimeseriesList`` request. This matches the reference ``bomWater`` R
client, which never passes ``returnfields`` to that request type either.

Reference: https://kisters.com.au/wde/ (KISTERS WISKI REST API)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aquascope.collectors.base import BaseCollector
from aquascope.schemas.water_data import (
    DataSource,
    GeoLocation,
    StreamflowReading,
    WaterLevelReading,
    WaterQualitySample,
)
from aquascope.utils.http_client import CachedHTTPClient, RateLimiter

logger = logging.getLogger(__name__)

BOM_BASE = "https://www.bom.gov.au/waterdata/services"

#: Default BOM time-series name — quality-checked, merged daily mean.
DEFAULT_TS_NAME = "DMQaQc.Merged.DailyMean.24HR"

#: Typical unit for each BOM parameter type (fallback for when neither
#: ``getTimeseriesValues``'s ``metadata=true`` response nor
#: ``getTimeseriesList`` report a unit for a station/timeseries combination).
PARAMETER_UNITS: dict[str, str] = {
    "Water Course Discharge": "m3/s",
    "Water Course Level": "m",
    "Storage Level": "m",
    "Storage Volume": "ML",
    "Storage Percentage Full": "%",
    "Electrical Conductivity At 25C": "uS/cm",
    "Turbidity": "NTU",
    "Water Temperature": "degC",
    "Rainfall": "mm",
    "pH": "pH",
    "Ground Water Level": "m",
}

#: Parameter types that represent a gauge/level reading rather than a
#: discrete water-quality sample — normalised to ``WaterLevelReading``.
LEVEL_PARAMETERS: set[str] = {
    "Water Course Level",
    "Storage Level",
    "Ground Water Level",
}


class BOMCollector(BaseCollector):
    """
    Collect streamflow, water-level, and storage data from BOM Water Data
    Online (Australia).

    Results are normalised to ``StreamflowReading`` (discharge),
    ``WaterLevelReading`` (gauge/storage/groundwater level), or
    ``WaterQualitySample`` (everything else), all with
    ``source = DataSource.BOM``.
    """

    name: str = "bom"

    def __init__(self, client: CachedHTTPClient | None = None):
        super().__init__(
            client
            or CachedHTTPClient(
                rate_limiter=RateLimiter(max_calls=20, period_seconds=60),
                cache_ttl_seconds=3600,
            )
        )

    # ------------------------------------------------------------------ #
    # fetch_raw
    # ------------------------------------------------------------------ #
    def fetch_raw(
        self,
        station_id: str | None = None,
        parameter_type: str = "Water Course Discharge",
        start_date: str | None = None,
        end_date: str | None = None,
        days: int | None = None,
        ts_name: str = DEFAULT_TS_NAME,
        **kwargs: Any,
    ) -> list[dict]:
        """
        Fetch raw observations for a BOM station from Water Data Online.

        Parameters
        ----------
        station_id : str
            AWRC station number (e.g. ``"410001"`` for Murrumbidgee River
            at Wagga Wagga).
        parameter_type : str
            BOM parameter type name, e.g. ``"Water Course Discharge"``,
            ``"Water Course Level"``, ``"Storage Level"``.
        start_date : str | None
            Start date ``YYYY-MM-DD``. Combined with ``end_date`` to build
            the ``from``/``to`` query window.
        end_date : str | None
            End date ``YYYY-MM-DD``. Defaults to now (UTC) when omitted.
        days : int | None
            Convenience alternative to ``start_date`` — last N days from now.
            Ignored if ``start_date`` is supplied.
        ts_name : str
            BOM time-series name. Defaults to the quality-checked merged
            daily mean (``DMQaQc.Merged.DailyMean.24HR``).

        Returns
        -------
        list[dict]
            Raw observation rows, each merged with station/timeseries
            metadata. Returns an empty list if no matching time series is
            found or the upstream API is unreachable.
        """
        if not station_id:
            raise ValueError("BOMCollector.fetch_raw requires a station_id.")

        if start_date is None:
            window_days = days if days is not None else 30
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=window_days)
            start_date = start.strftime("%Y-%m-%d")
            if end_date is None:
                end_date = end.strftime("%Y-%m-%d")

        ts_meta = self._resolve_timeseries(station_id, parameter_type, ts_name)
        if ts_meta is None:
            logger.warning(
                "No BOM timeseries found for station_no=%s, parametertype_name=%s, ts_name=%s",
                station_id,
                parameter_type,
                ts_name,
            )
            return []

        metadata, values = self._fetch_timeseries_values(ts_meta["ts_id"], start_date, end_date)

        station_name = ts_meta.get("station_name")
        latitude = metadata.get("latitude")
        longitude = metadata.get("longitude")
        unit = metadata.get("unit") or ts_meta.get("unit") or PARAMETER_UNITS.get(parameter_type, "")

        rows: list[dict] = []
        for timestamp, value, quality_code in values:
            rows.append(
                {
                    "station_no": ts_meta.get("station_no", station_id),
                    "station_name": station_name,
                    "parameter_type": parameter_type,
                    "unit": unit,
                    "latitude": latitude,
                    "longitude": longitude,
                    "timestamp": timestamp,
                    "value": value,
                    "quality_code": quality_code,
                }
            )
        return rows

    def _resolve_timeseries(self, station_id: str, parameter_type: str, ts_name: str) -> dict | None:
        """Look up the ``ts_id`` for a station/parameter.

        Deliberately omits ``returnfields``: BOM's KiWIS instance returns an
        HTTP 500 for *any* ``returnfields`` value on a ``getTimeseriesList``
        request. The reference ``bomWater`` R client never passes
        ``returnfields`` to this request type either, relying on the
        server's default columns instead.
        """
        params = {
            "service": "kisters",
            "type": "QueryServices",
            "format": "json",
            "request": "getTimeseriesList",
            "station_no": station_id,
            "parametertype_name": parameter_type,
            "ts_name": ts_name,
        }
        try:
            data = self.client.get_json(BOM_BASE, params=params)
        except Exception:
            logger.warning("BOM getTimeseriesList request failed for station %s", station_id, exc_info=True)
            return None

        row = self._first_data_row(data)
        if row is None:
            return None

        return {
            "ts_id": row.get("ts_id"),
            "station_no": row.get("station_no", station_id),
            "station_name": row.get("station_name"),
            "unit": row.get("parametertype_unitname"),
        }

    def _fetch_timeseries_values(
        self, ts_id: str, start_date: str, end_date: str | None
    ) -> tuple[dict, list[tuple[str, str, str | None]]]:
        """Fetch ``(timestamp, value, quality_code)`` tuples plus station metadata.

        Requests ``metadata=true``, which returns ``station_latitude``,
        ``station_longitude``, and ``ts_unitsymbol`` alongside the data in
        the same response -- avoiding a separate ``getStationList`` call.
        It also fixes a real unit gap: the default ``getTimeseriesList``
        columns never include ``parametertype_unitname``, so without this,
        unit always fell through to the ``PARAMETER_UNITS`` table.
        """
        params = {
            "service": "kisters",
            "type": "QueryServices",
            "format": "json",
            "request": "getTimeseriesValues",
            "ts_id": ts_id,
            "from": start_date,
            "returnfields": "Timestamp,Value,Quality Code",
            "metadata": "true",
        }
        if end_date:
            params["to"] = end_date

        try:
            data = self.client.get_json(BOM_BASE, params=params)
        except Exception:
            logger.warning("BOM getTimeseriesValues request failed for ts_id %s", ts_id, exc_info=True)
            return {}, []

        series = self._first_series(data)
        if series is None:
            return {}, []

        def _float_or_none(val: Any) -> float | None:
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        metadata = {
            "latitude": _float_or_none(series.get("station_latitude")),
            "longitude": _float_or_none(series.get("station_longitude")),
            "unit": series.get("ts_unitsymbol"),
        }

        columns = [c.strip() for c in series.get("columns", "").split(",")]
        out: list[tuple[str, str, str | None]] = []
        for record in series.get("data", []):
            row = dict(zip(columns, record))
            timestamp = row.get("Timestamp")
            value = row.get("Value")
            if timestamp is None or value is None:
                continue
            out.append((timestamp, value, row.get("Quality Code")))
        return metadata, out

    @staticmethod
    def _first_data_row(data: Any) -> dict | None:
        """Parse a ``getTimeseriesList``-style response.

        BOM returns either ``["No matches."]`` (no results) or a list of
        lists where the first row is the header and subsequent rows are data.
        """
        if not isinstance(data, list) or not data:
            return None
        if data[0] == "No matches.":
            return None
        header, *rows = data
        if not rows or not isinstance(header, list):
            return None
        return dict(zip(header, rows[0]))

    @staticmethod
    def _first_series(data: Any) -> dict | None:
        """Parse a ``getTimeseriesValues`` response into its first series object."""
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return None

    # ------------------------------------------------------------------ #
    # normalise
    # ------------------------------------------------------------------ #
    def normalise(self, raw: list[dict]) -> list[WaterQualitySample | WaterLevelReading | StreamflowReading]:
        """
        Normalise raw BOM rows into ``StreamflowReading`` (discharge),
        ``WaterLevelReading`` (level-type parameters), or
        ``WaterQualitySample`` (everything else).
        """
        if not raw:
            return []

        records: list[WaterQualitySample | WaterLevelReading | StreamflowReading] = []
        skipped = 0
        for row in raw:
            try:
                value = row.get("value")
                if value is None or str(value).strip() in ("", "NaN", "--"):
                    continue
                value = float(value)

                dt_str = row.get("timestamp")
                if not dt_str:
                    continue
                sample_dt = datetime.fromisoformat(str(dt_str)).replace(tzinfo=None)

                location = None
                lat, lon = row.get("latitude"), row.get("longitude")
                if lat is not None and lon is not None:
                    location = GeoLocation(latitude=lat, longitude=lon)

                parameter_type = row.get("parameter_type", "")
                unit = row.get("unit") or PARAMETER_UNITS.get(parameter_type, "")
                quality_code = row.get("quality_code")
                remark = f"Quality Code: {quality_code}" if quality_code is not None else None

                if parameter_type in LEVEL_PARAMETERS:
                    records.append(
                        WaterLevelReading(
                            source=DataSource.BOM,
                            station_id=str(row.get("station_no", "unknown")),
                            station_name=row.get("station_name"),
                            location=location,
                            reading_datetime=sample_dt,
                            water_level=value,
                            unit=unit or "m",
                            remark=remark,
                        )
                    )
                elif parameter_type == "Water Course Discharge":
                    records.append(
                        StreamflowReading(
                            source=DataSource.BOM,
                            station_id=str(row.get("station_no", "unknown")),
                            station_name=row.get("station_name"),
                            location=location,
                            reading_datetime=sample_dt,
                            discharge_cms=value,
                            source_type="in_situ",
                            unit=unit or "m3/s",
                            remark=remark,
                        )
                    )
                else:
                    records.append(
                        WaterQualitySample(
                            source=DataSource.BOM,
                            station_id=str(row.get("station_no", "unknown")),
                            station_name=row.get("station_name"),
                            location=location,
                            sample_datetime=sample_dt,
                            parameter=parameter_type,
                            value=value,
                            unit=unit,
                            remark=remark,
                        )
                    )

            except (ValueError, KeyError, TypeError) as exc:
                skipped += 1
                logger.debug("Skipping BOM row: %s", exc)

        if skipped:
            logger.warning("BOM normalise: skipped %d of %d row(s) that failed to parse.", skipped, len(raw))

        return records
