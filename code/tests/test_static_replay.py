from __future__ import annotations

from pathlib import Path

import pytest

from optomind_optics.harness.static_replay import ReplayCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "tmm_research_harness"


@pytest.fixture(scope="module")
def replay_catalog() -> ReplayCatalog:
    return ReplayCatalog(OUTPUT_ROOT)


def test_catalog_projects_all_six_formal_runs(replay_catalog: ReplayCatalog) -> None:
    catalog = replay_catalog.catalog()

    assert catalog["mode"] == "read_only_artifact_replay"
    assert catalog["totals"] == {
        "runs": 6,
        "routes": 24,
        "iterations": 126,
        "completed_executions": 105,
        "valid_candidates": 926,
        "forward_evaluations": 101_711,
    }
    assert [run["group"] for run in catalog["runs"]] == [1, 2, 3, 4, 5, 6]
    assert all(run["winner"]["score"] is not None for run in catalog["runs"])


def test_each_replay_keeps_routes_iterations_and_raw_evidence(
    replay_catalog: ReplayCatalog,
) -> None:
    for run_id in replay_catalog.discover_run_ids():
        replay = replay_catalog.get_run(run_id)

        assert replay["read_only"] is True
        assert replay["summary"]["question"]
        assert replay["scoring"]["locked"] is True
        assert replay["scoring"]["formula"]
        assert replay["routes"]
        assert replay["leaderboard"]
        assert replay["champion"]["score"] is not None
        assert len(replay["event_timeline"]) > 0
        assert replay["event_timeline"][0]["event_type"] == "request_received"
        assert replay["event_timeline"][-1]["event_type"] == "research_finished"
        assert sum(len(route["rounds"]) for route in replay["routes"]) == replay[
            "summary"
        ]["iteration_count"]
        assert all(route["route_kind"] for route in replay["routes"])

        linked_files = [
            file["path"]
            for stage in replay["evidence"]
            for file in stage["files"]
        ]
        assert "REQUEST.json" in linked_files
        assert "SCORING_STANDARD.json" in linked_files
        assert "SCORING_RANKING.json" in linked_files
        assert "FINAL_ANSWER.md" in linked_files
        for relative_path in linked_files:
            resolved = replay_catalog.resolve_artifact(run_id, relative_path)
            assert resolved.is_file()
            assert resolved.is_relative_to(OUTPUT_ROOT.resolve())


def test_artifact_endpoint_rejects_traversal_and_non_text_files(
    replay_catalog: ReplayCatalog,
) -> None:
    run_id = replay_catalog.discover_run_ids()[0]

    with pytest.raises(FileNotFoundError):
        replay_catalog.resolve_artifact(run_id, "../../README.md")
    with pytest.raises(FileNotFoundError):
        replay_catalog.resolve_artifact(run_id, "secret.exe")


def test_catalog_can_add_completed_local_runs_without_changing_formal_root(
    tmp_path: Path,
    replay_catalog: ReplayCatalog,
) -> None:
    local_run = tmp_path / "local-completed-run"
    local_run.mkdir()
    source_run = OUTPUT_ROOT / replay_catalog.discover_run_ids()[0]
    for filename in (
        "REQUEST.json",
        "SCORING_STANDARD.json",
        "ITERATION_HISTORY.json",
        "SCORING_RANKING.json",
    ):
        (local_run / filename).write_bytes((source_run / filename).read_bytes())

    catalog = ReplayCatalog(OUTPUT_ROOT, additional_roots=(tmp_path,))

    assert "local-completed-run" in catalog.discover_run_ids()
    assert catalog._run_dir("local-completed-run").is_relative_to(tmp_path.resolve())
