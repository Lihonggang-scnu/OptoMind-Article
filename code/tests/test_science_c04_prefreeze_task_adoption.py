from dataclasses import asdict, FrozenInstanceError, replace
import pytest
from optomind_optics.harness.science.open_research_cli import parse_offline_config, build_offline_loop
from optomind_optics.harness.science.prefreeze_task_adoption import (
    build_prefreeze_config, prefreeze_task, prefreeze_task_ref)
from optomind_optics.harness.science.contracts import canonical_hash
from optomind_optics.harness.science.literature_bridge import (
    ResearchBrief, LiteratureClaim, DocumentVersionIdentity, WorkIdentity)

CONDITIONS = {"angle_deg": 0., "wavelength_domain_nm": [400., 900.], "design_domain_nm": [40., 240.],
              "geometry": "single_planar_film"}


def config():
    return parse_offline_config({"config": {"run_id": "adoption-test", "max_iterations": 1, "unknown_exploration_limit": 1}})[0]


def brief(cfg, specifications=None, **claim_updates):
    if specifications is None:
        specifications = [
            {"kind": "metric_definition", "target_path": "ResearchTask.objective.wavelength_nm", "value_or_expression": 600., "units": "nm"},
            {"kind": "metric_definition", "target_path": "ResearchTask.objective.target_R", "value_or_expression": .2, "units": "1"},
            {"kind": "constraint", "target_path": "ResearchTask.constraints.terminal_lower_nm", "value_or_expression": 80., "units": "nm"},
            {"kind": "constraint", "target_path": "ResearchTask.constraints.terminal_upper_nm", "value_or_expression": 180., "units": "nm"},
            {"kind": "constraint", "target_path": "ResearchTask.constraints.terminal_tolerance_nm", "value_or_expression": 2., "units": "nm"}]
    raw = {"claim_id": "source-claim", "document": DocumentVersionIdentity(work=WorkIdentity(key="doi:fixture"),
        snapshot_id="v1", source_hash="a"*64), "statement": "Synthetic anchored fixture specifies this task.",
        "material_depth": "full_text", "roles": ("problem_boundary",), "conditions": {"angle_deg": 0.},
        "conflicts": (), "source_spans": ({"source_block_id": "fixture:block1", "quote": "Use target R .2 at600nm and80–180nm bounds."},),
        "implications": tuple(specifications)}
    raw.update(claim_updates)
    claim = LiteratureClaim(**raw)
    return ResearchBrief(task_ref=prefreeze_task_ref(cfg), evidence_task_id="evidence-fixture",
        claim_refs=(claim.claim_id,), claims=(claim,), conditions={"angle_deg": 0.},
        conflicts=(), gaps=(), read_receipts=(), input_hash="b"*64)


def adopt(cfg, b):
    return build_prefreeze_config(cfg, b, task_conditions=CONDITIONS, adopted_claim_ids=b.claim_refs)


def test_exact_canonical_draft_and_immutable_source_to_config_receipt():
    cfg = config(); b = brief(cfg); result = adopt(cfg, b)
    assert prefreeze_task_ref(cfg) == prefreeze_task(cfg).ref()
    assert result.config.objective_wavelength_nm == 600.
    assert result.config.objective_target == .2
    assert result.config.terminal_lower_nm == 80. and result.config.terminal_upper_nm == 180.
    assert cfg.objective_target == 0. and cfg.terminal_lower_nm is None
    with pytest.raises(FrozenInstanceError):
        result.config.objective_target = .4
    for receipt in result.receipts:
        body = dict(receipt); seal = body.pop("seal")
        assert canonical_hash(body) == seal
        assert body["source_document"]["source_hash"] == "a"*64
        assert body["source_spans"] and body["after_config_hash"] == canonical_hash(asdict(result.config))
        assert body["evidence_type"] == "source_author_claim_explicitly_adopted_not_measurement"


def test_no_explicit_ids_means_no_adoption():
    cfg = config(); b = brief(cfg)
    result = build_prefreeze_config(cfg, b, task_conditions=CONDITIONS, adopted_claim_ids=())
    assert result.config == cfg and result.receipts == ()


@pytest.mark.parametrize("change", [
    {"material_depth": "abstract_only"}, {"material_depth": "made_up_depth"},
    {"attribution": "system_bridge"}, {"source_spans": ()},
    {"source_spans": ({"source_block_id": "x", "quote": ""},)},
    {"conditions": {"angle_deg": 45.}}, {"conditions": {"unknown_condition": "missing"}},
    {"conflicts": ("unresolved opposing result",)}])
def test_unqualified_sources_fail_atomically(change):
    cfg = config()
    with pytest.raises(ValueError):
        adopt(cfg, brief(cfg, **change))
    assert cfg.objective_target == 0.


@pytest.mark.parametrize("path,kind,value,unit", [
    ("ResearchTask.objective.target_R", "metric_definition", .2, "nm"),
    ("ResearchTask.objective.target_R", "constraint", .2, "1"),
    ("ResearchTask.objective.target_R", "metric_definition", True, "1"),
    ("ResearchTask.objective.target_R", "metric_definition", float("nan"), "1"),
    ("ResearchTask.objective.target_R", "metric_definition", 1.1, "1"),
    ("ResearchTask.objective.wavelength_nm", "metric_definition", 950., "nm"),
    ("ResearchTask.constraints.terminal_lower_nm", "constraint", 5., "nm"),
    ("ResearchTask.constraints.terminal_tolerance_nm", "constraint", 1000., "nm"),
    ("ResearchTask.constraints.prior_distribution", "constraint", [0., 1.], "1"),
    ("ResearchTask.constraints.budget", "constraint", 1., "1"),
    ("ResearchTask.objective.loss_transform", "metric_definition", "invented", "1"),
    ("ResearchTask.objective.risk_aversion", "metric_definition", -1., "1")])
def test_invalid_target_units_and_domain_rejected(path, kind, value, unit):
    cfg = config()
    b = brief(cfg, [{"kind": kind, "target_path": path, "value_or_expression": value, "units": unit}])
    with pytest.raises(ValueError):
        adopt(cfg, b)


def test_stale_draft_identity_unknown_ids_and_conflicting_values_rejected():
    cfg = config(); b = brief(cfg)
    with pytest.raises(ValueError, match="exact pre-freeze"):
        adopt(replace(cfg, budget=11.), b)
    with pytest.raises(ValueError, match="unknown"):
        build_prefreeze_config(cfg, b, task_conditions=CONDITIONS, adopted_claim_ids=("unknown",))
    with pytest.raises(ValueError, match="unique"):
        build_prefreeze_config(cfg, b, task_conditions=CONDITIONS, adopted_claim_ids=(b.claim_refs[0], b.claim_refs[0]))
    specs = list(b.claims[0].implications)
    specs.append({**specs[0], "value_or_expression": 650.})
    with pytest.raises(ValueError, match="conflicting"):
        adopt(cfg, brief(cfg, specs))


def test_actual_frozen_loop_task_consumes_adopted_metric_and_constraints(tmp_path):
    cfg = config(); result = adopt(cfg, brief(cfg))
    loop, resources = build_offline_loop(tmp_path / "valid", {"config": asdict(result.config)})
    try:
        assert loop.task.objective["wavelength_nm"] == 600.
        assert loop.task.objective["target_R"] == .2
        frozen = loop.task.constraints["open_research_config"]
        assert frozen["terminal_lower_nm"] == 80.
        assert frozen["terminal_upper_nm"] == 180.
        assert frozen["terminal_tolerance_nm"] == 2.
        original_hash = loop.task.content_hash
        replacement = replace(result.config, objective_target=.7)
        assert loop.config.objective_target == .2 and loop.task.content_hash == original_hash
        assert replacement.objective_target == .7  # separate draft cannot mutate running task
    finally:
        for resource in resources: resource.close()


def test_actual_exponential_objective_and_risk_mapping():
    cfg = config()
    b = brief(cfg, [
        {"kind": "metric_definition", "target_path": "ResearchTask.objective.loss_transform", "value_or_expression": "exponential", "units": "1"},
        {"kind": "metric_definition", "target_path": "ResearchTask.objective.risk_aversion", "value_or_expression": 2., "units": "1"}])
    result = adopt(cfg, b)
    assert result.config.terminal_loss_transform == "exponential"
    assert result.config.terminal_risk_aversion == 2.


def test_brief_level_conflict_and_inverted_bounds_fail():
    cfg = config(); b = brief(cfg)
    with pytest.raises(ValueError, match="unresolved"):
        adopt(cfg, b.model_copy(update={"conflicts": ("unresolved",)}))
    specs = [
        {"kind": "constraint", "target_path": "ResearchTask.constraints.terminal_lower_nm", "value_or_expression": 180., "units": "nm"},
        {"kind": "constraint", "target_path": "ResearchTask.constraints.terminal_upper_nm", "value_or_expression": 80., "units": "nm"}]
    with pytest.raises(ValueError, match="inverted"):
        adopt(cfg, brief(cfg, specs))
