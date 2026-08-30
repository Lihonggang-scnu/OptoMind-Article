"""Registry for independently produced Article chapter asset packages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from optomind_optics.harness.article_delivery import ArticleDeliveryPackage
from optomind_optics.harness.article_global_quality_audit import GlobalQualityAuditReport
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import ArticlePresentationPackage
from optomind_optics.harness.article_reproducibility import ArticleReproducibilityPackage
from optomind_optics.harness.article_review import ArticleReviewResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChapterAssetSpec(_StrictModel):
    chapter_id: str
    manuscript_path: str
    review_path: str = ""
    reproducibility_path: str = ""
    presentation_path: str = ""
    delivery_path: str = ""
    global_audit_path: str = ""


class ChapterRegistryEntry(_StrictModel):
    chapter_id: str
    status: Literal["complete", "partial", "invalid"]
    manuscript_package_id: str
    manuscript_body_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    review_id: str
    story_id: str
    section_ids: List[str]
    paragraph_count: int = Field(ge=0)
    asset_paths: Dict[str, str] = Field(default_factory=dict)
    missing_asset_kinds: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ArticleChapterRegistry(_StrictModel):
    schema_version: Literal["article-chapter-registry.v1"] = (
        "article-chapter-registry.v1"
    )
    registry_id: str
    status: Literal["complete", "partial", "invalid"]
    expected_chapter_count: int = Field(ge=1)
    registered_chapter_count: int = Field(ge=0)
    complete_chapter_count: int = Field(ge=0)
    missing_chapter_count: int = Field(ge=0)
    shared_plan_id: str = ""
    shared_ledger_id: str = ""
    shared_architecture_id: str = ""
    shared_story_id: str = ""
    entries: List[ChapterRegistryEntry] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


def _read(path: str, model_type: Any) -> Any:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise ValueError(f"chapter asset does not exist: {resolved}")
    return model_type.model_validate(json.loads(resolved.read_text(encoding="utf-8")))


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def build_chapter_registry(
    specs: Sequence[ChapterAssetSpec | Mapping[str, Any]],
    *,
    expected_chapter_count: int = 8,
) -> ArticleChapterRegistry:
    if expected_chapter_count < 1:
        raise ValueError("expected_chapter_count must be positive")
    models = [
        item if isinstance(item, ChapterAssetSpec) else ChapterAssetSpec.model_validate(item)
        for item in specs
    ]
    if len({item.chapter_id for item in models}) != len(models):
        raise ValueError("chapter_id values must be unique")
    entries: List[ChapterRegistryEntry] = []
    errors: List[str] = []
    warnings: List[str] = []
    seen_sections: set[str] = set()
    seen_paragraphs: set[str] = set()
    shared: Dict[str, str] = {}
    for spec in models:
        try:
            manuscript = _read(spec.manuscript_path, ArticleManuscriptPackage)
            identity = {
                "plan_id": manuscript.plan_id,
                "ledger_id": manuscript.ledger_id,
                "architecture_id": manuscript.architecture_id,
                "story_id": manuscript.story_id,
            }
            if not shared:
                shared = identity
            for field, expected in shared.items():
                if identity[field] != expected:
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} {field} {identity[field]!r} does not match shared {expected!r}"
                    )
            section_ids = [section.section_id for section in manuscript.body.sections]
            duplicate_sections = sorted(set(section_ids) & seen_sections)
            if duplicate_sections:
                raise ValueError(
                    f"chapter {spec.chapter_id!r} duplicates section IDs {duplicate_sections}"
                )
            paragraph_ids = [item.paragraph_id for item in manuscript.source_map]
            duplicate_paragraphs = sorted(set(paragraph_ids) & seen_paragraphs)
            if duplicate_paragraphs:
                raise ValueError(
                    f"chapter {spec.chapter_id!r} duplicates paragraph IDs {duplicate_paragraphs}"
                )
            seen_sections.update(section_ids)
            seen_paragraphs.update(paragraph_ids)
            asset_paths = {"manuscript": str(Path(spec.manuscript_path).resolve())}
            missing: List[str] = []
            chapter_warnings: List[str] = []
            review = None
            if spec.review_path:
                review = _read(spec.review_path, ArticleReviewResult)
                asset_paths["review"] = str(Path(spec.review_path).resolve())
                if review.review_id != manuscript.review_id:
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} review_id does not match manuscript"
                    )
            else:
                missing.append("review")
            reproducibility = None
            if spec.reproducibility_path:
                reproducibility = _read(
                    spec.reproducibility_path, ArticleReproducibilityPackage
                )
                asset_paths["reproducibility"] = str(
                    Path(spec.reproducibility_path).resolve()
                )
                if (
                    reproducibility.review_id != manuscript.review_id
                    or reproducibility.manuscript_body_id != manuscript.body_id
                ):
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} reproducibility identity does not match manuscript"
                    )
            else:
                missing.append("reproducibility")
            presentation = None
            if spec.presentation_path:
                presentation = _read(
                    spec.presentation_path, ArticlePresentationPackage
                )
                asset_paths["presentation"] = str(Path(spec.presentation_path).resolve())
                if presentation.manuscript_body_id != manuscript.body_id:
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} presentation body does not match manuscript"
                    )
                if reproducibility and (
                    presentation.reproducibility_package_id != reproducibility.package_id
                ):
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} presentation repro package does not match"
                    )
            else:
                missing.append("presentation")
            if spec.delivery_path:
                delivery = _read(spec.delivery_path, ArticleDeliveryPackage)
                asset_paths["delivery"] = str(Path(spec.delivery_path).resolve())
                if delivery.manuscript_body_id != manuscript.body_id:
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} delivery body does not match manuscript"
                    )
                if presentation and delivery.presentation_package_id != presentation.package_id:
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} delivery presentation does not match"
                    )
                if delivery.blockers or delivery.errors:
                    chapter_warnings.append("delivery carries blockers or errors")
            else:
                missing.append("delivery")
            if spec.global_audit_path:
                audit = _read(spec.global_audit_path, GlobalQualityAuditReport)
                asset_paths["global_audit"] = str(Path(spec.global_audit_path).resolve())
                if audit.article_id != manuscript.package_id:
                    raise ValueError(
                        f"chapter {spec.chapter_id!r} audit article_id does not match manuscript package"
                    )
                if audit.status == "blocked":
                    chapter_warnings.append("global audit is blocked")
            else:
                missing.append("global_audit")
            status: Literal["complete", "partial", "invalid"] = (
                "complete" if not missing and not chapter_warnings else "partial"
            )
            entries.append(
                ChapterRegistryEntry(
                    chapter_id=spec.chapter_id,
                    status=status,
                    manuscript_package_id=manuscript.package_id,
                    manuscript_body_id=manuscript.body_id,
                    plan_id=manuscript.plan_id,
                    ledger_id=manuscript.ledger_id,
                    architecture_id=manuscript.architecture_id,
                    review_id=manuscript.review_id,
                    story_id=manuscript.story_id,
                    section_ids=section_ids,
                    paragraph_count=len(paragraph_ids),
                    asset_paths=asset_paths,
                    missing_asset_kinds=missing,
                    warnings=chapter_warnings,
                )
            )
        except Exception as exc:
            errors.append(f"chapter {spec.chapter_id!r}: {exc}")
    complete_count = sum(item.status == "complete" for item in entries)
    missing_count = max(0, expected_chapter_count - len(entries))
    if missing_count:
        warnings.append(
            f"{missing_count} of {expected_chapter_count} expected chapter packages are not registered"
        )
    if errors:
        status = "invalid"
    elif len(entries) == expected_chapter_count and complete_count == len(entries):
        status = "complete"
    else:
        status = "partial"
    payload = {
        "expected_chapter_count": expected_chapter_count,
        "entries": [item.model_dump(mode="json") for item in entries],
        "errors": errors,
    }
    return ArticleChapterRegistry(
        registry_id="chapter-registry-" + _digest(payload),
        status=status,
        expected_chapter_count=expected_chapter_count,
        registered_chapter_count=len(entries),
        complete_chapter_count=complete_count,
        missing_chapter_count=missing_count,
        shared_plan_id=shared.get("plan_id", ""),
        shared_ledger_id=shared.get("ledger_id", ""),
        shared_architecture_id=shared.get("architecture_id", ""),
        shared_story_id=shared.get("story_id", ""),
        entries=entries,
        warnings=warnings,
        validation_errors=errors,
    )


def require_complete_chapter_registry(
    registry: ArticleChapterRegistry | Mapping[str, Any],
) -> ArticleChapterRegistry:
    model = (
        registry
        if isinstance(registry, ArticleChapterRegistry)
        else ArticleChapterRegistry.model_validate(registry)
    )
    if model.status != "complete":
        raise ValueError(
            "Article chapter registry is not complete: "
            f"registered={model.registered_chapter_count}, "
            f"complete={model.complete_chapter_count}, "
            f"missing={model.missing_chapter_count}"
        )
    return model


__all__ = [
    "ArticleChapterRegistry",
    "ChapterAssetSpec",
    "ChapterRegistryEntry",
    "build_chapter_registry",
    "require_complete_chapter_registry",
]
