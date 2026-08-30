from __future__ import annotations

from pathlib import Path

from optomind_optics.harness.article_stage17_acceptance import audit_stage17_acceptance


ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "stage17_real_integration"


def _artifacts():
    return {
        "pipeline_result": str(REAL / "selective_emitter_006" / "pipeline" / "FINAL_PIPELINE_RESULT.json"),
        "synthesis": str(REAL / "article_continuation_040_structured_attainment_tolerant_full" / "01-result_synthesis.json"),
        "architecture": str(REAL / "article_upper_replay_046_section_subject_binding" / "02-architecture.json"),
        "writing": str(REAL / "article_global_revision_execution_096_pbs_correct_lineage_smoke" / "03-writing-revalidated.json"),
        "review": str(REAL / "article_global_revision_execution_096_pbs_correct_lineage_smoke" / "04-review.json"),
        "manuscript": str(REAL / "article_display_precision_fix_109_smoke_best_candidate_synced" / "ARTICLE_MANUSCRIPT_PACKAGE.json"),
        "reproducibility": str(REAL / "article_reproducibility_112_smoke_best_candidate_overlay" / "ARTICLE_REPRODUCIBILITY_PACKAGE.json"),
        "presentation": str(REAL / "article_presentation_131_same_lineage_visuals" / "ARTICLE_PRESENTATION_PACKAGE.json"),
        "delivery": str(REAL / "article_delivery_132_same_lineage_visuals" / "ARTICLE_DELIVERY_PACKAGE.json"),
        "global_audit": str(REAL / "article_global_quality_audit_107_smoke_best_candidate" / "GLOBAL_QUALITY_AUDIT.json"),
        "chapter_registry": str(REAL / "article_chapter_registry_135" / "ARTICLE_CHAPTER_REGISTRY.json"),
        "full_structure": str(REAL / "article_full_structure_134_assembled_input_smoke" / "FULL_ARTICLE_STRUCTURE.json"),
        "gap_compilation": str(REAL / "article_structure_gap_compilation_136_smoke" / "STRUCTURE_GAP_TASK_COMPILATION.json"),
        "gap_query_plan": str(REAL / "article_gap_query_planning_137_smoke" / "GAP_QUERY_PLANNING.json"),
        "gap_queue": str(REAL / "article_gap_task_queue_140_smoke" / "GAP_TASK_QUEUE.json"),
        "article_memory_manifest": str(ROOT / "article_memory" / "ARTICLE_MEMORY_MANIFEST.json"),
        "final_pdf": str(REAL / "article_delivery_132_same_lineage_visuals" / "latex" / "main.pdf"),
    }


def test_current_stage17_report_is_partial_and_names_missing_chapters():
    result = audit_stage17_acceptance(_artifacts())
    assert result.status == "partial"
    assert any("7 chapter packages are missing" in blocker for blocker in result.blockers)
    registry_row = next(row for row in result.rows if row.stage == "14")
    assert registry_row.status == "partial"
    assert any(row.stage == "17" and row.status == "passed" for row in result.rows)


def test_missing_pdf_fails_stage17():
    artifacts = _artifacts()
    artifacts["final_pdf"] = str(REAL / "missing.pdf")
    result = audit_stage17_acceptance(artifacts)
    assert result.status == "failed"
    assert any("Stage 17 PDF" in blocker for blocker in result.blockers)
