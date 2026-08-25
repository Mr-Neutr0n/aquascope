# Caravan-format export: large-sample datasets from the Archive

[Caravan](https://doi.org/10.1038/s41597-023-01975-w) (Kratzert et al. 2023)
is the layout the large-sample and machine-learning hydrology community
trains on: one CSV per gauge with daily forcing and area-normalised
streamflow in mm/d, plus three attribute tables per sub-dataset. Extending
it normally means running Google Earth Engine. The Archive already holds
every ingredient (station catalog, daily discharge bundles, BasinATLAS
catchments), so `aquascope caravan export` writes the same layout from it:

```bash
pip install "aquascope[archive,basins]"
aquascope caravan export --source uk_ea --out caravan_gb --max-stations 50
aquascope caravan export --source usgs --out caravan_us --station USGS-01013500 --station USGS-01646500 --fetch-missing
aquascope caravan validate caravan_gb --prefix aquascope_uk_ea
```

```
caravan_gb/
  attributes/aquascope_uk_ea/attributes_other_aquascope_uk_ea.csv        gauge_id, gauge_name, gauge_lat, gauge_lon, area, country, ...
  attributes/aquascope_uk_ea/attributes_caravan_aquascope_uk_ea.csv      p_mean, pet_mean_FAO_PM, aridity_FAO_PM, frac_snow, moisture_index_FAO_PM, seasonality_FAO_PM, high/low_prec_freq/dur
  attributes/aquascope_uk_ea/attributes_hydroatlas_aquascope_uk_ea.csv   catchment attributes under Caravan's HydroATLAS column names
  attributes/aquascope_uk_ea/attributes_basinatlas_raw_aquascope_uk_ea.csv  the containing sub-basin's full BasinATLAS row
  timeseries/csv/aquascope_uk_ea/aquascope_uk_ea_<station_id>.csv        date, forcing columns, streamflow (mm/d)
  licenses/aquascope_uk_ea.md, provenance.json, README.md
```

Sources today: `usgs`, `uk_ea`, `hubeau_hydrometrie` (the sources whose
daily discharge the Archive mirrors and whose agencies publish a catchment
area). Stations come from the archive bundle, longest records first; a
station needs ten years of daily flow (`--min-years`); `--fetch-missing`
pulls stations the archive has not reached yet straight from the agency.

## What is the same as Caravan, and what is not

Same: the folder and file layout, `gauge_id = <prefix>_<station_id>`,
streamflow in mm/d with NaN (never negative numbers) for gaps, daily values
in local time, values rounded to two decimals, and the climate indices,
which are a port of `caravan_utils.calculate_climate_indices` (Knoben's
moisture index and seasonality, the snow fraction, the 5 x p_mean and 1 mm
frequency and duration indices) over Caravan's reference period 1981 to 2020
where the forcing covers it. The export is meant to be dropped next to a
Caravan copy or read by the same loaders.

Different, and written into `provenance.json`, `README.md` and the
`licenses/` note of every export:

- **Forcing is at the gauge point, not a basin average.** It comes from
  Open-Meteo's reanalysis blend (ERA5-Land where it has the variable, ERA5
  otherwise) and only for the daily variables Open-Meteo serves:
  `total_precipitation_sum`, `potential_evaporation_sum_FAO_PENMAN_MONTEITH`
  (FAO-56 ET0, there is no ERA5-Land potential evaporation),
  `temperature_2m_mean/min/max`, and `surface_solar_radiation_downwards_mean`
  (downward shortwave, so it is not called net radiation). `--era5` switches to
  plain ERA5. Open-Meteo meters its free tier per minute; a 40-year request is
  heavy, so the exporter pauses between gauges (`--pause`) and waits out 429s.
- **HydroATLAS attributes are BasinATLAS's own upstream values** for the
  level-12 sub-basin containing the gauge (its outlet, not the gauge, closes
  the catchment), written under Caravan's column names
  (`ele_mt_sav`, `pre_mm_syr`, ... hold the `_u`/`_p` upstream aggregate where
  BasinATLAS has one). The raw sub-basin row is kept next to them.
- **Area** is the agency's where it publishes one (USGS `drainage_area`, UK
  EA `catchmentArea`, Hub'Eau `surface_bv`), else BasinATLAS `up_area` of the
  containing sub-basin; `area_source` in `attributes_other` says which.
- No basin shapefiles.

## From Python

```python
from aquascope.archive.caravan import export_caravan, validate_caravan
report = export_caravan("uk_ea", "caravan_gb", max_stations=20)
print(report.n_ok, validate_caravan("caravan_gb", "aquascope_uk_ea"))
```

`climate_indices(frame)` and `fetch_forcing(lat, lon, start, end)` are
importable on their own.

## Towards CAMELS-TW

This is the exporter the CAMELS-TW epic
([#100](https://github.com/Rekin226/aquascope/issues/100)) needs; what it
still lacks for Taiwan is a daily discharge collector with a station catalog
([#211](https://github.com/Rekin226/aquascope/issues/211)). Once that source
is harvested, `aquascope caravan export --source taiwan_wra_flow_daily` is
the whole pipeline.
