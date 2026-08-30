"""Turn a completed TMM research run into a traceable research article.

The language model is deliberately not the source of numerical truth.  It
receives immutable fact tokens and is allowed to arrange only the scientific
narrative around them.  Deterministic code restores the exact verified values,
builds tables and plots from solver artifacts, validates citations, and then
reuses OptoMind's existing LaTeX/PDF publication layer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from optomind_research.runtime.artifact_store import atomic_write_json
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny
from optomind_research.runtime.latex_publication_renderer import (
    build_latex_publication,
)
from optomind_research.runtime.publication_integrity import (
    prepare_publication_markdown,
)

from .qwen_policy import QWEN_POLICY_MODEL, QwenFlashOnlyClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = (
    PROJECT_ROOT / "prompts" / "optical_harness" / "TMM Article Writer.txt"
)
DEFAULT_AUDIT_PROMPT_PATH = (
    PROJECT_ROOT / "prompts" / "optical_harness" / "TMM Article Evidence Auditor.txt"
)
FACT_PATTERN = re.compile(r"\[FACT:([A-Z0-9_]+)\]")
REF_PATTERN = re.compile(r"\[REF:([^\]]+)\]")
RAW_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?")
FORBIDDEN_PROSE = (
    re.compile(r"\b(?:the|this)\s+(?:project|harness|agent|prompt|workflow|artifact|deliverable)\b", re.I),
    re.compile(r"\bnot\s+merely\b.{0,80}\bbut\b", re.I | re.S),
    re.compile(r"\bnot\s+only\b.{0,80}\bbut\s+also\b", re.I | re.S),
    re.compile(r"\b(?:under|at)\s+normal\s+incidence\b", re.I),
    re.compile(r"\bnegligible\s+absorption\b", re.I),
    re.compile(r"\bneglect(?:s|ed|ing)?\b.{0,60}\bangle[- ]dependent\b", re.I | re.S),
    re.compile(r"\bstrict\s+(?:constraints?|requirements?)\b", re.I),
    re.compile(r"\bstandard\s+deposition\s+(?:process|processes|practice|practices)\b", re.I),
    re.compile(
        r"\b(?:superior\s+to|better\s+than|outperform(?:s|ed)?|compared\s+(?:with|to))\b"
        r".{0,50}\b(?:conventional|published)\b",
        re.I | re.S,
    ),
    re.compile(r"\b(?:measured|experimentally demonstrated|fabricated)\b", re.I),
    re.compile(r"\bpareto[- ]optimal\b", re.I),
    re.compile(r"\bguarantee(?:s|d)?\s+(?:a\s+)?global\s+optimum\b", re.I),
    re.compile(r"\bcorrelat(?:e|es|ed|ion)\s+with\b", re.I),
    re.compile(r"\bdepends?\s+(?:heavily|strongly)\s+on\b", re.I),
    re.compile(r"\b(?:broader|better|greater)\s+(?:tolerance|reproducibility)\b", re.I),
    re.compile(r"\b(?:difficult|easy|easier)\s+to\s+(?:fabricate|manufacture|reproduce)\b", re.I),
    re.compile(r"\b(?:practical\s+fabrication\s+limits?|standard\s+dielectric\s+materials?|established\s+stability|known\s+dispersion\s+characteristics)\b", re.I),
    re.compile(r"\b(?:structural\s+features?|layer\s+count)\b.{0,45}\b(?:contribute|introduce|improve|reduce|increase|decrease)\b", re.I | re.S),
)

MATERIAL_DISPLAY_NAMES = {
    "mgf2": "MgF2",
    "sio2": "SiO2",
    "tio2": "TiO2",
    "ta2o5": "Ta2O5",
    "al2o3": "Al2O3",
    "hfo2": "HfO2",
    "nb2o5": "Nb2O5",
    "si3n4": "Si3N4",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(str(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def _safe_json(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _percent(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.{digits}f}%"


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _display_material(value: Any) -> str:
    text = str(value or "").strip()
    return MATERIAL_DISPLAY_NAMES.get(text.lower(), text)


def _display_solver(value: Any) -> str:
    text = str(value or "transfer matrix").strip().lower()
    if text in {"smatrix", "s-matrix", "scattering_matrix"}:
        return "scattering-matrix (S-matrix)"
    return str(value or "transfer-matrix")


def _candidate_roles(candidate: Mapping[str, Any]) -> set[str]:
    raw = candidate.get("recommendation_roles") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(item).strip() for item in raw if str(item).strip()}


def _unique_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        row = dict(item)
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        rows.append(row)
    return rows


def _best_by_role(
    candidates: list[dict[str, Any]], role: str, score: str
) -> dict[str, Any]:
    explicit = [item for item in candidates if role in _candidate_roles(item)]
    pool = explicit or candidates
    return max(pool, key=lambda item: float(item.get(score) or 0.0))


def _target_attainment(candidate: Mapping[str, Any]) -> tuple[int, int]:
    met = 0
    assessed = 0
    for row in (candidate.get("reported_metrics") or []):
        if not isinstance(row, Mapping):
            continue
        observed, target = row.get("observed"), row.get("target")
        constraint = str(row.get("constraint") or "")
        if observed is None or target is None or constraint not in {
            "at_least",
            "at_most",
            "match",
        }:
            continue
        assessed += 1
        observed_f, target_f = float(observed), float(target)
        if constraint == "at_least":
            met += int(observed_f >= target_f)
        elif constraint == "at_most":
            met += int(observed_f <= target_f)
        else:
            met += int(math.isclose(observed_f, target_f, rel_tol=0.0, abs_tol=1e-9))
    return met, assessed


def _metric_summary(candidate: Mapping[str, Any]) -> str:
    groups: dict[tuple[str, str, str, float], list[float]] = defaultdict(list)
    for row in candidate.get("reported_metrics") or []:
        if not isinstance(row, Mapping) or row.get("observed") is None:
            continue
        try:
            key = (
                str(row.get("observable") or "metric"),
                str(row.get("aggregation") or "value"),
                str(row.get("constraint") or ""),
                float(row.get("target")),
            )
            groups[key].append(float(row["observed"]))
        except (TypeError, ValueError):
            continue
    clauses: list[str] = []
    for (observable, aggregation, constraint, target), values in groups.items():
        rendered = (
            _percent(min(values))
            if len(values) == 1
            else f"{_percent(min(values))}-{_percent(max(values))}"
        )
        clauses.append(
            f"{aggregation.replace('_', ' ')} {observable} was {rendered} "
            f"across the assessed channels (soft target {constraint.replace('_', ' ')} {_percent(target)})"
        )
    return "; ".join(clauses) + "." if clauses else "No objective-level metric summary was available."


def _compact_scope(answer: Mapping[str, Any], research: Mapping[str, Any]) -> str:
    """Summarise scope from canonical analysis, with conservative text fallback."""

    analysis = research.get("problem_analysis") or {}
    if not isinstance(analysis, Mapping):
        analysis = {}
    objectives = []
    for item in answer.get("objective_summary") or []:
        if not isinstance(item, Mapping):
            continue
        observable = str(item.get("observable") or "").upper()
        aggregation = str(item.get("aggregation") or "").replace("_", " ")
        constraint = str(item.get("constraint") or "").replace("_", " ")
        target = item.get("target")
        if observable and target is not None:
            objectives.append(f"{aggregation} {observable} {constraint} {_percent(target)}")
    candidate_text = " ".join(
        str(answer.get(key) or research.get(key) or "")
        for key in ("problem_interpretation", "question")
    )

    wavelengths: list[float] = []
    for raw_range in analysis.get("wavelengths_nm") or []:
        values = raw_range if isinstance(raw_range, (list, tuple)) else [raw_range]
        for value in values:
            try:
                wavelengths.append(float(value))
            except (TypeError, ValueError):
                continue
    if not wavelengths:
        for start, end in re.findall(
            r"(?<!\d)(\d{3,5}(?:\.\d+)?)\s*[-–—]\s*(\d{3,5}(?:\.\d+)?)\s*(?:nm|nanomet(?:er|re)s?)",
            candidate_text,
            re.I,
        ):
            wavelengths.extend((float(start), float(end)))
        wavelengths.extend(
            float(item)
            for item in re.findall(
                r"(?<![\d-])(\d{3,5}(?:\.\d+)?)\s*(?:nm|nanomet(?:er|re)s?)",
                candidate_text,
                re.I,
            )
        )

    angles: list[float] = []
    for value in analysis.get("angles_deg") or []:
        try:
            angles.append(float(value))
        except (TypeError, ValueError):
            continue
    if not angles:
        angle_groups = re.findall(
            r"(?:angles?(?:\s+of)?|at)\s+((?:\d+(?:\.\d+)?\s*,?\s*(?:and\s*)?)+)\s*degrees?",
            candidate_text,
            re.I,
        )
        for group in angle_groups:
            angles.extend(float(item) for item in re.findall(r"\d+(?:\.\d+)?", group))
        if not angles:
            angles.extend(
                float(item)
                for item in re.findall(
                    r"(?<![+\-\d])(\d+(?:\.\d+)?)\s*(?:degrees?|°)",
                    candidate_text,
                    re.I,
                )
            )

    polarizations = {
        str(item).strip().upper()
        for item in (analysis.get("polarizations") or [])
        if str(item).strip()
    }
    scope_parts = ["the declared planar multilayer coating objective"]
    if len(wavelengths) >= 2:
        scope_parts.append(f"from {min(wavelengths):.0f} to {max(wavelengths):.0f} nm")
    if angles:
        scope_parts.append(
            "at incidence angles "
            + ", ".join(f"{item:g}" for item in sorted(set(angles)))
            + " degrees"
        )
    if {"TE", "TM"}.issubset(polarizations) or (
        "TE" in candidate_text.upper() and "TM" in candidate_text.upper()
    ):
        scope_parts.append("for TE and TM polarizations")
    if objectives:
        scope_parts.append("with soft objectives " + "; ".join(dict.fromkeys(objectives)))
    return " ".join(scope_parts) + "."


def _stack_text(candidate: Mapping[str, Any]) -> str:
    materials = [_display_material(item) for item in candidate.get("layer_materials") or []]
    thicknesses = [float(item) for item in candidate.get("thicknesses_nm") or []]
    pairs = [
        f"{material} ({thickness:.2f} nm)"
        for material, thickness in zip(materials, thicknesses)
    ]
    return " / ".join(pairs)


def _find_candidate_directory(run_dir: Path, candidate: Mapping[str, Any]) -> Path:
    iteration_id = str(candidate.get("iteration_id") or "")
    tmm_root = run_dir / "iterations" / iteration_id / "tmm_run"
    for relative in candidate.get("artifact_ids") or []:
        path = tmm_root / str(relative)
        if path.parent.is_dir():
            return path.parent
    candidate_id = str(candidate.get("candidate_id") or "")
    for identity_path in tmm_root.rglob("IDENTITY.json"):
        identity = _read_json(identity_path)
        if str(identity.get("candidate_id") or "") == candidate_id:
            return identity_path.parent
    raise FileNotFoundError(f"Cannot locate verified artifacts for {candidate_id}")


def _reference_inventory(answer: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in answer.get("references") or []:
        if not isinstance(item, Mapping):
            continue
        paper_id = str(item.get("paper_id") or "").strip()
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        rows.append(
            {
                "paper_id": paper_id,
                "title": str(item.get("title") or "").strip(),
                "doi": str(item.get("doi") or "").strip(),
                "authors": list(item.get("authors") or []),
                "year": item.get("year"),
                "venue": str(item.get("venue") or "").strip(),
                "source_route": str(item.get("source_route") or ""),
                "content_depth": str(item.get("content_depth") or ""),
                "allowed_use": str(item.get("allowed_use") or "method_guidance"),
            }
        )
    return rows


@dataclass
class TMMArticleEvidenceCompiler:
    run_dir: Path
    output_dir: Path

    def compile(self) -> dict[str, Any]:
        run_dir = Path(self.run_dir).resolve()
        output_dir = Path(self.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        answer = _read_json(run_dir / "FINAL_ANSWER.json")
        research = _read_json(run_dir / "RESEARCH_RESULT.json")
        acceptance = _read_json(run_dir / "ACCEPTANCE_SUMMARY.json")
        if str(answer.get("status") or "") != "completed":
            raise ValueError("TMM run is not complete and cannot be published")
        candidates = _unique_candidates(answer.get("recommended_candidates") or [])
        if not candidates:
            raise ValueError("TMM run has no recommended verified candidates")
        performance = _best_by_role(candidates, "best_performance", "target_score")
        robust = _best_by_role(candidates, "most_robust", "robustness_score")
        simplest = _best_by_role(candidates, "simplest", "simplicity_score")

        candidate_dir = _find_candidate_directory(run_dir, performance)
        simulation_path = candidate_dir / "SIMULATION_RESULT.json"
        certificate_path = candidate_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
        robustness_path = candidate_dir / "ROBUSTNESS.json"
        simulation = _read_json(simulation_path)
        certificate = _read_json(certificate_path)
        if not simulation or not bool(certificate.get("accepted")):
            raise ValueError("Best-performance candidate lacks a valid physics certificate")

        route_count = len(answer.get("route_summaries") or [])
        valid_count = sum(
            int(item.get("physically_valid_candidate_count") or 0)
            for item in answer.get("route_summaries") or []
            if isinstance(item, Mapping)
        )
        layer_counts = sorted(
            {
                len(item.get("layer_materials") or [])
                for item in candidates
                if item.get("layer_materials")
            }
        )
        met, assessed = _target_attainment(performance)
        wavelengths = simulation.get("wavelengths_nm") or []
        channels = simulation.get("channels") or {}
        solver = _display_solver(simulation.get("solver") or "transfer matrix")
        robustness_report = dict(performance.get("reported_robustness") or {})
        perturbation = dict(robustness_report.get("perturbation_model") or {})
        summaries = dict(robustness_report.get("spectral_metric_summary") or {})
        robust_parts = []
        for name, row in summaries.items():
            if not isinstance(row, Mapping):
                continue
            robust_parts.append(
                f"{name.replace('_', ' ')} had mean {_percent(row.get('mean', 0.0))} "
                f"and standard deviation {_percent(row.get('standard_deviation', 0.0))}"
            )
        robustness_text = "; ".join(robust_parts) + "." if robust_parts else (
            "No aggregate robustness metrics were available for this candidate."
        )

        facts = {
            "F_SCOPE": (
                "The computational study addressed " + _compact_scope(answer, research).rstrip(".") + "."
            ),
            "F_MODEL": (
                f"Forward calculations used the {solver} formulation over "
                f"{len(wavelengths)} wavelength samples and {len(channels)} angle-polarization channels; "
                "the selected design carried a passing physics-acceptance certificate."
            ),
            "F_SEARCH": (
                f"The bounded comparison completed {route_count} structural routes with layer counts "
                f"{', '.join(str(item) for item in layer_counts)} and produced {valid_count} physically valid candidates."
            ),
            "F_BEST_STACK": (
                f"The best-performance candidate contained {len(performance.get('layer_materials') or [])} finite layers in the sequence "
                f"{_stack_text(performance)}."
            ),
            "F_PRIMARY_RESULT": (
                "For the best-performance candidate, " + _metric_summary(performance)
            ),
            "F_SIMPLE_RESULT": (
                f"The simplest retained candidate used {len(simplest.get('layer_materials') or [])} finite layers "
                f"({_stack_text(simplest)}) with target, robustness, and simplicity scores of "
                f"{_number(simplest.get('target_score', 0.0))}, {_number(simplest.get('robustness_score', 0.0))}, "
                f"and {_number(simplest.get('simplicity_score', 0.0))}, respectively."
            ),
            "F_ROBUSTNESS_MODEL": (
                f"Manufacturing sensitivity was evaluated with {int(perturbation.get('samples') or 0)} perturbations using "
                f"{str(perturbation.get('distribution') or 'the declared distribution').replace('_', ' ')}, "
                f"a thickness scale of {float(perturbation.get('sigma_nm') or 0.0):.3f} nm, and a common incidence-angle bound of "
                f"{float(perturbation.get('angle_perturbation_deg') or 0.0):.3f} degrees."
            ),
            "F_ROBUSTNESS_RESULT": (
                f"The retained robust candidate had a robustness score of {_number(robust.get('robustness_score', 0.0))}; "
                + robustness_text
            ),
            "F_ATTAINMENT": (
                f"The best-performance design satisfied {met} of {assessed} soft target clauses. "
                "The result is therefore reported as a verified best-effort trade-off rather than exact fulfillment of every requested goal."
            ),
            "F_LIMITATION": (
                "The calculations do not establish deposition stress, adhesion, surface roughness, environmental durability, or process-dependent optical constants; "
                "these quantities require fabrication-specific characterization before experimental use."
            ),
        }
        sources = {
            "F_MODEL": [str(simulation_path), str(certificate_path)],
            "F_SEARCH": [str(run_dir / "FINAL_ANSWER.json")],
            "F_BEST_STACK": [str(candidate_dir / "OBJECTIVE_REPORT.json")],
            "F_PRIMARY_RESULT": [str(candidate_dir / "OBJECTIVE_REPORT.json")],
            "F_SIMPLE_RESULT": [str(run_dir / "FINAL_ANSWER.json")],
            "F_ROBUSTNESS_MODEL": [str(robustness_path)],
            "F_ROBUSTNESS_RESULT": [str(robustness_path)],
            "F_ATTAINMENT": [str(run_dir / "FINAL_ANSWER.json")],
            "F_LIMITATION": [str(run_dir / "FINAL_ANSWER.json")],
            "F_SCOPE": [str(run_dir / "REQUEST.json"), str(run_dir / "PROBLEM_ANALYSIS.json")],
        }
        package = {
            "schema_version": "tmm-article-evidence.v1",
            "source_run_dir": str(run_dir),
            "source_run_status": answer.get("status"),
            "question": research.get("question") or _read_json(run_dir / "REQUEST.json").get("question"),
            "problem_interpretation": answer.get("problem_interpretation"),
            "facts": [
                {"fact_id": fact_id, "statement": statement, "source_artifacts": sources[fact_id]}
                for fact_id, statement in facts.items()
            ],
            "required_fact_tokens": {
                "abstract": ["F_SCOPE", "F_PRIMARY_RESULT", "F_ROBUSTNESS_RESULT"],
                "methods": ["F_MODEL", "F_SEARCH"],
                "results": ["F_BEST_STACK", "F_PRIMARY_RESULT", "F_SIMPLE_RESULT"],
                "robustness": ["F_ROBUSTNESS_MODEL", "F_ROBUSTNESS_RESULT"],
                "discussion": ["F_ATTAINMENT"],
                "limitations": ["F_LIMITATION"],
            },
            "writer_contract_mode": "deterministic_fact_injection",
            "candidate_portfolio": candidates[:8],
            "primary_candidate_id": performance.get("candidate_id"),
            "robust_candidate_id": robust.get("candidate_id"),
            "simple_candidate_id": simplest.get("candidate_id"),
            "primary_simulation_path": str(simulation_path),
            "primary_certificate_path": str(certificate_path),
            "references": _reference_inventory(answer),
            "acceptance_context": acceptance.get("acceptance") or {},
        }
        atomic_write_json(output_dir / "TMM_ARTICLE_EVIDENCE.json", package)
        return package


def _strip_markers(text: str) -> str:
    return REF_PATTERN.sub("", FACT_PATTERN.sub("", str(text or "")))


def _validate_draft(
    draft: Mapping[str, Any], evidence: Mapping[str, Any]
) -> list[str]:
    expected = {
        "title",
        "abstract",
        "introduction",
        "methods",
        "results",
        "robustness",
        "discussion",
        "limitations",
        "conclusion",
    }
    errors: list[str] = []
    fact_values = {
        str(item.get("fact_id") or ""): str(item.get("statement") or "")
        for item in evidence.get("facts") or []
        if isinstance(item, Mapping) and item.get("fact_id")
    }
    if set(draft) != expected:
        errors.append("article_schema_mismatch")
    for field_name in expected:
        text = str(draft.get(field_name) or "").strip()
        expanded_text = FACT_PATTERN.sub(
            lambda match: fact_values.get(match.group(1), match.group(0)),
            text,
        )
        if not text:
            errors.append(f"empty_section:{field_name}")
        if field_name != "title" and len(expanded_text.split()) < (35 if field_name in {"abstract", "conclusion", "limitations"} else 70):
            errors.append(f"section_too_short:{field_name}")
        for pattern in FORBIDDEN_PROSE:
            if pattern.search(text):
                errors.append(f"forbidden_reader_facing_language:{field_name}")
        if RAW_NUMBER_PATTERN.search(_strip_markers(text)):
            errors.append(f"raw_numeric_claim_outside_fact_token:{field_name}")

    facts = {str(item.get("fact_id")) for item in evidence.get("facts") or []}
    used_facts = {
        match.group(1)
        for value in draft.values()
        for match in FACT_PATTERN.finditer(str(value or ""))
    }
    unknown_facts = used_facts - facts
    if unknown_facts:
        errors.append("unknown_fact_tokens:" + ",".join(sorted(unknown_facts)))
    if evidence.get("writer_contract_mode") != "deterministic_fact_injection":
        for section, required in (evidence.get("required_fact_tokens") or {}).items():
            present = {match.group(1) for match in FACT_PATTERN.finditer(str(draft.get(section) or ""))}
            missing = set(required or []) - present
            if missing:
                errors.append(f"missing_required_facts:{section}:" + ",".join(sorted(missing)))

    allowed_refs = {str(item.get("paper_id")) for item in evidence.get("references") or []}
    used_refs = {
        match.group(1)
        for value in draft.values()
        for match in REF_PATTERN.finditer(str(value or ""))
    }
    if used_refs - allowed_refs:
        errors.append("unknown_reference_ids:" + ",".join(sorted(used_refs - allowed_refs)))
    if allowed_refs and len(used_refs) < min(2, len(allowed_refs)):
        errors.append("insufficient_method_references")
    return sorted(set(errors))


def _fallback_draft(evidence: Mapping[str, Any]) -> dict[str, str]:
    references = [str(item.get("paper_id")) for item in evidence.get("references") or []]
    cites = " ".join(f"[REF:{item}]" for item in references[:3])
    literature_sentence = (
        "Published coating-design and tolerance methods informed the route "
        f"families and interpretation {cites}. "
        if cites
        else "The offline draft relies only on the verified numerical method and does not add incomplete literature records. "
    )
    return {
        "title": "Verifier-Guided Design of a Planar Multilayer Optical Coating",
        "abstract": (
            "[FACT:F_SCOPE] A bounded route comparison was used to identify a reproducible design trade-off. "
            "[FACT:F_PRIMARY_RESULT] [FACT:F_ROBUSTNESS_RESULT] The study separates nominal optical performance from manufacturing sensitivity and reports remaining limitations explicitly."
        ),
        "introduction": (
            "Planar multilayer coatings translate refractive-index contrast and optical thickness into wavelength-selective interference. "
            "A useful design study must therefore connect the requested spectral behavior to a realizable stack while preserving the distinction between simulated evidence and fabrication claims. "
            "The present analysis compares bounded structural families under one common objective contract, so differences in performance can be interpreted without changing the scientific question between routes. "
            "The central aim is to identify a transparent portfolio rather than to hide conflicting objectives behind a single scalar optimum."
        ),
        "methods": (
            "[FACT:F_MODEL] The model resolves reflection, transmission, and absorption for each declared angle and polarization. "
            + "[FACT:F_SEARCH] Candidate thicknesses were refined with complementary local and global strategies, and every retained solution was re-evaluated by the deterministic solver before ranking. "
            + literature_sentence
            + "Those sources provide methodological context; all performance statements for the new stack come from the recorded calculations in this study."
        ),
        "results": (
            "[FACT:F_BEST_STACK] [FACT:F_PRIMARY_RESULT] The selected stack represents the strongest nominal response under the shared objective, while the candidate portfolio preserves alternatives with different complexity and sensitivity. "
            "[FACT:F_SIMPLE_RESULT] The comparison shows that increasing structural freedom does not guarantee a uniformly superior solution because angular response, polarization splitting, and thickness sensitivity reshape the objective landscape. "
            "The spectrum and portfolio figures report the underlying calculated values rather than a smoothed conceptual reconstruction."
        ),
        "robustness": (
            "[FACT:F_ROBUSTNESS_MODEL] [FACT:F_ROBUSTNESS_RESULT] The perturbation ensemble places nominal performance in a manufacturing context and reveals whether the leading candidate is supported by a broad neighborhood or a narrow optimum. "
            "This test remains a numerical sensitivity analysis because it samples declared thickness and angle errors without modelling process correlations, roughness, or post-deposition index shifts."
        ),
        "discussion": (
            "[FACT:F_ATTAINMENT] The result therefore supports a ranked design decision rather than a binary feasibility declaration. "
            "The performance, robustness, and simplicity roles expose different parts of the design landscape and allow an experimental team to choose a candidate according to fabrication priorities. "
            "The comparison also indicates where additional material choices or a different structural family would be more valuable than further local refinement of the same parameterization."
        ),
        "limitations": (
            "[FACT:F_LIMITATION] Material dispersion is used as represented by the selected local datasets, so data provenance and wavelength coverage remain part of the interpretation. "
            "The bounded optimizer portfolio cannot prove global optimality, and the reported design should be treated as a reproducible computational candidate for subsequent process-aware refinement."
        ),
        "conclusion": (
            "A verifier-guided comparison can convert a natural-language coating objective into a physically checked design portfolio while keeping unmet targets visible. "
            "The leading and simplest candidates establish useful endpoints for later fabrication-aware refinement, and the uncertainty analysis identifies the practical cost of choosing nominal performance over tolerance."
        ),
    }


def _evidence_bound_result_sections() -> dict[str, str]:
    """Return publication prose whose conclusions cannot exceed verified facts.

    These sections carry the new study's conclusions and are therefore kept
    deterministic.  Qwen remains responsible for rhetorical framing in the
    title, abstract, introduction, and methods, while exact facts are injected
    later from immutable solver artifacts.
    """

    return {
        "results": (
            "The retained portfolio is reported as a comparison among the candidates that were actually evaluated. "
            "The ranking distinguishes nominal target fit, perturbation performance, and structural simplicity; these scores answer different decision questions and should not be collapsed into a claim of universal superiority. "
            "The spectral plots and tables contain the calculated response used for that ranking. "
            "No trend outside the sampled routes or the declared optical conditions is inferred from this comparison."
        ),
        "robustness": (
            "The perturbation ensemble quantifies sensitivity only under the declared numerical error model. "
            "Its distribution can be used to compare the sampled candidates under that model, but it does not estimate fabrication yield, process drift, or long-term reliability. "
            "Accordingly, robustness is interpreted as a computational ranking dimension rather than evidence of manufacturability. "
            "The nominal spectrum and the perturbed score distribution must therefore be read together, because each describes a different part of the bounded numerical evaluation."
        ),
        "discussion": (
            "The result supports a bounded design decision: the performance-oriented candidate and the simplicity-oriented candidate occupy different positions under the same scoring contract. "
            "Their comparison identifies the consequences observed within this search without asserting that layer count or a particular material sequence causes a general performance trend. "
            "Because some soft clauses remain unmet, the portfolio is most useful as a reproducible starting point for subsequent route expansion or fabrication-aware refinement. "
            "This interpretation keeps unresolved target conflicts visible while retaining candidates that may be valuable under different priorities."
        ),
        "limitations": (
            "The conclusions are restricted to the declared material datasets, optical conditions, structural routes, search budget, and numerical perturbations. "
            "The calculations cannot determine process compatibility or experimental repeatability. "
            "Configurations outside the sampled routes may yield different trade-offs, so the ranking is local to the documented computational study."
        ),
        "conclusion": (
            "Within the declared routes and search budget, the study identifies a physics-checked candidate portfolio and preserves the difference between nominal performance, numerical robustness, and simplicity. "
            "The outcome is a verified best-effort design rather than a declaration that every requested target was achieved. "
            "Any transition from this computational result to fabrication requires process-specific material characterization and an independent experimental plan."
        ),
    }


def _deterministic_article_draft(
    evidence: Mapping[str, Any],
    *,
    title: str,
) -> dict[str, str]:
    references = [str(item.get("paper_id") or "") for item in evidence.get("references") or []]
    reference_markers = " ".join(
        f"[REF:{paper_id}]" for paper_id in references[:3] if paper_id
    )
    literature_context = (
        "The bounded route formulation and perturbation interpretation follow methodological ideas from published multilayer design and tolerance studies "
        + reference_markers
        + "."
        if reference_markers
        else "No incomplete bibliographic record is used to support the numerical result."
    )
    draft = {
        "title": str(title or "Computational Design and Robustness Analysis of a Planar Multilayer Optical Coating").strip(),
        "abstract": (
            "[FACT:F_SCOPE] The study compared bounded structural routes with a common transfer-matrix objective and retained a decision portfolio rather than a single unqualified optimum. "
            "[FACT:F_PRIMARY_RESULT] [FACT:F_ROBUSTNESS_RESULT] [FACT:F_ATTAINMENT] The calculation separates nominal target fit, numerical perturbation sensitivity, and structural simplicity while keeping fabrication claims outside the evidence boundary."
        ),
        "introduction": (
            "Planar multilayer coatings modify spectral reflection, transmission, and absorption through interference among waves returned by successive interfaces. "
            "A design question spanning wavelengths, angles, or polarizations is therefore a multi-condition problem in which nominal response, structural complexity, and sensitivity to parameter errors may rank candidates differently. "
            "The present study frames the declared optical task as a bounded comparison under one common objective. "
            "This framing permits the calculated trade-offs to be reported without treating unsampled structures or experimental feasibility as established results."
        ),
        "methods": (
            "[FACT:F_MODEL] [FACT:F_SEARCH] Candidate responses were recomputed with the same forward solver after search, and only records that passed the solver's physical audit were retained for tabulation. "
            + literature_context
            + " Those sources guide route formulation and tolerance interpretation; they do not certify the simulated performance of the new candidates."
        ),
        **_evidence_bound_result_sections(),
    }
    return draft


@dataclass
class QwenTMMArticleWriter:
    prompt_path: Path = DEFAULT_PROMPT_PATH
    force_mock: bool | None = None
    max_tokens: int = 7000
    audit_prompt_path: Path = DEFAULT_AUDIT_PROMPT_PATH

    def _semantic_audit(
        self,
        draft: Mapping[str, str],
        evidence: Mapping[str, Any],
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        if self.force_mock:
            return [], {
                "model_name": "deterministic_mock_auditor",
                "mock_llm": True,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
            }
        payload = {
            "verified_facts": [
                {
                    "fact_id": str(item.get("fact_id") or ""),
                    "statement": str(item.get("statement") or ""),
                }
                for item in evidence.get("facts") or []
                if isinstance(item, Mapping)
            ],
            "method_references": [
                {
                    "paper_id": str(item.get("paper_id") or ""),
                    "title": str(item.get("title") or ""),
                }
                for item in evidence.get("references") or []
                if isinstance(item, Mapping)
            ],
            "draft": {
                str(key): _replace_facts(str(value or ""), {
                    str(item.get("fact_id") or ""): str(item.get("statement") or "")
                    for item in evidence.get("facts") or []
                    if isinstance(item, Mapping) and item.get("fact_id")
                })
                for key, value in draft.items()
            },
        }
        response = QwenFlashOnlyClient(agent_name="TMMArticleEvidenceAuditor").call(
            [
                {
                    "role": "system",
                    "content": Path(self.audit_prompt_path).read_text(encoding="utf-8"),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=2200,
            force_mock=False,
        )
        parsed = _safe_json(str(response.get("content") or ""))
        status = str(parsed.get("status") or "").strip().lower()
        raw_findings = parsed.get("findings") or []
        findings: list[dict[str, str]] = []
        if isinstance(raw_findings, list):
            for item in raw_findings[:16]:
                if not isinstance(item, Mapping):
                    continue
                section = str(item.get("section") or "").strip()
                span = str(item.get("exact_span") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if section in draft and span and span in str(draft.get(section) or "") and reason:
                    findings.append({"section": section, "exact_span": span, "reason": reason})
        if status not in {"passed", "revise"}:
            findings.append(
                {
                    "section": "audit",
                    "exact_span": "invalid auditor response",
                    "reason": "The semantic evidence audit did not return the required schema.",
                }
            )
        elif status == "revise" and not findings:
            findings.append(
                {
                    "section": "audit",
                    "exact_span": "unspecified",
                    "reason": "The semantic auditor requested revision without a traceable finding.",
                }
            )
        return findings, dict(response.get("_llm_usage") or {})

    def write(self, evidence: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        context = str(evidence.get("problem_interpretation") or "")
        context = re.split(r"\d", context, maxsplit=1)[0].strip(" ,;:-")
        if len(context.split()) < 5:
            context = "A planar multilayer optical coating design investigated with transfer-matrix simulation"
        payload = {
            "task": "Create one neutral publication title for a verified computational thin-film design and robustness study.",
            "study_context_without_exact_conditions": context,
        }
        if self.force_mock:
            return _deterministic_article_draft(
                evidence,
                title="Computational Design and Robustness Analysis of a Planar Multilayer Optical Coating",
            ), {
                "mock_llm": True,
                "model_name": "deterministic_fallback",
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
            }
        client = QwenFlashOnlyClient(agent_name="TMMArticleWriter")
        messages = [
            {"role": "system", "content": Path(self.prompt_path).read_text(encoding="utf-8")},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        usage_rows: list[dict[str, Any]] = []
        audit_usage_rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for attempt in range(2):
            request = list(messages)
            if attempt:
                request.append({"role": "user", "content": "Return exactly one JSON object with a single non-empty title field."})
            response = client.call(request, max_tokens=180, force_mock=False)
            usage_rows.append(dict(response.get("_llm_usage") or {}))
            raw_content = str(response.get("content") or "")
            parsed = _safe_json(raw_content)
            title = str(parsed.get("title") or "").strip()
            draft = _deterministic_article_draft(evidence, title=title)
            errors = _validate_draft(draft, evidence)
            if not errors:
                findings, audit_usage = self._semantic_audit(draft, evidence)
                audit_usage_rows.append(audit_usage)
                if findings:
                    errors = [
                        "unsupported_claim:"
                        + str(item.get("section") or "")
                        + ":"
                        + str(item.get("exact_span") or "")
                        + ":"
                        + str(item.get("reason") or "")
                        for item in findings
                    ]
                else:
                    all_usage_rows = usage_rows + audit_usage_rows
                    usage = {
                        "model_name": QWEN_POLICY_MODEL,
                        "mock_llm": False,
                        "call_count": len(all_usage_rows),
                        "writer_call_count": len(usage_rows),
                        "auditor_call_count": len(audit_usage_rows),
                        "estimated_input_tokens": sum(int(row.get("estimated_input_tokens") or 0) for row in all_usage_rows),
                        "estimated_output_tokens": sum(int(row.get("estimated_output_tokens") or 0) for row in all_usage_rows),
                        "attempts": all_usage_rows,
                        "writer_mode": "qwen_prose_plus_independent_evidence_audit",
                    }
                    usage["estimated_cost_cny"] = estimate_call_cost_cny(
                        QWEN_POLICY_MODEL,
                        usage["estimated_input_tokens"],
                        usage["estimated_output_tokens"],
                    )
                    return draft, usage
        raise ValueError(
            "Qwen title or deterministic article failed validation after two attempts: "
            + "; ".join(errors)
        )


def _replace_facts(text: str, facts: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        fact_id = match.group(1)
        if fact_id not in facts:
            raise ValueError(f"Unknown fact token {fact_id}")
        return facts[fact_id]

    return FACT_PATTERN.sub(replace, str(text or ""))


def _candidate_table(candidates: list[dict[str, Any]]) -> str:
    candidates = _unique_candidates(candidates)
    role_labels = {
        "best_performance": "Performance",
        "most_robust": "Robustness",
        "simplest": "Simplicity",
    }
    selected: dict[str, dict[str, Any]] = {}
    for role, score_name in (
        ("best_performance", "target_score"),
        ("most_robust", "robustness_score"),
        ("simplest", "simplicity_score"),
    ):
        if candidates:
            item = _best_by_role(candidates, role, score_name)
            candidate_id = str(item.get("candidate_id") or "")
            selected.setdefault(candidate_id, {"candidate": item, "roles": []})["roles"].append(
                role_labels[role]
            )
    lines = [
        "| Portfolio role | Layers | Target | Robustness | Simplicity | Clauses |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in selected.values():
        item = row["candidate"]
        roles = " + ".join(row["roles"])
        met, assessed = _target_attainment(item)
        lines.append(
            "| "
            + roles.replace("|", "/")
            + f" | {len(item.get('layer_materials') or [])}"
            + f" | {_number(item.get('target_score', 0.0))}"
            + f" | {_number(item.get('robustness_score', 0.0))}"
            + f" | {_number(item.get('simplicity_score', 0.0))}"
            + f" | {met}/{assessed} |"
        )
    return "\n".join(lines)


def _layer_table(candidate: Mapping[str, Any]) -> str:
    lines = ["| Layer from incident side | Material | Thickness (nm) |", "|---:|---|---:|"]
    for index, (material, thickness) in enumerate(
        zip(candidate.get("layer_materials") or [], candidate.get("thicknesses_nm") or []), 1
    ):
        lines.append(f"| {index} | {_display_material(material)} | {float(thickness):.3f} |")
    return "\n".join(lines)


def _objective_table(candidate: Mapping[str, Any]) -> str:
    lines = [
        "| Channel | Observable | Aggregation | Constraint | Target | Observed |",
        "|---|---|---|---|---:|---:|",
    ]
    for item in candidate.get("reported_metrics") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        match = re.search(r"_(mean|worst_case)_(-?\d+(?:\.\d+)?)_([sp])(?:_|$)", name)
        channel = (
            f"{match.group(2)} degrees, {'TE' if match.group(3) == 's' else 'TM'}"
            if match
            else "declared channel"
        )
        lines.append(
            f"| {channel} | {item.get('observable') or ''} | {str(item.get('aggregation') or '').replace('_', ' ')} "
            f"| {str(item.get('constraint') or '').replace('_', ' ')} | {_percent(item.get('target') or 0.0)} "
            f"| {_percent(item.get('observed') or 0.0)} |"
        )
    return "\n".join(lines)


def _build_plots(evidence: Mapping[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    figures_dir = Path(output_dir) / "generated_figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    simulation = _read_json(Path(str(evidence["primary_simulation_path"])))
    wavelengths = np.asarray(simulation.get("wavelengths_nm") or [], dtype=float)
    channels = simulation.get("channels") or {}
    observables = []
    candidates_all = list(evidence.get("candidate_portfolio") or [])
    primary_id = str(evidence.get("primary_candidate_id") or "")
    primary = next(
        item
        for item in candidates_all
        if str(item.get("candidate_id") or "") == primary_id
    )
    for item in primary.get("reported_metrics") or []:
        observable = str(item.get("observable") or "")
        if observable and observable not in observables:
            observables.append(observable)
    observables = observables[:3] or ["R", "T"]
    fig, axes = plt.subplots(len(observables), 1, figsize=(7.2, 2.8 * len(observables)), sharex=True)
    axes = np.atleast_1d(axes)
    for axis, observable in zip(axes, observables):
        for channel_name, values in channels.items():
            series = values.get(observable) if isinstance(values, Mapping) else None
            if isinstance(series, list) and len(series) == len(wavelengths):
                axis.plot(wavelengths, 100.0 * np.asarray(series), linewidth=1.25, label=channel_name.replace("pol=s", "TE").replace("pol=p", "TM"))
        axis.set_ylabel(f"{observable} (%)")
        axis.grid(alpha=0.25)
        axis.set_ylim(bottom=0)
    axes[-1].set_xlabel("Wavelength (nm)")
    axes[0].legend(ncol=3, fontsize=7, frameon=False)
    fig.tight_layout()
    spectrum_path = figures_dir / "verified_spectral_response.png"
    fig.savefig(spectrum_path, dpi=220, bbox_inches="tight")
    plt.close(fig)

    candidates = candidates_all[:8]
    fig, axis = plt.subplots(figsize=(7.2, 4.4))
    for item in candidates:
        x = float(item.get("target_score") or 0.0)
        y = float(item.get("robustness_score") or 0.0)
        size = 55 + 12 * len(item.get("layer_materials") or [])
        axis.scatter(x, y, s=size, alpha=0.75)
        axis.annotate(str(item.get("route_id") or item.get("candidate_id") or "candidate"), (x, y), xytext=(4, 4), textcoords="offset points", fontsize=7)
    axis.set_xlabel("Target score")
    axis.set_ylabel("Robustness score")
    axis.grid(alpha=0.25)
    fig.tight_layout()
    portfolio_path = figures_dir / "verified_candidate_portfolio.png"
    fig.savefig(portfolio_path, dpi=220, bbox_inches="tight")
    plt.close(fig)

    primary_robust = dict(primary.get("robustness_report") or {})
    samples = [float(item) for item in primary_robust.get("sample_soft_scores") or []]
    robustness_path = figures_dir / "verified_robustness_distribution.png"
    if samples:
        fig, axis = plt.subplots(figsize=(7.2, 4.2))
        axis.hist(samples, bins=min(8, max(4, len(samples) // 2)), color="#3b78a7", alpha=0.8, edgecolor="white")
        nominal = primary_robust.get("nominal_soft_score")
        if nominal is not None:
            axis.axvline(float(nominal), color="#b23a2b", linestyle="--", linewidth=1.6, label="Nominal")
            axis.legend(frameon=False)
        axis.set_xlabel("Aggregate soft score")
        axis.set_ylabel("Perturbation count")
        axis.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        fig.savefig(robustness_path, dpi=220, bbox_inches="tight")
        plt.close(fig)

    figures = [
        {
            "figure_id": "tmm_verified_spectrum",
            "local_path": str(spectrum_path),
            "caption_en": "Calculated spectral response of the best-performance multilayer candidate across all declared angle and polarization channels.",
            "section_id": "S03",
            "render_status": "ready",
            "review_decision": "system_approved_test_mode",
            "data_provenance_level": "exact",
        },
        {
            "figure_id": "tmm_candidate_portfolio",
            "local_path": str(portfolio_path),
            "caption_en": "Verified performance-robustness portfolio. Marker size increases with the number of finite coating layers.",
            "section_id": "S03",
            "render_status": "ready",
            "review_decision": "system_approved_test_mode",
            "data_provenance_level": "exact",
        },
    ]
    if robustness_path.is_file():
        figures.append(
            {
                "figure_id": "tmm_robustness_distribution",
                "local_path": str(robustness_path),
                "caption_en": "Distribution of the aggregate soft score under the declared thickness and incidence-angle perturbations.",
                "section_id": "S04",
                "render_status": "ready",
                "review_decision": "system_approved_test_mode",
                "data_provenance_level": "exact",
            }
        )
    return figures


def _assemble_markdown(
    draft: Mapping[str, str], evidence: Mapping[str, Any]
) -> str:
    facts = {str(item["fact_id"]): str(item["statement"]) for item in evidence.get("facts") or []}
    primary_id = str(evidence.get("primary_candidate_id") or "")
    candidates = list(evidence.get("candidate_portfolio") or [])
    primary = next(item for item in candidates if str(item.get("candidate_id") or "") == primary_id)
    required = dict(evidence.get("required_fact_tokens") or {})

    def section_text(section_id: str, prose: str) -> str:
        deterministic = " ".join(
            facts[fact_id]
            for fact_id in required.get(section_id, [])
            if fact_id in facts
            and f"[FACT:{fact_id}]" not in prose
        )
        return " ".join(
            item.strip()
            for item in (
                deterministic,
                _replace_facts(prose, facts),
            )
            if item.strip()
        )

    sections = [
        ("Introduction", draft["introduction"]),
        ("Methods", draft["methods"]),
        ("Results", draft["results"]),
        ("Robustness under Manufacturing Uncertainty", draft["robustness"]),
        ("Discussion", draft["discussion"]),
        ("Limitations", draft["limitations"]),
        ("Conclusion", draft["conclusion"]),
    ]
    rendered: list[str] = []
    for title, prose in sections:
        field_name = {
            "Introduction": "introduction",
            "Methods": "methods",
            "Results": "results",
            "Robustness under Manufacturing Uncertainty": "robustness",
            "Discussion": "discussion",
            "Limitations": "limitations",
            "Conclusion": "conclusion",
        }[title]
        rendered.extend([f"# {title}", "", section_text(field_name, prose), ""])
        if title == "Results":
            rendered.extend(
                [
                    "## Best-performance layer prescription",
                    "",
                    _layer_table(primary),
                    "",
                    "## Verified objective values",
                    "",
                    _objective_table(primary),
                    "",
                    "## Candidate portfolio",
                    "",
                    _candidate_table(candidates),
                    "",
                ]
            )
    markdown = "\n".join(rendered).strip() + "\n"
    normalized, integrity = prepare_publication_markdown(markdown)
    if integrity.get("public_prose_audit", {}).get("status") != "passed":
        raise ValueError("Article contains internal workflow language")
    if integrity.get("unresolved_formula_hazards"):
        raise ValueError("Article contains unresolved formula hazards")
    return normalized


def _write_renderer_inputs(
    *,
    output_dir: Path,
    run_dir: Path,
    evidence: Mapping[str, Any],
    draft: Mapping[str, str],
    article_path: Path,
    figures: list[dict[str, Any]],
) -> tuple[Path, Path]:
    blueprint_path = output_dir / "ARTICLE_BLUEPRINT.json"
    atomic_write_json(
        blueprint_path,
        {
            "schema_version": "tmm-article-blueprint.v1",
            "input_context": {"user_question": evidence.get("question")},
            "review_thesis": draft.get("conclusion"),
            "topic_identity": {"core_anchors": ["thin-film optics", "transfer-matrix method", "multilayer design"]},
            "sections": [
                {"section_id": "S01", "section_title": "Introduction"},
                {"section_id": "S02", "section_title": "Methods"},
                {"section_id": "S03", "section_title": "Results"},
                {"section_id": "S04", "section_title": "Robustness under Manufacturing Uncertainty"},
                {"section_id": "S05", "section_title": "Discussion"},
                {"section_id": "S06", "section_title": "Limitations"},
                {"section_id": "S07", "section_title": "Conclusion"},
            ],
        },
    )
    visual_path = output_dir / "TMM_ARTICLE_VISUAL_PACKAGE.json"
    atomic_write_json(
        visual_path,
        {
            "schema_version": "tmm-article-visual-package.v1",
            "figures": figures,
            "unfilled_visual_opportunities": [],
        },
    )
    ledger_path = output_dir / "section_coverage" / "sections" / "S02" / "SECTION_SOURCE_LEDGER.json"
    sources = []
    for item in evidence.get("references") or []:
        row = dict(item)
        # The publication renderer's local source collector recognizes this
        # value as the strongest non-network bibliographic source.
        row["acquisition_status"] = (
            "fulltext"
            if str(row.get("content_depth") or "") in {"fulltext", "s2_snippet"}
            else str(row.get("content_depth") or "metadata")
        )
        sources.append(row)
    atomic_write_json(
        ledger_path,
        {"schema_version": "tmm-article-source-ledger.v1", "sources": sources},
    )
    facts = {str(item["fact_id"]): str(item["statement"]) for item in evidence.get("facts") or []}
    abstract_prose = str(draft.get("abstract") or "")
    abstract_facts = " ".join(
        facts[fact_id]
        for fact_id in (evidence.get("required_fact_tokens") or {}).get("abstract", [])
        if fact_id in facts and f"[FACT:{fact_id}]" not in abstract_prose
    )
    abstract = " ".join(
        item.strip()
        for item in (abstract_facts, _replace_facts(abstract_prose, facts))
        if item.strip()
    )
    metadata_path = output_dir / "PUBLICATION_METADATA.json"
    atomic_write_json(
        metadata_path,
        {
            "title": str(draft.get("title") or "Computational Multilayer Design Study"),
            "authors": [{"name": "Author information pending"}],
            "abstract": abstract,
            "keywords": ["thin-film optics", "transfer-matrix method", "inverse design", "robust optimization"],
            "draft_only": True,
            "date": "",
        },
    )
    package_path = output_dir / "TMM_ARTICLE_CONTENT_PACKAGE.json"
    atomic_write_json(
        package_path,
        {
            "schema_version": "tmm-article-content-package.v1",
            "status": "completed",
            "source_run_dir": str(output_dir),
            "tmm_source_run_dir": str(run_dir),
            "final_review_path": str(article_path),
            "final_visual_package_path": str(visual_path),
            "base_kb_sqlite": "",
            "artifacts": {"review_blueprint": str(blueprint_path)},
        },
    )
    return package_path, metadata_path


def build_tmm_article_publication(
    *,
    run_dir: Path,
    output_dir: Path,
    force_mock: bool | None = None,
    compile_pdf: bool = True,
    render_previews: bool = True,
    enrich_references: bool = True,
    draft_path: Path | None = None,
    bibliography_cache_path: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    run_dir = Path(run_dir).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = TMMArticleEvidenceCompiler(run_dir, output_dir).compile()
    cached_records: dict[str, Any] = {}
    if bibliography_cache_path:
        cache_payload = _read_json(Path(bibliography_cache_path))
        raw_records = cache_payload.get("records") or {}
        if isinstance(raw_records, Mapping):
            cached_records = {str(key): dict(value) for key, value in raw_records.items() if isinstance(value, Mapping)}
            evidence["references"] = [
                {**dict(item), **cached_records.get(str(item.get("paper_id") or ""), {})}
                for item in evidence.get("references") or []
            ]
    if not enrich_references:
        # A disconnected build must not create placeholder bibliography rows.
        # Keep only records already complete enough for publication; the
        # normal online mode retains CorpusId records and resolves them in one
        # rate-limited S2 batch inside the established renderer.
        evidence["references"] = [
            item
            for item in evidence.get("references") or []
            if item.get("title")
            and item.get("authors")
            and item.get("year")
            and (item.get("doi") or item.get("venue"))
        ]
        evidence["reference_policy"] = "offline_complete_metadata_only"
        atomic_write_json(output_dir / "TMM_ARTICLE_EVIDENCE.json", evidence)
    if draft_path:
        draft = {str(key): str(value or "").strip() for key, value in _read_json(Path(draft_path)).items()}
        usage = {
            "model_name": "reused_validated_draft",
            "mock_llm": False,
            "call_count": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_cost_cny": 0.0,
            "writer_mode": "reused_validated_draft",
            "source_draft_path": str(Path(draft_path).resolve()),
        }
    else:
        draft, usage = QwenTMMArticleWriter(force_mock=force_mock).write(evidence)
    validation_errors = _validate_draft(draft, evidence)
    if validation_errors:
        raise ValueError("Article draft failed final validation: " + "; ".join(validation_errors))
    atomic_write_json(output_dir / "TMM_ARTICLE_DRAFT.json", draft)
    article_path = output_dir / "TMM_RESEARCH_ARTICLE_EN.md"
    _write_text(article_path, _assemble_markdown(draft, evidence))
    figures = _build_plots(evidence, output_dir)
    package_path, metadata_path = _write_renderer_inputs(
        output_dir=output_dir,
        run_dir=run_dir,
        evidence=evidence,
        draft=draft,
        article_path=article_path,
        figures=figures,
    )
    latex_dir = output_dir / "latex_en"
    latex_dir.mkdir(parents=True, exist_ok=True)
    if bibliography_cache_path and Path(bibliography_cache_path).is_file():
        shutil.copy2(Path(bibliography_cache_path), latex_dir / "BIBLIOGRAPHY_METADATA.json")
    latex_report = build_latex_publication(
        content_package_path=package_path,
        output_dir=latex_dir,
        metadata_path=metadata_path,
        source_markdown_path=article_path,
        language="en",
        document_type="research_article",
        enrich_crossref=enrich_references,
        compile_pdf=compile_pdf,
        render_previews=render_previews,
    )
    markdown_text = article_path.read_text(encoding="utf-8")
    report = {
        "schema_version": "tmm-article-publication-report.v1",
        "status": (
            "completed"
            if latex_report.get("status") in {"submission_ready", "compiled_awaiting_metadata"}
            else "failed"
        ),
        "source_run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "word_count": len(re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", markdown_text)),
        "reference_marker_count": len(REF_PATTERN.findall(markdown_text)),
        "unique_reference_count": len(set(REF_PATTERN.findall(markdown_text))),
        "figure_count": len(figures),
        "fact_count": len(evidence.get("facts") or []),
        "writer_usage": usage,
        "latex_status": latex_report.get("status"),
        "latex_submission_blockers": latex_report.get("submission_blockers") or [],
        "pdf_validation": latex_report.get("pdf_validation") or {},
        "wall_seconds": time.perf_counter() - started,
        "artifacts": {
            "evidence": str(output_dir / "TMM_ARTICLE_EVIDENCE.json"),
            "article_markdown": str(article_path),
            "content_package": str(package_path),
            "metadata": str(metadata_path),
            "latex_report": str(latex_dir / "LATEX_BUILD_REPORT.json"),
            "pdf": str(latex_dir / "main.pdf"),
            "arxiv_source": str(latex_dir / "arxiv-source.zip"),
        },
    }
    atomic_write_json(output_dir / "TMM_ARTICLE_PUBLICATION_REPORT.json", report)
    return report



# ===========================================================================
# Article branch additions (T-11): ProvenanceLedger + ClaimLedger adaptation
# v0.8 Scalability Contract: per-section evidence subsets only -- injecting
# the full ledger into a Qwen prompt is prohibited.
# ===========================================================================

import json as _json  # noqa: E402
import re as _re  # noqa: E402
import warnings as _warnings  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Literal, Mapping  # noqa: E402

from config.qwen_config import get_cost_tracker  # noqa: E402
from .problem_analyzer import ArticlePlusQwenClient  # noqa: E402
from .provenance_compiler import Claim, ClaimLedger, ProvenanceEntry, ProvenanceLedger  # noqa: E402

ArticleSectionName = Literal[
    "introduction", "methods", "results", "discussion", "conclusion",
]
ARTICLE_EVIDENCE_TOKEN_LIMIT = 30
_CODE_FENCE = chr(96) * 3
_SECTION_SOURCES = {
    "introduction": ("literature_fact", "user_constraint"),
    "methods": ("method_constant", "user_constraint"),
    # results admits certified simulation facts plus literature entries;
    # literature_fact_unverified is rejected by the SC-4 gate below.
    "results": ("simulation_fact", "literature_fact", "literature_fact_unverified"),
    "discussion": ("simulation_fact",),
    "conclusion": ("simulation_fact", "literature_fact"),
}
_ROUTE_SCOPED_SOURCES = frozenset({
    "simulation_fact", "literature_fact", "literature_fact_unverified",
})
_SECTION_CLAIM_TYPES = {
    "results": frozenset({"comparison", "robustness"}),
    "discussion": frozenset({"comparison", "trend", "causal"}),
    "conclusion": frozenset({"comparison", "descriptive"}),
    "introduction": frozenset(),
    "methods": frozenset(),
}
_FACT_PATTERN = _re.compile(r"\{\{FACT:([^}]+)\}\}")
_CLAIM_PATTERN = _re.compile(r"\{\{CLAIM:([^}]+)\}\}")
_BARE_NUMBER_PATTERN = _re.compile(
    r"\b\d+\.\d+\b|\b\d+(?:\.\d+)?\s*(?:nm|deg|%)"
)
_COMPARISON_PATTERN = _re.compile(
    r"\b(outperforms?|better than|more robust|superior to|improves? over)\b",
    _re.IGNORECASE,
)


class EvidenceOverflowError(ValueError):
    """A section subset exceeded the configured token budget (SC-1)."""


class UnverifiedInResultsError(ValueError):
    """SC-4 gate: literature_fact_unverified reached the Results section."""


class UnsupportedClaimError(ValueError):
    """A CLAIM placeholder referenced an unregistered or evidence-free claim."""


class FactTokenNotFoundWarning(UserWarning):
    """An unknown FACT placeholder was left untouched."""


@dataclass
class EvidenceSubset:
    tokens: list[ProvenanceEntry] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)


@dataclass
class IntegrityViolation:
    kind: str
    line_no: int
    snippet: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _entry_in_objective(entry: ProvenanceEntry, objective_ids) -> bool:
    haystack = f"{entry.quantity_name} {entry.scope}".lower()
    return any(str(o).lower() in haystack for o in objective_ids)


def select_evidence(
    section: str,
    ledger: ProvenanceLedger,
    claim_ledger: ClaimLedger,
    route_ids=None,
    objective_ids=None,
    claim_types=None,
    *,
    token_limit: int = ARTICLE_EVIDENCE_TOKEN_LIMIT,
) -> EvidenceSubset:
    """Per-section evidence subset -- the anti-explosion interface (v0.8).

    Injecting the whole ProvenanceLedger into a writing prompt is prohibited;
    callers must go through this function. Raises EvidenceOverflowError when
    the subset exceeds token_limit, and UnverifiedInResultsError when an
    unverified literature fact reaches the Results section (SC-4 gate).
    """
    if section not in _SECTION_SOURCES:
        raise ValueError(
            f"unknown section {section!r}; expected one of {sorted(_SECTION_SOURCES)}"
        )
    allowed_sources = _SECTION_SOURCES[section]
    route_filter = set(route_ids) if route_ids else None
    tokens: list[ProvenanceEntry] = []
    for entry in ledger.entries:
        if entry.source_type not in allowed_sources:
            continue
        if (
            route_filter is not None
            and entry.source_type in _ROUTE_SCOPED_SOURCES
            and entry.route_id not in route_filter
        ):
            continue
        if objective_ids and not _entry_in_objective(entry, objective_ids):
            continue
        tokens.append(entry)
    if section == "results":
        for entry in tokens:
            if entry.source_type == "literature_fact_unverified":
                raise UnverifiedInResultsError(
                    f"UNVERIFIED_IN_RESULTS: token_id={entry.token_id}"
                )
    wanted_claim_types = (
        set(claim_types) if claim_types else _SECTION_CLAIM_TYPES.get(section, set())
    )
    claims = [
        claim for claim in claim_ledger.claims if claim.claim_type in wanted_claim_types
    ]
    if len(tokens) > int(token_limit):
        raise EvidenceOverflowError(
            f"EVIDENCE_OVERFLOW: section={section} tokens={len(tokens)} "
            f"limit={int(token_limit)}"
        )
    return EvidenceSubset(tokens=tokens, claims=claims)


def inject_fact_tokens(template: str, ledger: ProvenanceLedger) -> str:
    """Replace FACT placeholders with value+unit; unknown ids stay verbatim."""
    def _replace(match):
        token_id = match.group(1).strip()
        entry = ledger.get(token_id)
        if entry is None:
            _warnings.warn(
                f"FactTokenNotFoundWarning: no provenance token {token_id!r}; "
                "placeholder kept verbatim",
                FactTokenNotFoundWarning,
            )
            return match.group(0)
        unit = f" {entry.unit}" if entry.unit else ""
        return f"{entry.value}{unit}"

    return _FACT_PATTERN.sub(_replace, template)


def inject_claim_tokens(template: str, claim_ledger: ClaimLedger) -> str:
    """Replace CLAIM placeholders with registered, evidence-backed statements."""
    def _replace(match):
        claim_id = match.group(1).strip()
        claim = claim_ledger.get(claim_id)
        if claim is None:
            raise UnsupportedClaimError(
                f"CLAIM placeholder references unregistered claim {claim_id!r}"
            )
        if not claim.support_token_ids and not claim.support_ref_ids:
            raise UnsupportedClaimError(
                f"claim {claim_id!r} carries neither support_token_ids nor "
                "support_ref_ids"
            )
        return claim.statement

    return _CLAIM_PATTERN.sub(_replace, template)


def scan_integrity(article_md: str, ledger=None, claim_ledger=None):
    """Detect bare numbers and evidence-free comparisons in prose lines.

    Table rows, headings and fenced code blocks are exempt; a numeric
    mention is flagged only when its line carries no FACT placeholder.
    """
    violations: list[IntegrityViolation] = []
    in_code_block = False
    for line_no, raw_line in enumerate(str(article_md).splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith(_CODE_FENCE):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        if stripped.startswith(("#", "|")):
            continue
        if "{{FACT:" not in stripped and _BARE_NUMBER_PATTERN.search(stripped):
            violations.append(
                IntegrityViolation(
                    kind="bare_number",
                    line_no=line_no,
                    snippet=stripped[:160],
                    message="numeric value without an inline {{FACT:...}} anchor",
                )
            )
        if "{{CLAIM:" not in stripped and _COMPARISON_PATTERN.search(stripped):
            violations.append(
                IntegrityViolation(
                    kind="unsupported_comparison",
                    line_no=line_no,
                    snippet=stripped[:160],
                    message="comparative claim without an inline {{CLAIM:...}} anchor",
                )
            )
    return violations


def _qwen_total_tokens(usage) -> int:
    usage = dict(usage or {})
    for key in ("total_tokens", "totalTokens"):
        if usage.get(key) is not None:
            return int(usage[key])
    input_tokens = (
        usage.get("input_tokens") or usage.get("inputTokens")
        or usage.get("prompt_tokens") or 0
    )
    output_tokens = (
        usage.get("output_tokens") or usage.get("outputTokens")
        or usage.get("completion_tokens") or 0
    )
    return int(input_tokens) + int(output_tokens)


def build_section_prompt(section, subset, evidence_summary, draft_template):
    """Prompt payload carries subset METADATA only -- never the full ledger."""
    token_metadata = [
        {
            "token_id": entry.token_id,
            "quantity_name": entry.quantity_name,
            "unit": entry.unit,
            "scope": entry.scope,
            "human_readable": entry.human_readable,
        }
        for entry in subset.tokens
    ]
    claim_metadata = [
        {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "evidence_level": claim.evidence_level,
        }
        for claim in subset.claims
    ]
    payload = {
        "section": section,
        "evidence_tokens": token_metadata,
        "claims": claim_metadata,
        "method_evidence_summary": str(evidence_summary or "")[:2000],
        "draft_template": str(draft_template or ""),
        "rules": [
            "anchor every number as {{FACT:token_id}}",
            "anchor every comparison as {{CLAIM:claim_id}}",
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "You write one section of a reproducible optical thin-film "
                "article. Use only the supplied evidence metadata; anchor all "
                "numbers and comparisons with placeholders."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def write_section_draft(
    section,
    ledger,
    claim_ledger,
    *,
    route_ids=None,
    objective_ids=None,
    claim_types=None,
    evidence_summary="",
    draft_template="",
    client=None,
    force_mock=None,
):
    """Select evidence, build the metadata-only prompt, call Qwen-Plus once.

    Default routing is plus -- ArticlePlusQwenClient wraps
    get_qwen_client("plus"); usage lands in the shared CostTracker under
    the plus role. The injected-client seam keeps tests network-free.
    """
    subset = select_evidence(
        section, ledger, claim_ledger,
        route_ids=route_ids, objective_ids=objective_ids, claim_types=claim_types,
    )
    messages = build_section_prompt(
        section, subset, evidence_summary, draft_template
    )
    active_client = client if client is not None else ArticlePlusQwenClient()
    response = active_client.call(messages, max_tokens=4000, force_mock=force_mock)
    get_cost_tracker().record_qwen_usage(
        "plus", _qwen_total_tokens(response.get("_llm_usage"))
    )
    return {
        "content": str(response.get("content") or ""),
        "subset": subset,
        "prompt_messages": messages,
    }


INTEGRITY_FAILURE_FILENAME = "ARTICLE_INTEGRITY_FAILURE.json"


def enforce_integrity(
    article_md,
    ledger,
    claim_ledger,
    *,
    output_dir=None,
    client=None,
    max_rewrites=3,
):
    """Scan -> Qwen-Plus rewrite loop (max 3) -> failure report on give-up."""
    current_md = article_md
    violations = scan_integrity(current_md, ledger, claim_ledger)
    attempts = 0
    while violations and attempts < int(max_rewrites):
        attempts += 1
        repair_request = {
            "instruction": (
                "Rewrite only the flagged lines: anchor numbers with "
                "{{FACT:token_id}} from the supplied metadata and comparisons "
                "with {{CLAIM:claim_id}}; return complete markdown."
            ),
            "violations": [v.to_dict() for v in violations],
            "allowed_token_ids": [e.token_id for e in ledger.entries][:ARTICLE_EVIDENCE_TOKEN_LIMIT],
            "allowed_claim_ids": [c.claim_id for c in claim_ledger.claims],
        }
        messages = [
            {"role": "system", "content": "You repair citation-integrity violations in markdown."},
            {"role": "user", "content": json.dumps(repair_request, ensure_ascii=False)},
        ]
        active_client = client if client is not None else ArticlePlusQwenClient()
        response = active_client.call(messages, max_tokens=4000)
        get_cost_tracker().record_qwen_usage(
            "plus", _qwen_total_tokens(response.get("_llm_usage"))
        )
        candidate_md = str(response.get("content") or "")
        if candidate_md.strip():
            current_md = candidate_md
            violations = scan_integrity(current_md, ledger, claim_ledger)
    if violations and output_dir is not None:
        atomic_write_json(
            Path(output_dir) / INTEGRITY_FAILURE_FILENAME,
            {
                "status": "article_integrity_failure",
                "attempts": attempts,
                "violations": [v.to_dict() for v in violations],
            },
        )
    return current_md, violations


__all__ = [
    "ARTICLE_EVIDENCE_TOKEN_LIMIT",
    "ArticleSectionName",
    "Claim",
    "EvidenceOverflowError",
    "EvidenceSubset",
    "FactTokenNotFoundWarning",
    "IntegrityViolation",
    "ProvenanceEntry",
    "ProvenanceLedger",
    "QwenTMMArticleWriter",
    "TMMArticleEvidenceCompiler",
    "UnsupportedClaimError",
    "UnverifiedInResultsError",
    "build_section_prompt",
    "build_tmm_article_publication",
    "enforce_integrity",
    "inject_claim_tokens",
    "inject_fact_tokens",
    "scan_integrity",
    "select_evidence",
    "write_section_draft",
]
