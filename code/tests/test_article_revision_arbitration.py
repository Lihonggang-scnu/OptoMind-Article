from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.article_revision_arbitration import arbitrate_article_revision


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
BASE = REAL / "article_review_replay_054_advice_router" / "04-review.json"
SMOKE = REAL / "article_global_revision_execution_096_pbs_correct_lineage_smoke" / "04-review.json"
FULL = REAL / "article_global_revision_execution_098_pbs_correct_lineage_full" / "04-review.json"


def _load(path: Path) -> ArticleReviewResult:
    return ArticleReviewResult.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_arbitration_accepts_smoke_candidate_and_rejects_worse_full_candidate():
    if not all(path.is_file() for path in (BASE, SMOKE, FULL)):
        pytest.skip("real revision review assets are not present")
    smoke = arbitrate_article_revision(_load(BASE), _load(SMOKE))
    worse = arbitrate_article_revision(_load(BASE), _load(FULL))
    assert smoke.candidate_accepted is True
    assert smoke.selected_review_id == smoke.candidate_review_id
    assert worse.candidate_accepted is False
    assert worse.selected_review_id == worse.baseline_review_id
    assert worse.candidate_score.scientific_finding_count > worse.baseline_score.scientific_finding_count


def test_arbitration_rejects_mixed_lineage():
    if not BASE.is_file() or not SMOKE.is_file():
        pytest.skip("real revision review assets are not present")
    base = _load(BASE)
    candidate = _load(SMOKE).model_copy(update={"plan_id": "mixed-plan"})
    with pytest.raises(ValueError, match="lineage mismatch"):
        arbitrate_article_revision(base, candidate)
