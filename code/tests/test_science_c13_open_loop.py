from dataclasses import asdict, replace
import json
import numpy as np
import pytest
from optomind_optics.harness.science.contracts import SelectedResearchResult, PredictionSpec, Observation, ModelCandidate
from optomind_optics.harness.science.controller import route_research_state
from optomind_optics.harness.science.open_research_cli import build_offline_loop, main
from optomind_optics.harness.science.open_research_loop import initial_planar_model, OpenResearchConfig
from optomind_optics.harness.science.model_search import propose_extensions
from optomind_optics.harness.science.model_compiler import compile_model


def close(resources):
    for resource in resources:resource.close()


@pytest.fixture(scope="module")
def optical_run(tmp_path_factory):
    output=tmp_path_factory.mktemp("c13-optical")
    loop,resources=build_offline_loop(output,{"family":"F3","world_seed":13})
    result=loop.run(output)
    yield output,loop,result
    close(resources)


def test_actual_optical_mismatch_changes_model_and_design_with_heldout(optical_run):
    output,loop,result=optical_run
    assert isinstance(result,SelectedResearchResult)
    assert result.candidate_roles["adoption_status"]=="proposed_design"
    assert result.candidate_roles["design_changed"] is True
    assert result.claim_refs and result.claim_strength=="design"
    assert all(loop.repository.load(ref).scope.get("strength")!="mechanism" for ref in result.claim_refs)
    selected=loop.repository.load(result.candidate_ref)
    assert any(node["operator"]=="cauchy_nk" for node in selected.model_ir["nodes"])
    trace=json.loads((output/"SCIENCE_OPEN_TRAJECTORY.json").read_text())
    assert trace["iterations"][0]["action"]=="bounded_structure_search"
    assert trace["iterations"][0]["adequacy"]["state"]=="model_family_inadequate"
    assert trace["validation"]["summary"]["state"]=="supported_within_frozen_noise_envelope"
    assert trace["iterations"][-1]["action"]=="stop"
    assert sum(v["cost"] for v in trace["cost_usage"].values())<=loop.config.budget
    validation=[r for r in trace["evidence_uses"] if r["purpose"]=="validate"]
    assert len(validation)==1
    assert not any(r["acquisition_hash"]==validation[0]["acquisition_hash"] and r["purpose"]!="validate" for r in trace["evidence_uses"])
    for ref in loop.repository.latest_snapshot().object_refs:
        obj=loop.repository.load(ref)
        if not isinstance(obj,Observation):continue
        predictions=[loop.repository.load(p) for p in result.prediction_refs]
        matching=[p for p in predictions if p.grid.get("session")==obj.binding["task_id"]]
        for p in matching:
            assert loop.repository.object_commit_sequence(p.ref())<loop.repository.acquisition_commit_sequence(obj.acquisition_id)


def test_completed_checkpoint_has_no_new_purchases_or_snapshot(optical_run):
    output,loop,result=optical_run
    sequence=loop.repository.latest_snapshot().committed_sequence
    usage=loop.runner.gates["R"].usage()
    assert loop.run(output).ref()==result.ref()
    assert loop.repository.latest_snapshot().committed_sequence==sequence
    assert loop.runner.gates["R"].usage()==usage


def test_cauchy_classical_extension_is_typed_nested_and_keeps_known_media():
    parent=initial_planar_model()
    proposal=propose_extensions(parent,mismatch="residual",operators=("cauchy",),max_depth=1)[0]
    assert proposal.origin=="typed_classical_beam"
    extended=compile_model(proposal.model_ir)
    baseline=compile_model(parent)
    values={p.name:p.initial for p in parent.parameters}
    extra={p.name:0. for p in proposal.model_ir.parameters if p.name not in values}
    grid=np.linspace(450,750,11)
    assert np.allclose(extended.predict({**values,**extra},grid),baseline.predict(values,grid),atol=1e-12)
    assert next(n for n in proposal.model_ir.nodes if n.node_id=="substrate").operator=="constant_nk"


@pytest.mark.parametrize("state,route",[("numerical_failure","refine_computation"),("noise_unvalidated","calibrate_noise"),
    ("parameter_fit_required","refit_parameters"),("model_family_inadequate","propose_structure"),
    ("nonidentifiable","identify_task_relevant_direction"),("uncalibrated","bounded_unknown_exploration")])
def test_failure_classes_do_not_collapse(state,route):assert route_research_state(state)==route


def test_group_overlap_rejected_before_side_effects(tmp_path):
    with pytest.raises(ValueError,match="distinct frozen groups"):
        build_offline_loop(tmp_path,{"config":{"session_groups":[[s,"same"] for s in ("training","selection-0","selection-1","validation","reference")]}})
    assert not (tmp_path/"provider.sqlite").exists()


def test_unknown_exploration_is_bounded_and_final_validation_reserved(tmp_path):
    loop,resources=build_offline_loop(tmp_path,{"family":"F3","config":{"unknown_exploration_limit":0,"budget":2.,"max_iterations":1}})
    try:
        result=loop.run(tmp_path)
        assert result.candidate_roles["adoption_status"]=="withheld_after_failed_validation"
        assert loop.runner.gates["R"].usage()["cost"]==2.
        trace=json.loads((tmp_path/"SCIENCE_OPEN_TRAJECTORY.json").read_text())
        assert trace["iterations"][0]["valuation"]["status"]!="zero"
        assert trace["iterations"][0]["action"]=="stop"
    finally:close(resources)


def test_crash_after_actual_purchase_recovers_receipt_without_duplicate(tmp_path):
    fired=[]
    def crash(stage):
        if stage=="after_provider_return" and not fired:
            fired.append(True);raise RuntimeError("simulated process loss")
    payload={"family":"F0","config":{"max_iterations":1,"unknown_exploration_limit":0}}
    loop,resources=build_offline_loop(tmp_path,payload,fault_hook=crash)
    with pytest.raises(RuntimeError,match="simulated process loss"):loop.run(tmp_path)
    assert resources[-1].db.execute("SELECT count(*) FROM receipts").fetchone()[0]==1
    close(resources)
    loop,resources=build_offline_loop(tmp_path,payload)
    try:
        result=loop.run(tmp_path)
        assert isinstance(result,SelectedResearchResult)
        rows=resources[-1].db.execute("SELECT key FROM receipts").fetchall()
        assert sum(":initial:" in key for key, in rows)==1
        assert len(rows)==loop.runner.gates["R"].usage()["calls"]
    finally:close(resources)


def test_cli_preview_never_creates_run_and_offline_is_required(tmp_path,capsys):
    assert main(["--offline","--preview","--output",str(tmp_path/"absent")])==0
    assert not (tmp_path/"absent").exists()
    assert json.loads(capsys.readouterr().out)["network_calls"]==0
    with pytest.raises(SystemExit):main(["--output",str(tmp_path)])


def test_actual_multilayer_byrnes_to_veritmm_runtime(tmp_path):
    loop,resources=build_offline_loop(tmp_path,{"fixture":"multilayer","family":"F3","world_seed":13})
    try:
        result=loop.run(tmp_path)
        assert result.candidate_roles["adoption_status"]=="proposed_design"
        candidate=loop.repository.load(result.candidate_ref)
        assert sum(n["operator"]=="layer" for n in candidate.model_ir["nodes"])==3
        assert any(n["operator"]=="cauchy_nk" for n in candidate.model_ir["nodes"])
        observations=[loop.repository.load(ref) for ref in loop.repository.latest_snapshot().object_refs if ref.object_id.startswith("observation:")]
        assert observations and all(o.binding["evaluator_version"]=="multilayer-byrnes/1" for o in observations)
    finally:close(resources)


def test_schema_replay_proposes_real_model_and_program_without_paid_calls(tmp_path):
    from optomind_optics.harness.science.program_search import adaptive_measurement_program
    parent=initial_planar_model()
    model=propose_extensions(parent,mismatch="preview",operators=("cauchy",),max_depth=1)[0].model_ir
    program=adaptive_measurement_program(program_id="offline-open-research:iteration:0",channel="R",reference_channel="R",
        coarse_grid=np.linspace(420.,860.,17)[::2].tolist(),fine_grid=np.linspace(420.,860.,17).tolist(),threshold=.01,
        sample_id="selection-0",reference_id="reference")
    proposal={"models":[{"model_ir":asdict(model),"edit":["replace variable constitutive node"],
        "expected_difference":"wavelength-dependent reflectance change","task_impact":"may change selected thickness"}],"program":asdict(program)}
    loop,resources=build_offline_loop(tmp_path,{"family":"F3","proposal_replay":[proposal]})
    try:
        result=loop.run(tmp_path)
        assert isinstance(result,SelectedResearchResult)
        record=loop._find("offline-open-research:iteration:0:provider")
        assert record.actual_messages[0]["role"]=="system"
        assert record.summary["usage"]["paid_calls"]==0
        proposals=loop._find("offline-open-research:iteration:0:proposals")
        assert any(p["origin"]=="schema_joint_proposer" for p in proposals.summary["proposals"])
    finally:close(resources)


def test_qwen_seam_requires_explicit_live_opt_in_and_preview_is_exact():
    from optomind_optics.harness.science.joint_proposer import QwenJointProposer,parse_joint_proposal
    class NeverCall:
        def call(self,*a,**k):raise AssertionError("paid client called")
    provider=QwenJointProposer(client=NeverCall(),protocol_id="test/1")
    context={"parent_model_ir":asdict(initial_planar_model()),"source_refs":[],"max_candidates":2,"program_id":"test"}
    messages=provider.preview(context)
    assert "operator_signatures" in json.loads(messages[1]["content"])
    with pytest.raises(PermissionError,match="not authorized"):provider.propose(context)
    with pytest.raises(ValueError,match="unapproved"):parse_joint_proposal({"models":[],"approved":True},context)


def test_article_stage_consumes_actual_open_research_policy(tmp_path):
    from test_article_pipeline import _happy_stack,_request
    from optomind_optics.harness.science.pipeline_adapter import OpenResearchPipelinePolicy
    loop,resources=build_offline_loop(tmp_path/"science-run",{"family":"F0","config":{"max_iterations":1,"unknown_exploration_limit":0}})
    pipeline,calls,request=_happy_stack(tmp_path)
    class Coordinator:pass
    coordinator=Coordinator();coordinator.science_repository=loop.repository
    coordinator.science_task_ref=loop.task.ref();coordinator.execution_gate=loop.runner.gates["R"]
    policy=OpenResearchPipelinePolicy(loop=loop,coordinator=coordinator)
    pipeline.science_policy=policy
    try:
        result=pipeline.run(_request(str(tmp_path/"article")))
        assert policy.selected_result is not None
        assert loop.repository.load(policy.selected_result.ref())==policy.selected_result
        assert not calls["execute"]
    finally:close(resources)


def test_actual_task_value_executes_committed_continuation_and_retains_it(tmp_path):
    payload={"family":"F0","world_seed":13,"config":{"measurement_std":.004,"objective_target":.1,
        "action_loss_cost":0.,"design_thicknesses_nm":[135+i*.1 for i in range(121)],
        "max_iterations":1,"unknown_exploration_limit":0}}
    loop,resources=build_offline_loop(tmp_path,payload)
    try:
        result=loop.run(tmp_path)
        decision=loop._find("offline-open-research:iteration:0:decision")
        continuation=loop._find("offline-open-research:iteration:0:continuation")
        assert decision.summary["action"]=="task_valued_measurement"
        assert continuation.summary["outcome"]=="committed_continuation_completed"
        assert result.candidate_roles["terminal_choice"]==continuation.summary["selected_design_id"]
        assert loop.runner.gates["R"].usage()["calls"]==3
    finally:close(resources)


def test_invalid_schema_program_cannot_reach_final_validation_sample():
    from optomind_optics.harness.science.joint_proposer import parse_joint_proposal
    context={"parent_model_ir":asdict(initial_planar_model()),"source_refs":[],"max_candidates":2,"program_id":"allowed"}
    with pytest.raises(ValueError,match="execution identity"):
        parse_joint_proposal({"models":[],"program":{"program_id":"other","nodes":[],"initial_samples":[]}},context)


def test_claims_negative_results_and_selected_result_load_for_article(optical_run):
    _,loop,result=optical_run
    from optomind_optics.harness.science.article_bridge import load_result_bundle
    bundle=load_result_bundle(loop.repository,result.ref())
    assert bundle.claim_ledger.claims
    represented={ref.content_hash for claim in bundle.claim_ledger.claims for ref in claim.evidence_refs}
    assert all(ref.content_hash in represented for ref in result.negative_result_refs)


def test_same_observations_different_frozen_objective_changes_identification_decision(tmp_path):
    common={"measurement_std":.004,"action_loss_cost":0.,"design_thicknesses_nm":[135+i*.1 for i in range(121)],
            "max_iterations":1,"unknown_exploration_limit":0}
    decisions=[]
    for target in (0.,.1):
        path=tmp_path/str(target)
        loop,resources=build_offline_loop(path,{"family":"F0","world_seed":13,"config":{**common,"objective_target":target}})
        try:
            observations,_,_=loop._acquire(loop._program("initial",loop.config.training_session),reserve=loop.config.measurement_cost)
            candidate,fit,_=loop._fit(loop.initial_model,[observations["measure"]])
            value=loop._value(candidate,fit,loop.config.budget-1)
            decisions.append(value["decision"]["selected_actions"])
        finally:close(resources)
    assert decisions==[(),("new_independent_session",)]


def test_task_valued_program_recovers_after_second_purchase(tmp_path):
    count=[]
    def crash(stage):
        if stage=="after_provider_return":
            count.append(1)
            if len(count)==2:raise RuntimeError("interrupted valued acquisition")
    payload={"family":"F0","world_seed":13,"config":{"measurement_std":.004,"objective_target":.1,"action_loss_cost":0.,
        "design_thicknesses_nm":[135+i*.1 for i in range(121)],"max_iterations":1,"unknown_exploration_limit":0}}
    loop,resources=build_offline_loop(tmp_path,payload,fault_hook=crash)
    try:
        with pytest.raises(RuntimeError,match="interrupted valued acquisition"):loop.run(tmp_path)
    finally:close(resources)
    loop,resources=build_offline_loop(tmp_path,payload)
    try:
        result=loop.run(tmp_path)
        assert result.candidate_roles["terminal_choice"]==loop._find("offline-open-research:iteration:0:continuation").summary["selected_design_id"]
        assert resources[-1].db.execute("SELECT count(*) FROM receipts").fetchone()[0]==3
    finally:close(resources)


def test_invalid_provider_response_preserves_actual_usage_without_network():
    from optomind_optics.harness.science.joint_proposer import QwenJointProposer,ProposalResponseError
    class FakeClient:
        def call_metered(self,messages,**kwargs):return {"content":"{bad json}","_llm_usage":{"input_tokens":21,"output_tokens":3,"mock_llm":True}}
    context={"parent_model_ir":asdict(initial_planar_model()),"source_refs":[],"max_candidates":2,"program_id":"test"}
    proposer=QwenJointProposer(client=FakeClient(),protocol_id="fake/1",allow_live=True)
    with pytest.raises(ProposalResponseError) as error:proposer.propose(context)
    assert error.value.usage["input_tokens"]==21
    assert len(error.value.response_hash)==64


def test_literature_bridge_reaches_actual_proposal_messages_without_likelihood(tmp_path):
    unit={"canonical_paper_id":"P0001","snapshot_id":"offline-source-v1","source_hash":"b"*64,
        "material_depth":"full_text","roles":["model_mechanism"],
        "anchors":[{"source_block_id":"equation-1","quote":"This offline fixture describes a wavelength-dependent constitutive relation."}],
        "conditions":{"angle_deg":0.},"counter_evidence":[]}
    loop,resources=build_offline_loop(tmp_path,{"family":"F3","proposal_replay":[{"models":[]}],
        "literature_source_units":[unit],"config":{"literature_request_limit":1}})
    try:
        result=loop.run(tmp_path)
        request=loop._find("offline-open-research:literature:0:request")
        response=loop._find("offline-open-research:literature:0:result")
        actual=loop._find("offline-open-research:iteration:0:provider")
        context=json.loads(actual.actual_messages[1]["content"])
        assert request.summary["value_status"]=="unknown" and request.summary["slot_consumed_before_request"]
        assert response.summary["research_brief"]["claims"][0]["claim_type"]=="LiteratureClaim"
        assert context["research_brief"]["read_receipts"][0]["source_spans"][0]["quote"]==unit["anchors"][0]["quote"]
        assert context["literature_is_likelihood"] is False
        inference=loop.repository.load(result.inference_ref)
        assert all(isinstance(loop.repository.load(ref),Observation) for ref in inference.observation_refs)
        trajectory=json.loads((tmp_path/"SCIENCE_OPEN_TRAJECTORY.json").read_text())
        assert trajectory["literature_budget"]["consumed_request_slots"]==1
    finally:close(resources)


def test_failed_literature_request_consumes_durable_slot_and_does_not_retry(tmp_path,monkeypatch):
    from optomind_optics.harness.science.literature_request import ReplayEvidenceProvider
    calls=[]
    def fail(self,request):calls.append(request);raise OSError("offline source unavailable")
    monkeypatch.setattr(ReplayEvidenceProvider,"request",fail)
    loop,resources=build_offline_loop(tmp_path,{"literature_source_units":[],"config":{"literature_request_limit":1}})
    try:
        # Exercise the boundary directly; it needs no fabricated measurements.
        candidate=loop._put(ModelCandidate(object_id="bounded-proposal",source_run_id=loop.config.run_id,model_ir=asdict(initial_planar_model())))
        assert loop._literature(0,candidate,{"state":"model_family_inadequate"}) is None
        assert loop._literature(0,candidate,{"state":"model_family_inadequate"}) is None
        assert loop._literature(1,candidate,{"state":"model_family_inadequate"}) is None
        assert len(calls)==1
        assert loop._find("offline-open-research:literature:0:result").summary["request_slot_consumed"] is True
    finally:close(resources)


def test_review_evidence_provider_uses_existing_service(monkeypatch):
    from optomind_research.runtime.upgrade3 import article_evidence
    from optomind_optics.harness.science.literature_request import ReviewEvidenceProvider
    calls=[]
    def collect(request,**kwargs):calls.append((request,kwargs));return "verified_bundle"
    monkeypatch.setattr(article_evidence,"collect_evidence",collect)
    reader=lambda **kwargs:None
    provider=ReviewEvidenceProvider(index="existing-index",source_resolver=reader,protocol_id="review-local/1")
    request=article_evidence.EvidenceRequest(question="source-backed evidence",required_outputs=("model_seed",))
    assert provider.request(request)=="verified_bundle"
    assert calls[0][1]["index"]=="existing-index" and calls[0][1]["source_resolver"] is reader


def test_frozen_config_change_is_rejected_before_any_acquisition(tmp_path):
    loop,resources=build_offline_loop(tmp_path)
    try:
        loop.config=replace(loop.config,objective_target=.3)
        with pytest.raises(ValueError,match="changed after protocol"):loop.run(tmp_path)
        assert loop.runner.gates["R"].usage()["calls"]==0.
    finally:close(resources)


def test_live_plain_client_without_account_metering_is_blocked():
    from optomind_optics.harness.science.joint_proposer import QwenJointProposer
    class PlainClient:
        def call(self,*args,**kwargs):raise AssertionError("unmetered dispatch")
    provider=QwenJointProposer(client=PlainClient(),protocol_id="plain/1",allow_live=True)
    with pytest.raises(PermissionError,match="account-metered"):provider.propose({})
