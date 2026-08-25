# AquaScope Gauges: a GeoLibre plugin

Every public water gauge AquaScope can reach (45,000+ stations from USGS, UK
Environment Agency, Hub'Eau, PEGELONLINE, Ireland OPW, Taiwan CWA and more), as a
clustered layer inside [GeoLibre](https://github.com/opengeos/GeoLibre). Click a
gauge for its name, agency and period, then jump to the observed record and flood
frequency in [AquaScope Explorer](https://rekin226-aquascope-explorer.static.hf.space/)
or to the agency page. A right-sidebar panel holds the legend, per-source counts
and a name search.

Self-contained ES module (`index.js`), no build step. It fetches the station
catalog straight from the public dataset
[`Rekin226/aquascope-gauges`](https://huggingface.co/datasets/Rekin226/aquascope-gauges)
(`stations.geojson`, CORS-enabled, refreshed weekly). Nothing else is bundled.

## Try it

The built plugin is served with the Explorer:

- manifest: `https://rekin226-aquascope-explorer.static.hf.space/geolibre-plugin/plugin.json`

In a GeoLibre build pointed at a registry that lists this manifest, install it
from **Settings → Manage Plugins**. To test locally with GeoLibre's own
registry flow, follow
[opengeos/geolibre-plugins → Develop](https://opengeos.org/geolibre-plugins/develop/).

## Registry entry (for the opengeos/geolibre-plugins PR)

```json
{
  "id": "aquascope-gauges",
  "name": "AquaScope Gauges",
  "version": "0.1.0",
  "description": "Every public water gauge AquaScope can reach (45,000+ stations from USGS, UK EA, Hub'Eau, PEGELONLINE, Ireland OPW, Taiwan CWA) as a clustered layer; click a gauge for its observed record and flood frequency in AquaScope Explorer.",
  "author": "AquaScope contributors",
  "homepage": "https://github.com/Rekin226/aquascope/tree/main/integrations/geolibre",
  "manifestUrl": "https://rekin226-aquascope-explorer.static.hf.space/geolibre-plugin/plugin.json",
  "categories": ["Hydrology", "Data"],
  "minGeoLibreVersion": "1.9.0"
}
```

Or copy `plugin.json`, `index.js`, `style.css` into `plugins/aquascope-gauges/`
in that repo and point `manifestUrl` at the relative path.

Licence: MIT (this plugin). Station data: per-source open licences, listed on
the dataset card.
