"""Offline C13 entry point. Explicit opt-in; never constructs an LLM client.

python -m optomind_optics.harness.science.open_research_cli --offline --output RUN
The evaluator is an in-process test facility, not an OS security boundary.
"""
from dataclasses import asdict, replace, fields
import argparse
import json
from pathlib import Path
import sqlite3

from .contracts import canonical_hash
from .jobs import ExecutionGate, ToolCapability
from .repository import ScienceRepository
from .program_runner import ProgramRunner
from .world_client import WorldClient, SyntheticMeasurementProvider, TaskManifest
from .open_research_loop import OpenResearchConfig, OpenResearchLoop
from optomind_research.runtime.artifact_store import atomic_write_json


class DurableOfflineProvider(SyntheticMeasurementProvider):
    """Persist actual API receipts before returning them to the effect gate.

    Unrecorded effects remain unknown; there is deliberately no blind resubmit on
    recovery. This adapter is a local synthetic test fixture, not a device driver.
    """
    def __init__(self, client, *, sample_tasks, path):
        super().__init__(client,sample_tasks=sample_tasks)
        self.db=sqlite3.connect(str(path))
        self.db.execute("PRAGMA journal_mode=WAL");self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY,binding TEXT,result TEXT)")

    def submit(self,key,payload):
        binding=canonical_hash(payload)
        saved=self.db.execute("SELECT binding,result FROM receipts WHERE key=?",(key,)).fetchone()
        if saved:
            if saved[0]!=binding:raise ValueError("offline provider idempotency binding changed")
            return json.loads(saved[1])
        result=super().submit(key,payload)
        with self.db:self.db.execute("INSERT INTO receipts VALUES(?,?,?)",(key,binding,json.dumps(result,allow_nan=False)))
        return result

    def status(self,key):
        row=self.db.execute("SELECT result FROM receipts WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else {"state":"unknown_outcome"}

    def close(self):self.db.close()


def _parse_base_offline_config(payload):
    unknown=set(payload)-{"family","world_seed","config","fixture","proposal_replay","literature_source_units","evidence_service","experiment_policy","method_library","task_adoption"}
    if unknown:raise ValueError("unknown offline config fields: "+str(sorted(unknown)))
    if "evidence_service" in payload:
        service=payload["evidence_service"]
        if not isinstance(service,dict) or service.get("mode","preview") not in {"preview","replay"}:
            raise ValueError("offline entry accepts only preview/replay evidence service configuration")
    family=payload.get("family","F3");seed=int(payload.get("world_seed",13))
    config=dict(payload.get("config",{}))
    iterations=int(config.get("max_iterations",2))
    if not 1<=iterations<=8:raise ValueError("max_iterations must be between 1 and 8")
    selection=tuple(f"selection-{i}" for i in range(iterations))
    sessions=("training",*selection,"validation","reference")
    defaults={"run_id":"offline-open-research","training_session":"training","selection_sessions":selection,
              "validation_session":"validation","reference_session":"reference",
              "session_groups":tuple((session,f"{session}:instrument") for session in sessions)}
    fixture=payload.get("fixture","single_film")
    if fixture not in {"single_film","multilayer"}:raise ValueError("unknown offline fixture")
    if fixture=="multilayer":
        defaults.update(design_parameter="d1",design_thicknesses_nm=tuple(float(x) for x in range(45,196,3)))
    defaults.update(config)
    for name in ("selection_sessions","wavelengths_nm","validation_wavelengths_nm","design_thicknesses_nm","grammar","robust_noise_std_multipliers","robust_thresholds"):
        if name in defaults:defaults[name]=tuple(defaults[name])
    if "session_groups" in defaults:defaults["session_groups"]=tuple(tuple(item) for item in defaults["session_groups"])
    for field in ("smc_config","gaussian_value_protocol","compute_utility_rates","profile_config"):
        if field in defaults:
            raw=defaults[field]
            defaults[field]=tuple(sorted(raw.items())) if isinstance(raw,dict) else tuple(tuple(item) for item in raw)
    if "robust_prior_vertices" in defaults:defaults["robust_prior_vertices"]=tuple(tuple(row) for row in defaults["robust_prior_vertices"])
    cfg=OpenResearchConfig(**defaults)
    return cfg,fixture,family,seed



def public_initial_model(fixture):
    from .open_research_loop import initial_planar_model
    if fixture=="single_film":return initial_planar_model()
    from optomind_eval.multilayer_tasks import multilayer_model
    public=multilayer_model().ir
    nodes=tuple(replace(node,attributes=(("thickness",65. if node.node_id=="l0" else 95.),))
                if node.node_id in {"l0","l2"} else node for node in public.nodes)
    return replace(public,nodes=nodes,parameters=tuple(p for p in public.parameters if p.name in {"n","d1"}),domain_nm=(400.,900.))


def resolve_offline_config(payload):
    cfg,fixture,family,seed=_parse_base_offline_config(payload)
    adoption=None
    if "task_adoption" in payload:
        raw=payload["task_adoption"]
        if not isinstance(raw,dict) or set(raw)!={"brief","adopted_claim_ids"}:
            raise ValueError("task adoption requires only brief and explicit adopted_claim_ids")
        ids=raw["adopted_claim_ids"]
        if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):
            raise ValueError("task adoption requires distinct explicit claim IDs")
        from .prefreeze_task_adoption import build_prefreeze_config
        model=public_initial_model(fixture)
        spec=next((p for p in model.parameters if p.name==cfg.design_parameter),None)
        if spec is None:raise ValueError("task adoption design parameter is not declared by public model")
        conditions={"wavelength_domain_nm":list(model.domain_nm),"design_domain_nm":[spec.lower,spec.upper],
                    "geometry":"single_planar_film" if fixture=="single_film" else "multilayer","angle_deg":0.}
        adoption=build_prefreeze_config(cfg,raw["brief"],task_conditions=conditions,adopted_claim_ids=tuple(ids))
        cfg=adoption.config
    return cfg,fixture,family,seed,adoption


def parse_offline_config(payload):
    return resolve_offline_config(payload)[:4]


def build_offline_loop(output, payload=None, *, fault_hook=None, experiment_policy=None, method_provider_factory=None, operator_admission=None):
    # Only this evaluator-side fixture imports hidden world construction. The
    # actual OpenResearchLoop receives transport, public sessions and receipts.
    from optomind_eval.worlds import make_world
    from optomind_eval.service import MeasurementService
    output=Path(output);payload=payload or {}
    cfg,fixture,family,seed,adoption=resolve_offline_config(payload)
    from .offline_method_config import inspect_activation, policy_from_config, library_factory
    inspect_activation(payload)
    if 'experiment_policy' in payload:
        if experiment_policy is not None:raise ValueError("policy activation supplied both in config and host API")
        experiment_policy=policy_from_config(payload)
    method_resources=[]
    if 'method_library' in payload:
        if method_provider_factory is not None:raise ValueError("method library supplied both in config and host API")
        method_provider_factory,method_resources=library_factory(payload['method_library'])
    output.mkdir(parents=True,exist_ok=True)
    # The same specimen law is measured in independent calibration sessions.
    # This freezes held-out *session* groups, not independent specimen transfer.
    base=make_world(family,task_id="source",split="V",seed=seed)
    worlds=[]
    for index,(session,group) in enumerate(cfg.session_groups):
        if not group.endswith(":instrument"):raise ValueError("offline fixture group must end in :instrument")
        manifest=replace(base.task,task_id=session,group_id=group[:-11],cost_model={"R":cfg.measurement_cost,"T":2*cfg.measurement_cost})
        world=replace(base,task=manifest,seed=seed+1000*index,independent_noise_std=cfg.measurement_std,
                      shared_calibration_std=cfg.calibration_std)
        if session==cfg.reference_session:
            world=replace(world,film_n=1.,thickness_nm=0.,dispersion_b_um2=0.,gain=1.,offset=0.,resolution_nm=0.)
        worlds.append(world)
    service=MeasurementService(worlds,budget=cfg.budget)
    initial_model=None
    if fixture=="multilayer":
        from optomind_eval.open_multilayer_service import MultilayerMeasurementService
        from optomind_eval.multilayer_tasks import multilayer_model
        service=MultilayerMeasurementService(sessions=cfg.session_groups,reference_session=cfg.reference_session,
            seed=seed,family=family,budget=cfg.budget,noise_std=cfg.measurement_std,
            calibration_std=cfg.calibration_std,cost=cfg.measurement_cost)
        initial_model=public_initial_model(fixture)
    provider=DurableOfflineProvider(WorldClient(service.handle),sample_tasks={s:s for s,_ in cfg.session_groups},path=output/"provider.sqlite")
    cap=ToolCapability(effect_class="external_measurement",domain="V1_independent_Byrnes" if fixture=="multilayer" else "V1_independent_Airy",channels=("R",),units=("1",),supports_status=True)
    gate=ExecutionGate(output/"jobs.sqlite",limits={"calls":float(4*cfg.max_iterations+3),"cost":cfg.budget},capability=cap,fault_hook=fault_hook)
    runner=ProgramRunner(output/"programs.sqlite",gates={"R":gate},providers={"R":provider})
    repo=ScienceRepository(output/"science")
    # Offline CLI only permits data replay. It never imports a live Qwen client.
    proposer=None
    if "proposal_replay" in payload:
        from .joint_proposer import ReplayJointProposer
        proposer=ReplayJointProposer(payload["proposal_replay"])
    evidence_provider=None
    if "literature_source_units" in payload:
        from .literature_request import ReplayEvidenceProvider
        evidence_provider=ReplayEvidenceProvider(payload["literature_source_units"])
    if "evidence_service" in payload:
        from optomind_research.runtime.upgrade3.evidence_service import create_evidence_service
        config=payload["evidence_service"]
        if config.get("mode","preview") not in {"preview","replay"}:raise ValueError("offline entry accepts only preview/replay evidence service")
        evidence_provider=create_evidence_service(config,root=output)
    loop=None
    try:
        loop=OpenResearchLoop(repository=repo,runner=runner,config=cfg,initial_model=initial_model,
                              proposal_provider=proposer,evidence_provider=evidence_provider,experiment_policy=experiment_policy,method_provider_factory=method_provider_factory,operator_admission=operator_admission)
        if adoption is not None:
            from .prefreeze_task_adoption import prefreeze_task
            base_cfg=_parse_base_offline_config(payload)[0]
            draft=loop._put(prefreeze_task(base_cfg))
            loop._context("prefreeze-task-adoption",{
                "draft_ref":draft.ref().model_dump(mode="json"),"before_config_hash":adoption.before_config_hash,
                "after_config_hash":adoption.after_config_hash,"receipts":adoption.receipts,
                "brief":payload["task_adoption"]["brief"],"adopted_claim_ids":payload["task_adoption"]["adopted_claim_ids"],
                "stage":"explicit adoption before purchases; effective task configuration frozen"},(draft.ref(),))
    except BaseException:
        if loop is not None:loop.compute_ledger.close()
        for resource in (repo,runner,gate,*method_resources,provider):resource.close()
        raise
    return loop,(repo,runner,gate,loop.compute_ledger,*method_resources,provider)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline",action="store_true",help="required: synthetic observation API, no network or LLM")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--config",type=Path,help="JSON containing fixture, family, world_seed, public config overrides and optional proposal_replay")
    parser.add_argument("--resume",action="store_true",help="recover the same frozen config and durable receipts")
    parser.add_argument("--preview",action="store_true",help="validate/display config without purchases or fitting")
    parser.add_argument("--adoption-brief",type=Path,help="source-qualified ResearchBrief JSON bound to preview draft_task_ref")
    parser.add_argument("--adopt-task-claim",action="append",default=[],help="explicit pre-freeze task claim ID; repeat for multiple claims")
    args=parser.parse_args(argv)
    if not args.offline:parser.error("this CLI only supports explicit --offline; real acquisitions need an authorized provider")
    payload=json.loads(args.config.read_text()) if args.config else {}
    if bool(args.adoption_brief)!=bool(args.adopt_task_claim):
        parser.error("--adoption-brief requires explicit --adopt-task-claim selections")
    if args.adoption_brief:
        if "task_adoption" in payload:parser.error("task adoption supplied in both config and CLI flags")
        payload["task_adoption"]={"brief":json.loads(args.adoption_brief.read_text()),"adopted_claim_ids":args.adopt_task_claim}
    manifest=args.output/"OFFLINE_RUN_CONFIG.json"
    if args.resume:
        if not manifest.exists():parser.error("--resume requires an existing OFFLINE_RUN_CONFIG.json")
        saved=json.loads(manifest.read_text())
        if (args.config or args.adoption_brief) and canonical_hash(payload)!=canonical_hash(saved):parser.error("resume config differs from the frozen run")
        payload=saved
    elif manifest.exists() and not args.preview:
        parser.error("output already contains a run; use --resume or a new output directory")
    if args.preview:
        cfg,fixture,family,seed,adoption=resolve_offline_config(payload)
        from .joint_proposer import proposal_messages
        public_model=public_initial_model(fixture)
        context={"phase":"before_purchase_preview","iteration":0,"parent_model_ir":asdict(public_model),
            "source_refs":[],"observations":[],"max_candidates":cfg.max_candidates,
            "program_id":f"{cfg.run_id}:iteration:0","available_channels":["R"],
            "allowed_samples":[cfg.selection_sessions[0],cfg.reference_session],
            "objective":{"wavelength_nm":cfg.objective_wavelength_nm,"target_R":cfg.objective_target},
            "remaining_budget":cfg.budget-2*cfg.measurement_cost}
        from .offline_method_config import inspect_activation
        activation=inspect_activation(payload)
        from .prefreeze_task_adoption import prefreeze_task_ref
        base_cfg=_parse_base_offline_config(payload)[0]
        adoption_preview=None if adoption is None else {"receipts":adoption.receipts,"before_config_hash":adoption.before_config_hash,"after_config_hash":adoption.after_config_hash}
        print(json.dumps({"mode":"offline-preview","configuration":asdict(cfg),"fixture":fixture,"activation":activation,
                          "draft_task_ref":prefreeze_task_ref(base_cfg).model_dump(mode="json"),"task_adoption":adoption_preview,
                          "paid_calls":0,"network_calls":0,"scope":"synthetic purchased raw data; independent Airy or Byrnes vs production VeriTMM",
                          "proposal_message_preview":proposal_messages(context),
                          "preview_note":"No observations yet. Exact later messages are persisted before each proposal call.",
                          "command_requires":"new output directory or --resume"},indent=2));return 0
    # Reject malformed or stale local activation before creating any run files.
    parse_offline_config(payload)
    from .offline_method_config import inspect_activation
    inspect_activation(payload)
    args.output.mkdir(parents=True,exist_ok=True)
    if not manifest.exists():atomic_write_json(manifest,payload)
    resources=()
    try:
        loop,resources=build_offline_loop(args.output,payload)
        result=loop.run(args.output)
        print(json.dumps({"selected_result":result.ref().model_dump(),"candidate_roles":result.candidate_roles,
                          "output":str(args.output),"paid_calls":0,"network_calls":0},indent=2))
        return 0
    finally:
        for resource in resources:resource.close()


if __name__=="__main__":raise SystemExit(main())
