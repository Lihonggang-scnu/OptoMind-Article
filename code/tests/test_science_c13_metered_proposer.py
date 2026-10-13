"""Actual Review client + ledgers, fake HTTP only; all real sockets forbidden."""
from dataclasses import asdict
import json
import socket
from types import SimpleNamespace

import pytest
from optomind_research.runtime.upgrade3.module4 import runtime
from optomind_research.runtime.upgrade3.dual_budget import DualBudgetLedger
from optomind_optics.harness.science.metered_proposer import QwenDirectMeteredAdapter
from optomind_optics.harness.science.joint_proposer import QwenJointProposer, ProposalResponseError
from optomind_optics.harness.science.open_research_loop import initial_planar_model


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    project = runtime.GlobalBudgetLedger(limit_cny=2., path=tmp_path / "project.sqlite")
    account = runtime.GlobalBudgetLedger(limit_cny=3., path=tmp_path / "account.sqlite")
    dual = DualBudgetLedger(project, account, tmp_path / "pairs")
    client = runtime.QwenDirectClient(
        budget_ledger=dual, key_file=tmp_path / "never-read.key", key_index=1,
        max_retries=0, max_keys=1, max_output_tokens=512,
        thinking=False, prompt_token_counter=lambda wire, messages: 100,
    )
    # Inject only the credential resolver and HTTP boundary, not the client or
    # accounting implementation. No credentials are read or written in tests.
    monkeypatch.setattr(client, "_keys", lambda: ["fake-offline-key"])
    return SimpleNamespace(client=client, dual=dual, project=project, account=account, calls=[])


def fake_transport(monkeypatch, setup, content='{"models":[]}', usage=None, failure=None):
    if usage is None:
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                 "completion_tokens_details": {"reasoning_tokens": 0}}
    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({"model": setup.client.model, "id": "offline-response",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": usage}).encode()
    def opened(request, timeout):
        setup.calls.append(json.loads(request.data))
        for ledger in (setup.project, setup.account):
            rows = ledger.as_dict()["reservations"]
            assert len(rows) == 1 and rows[0]["status"] == "reserved"
            assert rows[0]["amount_cny"] > 0
        records = list(setup.dual.mapping_dir.glob("dual-*.json"))
        assert len(records) == 1
        assert json.loads(records[0].read_text())["state"] == "ready_for_http"
        if failure is not None:
            raise failure
        return Response()
    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=opened))
    return usage


def proposer(setup, *, allow_live=True):
    return QwenJointProposer(client=QwenDirectMeteredAdapter(client=setup.client),
                            protocol_id="offline-transport-test/1", allow_live=allow_live, max_tokens=128)


def context():
    return {"parent_model_ir": asdict(initial_planar_model()), "source_refs": [],
            "max_candidates": 2, "program_id": "adapter-test"}


def test_real_client_reserves_both_before_http_and_settles_usage(setup, monkeypatch):
    usage = fake_transport(monkeypatch, setup)
    batch = proposer(setup).propose(context())
    assert batch.usage == usage
    assert not batch.models and batch.program is None
    assert setup.calls[0]["max_completion_tokens"] == 128
    assert len(setup.calls) == 1
    expected = runtime.estimated_cost_cny(usage, model=setup.client.model)
    for ledger in (setup.project, setup.account):
        state = ledger.as_dict()
        assert state["reserved_cny"] == 0 and state["actual_cny"] == expected > 0
        assert state["reservations"][0]["status"] == "settled"
        assert state["reservations"][0]["telemetry"]["usage"] == usage


@pytest.mark.parametrize("content", ["not JSON", '{"models":[],"approved":true}'])
def test_malformed_proposal_is_still_charged(setup, monkeypatch, content):
    usage = fake_transport(monkeypatch, setup, content)
    with pytest.raises(ProposalResponseError) as exc:
        proposer(setup).propose(context())
    assert exc.value.usage == usage
    assert len(setup.calls) == 1
    for ledger in (setup.project, setup.account):
        assert ledger.as_dict()["actual_cny"] > 0
        assert ledger.as_dict()["reservations"][0]["status"] == "settled"


@pytest.mark.parametrize("scope", ["project", "account"])
def test_budget_refusal_never_opens_transport(setup, monkeypatch, scope):
    fake_transport(monkeypatch, setup)
    ledger = getattr(setup, scope)
    ledger.reserve(ledger.limit_cny, "already-consumed")
    with pytest.raises(runtime.QwenTransportError):
        proposer(setup).propose(context())
    assert not setup.calls
    assert ledger.as_dict()["reserved_cny"] == ledger.limit_cny
    if scope == "account":
        assert setup.project.as_dict()["reserved_cny"] == 0
        assert setup.project.as_dict()["actual_cny"] == 0


@pytest.mark.parametrize("failure", [TimeoutError("unknown outcome"), KeyboardInterrupt()])
def test_interruption_retains_holds_without_retry(setup, monkeypatch, failure):
    fake_transport(monkeypatch, setup, failure=failure)
    with pytest.raises((runtime.QwenTransportError, KeyboardInterrupt)):
        proposer(setup).propose(context())
    assert len(setup.calls) == 1
    for ledger in (setup.project, setup.account):
        state = ledger.as_dict()
        assert state["reserved_cny"] > 0 and state["actual_cny"] == 0
        assert state["reservations"][0]["status"] in {"reserved", "uncertain"}
    setup.dual.recover_settlements()
    assert all(ledger.as_dict()["reserved_cny"] > 0 for ledger in (setup.project, setup.account))
    assert len(setup.calls) == 1


def test_missing_usage_keeps_unknown_hold_without_fabricated_tokens(setup, monkeypatch):
    fake_transport(monkeypatch, setup, usage={})
    assert proposer(setup).propose(context()).usage == {}
    for ledger in (setup.project, setup.account):
        assert ledger.as_dict()["reservations"][0]["status"] == "uncertain"
        assert ledger.as_dict()["reserved_cny"] > 0


def test_preview_and_no_live_opt_in_never_dispatch(setup, monkeypatch):
    fake_transport(monkeypatch, setup)
    instance = proposer(setup, allow_live=False)
    assert instance.preview(context())
    with pytest.raises(PermissionError): instance.propose(context())
    assert not setup.calls and not setup.project.as_dict()["reservations"]


@pytest.mark.parametrize("field,value", [("max_retries", 1), ("max_keys", 2),
    ("key_file", None), ("key_index", None), ("budget_ledger", None)])
def test_unsafe_client_mutation_is_rejected_before_dispatch(setup, monkeypatch, field, value):
    fake_transport(monkeypatch, setup)
    adapter = QwenDirectMeteredAdapter(client=setup.client)
    setattr(setup.client, field, value)
    with pytest.raises((ValueError, TypeError)):
        adapter.call_metered([], max_tokens=128)
    assert not setup.calls


def test_nonreal_client_is_rejected():
    with pytest.raises(TypeError, match="QwenDirectClient"):
        QwenDirectMeteredAdapter(client=SimpleNamespace())


@pytest.mark.parametrize("tokens", [True, 0, 32, 513, 16001, 128.5])
def test_no_silent_token_cap_expansion(setup, tokens):
    with pytest.raises(ValueError):
        QwenDirectMeteredAdapter(client=setup.client).call_metered([], max_tokens=tokens)


def test_mock_request_is_rejected(setup):
    with pytest.raises(ValueError, match="no mock fallback"):
        QwenDirectMeteredAdapter(client=setup.client).call_metered([], max_tokens=128, force_mock=True)


def test_single_or_in_memory_or_uncapped_ledger_is_rejected(setup, tmp_path):
    setup.client.budget_ledger = setup.project
    with pytest.raises(TypeError, match="DualBudgetLedger"):
        QwenDirectMeteredAdapter(client=setup.client)
    setup.client.budget_ledger = setup.dual
    setup.dual.total = runtime.GlobalBudgetLedger(limit_cny=3.)
    with pytest.raises(ValueError, match="persistent"):
        QwenDirectMeteredAdapter(client=setup.client)
    setup.dual.total = runtime.GlobalBudgetLedger(path=tmp_path / "uncapped.sqlite")
    with pytest.raises(ValueError, match="caps"):
        QwenDirectMeteredAdapter(client=setup.client)


def test_aliased_ledger_after_construction_is_rejected(setup):
    adapter = QwenDirectMeteredAdapter(client=setup.client)
    setup.dual.total = setup.project
    with pytest.raises(ValueError, match="distinct"):
        adapter.call_metered([], max_tokens=128)


def test_missing_ledger_is_not_recreated(setup):
    adapter = QwenDirectMeteredAdapter(client=setup.client)
    setup.account.path.unlink()
    with pytest.raises(ValueError, match="no budget reset"):
        adapter.call_metered([], max_tokens=128)
    assert not setup.account.path.exists()


def test_construct_and_preview_do_not_resolve_credentials(setup, monkeypatch):
    def forbidden(): raise AssertionError("credential lookup")
    monkeypatch.setattr(setup.client, "_keys", forbidden)
    instance = proposer(setup)
    assert instance.preview(context())
    assert not setup.project.as_dict()["reservations"]


def test_existing_conservative_counter_fallback_is_used(setup, monkeypatch):
    setup.client.prompt_token_counter = None
    fake_transport(monkeypatch, setup)
    proposer(setup).propose(context())
    assert len(setup.calls) == 1
