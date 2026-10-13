from dataclasses import asdict
from pathlib import Path
import argparse
import json
import numpy as np

from .diagnostic_design import DiagnosticAction, DiagnosticConfig, DiagnosticDatum, DiagnosticForward, DiagnosticFrontEnd, DiagnosticSearchSpace, action_from_dict, config_from_dict, design_diagnostic_action, family_from_dict, refit_competition
from .diagnostic_research import DiagnosticResearchLoop
from .repository import ScienceRepository
from optomind_research.runtime.artifact_store import atomic_write_json


def demo_public_config():
    from optomind_eval.diagnostic_world import reversal_family
    families = (reversal_family("forward-order"), reversal_family("reversed-order", reverse=True))
    return {"run_id": "diagnostic-reversal-demo", "families": [asdict(f) for f in families],
        "config": asdict(DiagnosticConfig(fit_starts=3, adversarial_shortlist=2, adversarial_starts=2, adversarial_max_nfev=80)),
        "search_space": asdict(DiagnosticSearchSpace()),
        "initial_action": asdict(DiagnosticAction("initial-bare", DiagnosticFrontEnd("bare"), tuple(np.linspace(440., 810., 15)), session_id="initial"))}


def parse_public_config(payload):
    if set(payload) != {"run_id", "families", "config", "search_space", "initial_action"}:
        raise ValueError("diagnostic public config fields differ from the versioned contract")
    families = tuple(family_from_dict(row) for row in payload["families"])
    cfg = config_from_dict(payload["config"])
    space = DiagnosticSearchSpace(**payload["search_space"])
    initial = action_from_dict(payload["initial_action"])
    return families, cfg, space, initial


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--config")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--world")
    parser.add_argument("--observations")
    parser.add_argument("--write-example", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    if args.write_example:
        public = demo_public_config()
        atomic_write_json(output / "PUBLIC_CONFIG.json", public)
        atomic_write_json(output / "SEALED_SYNTHETIC_WORLD.json", {"family_id": "reversed-order", "parameters": {"sample.d0": 87., "sample.d1": 102., "sample.d2": 55.}, "seed": 9127})
        print(json.dumps({"status": "example_written", "output": str(output)})); return
    if not args.config:
        parser.error("--config is required; generate a public example with --write-example")
    public = json.loads(Path(args.config).read_text())
    families, cfg, space, initial = parse_public_config(public)
    atomic_write_json(output / "PUBLIC_CONFIG.json", public)
    if args.offline:
        if not args.world or args.observations:
            parser.error("--offline requires separate --world and excludes --observations")
        world = json.loads(Path(args.world).read_text())
        if set(world) - {"family_id", "parameters", "seed", "mismatch_layer"}:
            raise ValueError("unknown sealed evaluator input")
        from optomind_eval.diagnostic_world import SyntheticDiagnosticProvider
        provider = SyntheticDiagnosticProvider(family=next(f for f in families if f.family_id == world["family_id"]), parameters=world["parameters"], config=cfg, seed=world.get("seed", 9127), mismatch_layer=world.get("mismatch_layer"))
        repository = ScienceRepository(output / "science")
        try:
            loop = DiagnosticResearchLoop(families=families, config=cfg, search_space=space, repository=repository, run_id=public["run_id"], provider=provider)
            report = loop.run(initial, output)
        finally:
            repository.close()
        from .diagnostic_artifacts import export_diagnostic_artifacts
        export_diagnostic_artifacts(output)
        print(json.dumps({"state": report["competition"]["state"], "selected_family": report["competition"]["selected_family"], "cost": report["measurement_cost"], "output": str(output)}, ensure_ascii=False))
    else:
        if not args.observations or args.world:
            parser.error("planning requires --observations and forbids hidden --world")
        rows = json.loads(Path(args.observations).read_text())
        data = tuple(DiagnosticDatum(row["acquisition_id"], action_from_dict(row["action"]), tuple(row["values"]), tuple(tuple(v) for v in row["covariance"])) for row in rows)
        forward = DiagnosticForward(cfg)
        competition = refit_competition(families, data, forward, cfg)
        selection = design_diagnostic_action(families, data, competition, forward, cfg, space, session_id="planned-next", remaining_budget=cfg.measurement_budget)
        atomic_write_json(output / "COMPETITION.json", asdict(competition))
        atomic_write_json(output / "NEXT_MEASUREMENT.json", asdict(selection))
        print(json.dumps({"state": selection.state, "output": str(output)}))


if __name__ == "__main__":
    main()
