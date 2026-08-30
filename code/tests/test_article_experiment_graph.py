from __future__ import annotations

import json
import sqlite3

import pytest

from optomind_optics.harness.article_contracts import (
    ArticleDecision,
    ArticleNodePayload,
    ArticleStage,
    CoverageStatus,
    HypothesisStatus,
    ObservationCard,
)
from optomind_optics.harness.contracts import ActionProposal, ActionType, ExperimentStatus
from optomind_optics.harness.experiment_graph import ExperimentGraph


def test_legacy_graph_api_and_node_shape_unchanged(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "graph.sqlite", "run_legacy")
    node = graph.create_node(
        "task_hash",
        ActionProposal(action_type=ActionType.run_solver, parameters={"a": 1}),
    )
    graph.set_status(node, ExperimentStatus.running)
    graph.set_status(node, ExperimentStatus.physically_valid, certificate_id="cert-1")
    payload = graph.node(node)
    assert set(payload.keys()) == {
        "node_id",
        "run_id",
        "task_hash",
        "action",
        "created_at",
        "parent_ids",
        "status",
        "history",
    }
    assert payload["status"] == "physically_valid"
    assert payload["action"] == {
        "action_type": "run_solver",
        "parameters": {"a": 1},
        "rationale": "",
        "proposed_by": "deterministic_policy",
    }
    statuses = [
        item["payload"]["status"]
        for item in payload["history"]
        if item["event_type"] == "status"
    ]
    assert statuses == ["proposed", "running", "physically_valid"]
    with pytest.raises(KeyError):
        graph.node("missing-node")


def test_legacy_record_event_still_works(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_events")
    node = graph.create_node("h", ActionProposal(action_type=ActionType.stop))
    graph.record_event(node, "diagnosis", {"code": "BUDGET_EXHAUSTED"})
    history = graph.node(node)["history"]
    assert history[-1]["event_type"] == "diagnosis"
    assert history[-1]["payload"] == {"code": "BUDGET_EXHAUSTED"}


def test_export_preserves_existing_keys_and_adds_article_view(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_export")
    graph.create_node("h", ActionProposal(action_type=ActionType.run_solver))
    exported = graph.export()
    assert exported["schema_version"] == "optical-experiment-graph.v1"
    assert exported["run_id"] == "run_export"
    assert len(exported["nodes"]) == 1
    assert exported["article_schema_version"] == "optical-experiment-graph.article.v1"
    assert exported["article_nodes"] == []


def test_article_node_lineage_parent_child_and_cross_kind(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_lineage")
    parent = graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.baseline_experiments),
        node_id="article_parent",
    )
    child = graph.create_article_node(
        ArticleNodePayload(
            stage=ArticleStage.controlled_improvement,
            hypothesis_ids=["h1"],
            atomic_change={"variable": "thickness_layer_3", "delta_nm": 2.0},
            expected_discriminator={"metric": "R_mean", "direction": "lower"},
            budget_lease_id="lease-1",
            stop_decision=ArticleDecision.continue_run,
        ),
        parent_ids=[parent],
        node_id="article_child",
    )
    tmm_node = graph.create_node(
        "tmm_hash",
        ActionProposal(action_type=ActionType.run_optimizer),
        parent_ids=[parent],
        node_id="tmm_child",
    )
    parent_view = graph.article_node(parent)
    child_view = graph.article_node(child)
    assert parent_view["child_ids"] == ["article_child", "tmm_child"]
    assert child_view["parent_ids"] == ["article_parent"]
    assert child_view["payload"]["stage"] == "controlled_improvement"
    assert child_view["payload"]["budget_lease_id"] == "lease-1"
    assert child_view["payload"]["stop_decision"] == "continue_run"
    assert set(graph.frontier()[0].keys()) == {
        "node_id",
        "run_id",
        "task_hash",
        "action",
        "created_at",
        "parent_ids",
        "status",
        "history",
    }
    assert [item["node_id"] for item in graph.frontier()] == ["tmm_child"]
    assert [item["node_id"] for item in graph.article_frontier()] == ["article_child"]


def test_article_events_append_only_and_replay_latest(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_events2")
    node = graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.baseline_experiments),
        node_id="a1",
    )
    graph.set_article_stage(node, ArticleStage.controlled_improvement, reason="explore")
    graph.set_article_stage(node, "claim_ledger", reason="improvement done")
    graph.set_article_decision(node, ArticleDecision.continue_run, reason="progress")
    graph.record_hypothesis_update(
        node,
        "h1",
        from_status=HypothesisStatus.under_test,
        to_status="confirmed",
        reason="discriminator matched",
    )
    graph.record_hypothesis_update(
        node, "h1", "confirmed", HypothesisStatus.retired, reason="superseded"
    )
    graph.record_observation(
        node,
        ObservationCard(
            observation_id="obs-1",
            experiment_id="exp-1",
            status=ExperimentStatus.physically_valid,
            artifact_ids=["SIMULATION_RESULT.json"],
            summary="Discriminator confirmed.",
        ),
    )
    graph.record_coverage(
        node, "route_04", CoverageStatus.not_run, reason="budget exhausted"
    )
    graph.record_charter(node, "charter-1", ArticleStage.charter_locked, reason="locked")

    view = graph.article_node(node)
    assert view["status"] == "proposed"
    assert view["stage"] == "claim_ledger"
    assert view["decision"]["decision"] == "continue_run"
    assert view["decision"]["reason"] == "progress"
    assert [item["payload"]["coverage_status"] for item in view["history"] if item["event_type"] == "article.coverage"] == ["not_run"]
    assert view["payload"]["stage"] == "baseline_experiments"
    assert len(view["hypothesis_updates"]) == 2
    assert view["hypothesis_updates"][0]["to_status"] == "confirmed"
    assert view["hypothesis_updates"][1]["to_status"] == "retired"

    event_ids = [item["event_id"] for item in view["history"]]
    assert event_ids == sorted(event_ids)
    assert len(event_ids) == len(set(event_ids))
    event_types = [item["event_type"] for item in view["history"]]
    assert event_types[0] == "status"
    assert event_types[1] == "article.stage"
    assert event_types[-1] == "article.charter"
    assert all(
        item["payload"].get("schema_version") == "article-event.v1"
        for item in view["history"]
        if item["event_type"].startswith("article.")
    )


def test_article_event_rejections_are_clear(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_reject")
    article_node = graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.hypotheses_formed), node_id="a1"
    )
    tmm_node = graph.create_node(
        "h", ActionProposal(action_type=ActionType.run_solver), node_id="t1"
    )
    with pytest.raises(ValueError, match="Unknown article event type"):
        graph.record_article_event(article_node, "article.bogus", {"schema_version": "article-event.v1"})
    with pytest.raises(ValueError, match="schema_version"):
        graph.record_article_event(
            article_node,
            "article.stage",
            {"schema_version": "article-event.v9", "stage": "claim_ledger"},
        )
    with pytest.raises(ValueError, match="hypothesis_id"):
        graph.record_article_event(
            article_node,
            "article.hypothesis_update",
            {"schema_version": "article-event.v1", "to_status": "confirmed"},
        )
    with pytest.raises(KeyError, match="Unknown node_id"):
        graph.record_article_event(
            "missing",
            "article.stage",
            {"schema_version": "article-event.v1", "stage": "claim_ledger"},
        )
    with pytest.raises(ValueError, match="TMM node"):
        graph.record_article_event(
            tmm_node,
            "article.stage",
            {"schema_version": "article-event.v1", "stage": "claim_ledger"},
        )
    with pytest.raises(ValueError, match="not an article node"):
        graph.article_node(tmm_node)
    with pytest.raises(KeyError):
        graph.article_node("missing-article")

    other_run = ExperimentGraph(tmp_path / "g.sqlite", "run_other")
    with pytest.raises(KeyError, match="Unknown node_id"):
        other_run.record_article_event(
            article_node,
            "article.stage",
            {"schema_version": "article-event.v1", "stage": "claim_ledger"},
        )


def test_export_json_round_trip_keeps_article_view(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_roundtrip")
    graph.create_node("h", ActionProposal(action_type=ActionType.run_solver), node_id="t1")
    graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.figure_first_planning),
        node_id="a1",
    )
    exported = graph.export()
    restored = json.loads(json.dumps(exported, sort_keys=True))
    assert restored["schema_version"] == "optical-experiment-graph.v1"
    assert restored["article_schema_version"] == "optical-experiment-graph.article.v1"
    assert [item["node_id"] for item in restored["nodes"]] == ["t1"]
    assert [item["node_id"] for item in restored["article_nodes"]] == ["a1"]
    assert restored["article_nodes"][0]["payload"]["schema_version"] == "article-node.v1"
    assert restored["article_nodes"][0]["payload"]["stage"] == "figure_first_planning"
    assert restored["article_nodes"][0]["status"] == "proposed"


def test_create_article_node_accepts_dict_payload(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_dict")
    node = graph.create_article_node(
        {
            "stage": "hypotheses_formed",
            "hypothesis_ids": ["h1", "h2"],
            "summary": "two hypotheses",
        }
    )
    view = graph.article_node(node)
    assert view["payload"]["stage"] == "hypotheses_formed"
    assert view["payload"]["hypothesis_ids"] == ["h1", "h2"]
    with pytest.raises(ValueError):
        graph.create_article_node({"stage": "bogus_stage"})


def test_legacy_database_migrates_additively_and_stays_readable(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        CREATE TABLE nodes (
            node_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            task_hash TEXT NOT NULL,
            action_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE edges (
            run_id TEXT NOT NULL,
            parent_id TEXT NOT NULL,
            child_id TEXT NOT NULL,
            PRIMARY KEY(parent_id, child_id),
            FOREIGN KEY(parent_id) REFERENCES nodes(node_id),
            FOREIGN KEY(child_id) REFERENCES nodes(node_id)
        );
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(node_id) REFERENCES nodes(node_id)
        );
        """
    )
    connection.execute(
        "INSERT INTO nodes(node_id,run_id,task_hash,action_json,created_at) "
        "VALUES('old1','legacy_run','h1','{}',1.0)"
    )
    connection.execute(
        "INSERT INTO events(run_id,node_id,event_type,payload_json,created_at) "
        "VALUES('legacy_run','old1','status','{\"status\": \"proposed\"}',1.0)"
    )
    connection.commit()
    connection.close()

    graph = ExperimentGraph(path, "legacy_run")
    old = graph.node("old1")
    assert old["status"] == "proposed"
    assert old["task_hash"] == "h1"
    article = graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.literature_integrated),
        node_id="new_article",
    )
    view = graph.article_node(article)
    assert view["payload"]["stage"] == "literature_integrated"
    exported = graph.export()
    assert [item["node_id"] for item in exported["nodes"]] == ["old1"]
    assert [item["node_id"] for item in exported["article_nodes"]] == ["new_article"]


def test_tmm_frontier_stays_leaf_when_only_article_child_exists(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_frontier_article_child")
    tmm_parent = graph.create_node(
        "tmm_hash",
        ActionProposal(action_type=ActionType.run_solver),
        node_id="tmm_parent",
    )
    graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.controlled_improvement),
        parent_ids=[tmm_parent],
        node_id="article_child",
    )
    frontier_ids = [item["node_id"] for item in graph.frontier()]
    assert frontier_ids == ["tmm_parent"]
    assert [item["node_id"] for item in graph.article_frontier()] == ["article_child"]


def test_tmm_frontier_still_excludes_tmm_nodes_with_tmm_children(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_frontier_tmm_child")
    parent = graph.create_node(
        "h", ActionProposal(action_type=ActionType.generate_baseline), node_id="tmm_a"
    )
    graph.create_node(
        "h", ActionProposal(action_type=ActionType.run_solver), parent_ids=[parent], node_id="tmm_b"
    )
    assert [item["node_id"] for item in graph.frontier()] == ["tmm_b"]


def test_create_node_rejects_missing_parent_without_partial_state(tmp_path) -> None:
    path = tmp_path / "g.sqlite"
    graph = ExperimentGraph(path, "run_parent_check")
    with pytest.raises(KeyError, match="Unknown parent node_id"):
        graph.create_node(
            "h",
            ActionProposal(action_type=ActionType.run_solver),
            parent_ids=["ghost_parent"],
            node_id="should_not_exist",
        )
    with pytest.raises(KeyError):
        graph.node("should_not_exist")
    assert graph.export()["nodes"] == []
    assert graph.export()["article_nodes"] == []
    connection = sqlite3.connect(str(path))
    try:
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        connection.close()


def test_create_node_rejects_mixed_valid_and_missing_parents_atomically(tmp_path) -> None:
    path = tmp_path / "g.sqlite"
    graph = ExperimentGraph(path, "run_mixed_parents")
    valid = graph.create_node(
        "h", ActionProposal(action_type=ActionType.generate_baseline), node_id="valid_parent"
    )
    with pytest.raises(KeyError, match="Unknown parent node_id"):
        graph.create_node(
            "h",
            ActionProposal(action_type=ActionType.run_solver),
            parent_ids=[valid, "ghost_parent"],
            node_id="atomic_reject",
        )
    with pytest.raises(KeyError):
        graph.node("atomic_reject")
    exported = graph.export()
    assert [item["node_id"] for item in exported["nodes"]] == ["valid_parent"]


def test_create_node_rejects_cross_run_parent(tmp_path) -> None:
    path = tmp_path / "g.sqlite"
    graph_a = ExperimentGraph(path, "run_a")
    graph_b = ExperimentGraph(path, "run_b")
    parent_in_b = graph_b.create_node(
        "h", ActionProposal(action_type=ActionType.run_solver), node_id="parent_b"
    )
    with pytest.raises(KeyError, match="Unknown parent node_id"):
        graph_a.create_node(
            "h",
            ActionProposal(action_type=ActionType.run_solver),
            parent_ids=[parent_in_b],
            node_id="cross_run_reject",
        )
    with pytest.raises(KeyError):
        graph_a.node("cross_run_reject")
    assert [item["node_id"] for item in graph_a.export()["nodes"]] == []
    assert [item["node_id"] for item in graph_b.export()["nodes"]] == ["parent_b"]


def test_create_article_node_rejects_missing_or_cross_run_parents_without_partial_state(
    tmp_path,
) -> None:
    path = tmp_path / "g.sqlite"
    graph_a = ExperimentGraph(path, "run_a")
    graph_b = ExperimentGraph(path, "run_b")
    parent_in_b = graph_b.create_node(
        "h", ActionProposal(action_type=ActionType.generate_baseline), node_id="parent_b"
    )
    with pytest.raises(KeyError, match="Unknown parent node_id"):
        graph_a.create_article_node(
            ArticleNodePayload(stage=ArticleStage.baseline_experiments),
            parent_ids=["ghost_parent"],
            node_id="article_ghost",
        )
    with pytest.raises(KeyError, match="Unknown parent node_id"):
        graph_a.create_article_node(
            ArticleNodePayload(stage=ArticleStage.baseline_experiments),
            parent_ids=[parent_in_b],
            node_id="article_cross_run",
        )
    with pytest.raises(KeyError):
        graph_a.article_node("article_ghost")
    with pytest.raises(KeyError):
        graph_a.article_node("article_cross_run")
    assert graph_a.export()["nodes"] == []
    assert graph_a.export()["article_nodes"] == []
    connection = sqlite3.connect(str(path))
    try:
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        connection.close()


def test_create_article_node_with_valid_parent_still_works_after_validation(tmp_path) -> None:
    graph = ExperimentGraph(tmp_path / "g.sqlite", "run_valid_parent")
    tmm_parent = graph.create_node(
        "h", ActionProposal(action_type=ActionType.run_solver), node_id="tmm_parent"
    )
    article = graph.create_article_node(
        ArticleNodePayload(stage=ArticleStage.hypotheses_formed),
        parent_ids=[tmm_parent],
        node_id="article_valid",
    )
    view = graph.article_node(article)
    assert view["parent_ids"] == ["tmm_parent"]
    assert graph.frontier()[0]["node_id"] == "tmm_parent"
