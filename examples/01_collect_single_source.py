#!/usr/bin/env python3
"""Example 01 — Collect data from a single source.

Demonstrates how to use AquaScope collectors to fetch and normalise
water data from a single API.  Four examples are shown:

1. Taiwan EPA water quality monitoring
2. USGS real-time river gauges
3. Open-Meteo historical weather
4. BOM (Australia) streamflow and water level

Each collector follows the same pattern: ``fetch_raw()`` → ``normalise()``.
"""

from aquascope.collectors import BOMCollector, OpenMeteoCollector, TaiwanMOENVCollector, USGSCollector
from aquascope.utils.storage import save_records

# ── 1. Taiwan EPA water quality ──────────────────────────────────────────

print("▸ Fetching Taiwan MOENV water quality data …")
tw = TaiwanMOENVCollector()
raw_tw = tw.fetch_raw()
records_tw = tw.normalise(raw_tw)
print(f"  Got {len(records_tw)} records")
if records_tw:
    r = records_tw[0]
    print(f"  Sample: {r.station_name} | {r.parameter}={r.value} {r.unit}")

# ── 2. USGS real-time discharge ──────────────────────────────────────────

print("\n▸ Fetching USGS discharge data (last 7 days) …")
usgs = USGSCollector()
raw_usgs = usgs.fetch_raw(days=7)
records_usgs = usgs.normalise(raw_usgs)
print(f"  Got {len(records_usgs)} records")

# ── 3. Open-Meteo historical weather ────────────────────────────────────

print("\n▸ Fetching Open-Meteo weather for Taipei (last 30 days) …")
meteo = OpenMeteoCollector()
raw_meteo = meteo.fetch_raw(
    lat=25.03,
    lon=121.57,
    mode="weather",
    start_date="2024-01-01",
    end_date="2024-01-31",
)
records_meteo = meteo.normalise(raw_meteo)
print(f"  Got {len(records_meteo)} records")

# ── 4. BOM (Australia) streamflow and water level ───────────────────────
#
# Not every BOM station reports every parameter reliably: on some
# regulated rivers the discharge series is unpopulated even though the
# parameter is listed, but water level is still measured directly. Check
# a station's `Water Course Level` if `Water Course Discharge` comes back
# empty (see docs/data_sources.md for details).

print("\n▸ Fetching BOM discharge for Murrumbidgee River at Wagga Wagga (last 30 days) …")
bom = BOMCollector()
raw_bom_discharge = bom.fetch_raw(station_id="410001", parameter_type="Water Course Discharge", days=30)
records_bom_discharge = bom.normalise(raw_bom_discharge)
print(f"  Got {len(records_bom_discharge)} records")
if records_bom_discharge:
    r = records_bom_discharge[0]
    print(f"  Sample: {r.station_name} | {r.parameter}={r.value} {r.unit}")

print("\n▸ Fetching BOM water level for Murray River at Albury (last 30 days) …")
raw_bom_level = bom.fetch_raw(station_id="409001", parameter_type="Water Course Level", days=30)
records_bom_level = bom.normalise(raw_bom_level)
print(f"  Got {len(records_bom_level)} records")
if records_bom_level:
    r = records_bom_level[0]
    print(f"  Sample: {r.station_name} | water_level={r.water_level} {r.unit}")

# ── Save all to files ────────────────────────────────────────────────────

for label, records in [
    ("taiwan", records_tw),
    ("usgs", records_usgs),
    ("openmeteo", records_meteo),
    ("bom_discharge", records_bom_discharge),
    ("bom_level", records_bom_level),
]:
    if records:
        path = save_records(records, prefix=f"example_{label}", fmt="json")
        print(f"\n  ✓ Saved {label} → {path}")

print("\nDone!  Run `aquascope eda --file <path>` on any output file.")
