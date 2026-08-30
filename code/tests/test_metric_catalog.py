"""The metric vocabulary must have exactly one source of truth.

The seventeen executable metric names were written out by hand in six places:
the task contract that validates them, two dispatch sets inside the scoring
module, an analysis-axis set, a two-band detector, and the compiler prompt.
Nothing compared those copies, so they drifted -- the prompt offered fourteen of
the seventeen, which left three executable metrics unreachable by any request.

These tests pin every copy to the catalogue.  A rename now fails here instead of
failing in a paid run, and a metric added to the contract without a catalogue
row cannot be imported at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from optomind_optics.harness import benchmark_delivery, design_task, objectives
from optomind_optics.harness.design_task import (
    SUPPORTED_OBJECTIVE_METRICS,
    ObjectivePreference,
)
from optomind_optics.harness.metric_catalog import (
    METRIC_CATALOG,
    REPORT_ONLY_METRICS,
    SCOREABLE_METRICS,
    canonical_metric_id,
    catalog_document,
    verify_metric_reference,
    verify_metric_selection,
)
from tmm_engine.protocol import (
    SUPPORTED_BAND_OBSERVABLES,
    SUPPORTED_REQUESTED_OUTPUTS,
    describe_capabilities,
)


_PROMPT = (
    Path(__file__).resolve().parents[1]
    / "prompts"
    / "optical_harness"
    / "TMM Task Compiler.txt"
)


# --- the catalogue against the contract that actually validates -------------


class TestCatalogueMatchesTheTaskContract:
    def test_names_are_the_same_set(self) -> None:
        assert set(METRIC_CATALOG) == set(SUPPORTED_OBJECTIVE_METRICS)

    def test_report_only_partition_matches(self) -> None:
        assert set(REPORT_ONLY_METRICS) == set(design_task._REPORT_ONLY_METRICS)
        assert set(SCOREABLE_METRICS) | set(REPORT_ONLY_METRICS) == set(METRIC_CATALOG)
        assert not set(SCOREABLE_METRICS) & set(REPORT_ONLY_METRICS)

    def test_single_interval_metrics_match_the_contract(self) -> None:
        single = {
            name
            for name, row in METRIC_CATALOG.items()
            if row.required_region_keys == ("wavelength_nm",)
        }
        assert single == set(design_task._BAND_METRICS)

    def test_contrast_metric_is_the_only_two_interval_metric(self) -> None:
        two_interval = {
            name
            for name, row in METRIC_CATALOG.items()
            if len(row.required_region_keys) == 2
        }
        assert two_interval == {"band_emissivity_contrast"}
        assert METRIC_CATALOG["band_emissivity_contrast"].required_region_keys == (
            "preferred_wavelength_nm",
            "suppressed_wavelength_nm",
        )

    @pytest.mark.parametrize("name", sorted(METRIC_CATALOG))
    def test_every_row_builds_a_preference_the_contract_accepts(self, name: str) -> None:
        row = METRIC_CATALOG[name]
        region = {key: [500.0, 700.0] for key in row.required_region_keys}
        if len(row.required_region_keys) == 2:
            region["suppressed_wavelength_nm"] = [1200.0, 1600.0]
        sense = row.allowed_senses[0]
        preference = ObjectivePreference(
            objective_id=f"obj_{name}",
            metric=name,
            sense=sense,
            region=region,
            target=1.0 if sense == "match" else None,
        )
        assert preference.metric == name


# --- the catalogue against the scoring module's dispatch --------------------


class TestCatalogueMatchesTheScoringDispatch:
    @pytest.mark.parametrize("name", sorted(METRIC_CATALOG))
    def test_observable_agrees_with_the_scorer(self, name: str) -> None:
        row = METRIC_CATALOG[name]
        resolved = objectives._preference_observable(name)
        if len(row.observables) == 1:
            assert resolved == row.observables[0]
        else:
            # Multi-output and pure-report metrics take a different branch of
            # the scorer, which resolves no single observable for them.
            assert resolved is None
            assert not row.scoreable

    def test_worst_case_rows_are_exactly_the_worst_case_reduction(self) -> None:
        declared = {
            name
            for name, row in METRIC_CATALOG.items()
            if row.reduction == "band_worst_case"
        }
        source = objectives.__file__
        text = Path(source).read_text(encoding="utf-8")
        # The scorer decides worst-case behaviour from an inline set literal.
        block = text.split("worst_case = metric in {", 1)[1].split("}", 1)[0]
        assert set(re.findall(r'"([a-z_]+)"', block)) == declared

    def test_annotated_aggregation_set_stays_inside_the_catalogue(self) -> None:
        # ``_BAND_RTA_METRICS`` only decides whether an attainment row carries
        # an ``aggregation`` label, so it is allowed to be a subset -- but every
        # member must still be a single-observable band reduction.
        for name in objectives._BAND_RTA_METRICS:
            row = METRIC_CATALOG[name]
            assert row.reduction in {"band_mean", "band_worst_case"}
            assert len(row.observables) == 1


# --- the catalogue against the two remaining hand-written copies ------------


class TestCatalogueMatchesTheAnalysisSets:
    def test_band_preference_axis_names_are_all_legal(self) -> None:
        text = Path(benchmark_delivery.__file__).read_text(encoding="utf-8")
        block = text.split('if "band_preference" in axes:', 1)[1]
        block = block.split("in {", 1)[1].split("}", 1)[0]
        names = set(re.findall(r'"([A-Za-z_]+)"', block))
        assert names, "the band_preference axis no longer lists metric names"
        assert names <= set(SCOREABLE_METRICS)

    def test_two_band_detector_names_are_all_legal(self) -> None:
        from optomind_optics.harness import task_compiler

        text = Path(task_compiler.__file__).read_text(encoding="utf-8")
        block = text.split("directional_bands = {", 1)[1]
        block = block.split("in {", 1)[1].split("}", 1)[0]
        names = set(re.findall(r'"([A-Za-z_]+)"', block))
        assert names, "the two-band detector no longer lists metric names"
        assert names <= set(SCOREABLE_METRICS)
        for name in names:
            assert METRIC_CATALOG[name].required_region_keys == ("wavelength_nm",)

    @pytest.mark.parametrize(
        ("mapping_name", "expected_reduction"),
        [
            ("metric_by_observable", "band_mean"),
            ("worst_case_metric_by_observable", "band_worst_case"),
        ],
    )
    def test_objective_rebuild_mapping_is_consistent(
        self, mapping_name: str, expected_reduction: str
    ) -> None:
        # ``_synchronize_objectives_from_targets`` rebuilds ranking objectives
        # from the optimizer targets through two inline observable->metric
        # dictionaries.  Several catalogue rows share an observable, so the
        # mapping cannot be derived uniquely -- but each name it picks must
        # exist, must reduce the observable it is filed under, and must use the
        # reduction the branch claims.
        from optomind_optics.harness import task_compiler

        text = Path(task_compiler.__file__).read_text(encoding="utf-8")
        block = text.split(f"{mapping_name} = {{", 1)[1].split("}", 1)[0]
        pairs = re.findall(r'"([RTA])":\s*"([a-z_]+)"', block)
        assert len(pairs) == 3, f"{mapping_name} no longer maps all three observables"
        for observable, metric in pairs:
            row = METRIC_CATALOG[metric]
            assert row.observables == (observable,)
            assert row.reduction == expected_reduction
            assert row.scoreable


class TestCatalogueMatchesThePrompt:
    def test_prompt_offers_every_executable_metric(self) -> None:
        offered: set[str] | None = None
        for line in _PROMPT.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- Allowed objective metrics are exactly:"):
                offered = set(
                    re.findall(r"[A-Za-z_]+", stripped.split(":", 1)[1])
                )
                break
        assert offered is not None, "the template no longer enumerates the metrics"
        assert offered == set(METRIC_CATALOG)

    def test_prompt_report_only_sentence_matches_the_partition(self) -> None:
        text = _PROMPT.read_text(encoding="utf-8")
        line = next(
            candidate
            for candidate in text.splitlines()
            if candidate.strip().startswith("- Compound report metrics")
        )
        named = set(re.findall(r"[a-z_]+_[A-Za-z]+", line))
        assert named & set(REPORT_ONLY_METRICS) == set(REPORT_ONLY_METRICS), (
            "a report-only metric is missing from the sense=\"report\" rule"
        )


# --- the engine manifest as the source of the band-reduction atoms ----------


class TestEngineManifestDeclaresTheBandVocabulary:
    def test_band_observables_are_declared_outputs(self) -> None:
        assert set(SUPPORTED_BAND_OBSERVABLES) <= set(SUPPORTED_REQUESTED_OUTPUTS)

    def test_the_three_observable_declarations_agree(self) -> None:
        manifest = describe_capabilities()
        assert tuple(manifest.spectral_metrics.band_observables) == tuple(
            SUPPORTED_BAND_OBSERVABLES
        )
        assert tuple(manifest.optimization.objective_outputs) == tuple(
            SUPPORTED_BAND_OBSERVABLES
        )
        assert tuple(
            manifest.research_interface.objectives.observables
        ) == tuple(SUPPORTED_BAND_OBSERVABLES)

    def test_every_catalogue_observable_is_band_reducible(self) -> None:
        allowed = set(describe_capabilities().spectral_metrics.band_observables)
        for row in METRIC_CATALOG.values():
            assert set(row.observables) <= allowed

    def test_interval_key_and_unit_come_from_the_manifest(self) -> None:
        spectral = describe_capabilities().spectral_metrics
        assert spectral.interval_key == "wavelength_nm"
        assert spectral.interval_unit == "nm"
        assert spectral.reduction_confers_physics_validity is False


# --- the checker the metric-selection stage calls ---------------------------


class TestVerifyMetricReference:
    def test_accepts_a_well_formed_reference(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "mean_reflectance",
                "sense": "maximize",
                "region": {"wavelength_nm": [300, 800]},
            }
        )
        assert verdict.ok
        assert verdict.canonical_id == "mean_reflectance@300-800nm"
        assert verdict.normalized["region"]["wavelength_nm"] == [300.0, 800.0]

    def test_converts_a_declared_micrometre_band(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "mean_absorption",
                "sense": "maximize",
                "wavelength_unit": "um",
                "region": {"wavelength_nm": [5, 13]},
            }
        )
        assert verdict.ok
        assert verdict.normalized["region"]["wavelength_nm"] == [5000.0, 13000.0]
        assert verdict.canonical_id == "mean_absorption@5000-13000nm"

    def test_rejects_an_invented_name_and_says_what_is_legal(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "mean_reflectivity",
                "sense": "maximize",
                "region": {"wavelength_nm": [300, 800]},
            }
        )
        assert not verdict.ok
        assert "mean_reflectance" in verdict.repair_hint

    def test_rejects_scoring_a_report_only_metric(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "layer_absorption",
                "sense": "maximize",
                "region": {"wavelength_nm": [300, 800]},
            }
        )
        assert not verdict.ok
        assert "report-only" in verdict.repair_hint

    def test_rejects_the_wrong_interval_fields_for_the_contrast_metric(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "band_emissivity_contrast",
                "sense": "maximize",
                "region": {"wavelength_nm": [300, 800]},
            }
        )
        assert not verdict.ok
        assert "preferred_wavelength_nm" in verdict.repair_hint

    def test_rejects_identical_contrast_bands(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "band_emissivity_contrast",
                "sense": "maximize",
                "region": {
                    "preferred_wavelength_nm": [8000, 13000],
                    "suppressed_wavelength_nm": [8000, 13000],
                },
            }
        )
        assert not verdict.ok
        assert "identical" in verdict.repair_hint

    def test_rejects_a_match_sense_without_a_target(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "mean_transmittance",
                "sense": "match",
                "region": {"wavelength_nm": [540, 560]},
            }
        )
        assert not verdict.ok
        assert "target" in verdict.repair_hint

    def test_rejects_a_non_positive_band(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "mean_reflectance",
                "sense": "maximize",
                "region": {"wavelength_nm": [0, 800]},
            }
        )
        assert not verdict.ok

    def test_keeps_channel_selectors(self) -> None:
        verdict = verify_metric_reference(
            {
                "metric": "mean_reflectance",
                "sense": "maximize",
                "region": {
                    "wavelength_nm": [300, 800],
                    "angle_deg": 45.0,
                    "polarization": "s",
                },
            }
        )
        assert verdict.ok
        assert verdict.normalized["region"]["angle_deg"] == 45.0
        assert verdict.normalized["region"]["polarization"] == "s"

    def test_rejects_a_non_object_reference(self) -> None:
        assert not verify_metric_reference("mean_reflectance").ok
        assert not verify_metric_reference(None).ok

    def test_selection_keeps_one_verdict_per_reference(self) -> None:
        verdicts = verify_metric_selection(
            [
                {
                    "metric": "mean_reflectance",
                    "sense": "maximize",
                    "region": {"wavelength_nm": [300, 800]},
                },
                {"metric": "nope", "sense": "maximize", "region": {}},
            ]
        )
        assert [verdict.ok for verdict in verdicts] == [True, False]

    @pytest.mark.parametrize("name", sorted(SCOREABLE_METRICS))
    def test_every_scoreable_metric_has_a_verifiable_reference(self, name: str) -> None:
        row = METRIC_CATALOG[name]
        region = {key: [500.0, 700.0] for key in row.required_region_keys}
        if len(row.required_region_keys) == 2:
            region["suppressed_wavelength_nm"] = [1200.0, 1600.0]
        verdict = verify_metric_reference(
            {"metric": name, "sense": "maximize", "region": region}
        )
        assert verdict.ok, verdict.repair_hint
        assert verdict.canonical_id.startswith(f"{name}@")


class TestCanonicalMetricId:
    def test_ordinary_metric(self) -> None:
        assert (
            canonical_metric_id("mean_absorption", {"wavelength_nm": [5000, 13000]})
            == "mean_absorption@5000-13000nm"
        )

    def test_contrast_metric_names_both_bands(self) -> None:
        assert canonical_metric_id(
            "band_emissivity_contrast",
            {
                "preferred_wavelength_nm": [8000, 13000],
                "suppressed_wavelength_nm": [300, 2500],
            },
        ) == "band_emissivity_contrast@8000-13000nm_vs_300-2500nm"

    def test_ids_are_distinct_per_band(self) -> None:
        first = canonical_metric_id("mean_reflectance", {"wavelength_nm": [300, 800]})
        second = canonical_metric_id("mean_reflectance", {"wavelength_nm": [800, 1200]})
        assert first != second


# --- the document handed to the metric-selection prompt --------------------


class TestCatalogDocument:
    def test_default_document_hides_unscoreable_metrics(self) -> None:
        document = catalog_document()
        names = {row["name"] for row in document["metrics"]}
        assert names == set(SCOREABLE_METRICS)
        assert not names & set(REPORT_ONLY_METRICS)

    def test_full_document_lists_every_metric(self) -> None:
        document = catalog_document(scoreable_only=False)
        assert {row["name"] for row in document["metrics"]} == set(METRIC_CATALOG)

    def test_document_carries_the_engine_band_rules(self) -> None:
        document = catalog_document()
        band = document["band_reduction"]
        assert band["interval_key"] == "wavelength_nm"
        assert band["interval_unit"] == "nm"
        assert band["band_observables"] == list(SUPPORTED_BAND_OBSERVABLES)

    def test_document_is_json_serializable(self) -> None:
        import json

        payload = json.dumps(catalog_document(scoreable_only=False))
        assert "mean_reflectance" in payload

    def test_document_states_it_does_not_confer_validity(self) -> None:
        assert "physically accepted" in catalog_document()["scoring_role"]
