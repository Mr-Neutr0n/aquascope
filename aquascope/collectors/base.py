"""
Abstract base class for all data collectors.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from aquascope.schemas.station import Station
from aquascope.utils.http_client import CachedHTTPClient

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """
    Every collector must implement ``fetch_raw`` and ``normalise``.

    The public entry-point is ``collect()`` which chains those two steps.
    """

    name: str = "base"

    def __init__(self, client: CachedHTTPClient | None = None):
        self.client = client or CachedHTTPClient()

    @abstractmethod
    def fetch_raw(self, **kwargs) -> Any:
        """Fetch raw data from the upstream API."""

    @abstractmethod
    def normalise(self, raw: Any) -> Sequence[BaseModel]:
        """Convert raw API response into unified Pydantic records."""

    def stations(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        variable: str | None = None,
        max_items: int | None = None,
    ) -> list[Station]:
        """Return this source's station catalog.

        Sources that expose a catalog override this. ``bbox`` is
        ``(west, south, east, north)`` in WGS84 degrees, ``variable`` is one
        of :data:`aquascope.schemas.station.VARIABLES`, and ``max_items``
        caps the result for sources whose catalog is large. The default
        raises so callers (and the registry drift guard) can tell "no
        catalog" from "empty catalog".
        """
        raise NotImplementedError(f"{type(self).__name__} does not expose a station catalog.")

    @classmethod
    def supports_stations(cls) -> bool:
        """True when this collector overrides :meth:`stations`."""
        return cls.stations is not BaseCollector.stations

    def collect(
        self,
        *,
        as_xarray: bool = False,
        as_geodataframe: bool = False,
        **kwargs,
    ) -> Any:
        """Fetch + normalise in one call.

        By default returns the list of unified Pydantic records. Set
        ``as_xarray=True`` to get an ``xarray.Dataset`` (time-series) or
        ``as_geodataframe=True`` to get a ``geopandas.GeoDataFrame`` (point
        geometry) instead — both require the ``interop`` extra. The two flags
        are mutually exclusive.
        """
        if as_xarray and as_geodataframe:
            raise ValueError("as_xarray and as_geodataframe are mutually exclusive.")
        logger.info("[%s] Starting collection …", self.name)
        raw = self.fetch_raw(**kwargs)
        records = self.normalise(raw)
        logger.info("[%s] Collected %d records.", self.name, len(records))
        if as_xarray:
            from aquascope.io.interop import records_to_xarray

            return records_to_xarray(records)
        if as_geodataframe:
            from aquascope.io.interop import records_to_geodataframe

            return records_to_geodataframe(records)
        return records
