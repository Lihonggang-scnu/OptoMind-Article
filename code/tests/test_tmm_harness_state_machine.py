from __future__ import annotations

import json

import pytest

from optomind_optics.harness import HarnessStage, HarnessStateMachine, InvalidStageTransition


def _advance_to_searching(machine: HarnessStateMachine) -> None:
    machine.transition(HarnessStage.protocol_validated, "contract_ok")
    machine.transition(HarnessStage.capability_classified, "tmm_supported")
    machine.transition(HarnessStage.materials_resolved, "materials_resolved")
    machine.transition(HarnessStage.baseline_evaluated, "baseline_complete")
    machine.transition(HarnessStage.searching, "optimization_started")


def test_state_machine_records_tamper_evident_append_only_history(tmp_path) -> None:
    machine = HarnessStateMachine(tmp_path, "run_a", clock=lambda: 100.0)
    _advance_to_searching(machine)
    assert machine.stage == HarnessStage.searching
    events = machine.history
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert events[-1]["previous_event_hash"] == events[-2]["event_hash"]


def test_invalid_and_terminal_transitions_fail_closed(tmp_path) -> None:
    machine = HarnessStateMachine(tmp_path, "run_b")
    with pytest.raises(InvalidStageTransition):
        machine.transition(HarnessStage.searching, "skip_required_stages")
    machine.transition(HarnessStage.failed, "invalid_contract")
    with pytest.raises(InvalidStageTransition, match="Terminal"):
        machine.transition(HarnessStage.protocol_validated, "cannot_resume_terminal")


def test_diagnosis_can_retry_without_rewriting_history(tmp_path) -> None:
    machine = HarnessStateMachine(tmp_path, "run_c")
    _advance_to_searching(machine)
    machine.transition(HarnessStage.diagnosing, "optimizer_stagnation")
    machine.transition(HarnessStage.searching, "switch_optimizer")
    stages = [item["stage"] for item in machine.history]
    assert stages.count("searching") == 2
    assert "diagnosing" in stages


def test_resume_validates_hash_chain(tmp_path) -> None:
    machine = HarnessStateMachine(tmp_path, "run_d")
    machine.transition(HarnessStage.protocol_validated, "contract_ok")
    resumed = HarnessStateMachine(tmp_path, "run_d", resume=True)
    assert resumed.stage == HarnessStage.protocol_validated

    path = tmp_path / "STATE_HISTORY.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"][0]["reason"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        HarnessStateMachine(tmp_path, "run_d", resume=True)


def test_completed_portfolio_is_terminal(tmp_path) -> None:
    machine = HarnessStateMachine(tmp_path, "run_e")
    _advance_to_searching(machine)
    machine.transition(HarnessStage.candidate_verification, "candidates_ready")
    machine.transition(HarnessStage.portfolio_ranking, "physics_verified")
    machine.transition(HarnessStage.completed, "portfolio_written")
    assert machine.snapshot()["terminal"] is True


def test_diagnosis_is_available_during_material_resolution_and_can_recover(tmp_path) -> None:
    machine = HarnessStateMachine(tmp_path, "run_f")
    machine.transition(HarnessStage.protocol_validated, "contract_ok")
    machine.transition(HarnessStage.capability_classified, "tmm_supported")
    machine.transition(HarnessStage.materials_resolved, "first_resolution_attempt")
    machine.transition(HarnessStage.diagnosing, "material_range_error")
    machine.transition(HarnessStage.materials_resolved, "alternate_dataset_selected")
    assert machine.stage == HarnessStage.materials_resolved
