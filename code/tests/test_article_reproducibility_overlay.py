from __future__ import annotations

import json
from pathlib import Path

import pytest

from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_reproducibility import ArticleReproducibilityPackage
from optomind_optics.harness.article_reproducibility_overlay import (
    overlay_reproducibility_manuscript,
)


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"
REPRO = REAL / "article_reproducibility_063_054_handoff_runs_root" / "ARTICLE_REPRODUCIBILITY_PACKAGE.json"
BASE = REAL / "article_global_revision_execution_096_pbs_correct_lineage_smoke" / "05-manuscript.json"
DISPLAY = REAL / "article_display_precision_fix_109_smoke_best_candidate_synced" / "ARTICLE_MANUSCRIPT_PACKAGE.json"


def test_overlay_rejects_mismatched_bindings():
    if not all(path.is_file() for path in (REPRO, BASE, DISPLAY)):
        pytest.skip("overlay assets are not present")
    repro = ArticleReproducibilityPackage.model_validate(json.loads(REPRO.read_text(encoding="utf-8")))
    base = ArticleManuscriptPackage.model_validate(json.loads(BASE.read_text(encoding="utf-8")))
    display = base.model_copy(update={"source_map": [base.source_map[0].model_copy(update={"claim_ids": ["forged"]})] + list(base.source_map[1:])})
    with pytest.raises(ValueError, match="lineage mismatch"):
        overlay_reproducibility_manuscript(repro, base, display)
