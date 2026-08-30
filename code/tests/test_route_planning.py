"""Route planning: does the study explore the axes the problem actually has?

This stage decides the ceiling of a whole run, because an axis nobody proposed
cannot be reached by iterating inside the axes that were.  What it must get
right, and what these tests hold it to:

* the number of routes is the model's answer inside a bound, not a fixed
  portfolio width;
* fewer than the minimum is regenerated, more than the maximum is truncated by
  the model's own priority ordering;
* every route states what it tunes, and two routes never claim the same axis;
* the literature harvest is bounded before it reaches the prompt, and a harvest
  that fails leaves the stage working from theory instead of failing;
* everything the routes were justified by is recorded, and a citation the
  harvest cannot support is dropped rather than believed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import pytest

from optomind_optics.harness.route_planning import (
    DEFAULT_LITERATURE_LIMIT,
    DEFAULT_MAXIMUM_ROUTES,
    ROUTE_PLANNING_SCHEMA_VERSION,
    LiteratureHarvest,
    QwenLiteratureRoutePlanner,
    _axis_signature,
    _fallback_queries,
    _identity,
)


QUESTION = (
    "我需要一个在 300-800nm 高反射、同时在 5-13um 高吸收的多层膜设计，"
    "衬底是硅，镀膜层数不要太多"
)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class ScriptedClient:
    """Returns queued replies; records what it was asked."""

    model_name = "qwen3.5-plus"

    def __init__(self, replies: Sequence[Any]) -> None:
        self.replies = list(replies)
        self.calls: List[List[Mapping[str, str]]] = []

    def call(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append([dict(message) for message in messages])
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return {
            "content": content,
            "_llm_usage": {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160},
        }

    @property
    def payloads(self) -> List[Dict[str, Any]]:
        """The user-role payload of each call, parsed."""

        return [json.loads(call[-1]["content"]) for call in self.calls]


class ScriptedLiterature:
    """A paper-search double; records the limits it was asked for."""

    def __init__(self, results: Mapping[str, Any] | None = None, *, error: Exception | None = None) -> None:
        self.results = dict(results or {})
        self.error = error
        self.requests: List[tuple[str, int]] = []

    def search_papers(self, query: str, *, limit: int) -> Any:
        self.requests.append((query, limit))
        if self.error is not None:
            raise self.error
        return self.results.get(query, self.results.get("*", []))


def _paper(index: int, *, title: str | None = None, doi: str = "", paper_id: str | None = None) -> Dict[str, Any]:
    return {
        "paper_id": paper_id if paper_id is not None else f"S2-{index:03d}",
        "doi": doi,
        "title": title or f"Multilayer selective coating study {index}",
        "abstract": f"Abstract number {index} about a distributed Bragg reflector.",
        "year": 2000 + (index % 25),
        "venue": "Optics Express",
        "authors": [{"name": f"Author {index}"}],
        "citation_count": index,
    }


def _route(
    index: int,
    *,
    variables: Sequence[str] | str = ("layer thicknesses",),
    materials: Sequence[str] = ("TiO2", "SiO2"),
    priority: int = 1,
    evidence: Sequence[str] = ("L01",),
    topology: str | None = None,
    drop: Sequence[str] = (),
) -> Dict[str, Any]:
    route: Dict[str, Any] = {
        "route_id": f"route_{index:02d}",
        "title": f"Axis {index}",
        "route_kind": "periodic_stack",
        "scientific_hypothesis": (
            f"Varying the axis of route {index} moves best_target_score toward the "
            "quarter-wave limit."
        ),
        "design_principle": "Quarter-wave stacking opens a stop band around the design wavelength.",
        "proposed_materials": list(materials),
        "proposed_topology": topology or "Si substrate | 8 pairs of TiO2/SiO2 | air, 17 finite layers",
        "design_variables": [variables] if isinstance(variables, str) else list(variables),
        "soft_objectives": ["high mean reflectance across 300-800nm"],
        "manufacturing_considerations": ["keep the finite layer count below 24"],
        "evidence_ids": list(evidence),
        "theory_basis": ["transfer matrix method for isotropic layered media"],
        "expected_advantages": ["a wide stop band with few layers"],
        "known_risks": ["thickness errors shift the band edge"],
        "execution_request_english": (
            f"Optimize the layer thicknesses of an eight-pair TiO2/SiO2 stack on a "
            f"silicon substrate for route {index}, reporting mean reflectance over "
            "300-800nm and mean absorption over 5-13um."
        ),
        "priority": priority,
        "parent_route_id": None,
        "revision_reason": None,
        "expected_observations": [
            "best_target_score should rise across rounds while tightest_margin does not fall"
        ],
        "stop_conditions": [
            "if best_target_score improves by less than 1e-3 for two consecutive rounds, "
            "treat the axis as exhausted, stop and keep the current best"
        ],
    }
    for key in drop:
        route.pop(key, None)
    return route


def _plan_reply(routes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "problem_id": "dual-band-selective-coating",
        "planning_summary": "Three axes: thickness, material pair, and topology.",
        "routes": [dict(route) for route in routes],
        "research_influence": ["L01 motivated the periodic stack"],
        "unresolved_decisions": ["whether the infrared band needs a separate absorber"],
        "stop_if_all_routes_fail": "Report the best effort and record the axes tried.",
    }


def _query_reply(queries: Sequence[str] = ("dual band selective multilayer coating design",)) -> Dict[str, Any]:
    return {"queries": list(queries), "rationale": "from the two bands the request names"}


def _planner(
    replies: Sequence[Any],
    *,
    literature: ScriptedLiterature | None = None,
    **kwargs: Any,
) -> tuple[QwenLiteratureRoutePlanner, ScriptedClient]:
    client = ScriptedClient(replies)
    planner = QwenLiteratureRoutePlanner(
        client,
        literature_client=literature,
        **kwargs,
    )
    return planner, client


# ---------------------------------------------------------------------------
# 1. Search queries
# ---------------------------------------------------------------------------


class TestSearchQueries:
    def test_the_model_queries_are_used_when_they_are_usable(self) -> None:
        planner, client = _planner([_query_reply(("selective solar absorber multilayer", "dual band coating"))])
        result = planner.derive_queries(QUESTION)
        assert result.status == "derived"
        assert result.queries == (
            "selective solar absorber multilayer",
            "dual band coating",
        )
        assert result.usage and result.usage[0]["total_tokens"] == 160

    def test_the_question_reaches_the_query_prompt(self) -> None:
        planner, client = _planner([_query_reply()])
        planner.derive_queries(QUESTION)
        payload = client.payloads[0]
        assert payload["user_question"] == QUESTION
        assert payload["fixed_rules"]["results_per_query"] == DEFAULT_LITERATURE_LIMIT

    def test_more_queries_than_allowed_are_cut(self) -> None:
        planner, _ = _planner(
            [_query_reply(("one query here", "two query here", "three query here"))],
            maximum_queries=2,
        )
        assert len(planner.derive_queries(QUESTION).queries) == 2

    def test_duplicate_queries_collapse(self) -> None:
        planner, _ = _planner([_query_reply(("Bragg reflector design", "bragg REFLECTOR design"))])
        assert planner.derive_queries(QUESTION).queries == ("Bragg reflector design",)

    def test_a_failing_query_model_degrades_to_a_local_query(self) -> None:
        """A dead query stage must not take route planning down with it."""

        planner, _ = _planner([RuntimeError("gateway timeout")])
        result = planner.derive_queries(QUESTION)
        assert result.status == "fallback"
        assert len(result.queries) == 1 and result.queries[0].strip()
        assert result.validation_errors and "RuntimeError" in result.validation_errors[0]

    def test_an_empty_reply_degrades_to_a_local_query(self) -> None:
        planner, _ = _planner([{"queries": [], "rationale": "none"}])
        assert planner.derive_queries(QUESTION).status == "fallback"

    def test_a_chinese_only_question_still_yields_a_searchable_query(self) -> None:
        """The corpus is English, so an empty query would return nothing."""

        (query,) = _fallback_queries("我要一个可见光高反射的多层膜")
        assert "multilayer" in query and "transfer matrix" in query

    def test_the_local_query_keeps_the_bands_the_request_names(self) -> None:
        (query,) = _fallback_queries(QUESTION)
        assert "300" in query and "800nm" in query.replace(" ", "")


# ---------------------------------------------------------------------------
# 2. The literature harvest
# ---------------------------------------------------------------------------


class TestLiteratureHarvest:
    def test_forty_results_are_requested_and_kept(self) -> None:
        literature = ScriptedLiterature({"*": [_paper(index) for index in range(1, 61)]})
        planner, _ = _planner([], literature=literature)
        harvest = planner.harvest_literature(["dual band coating"])
        assert literature.requests == [("dual band coating", DEFAULT_LITERATURE_LIMIT)]
        assert harvest.status == "harvested"
        assert harvest.returned == DEFAULT_LITERATURE_LIMIT
        assert harvest.dropped_for_context == 20

    def test_the_limit_is_a_parameter_not_a_constant(self) -> None:
        literature = ScriptedLiterature({"*": [_paper(index) for index in range(1, 31)]})
        planner, _ = _planner([], literature=literature, literature_limit=12)
        harvest = planner.harvest_literature(["q"])
        assert literature.requests[0][1] == 12
        assert harvest.returned == 12
        assert planner.harvest_literature(["q"], limit=3).returned == 3

    def test_labels_are_contiguous_and_ordered(self) -> None:
        literature = ScriptedLiterature({"*": [_paper(index) for index in range(1, 6)]})
        planner, _ = _planner([], literature=literature)
        harvest = planner.harvest_literature(["q"])
        assert [paper.label for paper in harvest.papers] == ["L01", "L02", "L03", "L04", "L05"]
        assert harvest.allowed_labels == tuple(paper.label for paper in harvest.papers)

    def test_the_same_paper_from_two_queries_is_kept_once(self) -> None:
        shared = _paper(7, doi="10.1364/OE.7")
        literature = ScriptedLiterature(
            {
                "first": [shared, _paper(1)],
                "second": [dict(shared, paper_id="OTHER-ID"), _paper(2)],
            }
        )
        planner, _ = _planner([], literature=literature)
        harvest = planner.harvest_literature(["first", "second"])
        assert harvest.duplicates_dropped == 1
        titles = [paper.title for paper in harvest.papers]
        assert len(titles) == len(set(titles)) == 3
        merged = next(paper for paper in harvest.papers if paper.doi == "10.1364/oe.7")
        assert merged.queries == ("first", "second")

    def test_a_second_query_is_not_starved_by_the_first(self) -> None:
        """Concatenation would spend the whole budget on query one."""

        literature = ScriptedLiterature(
            {
                "broad": [_paper(index) for index in range(1, 40)],
                "narrow": [_paper(900, title="The one relevant paper")],
            }
        )
        planner, _ = _planner([], literature=literature, literature_limit=4)
        harvest = planner.harvest_literature(["broad", "narrow"])
        assert "The one relevant paper" in [paper.title for paper in harvest.papers]

    def test_a_long_corpus_is_bounded_by_characters_not_only_by_count(self) -> None:
        long_paper = lambda index: dict(_paper(index), abstract="word " * 4000)
        literature = ScriptedLiterature({"*": [long_paper(index) for index in range(1, 41)]})
        planner, _ = _planner([], literature=literature, character_budget=4000)
        harvest = planner.harvest_literature(["q"])
        assert 0 < harvest.returned < DEFAULT_LITERATURE_LIMIT
        assert harvest.character_count <= 4000
        assert harvest.dropped_for_context > 0

    def test_one_oversized_paper_still_survives(self) -> None:
        """Otherwise a single long abstract turns a hit into an empty harvest."""

        literature = ScriptedLiterature({"*": [dict(_paper(1), abstract="word " * 9000)]})
        planner, _ = _planner([], literature=literature, character_budget=1000)
        assert planner.harvest_literature(["q"]).returned == 1

    def test_each_summary_is_trimmed(self) -> None:
        literature = ScriptedLiterature({"*": [dict(_paper(1), abstract="detail " * 500)]})
        planner, _ = _planner([], literature=literature, summary_characters=200)
        (paper,) = planner.harvest_literature(["q"]).papers
        assert len(paper.summary) <= 210 and paper.summary.endswith("...")

    def test_a_records_response_pair_is_accepted(self) -> None:
        """The repository gateway returns ``(records, response)``."""

        literature = ScriptedLiterature({"*": ([_paper(1), _paper(2)], {"status": "ok"})})
        planner, _ = _planner([], literature=literature)
        assert planner.harvest_literature(["q"]).returned == 2

    def test_a_result_object_carrying_records_is_accepted(self) -> None:
        class Result:
            records = (_paper(1),)
            error = "s2_rate_limited"

        planner, _ = _planner([], literature=ScriptedLiterature({"*": Result()}))
        harvest = planner.harvest_literature(["q"])
        assert harvest.returned == 1
        assert any("s2_rate_limited" in item for item in harvest.errors)

    def test_a_record_without_a_title_is_skipped(self) -> None:
        literature = ScriptedLiterature({"*": [{"paper_id": "X"}, _paper(1)]})
        planner, _ = _planner([], literature=literature)
        assert planner.harvest_literature(["q"]).returned == 1

    def test_a_provider_failure_leaves_the_stage_working(self) -> None:
        planner, _ = _planner([], literature=ScriptedLiterature(error=TimeoutError("no route to host")))
        harvest = planner.harvest_literature(["q"])
        assert harvest.status == "unavailable"
        assert harvest.papers == ()
        assert any("TimeoutError" in item for item in harvest.errors)

    def test_no_literature_client_is_a_recorded_condition(self) -> None:
        planner, _ = _planner([])
        harvest = planner.harvest_literature(["q"])
        assert harvest.status == "unavailable"
        assert "theory" in " ".join(harvest.errors)

    def test_no_query_is_a_recorded_condition(self) -> None:
        planner, _ = _planner([], literature=ScriptedLiterature({"*": [_paper(1)]}))
        assert planner.harvest_literature(["   ", ""]).status == "empty"

    def test_identity_prefers_the_doi(self) -> None:
        assert _identity("A", "10.1/x", "Title") == _identity("B", "10.1/x", "Other title")
        assert _identity("A", "", "Title") != _identity("B", "", "Title")
        # Titles are the last resort, for records that arrive with no identifier
        # at all -- the case this de-duplication exists for.
        assert _identity("", "", "Same Title!") == _identity("", "", "same   title")


# ---------------------------------------------------------------------------
# 3. The route count is the model's answer
# ---------------------------------------------------------------------------


class TestTheRouteCountIsDecidedByTheModel:
    def test_two_proposed_axes_produce_two_routes(self) -> None:
        planner, _ = _planner(
            [_plan_reply([_route(1), _route(2, variables=("material pair",), materials=("Ta2O5", "MgF2"))])]
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.status == "planned"
        assert result.route_count == 2

    def test_one_axis_is_enough(self) -> None:
        """A single-axis problem must not be padded to a configured width."""

        planner, _ = _planner([_plan_reply([_route(1)])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.status == "planned" and result.route_count == 1

    def test_five_axes_all_survive(self) -> None:
        routes = [
            _route(1, variables=("layer thicknesses",)),
            _route(2, variables=("material pair",), materials=("Ta2O5", "MgF2")),
            _route(3, variables=("period",), topology="Si | 12 pairs | air, 24 finite layers"),
            _route(4, variables=("incidence angle",), materials=("Nb2O5", "SiO2")),
            _route(5, variables=("defect layer thickness",), topology="Si | 6 pairs | defect | 6 pairs | air, 25 finite layers"),
        ]
        planner, _ = _planner([_plan_reply(routes)])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.route_count == DEFAULT_MAXIMUM_ROUTES

    def test_more_than_the_maximum_is_truncated_by_priority(self) -> None:
        routes = [
            _route(index, variables=(f"axis {index}",), priority=priority)
            for index, priority in enumerate([5, 1, 4, 2, 3, 1], start=1)
        ]
        planner, _ = _planner([_plan_reply(routes)])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.route_count == DEFAULT_MAXIMUM_ROUTES
        kept = {route["title"] for route in result.plan["routes"]}
        assert "Axis 1" not in kept  # priority 5, the model's own last choice
        assert any("dropped" in warning for warning in result.warnings)

    def test_the_bound_is_configurable(self) -> None:
        routes = [_route(index, variables=(f"axis {index}",)) for index in range(1, 5)]
        planner, _ = _planner([_plan_reply(routes)], maximum_routes=2)
        assert planner.propose_routes(QUESTION, LiteratureHarvest()).route_count == 2

    def test_kept_routes_are_renumbered_in_priority_order(self) -> None:
        routes = [
            _route(9, variables=("second axis",), priority=2),
            _route(3, variables=("first axis",), priority=1),
        ]
        planner, _ = _planner([_plan_reply(routes)])
        plan = planner.propose_routes(QUESTION, LiteratureHarvest()).plan
        assert [route["route_id"] for route in plan["routes"]] == ["route_01", "route_02"]
        assert plan["routes"][0]["design_variables"] == ["first axis"]
        assert [route["priority"] for route in plan["routes"]] == [1, 2]


# ---------------------------------------------------------------------------
# 4. Regeneration and rejection
# ---------------------------------------------------------------------------


class TestRegeneration:
    def test_no_route_is_regenerated_not_accepted(self) -> None:
        planner, client = _planner([{"routes": []}, _plan_reply([_route(1)])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.status == "planned"
        assert result.attempts == 2
        assert len(client.calls) == 2

    def test_the_repair_request_states_what_was_wrong(self) -> None:
        planner, client = _planner(
            [_plan_reply([_route(1, drop=("design_variables",))]), _plan_reply([_route(1)])]
        )
        planner.propose_routes(QUESTION, LiteratureHarvest())
        repair = client.payloads[1]["repair_request"]
        assert repair["validation_errors"]
        assert "design_variables" in " ".join(repair["validation_errors"])
        assert client.payloads[1]["rejected_plan"]["routes"]

    def test_exhausted_attempts_report_invalid_with_the_reason(self) -> None:
        planner, client = _planner([{"routes": []}] * 3)
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.status == "invalid"
        assert result.attempts == 3 and len(client.calls) == 3
        assert result.plan is None and result.validation_errors

    def test_a_failing_planning_call_is_unavailable_not_a_crash(self) -> None:
        planner, _ = _planner([ConnectionError("stream closed")])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.status == "unavailable"
        assert "ConnectionError" in result.validation_errors[0]

    def test_prose_around_the_json_object_is_tolerated(self) -> None:
        payload = json.dumps(_plan_reply([_route(1)]), ensure_ascii=False)
        planner, _ = _planner([f"Here is the plan:\n```json\n{payload}\n```"])
        assert planner.propose_routes(QUESTION, LiteratureHarvest()).status == "planned"

    def test_one_bad_route_does_not_discard_the_good_ones(self) -> None:
        planner, _ = _planner(
            [
                _plan_reply(
                    [
                        _route(1),
                        {"route_id": "route_02", "title": "half a route"},
                        _route(3, variables=("period",)),
                    ]
                )
            ]
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.status == "planned" and result.route_count == 2
        assert any("route_02" in error for error in result.validation_errors)


# ---------------------------------------------------------------------------
# 5. Every route says what it tunes, and the axes stay distinct
# ---------------------------------------------------------------------------


class TestEveryRouteDeclaresItsAxis:
    def test_a_route_without_declared_variables_is_rejected(self) -> None:
        planner, _ = _planner(
            [
                _plan_reply([_route(1, drop=("design_variables",)), _route(2, variables=("period",))]),
            ]
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.route_count == 1
        assert any("does not say what it tunes" in error for error in result.validation_errors)

    def test_an_empty_variable_list_is_rejected(self) -> None:
        planner, _ = _planner([_plan_reply([_route(1, variables=())]), _plan_reply([_route(1)])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.attempts == 2 and result.route_count == 1

    def test_a_single_string_axis_is_accepted_as_one_variable(self) -> None:
        planner, _ = _planner([_plan_reply([_route(1, variables="layer thicknesses")])])
        plan = planner.propose_routes(QUESTION, LiteratureHarvest()).plan
        assert plan["routes"][0]["design_variables"] == ["layer thicknesses"]

    def test_two_routes_on_the_same_axis_collapse_to_one(self) -> None:
        """Two descriptions of one axis would spend two iteration budgets on it."""

        planner, _ = _planner([_plan_reply([_route(1), _route(2)])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.route_count == 1
        assert any("same axis" in warning for warning in result.warnings)

    def test_the_same_variables_over_different_materials_stay_distinct(self) -> None:
        planner, _ = _planner(
            [
                _plan_reply(
                    [
                        _route(1, materials=("TiO2", "SiO2")),
                        _route(2, materials=("Ta2O5", "MgF2")),
                    ]
                )
            ]
        )
        assert planner.propose_routes(QUESTION, LiteratureHarvest()).route_count == 2

    def test_the_axis_signature_ignores_wording_and_order(self) -> None:
        left = {
            "design_variables": ["Layer Thicknesses", "period"],
            "proposed_materials": ["SiO2", "TiO2"],
            "route_kind": "periodic_stack",
            "proposed_topology": "Si | 8 pairs | air",
        }
        right = {
            "design_variables": ["period", "layer_thicknesses"],
            "proposed_materials": ["TiO2", "SiO2"],
            "route_kind": "PERIODIC_STACK",
            "proposed_topology": "Si  |  8  pairs  |  air",
        }
        assert _axis_signature(left) == _axis_signature(right)


# ---------------------------------------------------------------------------
# 6. Literature reaches the prompt, and citations are verified
# ---------------------------------------------------------------------------


class TestLiteratureEntersPlanningUnderVerification:
    def _harvest(self, count: int = 3) -> LiteratureHarvest:
        planner, _ = _planner(
            [], literature=ScriptedLiterature({"*": [_paper(index) for index in range(1, count + 1)]})
        )
        return planner.harvest_literature(["q"])

    def test_the_papers_and_the_allowed_labels_reach_the_prompt(self) -> None:
        harvest = self._harvest()
        planner, client = _planner([_plan_reply([_route(1)])])
        planner.propose_routes(QUESTION, harvest)
        payload = client.payloads[0]
        assert [row["label"] for row in payload["literature"]] == ["L01", "L02", "L03"]
        assert payload["fixed_rules"]["evidence_ids_allowed"] == ["L01", "L02", "L03"]
        assert payload["fixed_rules"]["maximum_routes"] == DEFAULT_MAXIMUM_ROUTES
        assert payload["user_question"] == QUESTION

    def test_a_citation_outside_the_harvest_is_dropped_not_believed(self) -> None:
        harvest = self._harvest(2)
        planner, _ = _planner([_plan_reply([_route(1, evidence=("L01", "L99", "Smith 2019"))])])
        result = planner.propose_routes(QUESTION, harvest)
        assert result.plan["routes"][0]["evidence_ids"] == ["L01"]
        assert any("absent from the harvest" in warning for warning in result.warnings)

    def test_a_dropped_citation_does_not_reject_the_route(self) -> None:
        harvest = self._harvest(1)
        planner, _ = _planner([_plan_reply([_route(1, evidence=("L42",))])])
        result = planner.propose_routes(QUESTION, harvest)
        assert result.status == "planned" and result.route_count == 1

    def test_an_empty_harvest_asks_for_theory_based_routes(self) -> None:
        planner, client = _planner([_plan_reply([_route(1, evidence=())])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest(status="unavailable"))
        rules = client.payloads[0]["fixed_rules"]
        assert rules["evidence_ids_allowed"] == []
        assert "theory" in rules["theory_only"]
        assert result.status == "planned"
        assert result.plan["routes"][0]["theory_basis"]

    def test_the_problem_analysis_is_forwarded_when_present(self) -> None:
        planner, client = _planner([_plan_reply([_route(1)])])
        planner.propose_routes(
            QUESTION, LiteratureHarvest(), problem_analysis={"compatibility": "compatible"}
        )
        assert client.payloads[0]["problem_analysis"] == {"compatibility": "compatible"}


# ---------------------------------------------------------------------------
# 7. Pre-declarations and the executable contract
# ---------------------------------------------------------------------------


class TestPreDeclarationsAndContract:
    def test_declarations_are_separated_by_route_id(self) -> None:
        planner, _ = _planner([_plan_reply([_route(1), _route(2, variables=("period",))])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert set(result.pre_declarations) == {"route_01", "route_02"}
        assert result.pre_declarations["route_01"]["expected_observations"]
        assert result.pre_declarations["route_01"]["stop_conditions"]

    def test_declarations_are_kept_out_of_the_executable_route(self) -> None:
        """DesignRoute forbids extras, so they must be popped before validation."""

        planner, _ = _planner([_plan_reply([_route(1)])])
        route = planner.propose_routes(QUESTION, LiteratureHarvest()).plan["routes"][0]
        assert "expected_observations" not in route and "stop_conditions" not in route

    def test_a_bare_string_declaration_becomes_one_statement(self) -> None:
        raw = _route(1)
        raw["stop_conditions"] = "stop when best_target_score stalls for two rounds"
        planner, _ = _planner([_plan_reply([raw])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.pre_declarations["route_01"]["stop_conditions"] == [
            "stop when best_target_score stalls for two rounds"
        ]

    def test_every_route_validates_against_the_execution_contract(self) -> None:
        from optomind_optics.harness.strategy_planner import DesignRoute

        planner, _ = _planner([_plan_reply([_route(1), _route(2, variables=("period",))])])
        plan = planner.propose_routes(QUESTION, LiteratureHarvest()).plan
        for route in plan["routes"]:
            assert DesignRoute.model_validate(route).route_id == route["route_id"]

    def test_the_plan_carries_the_fields_the_run_reads(self) -> None:
        planner, _ = _planner([_plan_reply([_route(1)])])
        plan = planner.propose_routes(QUESTION, LiteratureHarvest()).plan
        assert plan["routes"] and plan["planning_summary"]
        assert plan["unresolved_decisions"] == [
            "whether the infrared band needs a separate absorber"
        ]
        assert plan["problem_id"] == "dual-band-selective-coating"

    def test_a_missing_problem_id_falls_back_to_the_question_digest(self) -> None:
        reply = _plan_reply([_route(1)])
        reply.pop("problem_id")
        planner, _ = _planner([reply])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())
        assert result.plan["problem_id"] == f"question-{result.question_digest}"


# ---------------------------------------------------------------------------
# 8. The stage end to end
# ---------------------------------------------------------------------------


class TestTheWholeStage:
    def test_query_then_search_then_routes(self) -> None:
        literature = ScriptedLiterature({"*": [_paper(index) for index in range(1, 9)]})
        planner, client = _planner(
            [_query_reply(("dual band selective coating",)), _plan_reply([_route(1), _route(2, variables=("period",))])],
            literature=literature,
        )
        result = planner.plan(QUESTION)
        assert result.status == "planned" and result.route_count == 2
        assert result.query_result.status == "derived"
        assert result.literature.status == "harvested"
        assert literature.requests == [("dual band selective coating", DEFAULT_LITERATURE_LIMIT)]
        assert len(client.calls) == 2

    def test_both_stages_are_metered_in_one_read(self) -> None:
        planner, _ = _planner(
            [_query_reply(), _plan_reply([_route(1)])],
            literature=ScriptedLiterature({"*": [_paper(1)]}),
        )
        result = planner.plan(QUESTION)
        assert len(result.usage) == 2
        assert sum(int(row["total_tokens"]) for row in result.usage) == 320

    def test_usage_is_absent_from_the_dump_so_the_sidecar_writes_it(self) -> None:
        planner, _ = _planner(
            [_query_reply(), _plan_reply([_route(1)])],
            literature=ScriptedLiterature({"*": [_paper(1)]}),
        )
        result = planner.plan(QUESTION)
        assert "usage" not in result.model_dump()
        sidecar = result.sidecar()
        assert len(sidecar["queries"]["usage"]) == 1
        assert len(sidecar["planning_usage"]) == 1

    def test_a_dead_provider_still_yields_a_plan(self) -> None:
        """Literature is motivation; losing it must not lose the study."""

        planner, _ = _planner(
            [_query_reply(), _plan_reply([_route(1, evidence=())])],
            literature=ScriptedLiterature(error=OSError("dns failure")),
        )
        result = planner.plan(QUESTION)
        assert result.status == "planned"
        assert result.literature.status == "unavailable"
        assert result.plan["routes"][0]["evidence_ids"] == []

    def test_the_sidecar_records_what_justified_the_plan(self) -> None:
        planner, _ = _planner(
            [_query_reply(("dual band coating",)), _plan_reply([_route(1)])],
            literature=ScriptedLiterature({"*": [_paper(1), _paper(2)]}),
        )
        sidecar = planner.plan(QUESTION).sidecar()
        assert sidecar["schema_version"] == ROUTE_PLANNING_SCHEMA_VERSION
        assert sidecar["status"] == "planned" and sidecar["route_count"] == 1
        assert sidecar["queries"]["queries"] == ["dual band coating"]
        assert [paper["label"] for paper in sidecar["literature"]["papers"]] == ["L01", "L02"]
        assert sidecar["literature"]["requested_limit"] == DEFAULT_LITERATURE_LIMIT
        assert sidecar["plan"]["routes"][0]["route_id"] == "route_01"
        assert sidecar["pre_declarations"]["route_01"]["stop_conditions"]
        assert json.loads(json.dumps(sidecar, ensure_ascii=False))

    def test_the_same_question_digests_the_same_way_as_the_scoring_standard(self) -> None:
        """The two artifacts must be joinable on the question they came from."""

        from optomind_optics.harness.scoring_standard import _question_digest as scoring_digest

        planner, _ = _planner([_query_reply(), _plan_reply([_route(1)])])
        result = planner.plan(QUESTION)
        assert result.question_digest == scoring_digest(QUESTION)

    def test_the_prompts_this_stage_ships_with_exist(self) -> None:
        from optomind_optics.harness.route_planning import (
            DEFAULT_ROUTE_PLANNING_PROMPT,
            DEFAULT_SEARCH_QUERY_PROMPT,
        )

        for path in (DEFAULT_SEARCH_QUERY_PROMPT, DEFAULT_ROUTE_PLANNING_PROMPT):
            assert Path(path).is_file()
            assert Path(path).read_text(encoding="utf-8").strip()

    def test_the_planning_prompt_states_the_axis_rule(self) -> None:
        from optomind_optics.harness.route_planning import DEFAULT_ROUTE_PLANNING_PROMPT

        text = Path(DEFAULT_ROUTE_PLANNING_PROMPT).read_text(encoding="utf-8")
        assert "design_variables" in text
        assert "never certifies" in text


# ---------------------------------------------------------------------------
# 9. Materials must be names the engine can actually resolve
# ---------------------------------------------------------------------------


class TestMaterialsAreResolvableBeforeARoundIsSpent:
    """A name the registry refuses is not a worse design, it is a dead route.

    The engine resolves a material by name and will not guess between
    datasets, so a loose formula such as ``SiN`` -- three different datasets --
    fails at compile time and takes the route's whole round quota with it.  The
    check therefore belongs here, where the answer is still repairable.
    """

    def test_an_ambiguous_material_is_repaired_rather_than_run(self) -> None:
        planner, client = _planner(
            [
                _plan_reply([_route(1, materials=("SiN", "SiO2"))]),
                _plan_reply([_route(1, materials=("Si3N4", "SiO2"))]),
            ]
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        assert result.status == "planned"
        assert result.attempts == 2
        assert len(client.calls) == 2

    def test_the_repair_request_names_the_defect_and_a_legal_name(self) -> None:
        planner, client = _planner(
            [
                _plan_reply([_route(1, materials=("SiN", "SiO2"))]),
                _plan_reply([_route(1, materials=("Si3N4", "SiO2"))]),
            ]
        )
        planner.propose_routes(QUESTION, LiteratureHarvest())

        repair = client.payloads[1]["repair_request"]
        complaint = " ".join(repair["validation_errors"])
        assert "SiN" in complaint
        assert "ambiguous" in complaint
        # The closest legal name is offered, so the repair is actionable
        # instead of a bare refusal.
        assert "si3n4" in complaint

    def test_a_route_whose_materials_all_resolve_is_untouched(self) -> None:
        planner, client = _planner([_plan_reply([_route(1, materials=("TiO2", "SiO2"))])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        assert result.status == "planned"
        assert result.attempts == 1
        assert len(client.calls) == 1

    def test_a_name_the_registry_resolves_but_never_listed_is_allowed(self) -> None:
        """The list is the guaranteed path, not a whitelist.

        The registry resolves far more names than it ships datasets for, so
        treating the list as exhaustive would amputate legal high-index
        materials such as Ta2O5.
        """

        planner, _ = _planner([_plan_reply([_route(1, materials=("Ta2O5", "MgF2"))])])
        assert planner.propose_routes(QUESTION, LiteratureHarvest()).status == "planned"

    def test_an_unresolvable_name_hidden_in_the_request_text_is_caught(self) -> None:
        """The request text is what the compiler reads, so it is checked too.

        A route whose declared list is clean can still reach the engine with an
        unresolvable name, because the compiler takes materials from
        execution_request_english and not from proposed_materials.
        """

        bad = _route(1, materials=("TiO2", "SiO2"))
        bad["execution_request_english"] = (
            "Optimize the layer thicknesses of an eight-pair SiN/SiO2 stack on a "
            "silicon substrate, reporting mean reflectance over 300-800nm."
        )
        planner, client = _planner(
            [_plan_reply([bad]), _plan_reply([_route(1, materials=("TiO2", "SiO2"))])]
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        assert result.status == "planned"
        assert result.attempts == 2
        assert "SiN" in " ".join(client.payloads[1]["repair_request"]["validation_errors"])

    def test_ordinary_optics_prose_is_not_mistaken_for_a_material(self) -> None:
        """The text scan must not fail a sound route over English words."""

        route = _route(1, materials=("TiO2", "SiO2"))
        route["execution_request_english"] = (
            "Optimize the layer thicknesses of an eight-pair TiO2/SiO2 stack under "
            "TE and TM polarization across the UV, NIR and IR bands, reporting mean "
            "reflectance over 300-800nm at AR-relevant angles."
        )
        planner, _ = _planner([_plan_reply([route])])
        assert planner.propose_routes(QUESTION, LiteratureHarvest()).status == "planned"

    def test_the_catalogue_reaches_the_model_carrying_no_wavelengths(self) -> None:
        """Names only: coverage is the scoring standard's business, not this stage's."""

        planner, client = _planner([_plan_reply([_route(1)])])
        planner.propose_routes(QUESTION, LiteratureHarvest())

        catalog = client.payloads[0]["material_catalog"]
        assert catalog["guaranteed_names"]
        assert "si3n4" in catalog["guaranteed_names"]
        assert "wavelength" not in json.dumps(catalog).casefold()
        # The stage says out loud that a resolvable name may still be short of
        # data at the band asked for, so a reader is not told otherwise.
        assert "interpolation" in catalog["coverage"]

    def test_the_model_is_told_that_n_and_k_are_measured_data(self) -> None:
        planner, client = _planner([_plan_reply([_route(1)])])
        planner.propose_routes(QUESTION, LiteratureHarvest())

        rules = client.payloads[0]["fixed_rules"]
        assert "never design variables" in rules["measured_not_tunable"]

    def test_the_artifact_records_what_was_checked(self) -> None:
        planner, _ = _planner([_plan_reply([_route(1, materials=("TiO2", "SiO2"))])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        report = result.material_verification
        assert report["checked"] is True
        assert report["catalog"]["guaranteed_names"]
        resolved = report["routes"]["route_01"]["resolved"]
        assert {row["resolved"] for row in resolved} == {"tio2", "sio2"}
        assert result.sidecar()["material_verification"]["checked"] is True

    def test_the_artifact_names_the_dataset_behind_each_material(self) -> None:
        """Which measurement ran, not just which word the model wrote.

        Two routes can carry the same material name and, if one of them spelled
        it with a synonym, different underlying data.  The artifact has to be
        able to tell them apart after the fact.
        """

        planner, _ = _planner([_plan_reply([_route(1, materials=("TiO2", "SiO2"))])])
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        rows = result.material_verification["routes"]["route_01"]["resolved"]
        by_name = {row["resolved"]: row for row in rows}
        assert by_name["tio2"]["dataset"] == "local_csv:tio2"
        assert by_name["tio2"]["source"].casefold().endswith("tio2.csv")
        low, high = by_name["tio2"]["coverage_um"]
        assert 0.0 < low < high

    def test_the_repaired_name_survives_into_the_artifact(self) -> None:
        """A successful repair must not erase the name that was repaired.

        The second attempt overwrites the first attempt's errors, so without a
        kept history the artifact would show two clean names and no trace that
        anything was ever sent back.
        """

        planner, _ = _planner(
            [
                _plan_reply([_route(1, materials=("SiN", "SiO2"))]),
                _plan_reply([_route(1, materials=("Si3N4", "SiO2"))]),
            ]
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        assert result.status == "planned"
        repaired = result.material_verification["repaired"]
        assert [entry["attempt"] for entry in repaired] == [1]
        rejected = repaired[0]["materials"]
        assert [row["proposed"] for row in rejected] == ["SiN"]
        assert rejected[0]["code"] == "ambiguous"
        assert rejected[0]["where"] == "proposed_materials"
        # The surviving route still reports only the clean names, so the two
        # records answer different questions and neither hides the other.
        final = result.material_verification["routes"]["route_01"]["resolved"]
        assert {row["resolved"] for row in final} == {"si3n4", "sio2"}
        # The orchestrator writes the sidecar straight to ROUTE_PLANNING.json,
        # so anything unserialisable in here loses the whole artifact.
        json.dumps(result.sidecar(), ensure_ascii=False)

    def test_without_a_catalogue_the_gap_is_recorded_not_hidden(self) -> None:
        """A check that could not run must not read as a check that passed."""

        planner, _ = _planner(
            [_plan_reply([_route(1, materials=("SiN",))])], material_catalog=None
        )
        result = planner.propose_routes(QUESTION, LiteratureHarvest())

        assert result.status == "planned"
        assert result.material_verification["checked"] is False
        assert any("material_catalog_unavailable" in item for item in result.warnings)
