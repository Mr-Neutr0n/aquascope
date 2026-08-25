"""Tests for the Hub'Eau (France) hydrometrie collector's normalise().

These test normalise() directly with hand-built fixture rows shaped like
Hub'Eau's real observations_tr response - no network calls."""

from __future__ import annotations

from unittest.mock import Mock

from aquascope.collectors.france_hubeau import HubeauHydrometrieCollector
from aquascope.schemas.water_data import DataSource, StreamflowReading, WaterLevelReading, WaterQualitySample


class TestFranceHubeauFetchRawPagination:
    def test_follows_next_link_across_pages(self):
        collector = HubeauHydrometrieCollector()

        page1 = {
            "data": [
                {
                    "code_station": "A1",
                    "grandeur_hydro": "Q",
                    "date_obs": "2026-07-08T10:00:00Z",
                    "resultat_obs": 1.0,
                }
            ],
            "next": "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr?cursor=abc&size=1",
        }
        page2 = {
            "data": [
                {
                    "code_station": "A1",
                    "grandeur_hydro": "Q",
                    "date_obs": "2026-07-08T10:05:00Z",
                    "resultat_obs": 2.0,
                }
            ],
            # no "next" key - this is the last page
        }

        mock_get_json = Mock(side_effect=[page1, page2])
        collector.client.get_json = mock_get_json

        raw = collector.fetch_raw(code_station="A1", size=1, max_items=None)

        assert len(raw) == 2
        assert raw[0]["resultat_obs"] == 1.0
        assert raw[1]["resultat_obs"] == 2.0
        assert mock_get_json.call_count == 2

        first_args, first_kwargs = mock_get_json.call_args_list[0]
        assert first_args[0] == "/observations_tr"
        assert first_kwargs["params"]["code_entite"] == "A1"

        second_args, second_kwargs = mock_get_json.call_args_list[1]
        assert second_args[0] == page1["next"]
        assert second_kwargs["params"] is None

    def test_stops_when_max_items_reached_mid_page(self):
        collector = HubeauHydrometrieCollector()
        page1 = {
            "data": [
                {
                    "code_station": "A1",
                    "grandeur_hydro": "Q",
                    "date_obs": "2026-07-08T10:00:00Z",
                    "resultat_obs": 1.0,
                },
                {
                    "code_station": "A1",
                    "grandeur_hydro": "Q",
                    "date_obs": "2026-07-08T10:01:00Z",
                    "resultat_obs": 2.0,
                },
            ],
            "next": "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr?cursor=abc",
        }
        mock_get_json = Mock(return_value=page1)
        collector.client.get_json = mock_get_json

        raw = collector.fetch_raw(code_station="A1", size=2, max_items=1)

        assert len(raw) == 1  # truncated to max_items even mid-page
        assert mock_get_json.call_count == 1  # never followed next_link


class TestFranceHubeauNormaliseGrandeur:
    def test_water_level_row(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1250.0,
            }
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        assert isinstance(samples[0], WaterLevelReading)
        assert samples[0].source == DataSource.HUBEAU
        assert samples[0].station_id == "K002000101"
        assert samples[0].water_level == 1.25
        assert samples[0].unit == "m"

    def test_discharge_row(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(
            return_value={"data": [{"code_site": "K0020001", "surface_bv": 120.0}]}
        )
        raw = [
            {
                "code_station": "K002000101",
                "code_site": "K0020001",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 84.3,
            }
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        assert isinstance(samples[0], StreamflowReading)
        assert samples[0].discharge_cms == 0.0843
        assert samples[0].unit == "m3/s"
        assert samples[0].catchment_area_km2 == 120.0

    def test_unknown_grandeur_falls_back_to_raw_code(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "X",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1.0,
            }
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        assert isinstance(samples[0], WaterQualitySample)
        assert samples[0].parameter == "X"
        assert samples[0].unit == ""


class TestFranceHubeauNormaliseLocation:
    def test_populates_location_when_coords_present(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "A402061001",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": -212.0,
                "latitude": 47.866921289,
                "longitude": 6.796285291,
            }
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        assert samples[0].location is not None
        assert samples[0].location.latitude == 47.866921289
        assert samples[0].location.longitude == 6.796285291

    def test_location_none_when_coords_absent(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(return_value={"data": []})
        raw = [
            {
                "code_station": "K002000101",
                "code_site": "K0020001",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 84.3,
            }
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        assert samples[0].location is None


class TestFranceHubeauNormaliseDatetime:
    def test_z_suffix_parses_to_tz_naive(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(return_value={"data": []})
        raw = [
            {
                "code_station": "K002000101",
                "code_site": "K0020001",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 84.3,
            }
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        dt = samples[0].reading_datetime
        assert dt.tzinfo is None
        assert dt.isoformat() == "2026-07-08T10:00:00"


class TestFranceHubeauNormaliseLogging:
    def test_warns_with_skip_count_when_rows_skipped(self, caplog):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1250.0,
            },
            {
                "code_station": "K002000101",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:05:00Z",
                "resultat_obs": None,
            },
        ]
        with caplog.at_level("WARNING"):
            samples = collector.normalise(raw)
        assert len(samples) == 1
        assert any("skipped 1/2" in r.message for r in caplog.records)

    def test_no_warning_when_nothing_skipped(self, caplog):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1250.0,
            }
        ]
        with caplog.at_level("WARNING"):
            collector.normalise(raw)
        assert not any("skipped" in r.message for r in caplog.records)


class TestFranceHubeauNormaliseEdgeCases:
    def test_skips_row_with_null_value(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:05:00Z",
                "resultat_obs": None,
            }
        ]
        assert collector.normalise(raw) == []

    def test_skips_row_missing_station_id(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:05:00Z",
                "resultat_obs": 10.0,
            }
        ]
        assert collector.normalise(raw) == []

    def test_skips_row_with_unparseable_datetime(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "Q",
                "date_obs": "not-a-date",
                "resultat_obs": 10.0,
            }
        ]
        assert collector.normalise(raw) == []

    def test_mixed_batch_skips_only_invalid_rows(self):
        collector = HubeauHydrometrieCollector()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1250.0,
            },
            {
                "code_station": "K002000101",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:05:00Z",
                "resultat_obs": None,
            },
        ]
        samples = collector.normalise(raw)
        assert len(samples) == 1
        assert isinstance(samples[0], WaterLevelReading)

    def test_batch_survives_unknown_site_in_metadata_lookup(self, caplog):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(return_value={"data": []})
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1250.0,
            },
            {
                "code_station": "K002000102",
                "code_site": "UNKNOWN_SITE",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:05:00Z",
                "resultat_obs": 84.3,
            },
        ]
        with caplog.at_level("WARNING"):
            samples = collector.normalise(raw)
        assert len(samples) == 2
        discharge = next(s for s in samples if isinstance(s, StreamflowReading))
        assert discharge.catchment_area_km2 is None
        assert any(
            "No metadata found for site code UNKNOWN_SITE" in r.message
            for r in caplog.records
        )


class TestFranceHubeauCatchmentAreaMetadata:
    def test_returns_empty_dict_without_network_for_empty_request(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock()

        assert collector._get_catchment_areas(set()) == {}
        collector.client.get_json.assert_not_called()

    def test_maps_catchment_area_for_requested_codes(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(
            return_value={
                "data": [
                    {"code_site": "K0020001", "surface_bv": 1250.5},
                    {"code_site": "K0020002", "surface_bv": 300.0},
                ]
            }
        )

        areas = collector._get_catchment_areas({"K0020001", "K0020002"})

        assert areas == {"K0020001": 1250.5, "K0020002": 300.0}
        collector.client.get_json.assert_called_once_with(
            "referentiel/sites",
            params={
                "size": 10_000,
                "fields": "code_site,surface_bv",
                "f": "json",
            },
        )

    def test_ignores_sites_not_requested(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(
            return_value={
                "data": [
                    {"code_site": "K0020001", "surface_bv": 1250.5},
                    {"code_site": "OTHER_SITE", "surface_bv": 999.0},
                ]
            }
        )

        areas = collector._get_catchment_areas({"K0020001"})

        assert areas == {"K0020001": 1250.5}

    def test_returns_none_for_codes_absent_from_response(self, caplog):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(return_value={"data": []})

        with caplog.at_level("WARNING"):
            areas = collector._get_catchment_areas({"UNKNOWN_SITE"})

        assert areas == {"UNKNOWN_SITE": None}
        assert any(
            "No metadata found for site code UNKNOWN_SITE" in r.message
            for r in caplog.records
        )

    def test_returns_none_when_catchment_area_is_none(self, caplog):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(
            return_value={
                "data": [
                    {
                        "code_site": "K0020001",
                        "surface_bv": None,
                    }
                ]
            }
        )

        with caplog.at_level("WARNING"):
            areas = collector._get_catchment_areas({"K0020001"})

        assert areas == {"K0020001": None}
        assert any(
            "Metadata for site K0020001 does not contain catchment area data." in r.message
            for r in caplog.records
        )

    def test_returns_empty_dict_on_runtime_error(self, caplog):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(side_effect=RuntimeError("API error"))

        with caplog.at_level("WARNING"):
            areas = collector._get_catchment_areas({"K0020001"})

        assert areas == {}
        assert any(
            "Cannot obtain hydrometric site metadata" in r.message
            for r in caplog.records
        )

    def test_returns_empty_dict_on_value_error(self, caplog):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(side_effect=ValueError("HTML response"))

        with caplog.at_level("WARNING"):
            areas = collector._get_catchment_areas({"K0020001"})

        assert areas == {}
        assert any(
            "Cannot obtain hydrometric site metadata" in r.message
            for r in caplog.records
        )

    def test_resolves_many_rows_in_single_referentiel_call(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock(
            return_value={
                "data": [
                    {"code_site": "K0020001", "surface_bv": 120.0},
                    {"code_site": "K0020002", "surface_bv": 340.5},
                ]
            }
        )
        raw = [
            {
                "code_station": f"S{i}",
                "code_site": code,
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 10.0,
            }
            for i, code in enumerate(["K0020001", "K0020002", "K0020001", "K0020002"])
        ]

        samples = collector.normalise(raw)

        assert len(samples) == 4
        assert all(isinstance(s, StreamflowReading) for s in samples)
        collector.client.get_json.assert_called_once_with(
            "referentiel/sites",
            params={"size": 10_000, "fields": "code_site,surface_bv", "f": "json"},
        )
        assert [s.catchment_area_km2 for s in samples] == [120.0, 340.5, 120.0, 340.5]

    def test_no_referentiel_call_without_discharge_rows(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "H",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 1250.0,
            }
        ]

        samples = collector.normalise(raw)

        assert len(samples) == 1
        assert isinstance(samples[0], WaterLevelReading)
        collector.client.get_json.assert_not_called()

    def test_no_referentiel_call_for_empty_batch(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock()

        assert collector.normalise([]) == []
        collector.client.get_json.assert_not_called()

    def test_no_referentiel_call_when_discharge_values_are_null(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock()
        raw = [
            {
                "code_station": "K002000101",
                "code_site": "K0020001",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": None,
            }
        ]

        assert collector.normalise(raw) == []
        collector.client.get_json.assert_not_called()

    def test_no_referentiel_call_when_discharge_row_lacks_site_code(self):
        collector = HubeauHydrometrieCollector()
        collector.client.get_json = Mock()
        raw = [
            {
                "code_station": "K002000101",
                "grandeur_hydro": "Q",
                "date_obs": "2026-07-08T10:00:00Z",
                "resultat_obs": 84.3,
            }
        ]

        samples = collector.normalise(raw)

        assert len(samples) == 1
        assert samples[0].catchment_area_km2 is None
        collector.client.get_json.assert_not_called()



class TestFranceHubeauElaborated:
    """``elaborated="QmnJ"`` fetches /obs_elab (multi-decade daily means) and normalises like Q."""

    def test_fetch_raw_hits_obs_elab_with_date_bounds(self):
        collector = HubeauHydrometrieCollector()
        collector.client = Mock()
        collector.client.get_json.return_value = {"data": [], "next": None}
        collector.fetch_raw(
            code_station="F700000103", elaborated="QmnJ", date_debut_obs="1990-01-01T00:00:00Z",
            date_fin_obs="2026-08-17", size=20000, max_items=None,
        )
        url, kwargs = collector.client.get_json.call_args.args[0], collector.client.get_json.call_args.kwargs
        assert url == "/obs_elab"
        params = kwargs["params"]
        assert params["code_entite"] == "F700000103" and params["grandeur_hydro_elab"] == "QmnJ"
        assert params["date_debut_obs_elab"] == "1990-01-01" and params["date_fin_obs_elab"] == "2026-08-17"
        assert "grandeur_hydro" not in params and "date_debut_obs" not in params

    def test_unknown_elaborated_code_rejected(self):
        collector = HubeauHydrometrieCollector()
        collector.client = Mock()
        try:
            collector.fetch_raw(code_station="X", elaborated="QmJ")
        except ValueError as exc:
            assert "QmJ" in str(exc)
        else:
            raise AssertionError("expected ValueError")
        collector.client.get_json.assert_not_called()

    def test_normalise_elaborated_rows_to_streamflow(self):
        collector = HubeauHydrometrieCollector()
        collector.client = Mock()
        collector.client.get_json.return_value = {"data": [{"code_site": "F7000001", "surface_bv": 43800.0}]}
        raw = [
            {"code_site": "F7000001", "code_station": "F700000103", "date_obs_elab": "2024-01-01",
             "resultat_obs_elab": 635776.0, "grandeur_hydro_elab": "QmnJ", "longitude": 2.36, "latitude": 48.84},
            {"code_site": "F7000001", "code_station": "F700000103", "date_obs_elab": "2024-01-02",
             "resultat_obs_elab": None, "grandeur_hydro_elab": "QmnJ", "longitude": 2.36, "latitude": 48.84},
            {"code_site": "F7000001", "code_station": "F700000103", "date_obs_elab": "2024-01-01",
             "resultat_obs_elab": 4500.0, "grandeur_hydro_elab": "HIXnJ", "longitude": 2.36, "latitude": 48.84},
        ]
        recs = collector.normalise(raw)
        flows = [r for r in recs if isinstance(r, StreamflowReading)]
        levels = [r for r in recs if isinstance(r, WaterLevelReading)]
        assert len(flows) == 1 and len(levels) == 1
        assert flows[0].discharge_cms == 635.776 and flows[0].catchment_area_km2 == 43800.0
        assert flows[0].reading_datetime.isoformat() == "2024-01-01T00:00:00"
        assert levels[0].water_level == 4.5
