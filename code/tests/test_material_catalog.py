"""The material vocabulary: can the engine actually resolve what a route names?

Route planning happens before any task is compiled, so this module is the only
place a bad material name can be caught while it is still cheap to fix.  What
it must get right, and what these tests hold it to:

* the listed names are exported from the registry and every one of them
  resolves, so the list cannot promise a name the engine would refuse;
* the list carries no wavelength coverage, because which band a material must
  cover is settled by the scoring standard and not here;
* a name the registry cannot pin to one dataset is rejected with the closest
  legal name attached, so the rejection is actionable;
* the list is the guaranteed path, not a whitelist -- a name the registry
  resolves anyway is still allowed;
* scanning free text never mistakes ordinary English or an optics abbreviation
  for a material, because a false positive there fails a sound route.
"""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness.material_catalog import (
    COVERAGE_DEFERRAL,
    MEASURED_NOT_TUNABLE,
    RouteMaterialCatalog,
    _looks_like_formula,
)


@pytest.fixture(scope="module")
def catalog() -> RouteMaterialCatalog:
    return RouteMaterialCatalog()


# ---------------------------------------------------------------------------
# 1. The exported vocabulary
# ---------------------------------------------------------------------------


class TestTheExportedNames:
    def test_the_registry_supplies_the_names(self, catalog) -> None:
        assert catalog.names
        assert "si3n4" in catalog.names and "sio2" in catalog.names

    def test_every_listed_name_resolves(self, catalog) -> None:
        """The list is a promise; a name that fails to resolve breaks it."""

        for name in catalog.names:
            assert catalog.verify(name).ok, name

    def test_the_list_carries_no_wavelengths(self, catalog) -> None:
        payload = json.dumps(catalog.prompt_payload()).casefold()
        assert "wavelength" not in payload
        assert "micron" not in payload

    def test_the_missing_coverage_check_is_stated_not_implied(self, catalog) -> None:
        """A resolvable name may still lack data at the band asked for."""

        assert "interpolation" in COVERAGE_DEFERRAL
        assert catalog.prompt_payload()["coverage"] == COVERAGE_DEFERRAL

    def test_the_measured_constants_are_declared_off_limits(self) -> None:
        assert "never design variables" in MEASURED_NOT_TUNABLE

    def test_the_provenance_says_where_the_list_came_from(self, catalog) -> None:
        provenance = catalog.provenance()
        assert provenance["guaranteed_names"] == list(catalog.names)
        assert "MaterialRegistry" in provenance["source"]
        # Serialisable, because it is written to an artifact.
        json.dumps(provenance)


# ---------------------------------------------------------------------------
# 1b. Which dataset was used, not just which name
# ---------------------------------------------------------------------------


class TestTheDatasetIsIdentified:
    """A canonical name is not provenance.

    Observed live: told that ``au`` is ambiguous among three datasets, the
    model answered ``gold``, which resolves to a different dataset again.  A
    record holding only the word ``gold`` cannot say which measurement ran.
    """

    def test_a_local_dataset_names_its_file(self, catalog) -> None:
        verdict = catalog.verify("sio2")

        assert verdict.ok
        assert verdict.provider == "local_csv"
        assert verdict.dataset() == "local_csv:sio2"
        assert verdict.source and verdict.source.casefold().endswith("sio2.csv")

    def test_a_remote_page_names_its_library_coordinates(self, catalog) -> None:
        verdict = catalog.verify("gold")

        assert verdict.ok
        assert verdict.provider == "rii_sqlite"
        assert verdict.dataset_id
        assert verdict.source

    def test_a_synonym_does_not_silently_pick_one_of_the_ambiguous_datasets(
        self, catalog
    ) -> None:
        """The synonym escapes the ambiguity by landing somewhere else.

        The exact ids are not asserted -- they belong to a third-party library
        and may be renumbered.  What must hold is that the dataset the run got
        is recorded, and that it is not one of the ones the registry refused to
        choose between, because a reader would otherwise assume the refusal was
        somehow resolved.
        """

        ambiguous = catalog.verify("au")
        synonym = catalog.verify("gold")

        assert not ambiguous.ok and ambiguous.code == "ambiguous"
        assert synonym.ok
        assert synonym.dataset() not in set(ambiguous.choices)

    def test_the_coverage_of_the_chosen_dataset_is_recorded(self, catalog) -> None:
        low, high = catalog.verify("sio2").coverage_um

        assert 0.0 < low < high

    def test_a_dataset_that_cannot_serve_visible_light_is_visible_as_such(
        self, catalog
    ) -> None:
        """``sio`` resolves cleanly and is useless for a visible-band run.

        It is silicon monoxide measured in the extreme ultraviolet, so a model
        that writes ``SiO`` meaning silica passes the name check and then has no
        data where it needs it.  The name gate cannot catch that; the recorded
        span is what makes it readable afterwards.
        """

        verdict = catalog.verify("sio")

        assert verdict.ok
        assert verdict.coverage_um is not None
        # 0.38um is the blue end of the visible band.
        assert verdict.coverage_um[1] < 0.38

    def test_the_record_survives_serialisation(self, catalog) -> None:
        payload = catalog.verify("tio2").as_dict()

        assert payload["dataset"]
        assert payload["coverage_um"] and len(payload["coverage_um"]) == 2
        json.dumps(payload)

    def test_a_rejected_name_carries_no_dataset(self, catalog) -> None:
        """Nothing ran, so there is nothing to attribute."""

        payload = catalog.verify("unobtainium").as_dict()

        assert payload["dataset"] is None
        assert payload["coverage_um"] is None
        assert payload["source"] is None


# ---------------------------------------------------------------------------
# 2. Verification
# ---------------------------------------------------------------------------


class TestVerification:
    def test_a_loose_formula_is_refused_with_the_datasets_named(self, catalog) -> None:
        verdict = catalog.verify("SiN")
        assert not verdict.ok
        assert verdict.code == "ambiguous"
        assert len(verdict.choices) > 1
        # The short identifiers the engine itself prints, not object reprs.
        assert all(len(choice) < 40 for choice in verdict.choices)

    def test_the_refusal_offers_the_closest_legal_name(self, catalog) -> None:
        """Same elements, exact stoichiometry: SiN means si3n4 here."""

        assert "si3n4" in catalog.verify("SiN").near
        assert "si3n4" in catalog.verify("SiN").message()


class TestAnAmbiguousNameGetsAnActionableRepair:
    """A list of dataset identifiers is not an instruction.

    Observed live: ``au`` was refused with three identifiers quoted, the model
    had no guaranteed gold to fall back on, and it spent all three attempts
    before stumbling onto a synonym.  The identifiers themselves are not
    accepted as material names, so quoting them alone asks the model to guess.
    """

    def test_the_identifiers_are_not_usable_as_names(self, catalog) -> None:
        """The premise of the repair, pinned so it cannot rot silently."""

        for identifier in catalog.verify("au").choices:
            assert not catalog.verify(identifier).ok, identifier

    def test_a_name_for_each_refused_dataset_is_offered(self, catalog) -> None:
        verdict = catalog.verify("au")

        assert verdict.dataset_names
        assert len(verdict.dataset_names) == len(verdict.choices)

    def test_every_offered_name_resolves_to_one_of_the_refused_datasets(
        self, catalog
    ) -> None:
        """An offer that does not work would cost another round, not save one."""

        verdict = catalog.verify("au")
        refused = set(verdict.choices)

        for name in verdict.dataset_names:
            resolved = catalog.verify(name)
            assert resolved.ok, name
            assert resolved.dataset() in refused, name

    def test_the_message_says_the_identifiers_cannot_be_written(
        self, catalog
    ) -> None:
        message = catalog.verify("au").message()

        assert "not accepted as material names" in message
        assert "name one dataset directly" in message

    def test_the_count_is_not_passed_off_as_the_whole_population(
        self, catalog
    ) -> None:
        """The error lists the tie, not the library.

        Gold ties three ways among two dozen datasets; reporting three as the
        total misleads a reader into thinking the material is barely present.
        """

        verdict = catalog.verify("au")
        population = catalog.dataset_population("au")

        assert population is not None
        assert population > len(verdict.choices)
        assert "ranks 3 datasets equally" in verdict.message()

    def test_a_listed_material_is_still_steered_to_the_local_dataset(
        self, catalog
    ) -> None:
        """Naming a dataset is the fallback, not the first suggestion.

        For a material the list does carry, the local file is the better answer
        -- it is on this machine and its span is wide -- so it must come first.
        """

        message = catalog.verify("SiN").message()

        assert message.index("si3n4") < message.index("name one dataset")

    def test_the_offer_reaches_the_artifact(self, catalog) -> None:
        payload = catalog.verify("au").as_dict()

        assert payload["dataset_names"]
        json.dumps(payload)


class TestWhatTheVerifierAcceptsAndRefuses:
    def test_an_exact_name_resolves_to_its_canonical_form(self, catalog) -> None:
        assert catalog.verify("Si3N4").resolved == "si3n4"
        assert catalog.verify("TiO2").resolved == "tio2"

    def test_an_unlisted_name_the_registry_knows_is_allowed(self, catalog) -> None:
        assert catalog.verify("Ta2O5").ok
        assert "ta2o5" not in catalog.names

    def test_an_invented_material_is_refused(self, catalog) -> None:
        verdict = catalog.verify("unobtainium")
        assert not verdict.ok and verdict.code == "not_found"

    def test_prose_instead_of_a_name_is_refused(self, catalog) -> None:
        assert not catalog.verify("silicon dioxide").ok

    def test_an_empty_name_is_refused_with_the_list_offered(self, catalog) -> None:
        verdict = catalog.verify("")
        assert not verdict.ok and verdict.near

    def test_the_place_the_name_came_from_is_carried(self, catalog) -> None:
        verdict = catalog.verify("SiN", where="proposed_materials")
        assert "proposed_materials" in verdict.message()

    def test_a_repeated_name_is_reported_once(self, catalog) -> None:
        assert len(catalog.verify_all(["SiO2", "SiO2", "sio2"])) == 1


# ---------------------------------------------------------------------------
# 3. Reading materials out of free text
# ---------------------------------------------------------------------------


class TestScanningFreeText:
    def test_an_unresolvable_formula_in_prose_is_found(self, catalog) -> None:
        found = catalog.scan_text("An eight-pair SiN/SiO2 stack on silicon.")
        assert [verdict.proposed for verdict in found] == ["SiN"]

    def test_resolvable_formulas_are_silent(self, catalog) -> None:
        assert catalog.scan_text("A TiO2/SiO2 stack with Ta2O5 caps on Si.") == ()

    @pytest.mark.parametrize(
        "text",
        [
            "Report R and T for TE and TM polarization across the UV and IR bands.",
            "Use an AR coating; keep the FWHM narrow and the layer stack thin.",
            "Evidence IDs L01 and V01 support the design under normal incidence.",
            "In this route, As deposited, No further annealing is applied.",
        ],
    )
    def test_ordinary_optics_prose_is_never_read_as_a_material(
        self, catalog, text
    ) -> None:
        """A false positive here fails a physically sound route."""

        assert catalog.scan_text(text) == ()

    @pytest.mark.parametrize("token", ["SiN", "Si3N4", "Ta2O5", "GaAs", "ZnS", "H2O"])
    def test_a_real_formula_is_recognised(self, token) -> None:
        assert _looks_like_formula(token)

    @pytest.mark.parametrize(
        "token",
        [
            "UV",
            "IR",
            "TE",
            "TM",
            "NIR",
            "AR",
            "In",
            "As",
            "No",
            "IDs",
            "V01",
            "L01",
            "FWHM",
            "layer",
            "stack",
            "bands",
            "under",
            "with",
            "caps",
            "Design",
        ],
    )
    def test_an_abbreviation_or_english_word_is_not_a_formula(self, token) -> None:
        assert not _looks_like_formula(token)

    def test_empty_text_is_not_scanned(self, catalog) -> None:
        assert catalog.scan_text("") == () and catalog.scan_text(None) == ()


# ---------------------------------------------------------------------------
# 4. Working without the engine
# ---------------------------------------------------------------------------


class _StubRegistry:
    """Stands in for the registry, so a caller can pin the vocabulary."""

    def resolve(self, material, **_kwargs):
        if str(material).casefold() in {"sio2", "ag"}:
            return type("Ref", (), {"material": str(material).casefold()})()
        raise LookupError(
            f"Material reference {material!r} is ambiguous among: stub:1, stub:2"
        )


class TestInjectedNames:
    def test_names_may_be_supplied_instead_of_exported(self) -> None:
        catalog = RouteMaterialCatalog(registry=_StubRegistry(), names=("sio2", "ag"))
        assert catalog.names == ("sio2", "ag")
        assert catalog.verify("sio2").ok
        assert not catalog.verify("SiN").ok

    def test_an_ambiguity_is_read_out_of_the_message_when_it_has_to_be(self) -> None:
        """Providers differ; the identifiers the message names are the record."""

        catalog = RouteMaterialCatalog(registry=_StubRegistry(), names=("sio2",))
        assert catalog.verify("SiN").choices == ("stub:1", "stub:2")
