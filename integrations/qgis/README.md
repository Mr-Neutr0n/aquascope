# AquaScope in QGIS

No plugin needed: the Archive is plain files that GDAL reads in place.

- **`aquascope_gauges.qlr`**: drag it into QGIS 3.28+ (or Layer → Add from
  Layer Definition File). It adds the world station catalog (styled by
  source, map tips with the agency link) read straight from
  `stations.parquet` on Hugging Face, and the BasinATLAS level-12 sub-basins
  from the indexed FlatGeobuf (visible when zoomed in, so only what is on
  screen is fetched).
- Prefer tiles for the sub-basins? Layer → Add Vector Tile Layer → New
  generic connection, URL
  `https://huggingface.co/datasets/Rekin226/aquascope-gauges/resolve/main/basins/lev12.pmtiles`
  (QGIS 3.32+).
- A station's daily record is `…/obs/<variable>/<source>/<station_id>.csv.gz`
  (Add Delimited Text Layer takes an HTTP URL); the Explorer link
  `https://rekin226-aquascope-explorer.static.hf.space/#s=<source>/<station_id>`
  gives flood frequency and trend for it.

Terms: catalog rows link back to the agency; mirrored observations follow the
source licence in the `license` column; BasinATLAS is CC BY 4.0 (Linke et al.
2019). Full notes in `docs/readers.md`.
