"""Explicit source-qualified task adoption before construction of a frozen loop.

This builder never edits an existing config/task, writes files, buys observations,
or treats literature as measurement evidence. Unsupported adopted implications
fail atomically rather than becoming arbitrary dictionary mutations.
"""
from dataclasses import asdict, dataclass, replace
import math
from .contracts import ObjectRef, ResearchTask, canonical_hash
from .literature_bridge import ResearchBrief
from .claim_compiler import compile_claim_implication
from .open_research_loop import OpenResearchConfig

# Deliberately the actual consumed scalar fields, not arbitrary task dictionary paths.
TARGETS = {
    "ResearchTask.objective.wavelength_nm": ("metric_definition", "objective_wavelength_nm", "nm"),
    "ResearchTask.objective.target_R": ("metric_definition", "objective_target", "1"),
    "ResearchTask.objective.loss_transform": ("metric_definition", "terminal_loss_transform", "1"),
    "ResearchTask.objective.risk_aversion": ("metric_definition", "terminal_risk_aversion", "1"),
    "ResearchTask.constraints.terminal_lower_nm": ("constraint", "terminal_lower_nm", "nm"),
    "ResearchTask.constraints.terminal_upper_nm": ("constraint", "terminal_upper_nm", "nm"),
    "ResearchTask.constraints.terminal_tolerance_nm": ("constraint", "terminal_tolerance_nm", "nm"),
}


@dataclass(frozen=True)
class PrefreezeAdoption:
    config: OpenResearchConfig
    receipts: tuple[dict, ...]
    draft_ref: ObjectRef
    before_config_hash: str
    after_config_hash: str


def prefreeze_task(config):
    if not isinstance(config, OpenResearchConfig):
        raise TypeError("OpenResearchConfig required")
    return ResearchTask(
        object_id=f"{config.run_id}:prefreeze-task-config", source_run_id=config.run_id,
        question="Draft task for explicit source-qualified adoption before protocol freeze",
        objective={"wavelength_nm": config.objective_wavelength_nm, "target_R": config.objective_target,
                   "loss": "squared_reflectance_error", "loss_transform": config.terminal_loss_transform,
                   "risk_aversion": config.terminal_risk_aversion, "design_pool_nm": config.design_thicknesses_nm},
        constraints={"open_research_config": asdict(config)})


def prefreeze_task_ref(config):
    return prefreeze_task(config).ref()


def _domain(conditions, key):
    value = conditions.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"actual public {key} required")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value) or value[0] >= value[1]:
        raise ValueError(f"invalid public {key}")
    return tuple(map(float, value))


def build_prefreeze_config(config, brief, *, task_conditions, adopted_claim_ids):
    """Return a newly validated immutable config and exact source-to-field receipts.

    task_conditions must come from the caller's actual public model/configuration.
    CLI derives it from ModelIR, never from claims. Draft identity binds the
    complete pre-adoption config, including budgets, sessions and design pool.
    """
    draft = prefreeze_task(config)
    brief = ResearchBrief.model_validate(brief.model_dump(mode="json") if isinstance(brief, ResearchBrief) else brief)
    if brief.task_ref != draft.ref():
        raise ValueError("research brief does not bind the exact pre-freeze draft task")
    ids = tuple(adopted_claim_ids)
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) for i in ids):
        raise ValueError("unique explicit adopted claim IDs required")
    claims = {c.claim_id: c for c in brief.claims}
    if len(claims) != len(brief.claims) or set(brief.claim_refs) != set(claims) or len(brief.claim_refs) != len(claims):
        raise ValueError("inconsistent research brief claim identities")
    if set(ids) - set(claims):
        raise ValueError("unknown adopted claim")
    wavelength_domain = _domain(task_conditions, "wavelength_domain_nm")
    design_domain = _domain(task_conditions, "design_domain_nm")
    if any(key not in task_conditions or task_conditions[key] != value for key, value in brief.conditions.items()):
        raise ValueError("research brief conditions do not match actual public task")
    if ids and brief.conflicts:
        raise ValueError("adopted brief has unresolved source conflicts")
    updates, receipts = {}, []
    for claim_id in ids:
        claim = claims[claim_id]
        if claim.conflicts:
            raise ValueError("adopted claim has unresolved source conflicts")
        if claim.material_depth not in {"full_text", "fulltext", "directed_read", "excerpt"}:
            raise ValueError("adopted claim lacks qualified source material depth")
        if not claim.source_spans or any(not span.get("source_block_id") or not str(span.get("quote", "")).strip() for span in claim.source_spans):
            raise ValueError("adopted claim needs anchored source quotes")
        if not claim.implications:
            raise ValueError("adopted claim has no explicit implications")
        for specification in claim.implications:
            compiled = compile_claim_implication(claim, specification, task_conditions=task_conditions)
            if compiled.status != "compiled":
                raise ValueError(f"adopted implication is not qualified: {compiled.status}")
            target = TARGETS.get(compiled.target_path)
            if target is None or (compiled.kind, compiled.units) != (target[0], target[2]):
                raise ValueError("unsupported pre-freeze target, kind, or exact units")
            field = target[1]
            value = compiled.value_or_expression
            if field == "terminal_loss_transform":
                if value not in ("identity", "exponential"):
                    raise ValueError("unsupported terminal loss transform")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("finite numeric implication required")
                value = float(value)
            if field in updates and updates[field] != value:
                raise ValueError("conflicting adopted implications for one configuration field")
            updates[field] = value
            receipts.append({
                "schema": "prefreeze-task-adoption/1", "claim_id": claim.claim_id,
                "source_document": claim.document.model_dump(mode="json"),
                "source_spans": list(claim.source_spans), "material_depth": claim.material_depth,
                "attribution": claim.attribution, "domain_predicate": compiled.domain_predicate,
                "actual_task_conditions": dict(task_conditions),
                "target_path": compiled.target_path, "config_field": field, "units": compiled.units,
                "before": getattr(config, field), "after": value,
                "consumer": "OpenResearchConfig before OpenResearchLoop construction",
                "evidence_type": "source_author_claim_explicitly_adopted_not_measurement",
                "draft_task_ref": draft.ref().model_dump(mode="json"),
                "brief_input_hash": brief.input_hash, "brief_content_hash": canonical_hash(brief),
                "adoption_explicit": True})
    candidate = replace(config, **updates)
    if not wavelength_domain[0] <= candidate.objective_wavelength_nm <= wavelength_domain[1]:
        raise ValueError("objective wavelength outside public model domain")
    if not 0 <= candidate.objective_target <= 1:
        raise ValueError("reflectance target must be in [0,1]")
    lower = design_domain[0] if candidate.terminal_lower_nm is None else candidate.terminal_lower_nm
    upper = design_domain[1] if candidate.terminal_upper_nm is None else candidate.terminal_upper_nm
    if (not all(math.isfinite(x) for x in (lower, upper)) or
            not design_domain[0] <= lower < upper <= design_domain[1]):
        raise ValueError("terminal bounds outside public parameter domain or inverted")
    tolerance = candidate.terminal_tolerance_nm
    if not any(lower <= d - tolerance and d + tolerance <= upper for d in candidate.design_thicknesses_nm):
        raise ValueError("adopted constraints leave no feasible frozen-pool design")
    before_hash, after_hash = canonical_hash(asdict(config)), canonical_hash(asdict(candidate))
    for receipt in receipts:
        receipt.update(before_config_hash=before_hash, after_config_hash=after_hash)
        receipt["seal"] = canonical_hash(receipt)
    return PrefreezeAdoption(candidate, tuple(receipts), draft.ref(), before_hash, after_hash)
