from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
import numpy as np

from .contracts import DecisionContext, ExperimentProgram, ModelCandidate, Observation, PredictionEvaluation, PredictionSpec
from .diagnostic_design import DiagnosticForward, action_from_dict, config_from_dict, family_from_dict
from .repository import ScienceRepository
from optomind_research.runtime.artifact_store import atomic_write_json


def export_diagnostic_artifacts(run_dir):
    root = Path(run_dir)
    report = json.loads((root / "DIAGNOSTIC_RESULT.json").read_text())
    public = json.loads((root / "PUBLIC_CONFIG.json").read_text())
    output = root / "materials"; output.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="diagnostic-mpl-"))
    os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="diagnostic-fonts-"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"})
    repository = ScienceRepository(root / "science", read_only=True)
    try:
        objects = [repository.load(ref) for ref in repository.latest_snapshot().object_refs]
        objects = [obj for obj in objects if obj.source_run_id==report["run_id"]]
        observations = {obj.ref().content_hash: obj for obj in objects if isinstance(obj, Observation)}
        predictions = {obj.ref().content_hash: obj for obj in objects if isinstance(obj, PredictionSpec)}
        evaluations = [obj for obj in objects if isinstance(obj, PredictionEvaluation)]
        initial = report["raw_data"][0]
        initial_action = action_from_dict(initial["action"])
        families = tuple(family_from_dict(row) for row in public["families"])
        config = config_from_dict(public["config"])
        forward = DiagnosticForward(config)
        first_competition = report["trajectory"][0]["before"] if report["trajectory"] else report["competition"]
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), constrained_layout=True)
        wl = np.asarray(initial_action.wavelengths_nm)
        values = np.asarray(initial["values"])
        sigma = np.sqrt(np.diag(initial["covariance"]))
        axes[0].errorbar(wl, values, yerr=2 * sigma, color="black", fmt="o", ms=3, label="Synthetic observation, +/-2 raw-noise SD")
        for family, fit in zip(families, first_competition["fits"]):
            if fit["parameters"]:
                axes[0].plot(wl, forward.predict(family, fit["parameters"], initial_action), lw=1.8, label=family.family_id + " fitted on initial data")
        axes[0].set(title="Before: bare-sample ambiguity", xlabel="Wavelength (nm)", ylabel="Reflectance")
        axes[0].legend(fontsize=8)
        diagnostic_evaluations = [evaluation for evaluation in evaluations if ":diagnostic-0:" in evaluation.object_id]
        if diagnostic_evaluations:
            observed = observations[diagnostic_evaluations[0].observation_ref.content_hash]
            raw = observed.raw_blocks[0]
            axes[1].errorbar(raw["coordinate"], raw["values"], yerr=2 * np.sqrt(np.diag(raw["covariance"])), color="black", fmt="o", ms=3, label="Blind synthetic acquisition, +/-2 raw-noise SD")
            for evaluation in diagnostic_evaluations:
                prediction = predictions[evaluation.prediction_ref.content_hash]
                line = axes[1].plot(prediction.coordinate, prediction.distribution["mean"], lw=1.8, label=prediction.distribution["candidate_id"] + " frozen prediction")[0]
                axes[1].fill_between(prediction.coordinate, prediction.lower, prediction.upper, alpha=.14, color=line.get_color())
            axes[1].set(title="After: automatically selected front end", xlabel="Wavelength (nm)", ylabel="Reflectance")
            axes[1].legend(fontsize=8)
        else:
            axes[1].text(.5, .5, "No diagnostic acquisition was authorized/selected", ha="center", va="center", transform=axes[1].transAxes)
            axes[1].set_axis_off()
        fig.suptitle(report["run_id"] + " | V1 synthetic, finite model set", fontsize=12)
        for suffix in ("png", "svg"):
            fig.savefig(output / ("01_frozen_predictions." + suffix), dpi=180)
        plt.close(fig)
        candidate_files = sorted(root.glob("CANDIDATES_*.json"))
        rows = []
        for path in candidate_files:
            selection = json.loads(path.read_text())
            for candidate in selection["candidates"]:
                action = candidate["action"]
                rows.append({"round": path.stem, "action_id": action["action_id"], "frontend_id": action["frontend"]["frontend_id"], "angle_deg": action["angle_deg"],
                    "polarization": action["polarization"], "cost_units": candidate["cost"], "screening_separation": candidate.get("screening_separation"),
                    "refitted_separation": candidate.get("refitted_separation"), "utility_per_cost": candidate.get("utility_per_cost"), "status": candidate["status"],
                    "reference_layers": json.dumps(action["frontend"]["layers"], separators=(",", ":"))})
        fields = ["round", "action_id", "frontend_id", "angle_deg", "polarization", "cost_units", "screening_separation", "refitted_separation", "utility_per_cost", "status", "reference_layers"]
        with (output / "action_benefit_cost.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), constrained_layout=True)
        fits = report["competition"]["fits"]
        x = np.arange(len(fits))
        discrepancy = [fit["discrepancy"] if fit["discrepancy"] is not None else np.nan for fit in fits]
        axes[0].bar(x, np.maximum(discrepancy, 1e-8), color=["#287a55" if fit["state"] == "retained" else "#b8473c" for fit in fits], label="Joint discrepancy after each family refit")
        for i, fit in enumerate(fits):
            axes[0].plot([i - .35, i + .35], [fit["threshold"]] * 2, color="black", lw=2, label="Frozen discrepancy threshold" if i == 0 else None)
        axes[0].set_xticks(x, [fit["family_id"] + "\n" + fit["state"] for fit in fits])
        axes[0].set(title="Every mechanism refitted on old + new evidence", ylabel="Whitened squared discrepancy (log scale)", yscale="log")
        axes[0].legend(fontsize=8)
        for category, marker, color in (("bare", "o", "#526777"), ("fixed-reference-baseline", "s", "#bf8f20"), ("searched", "^", "#3466a8")):
            subset = [row for row in rows if row["refitted_separation"] is not None and (row["frontend_id"] == category if category != "searched" else row["frontend_id"] not in {"bare", "fixed-reference-baseline"})]
            if subset:
                axes[1].scatter([row["cost_units"] for row in subset], [row["refitted_separation"] for row in subset], marker=marker, color=color, s=45, label=category)
        axes[1].set(title="Prospective adversarial score vs declared cost", xlabel="Acquisition + front-end cost (relative units)", ylabel="Finite-search refitted separation", yscale="symlog")
        axes[1].set_yscale("symlog",linthresh=1.)
        if rows:
            axes[1].legend(fontsize=8)
        fig.suptitle("Scoped numerical evidence; no population classification/cost guarantee", fontsize=12)
        for suffix in ("png", "svg"):
            fig.savefig(output / ("02_refits_and_action_cost." + suffix), dpi=180)
        plt.close(fig)
        snapshots = [json.loads(row[0]) for row in repository.db.execute("SELECT payload FROM snapshots ORDER BY sequence")]
        first_sequences = {}
        for snapshot in snapshots:
            for ref in snapshot["object_refs"]:
                first_sequences.setdefault(ref["content_hash"], snapshot["committed_sequence"])
        timeline = []
        for obj in objects:
            event = None
            if isinstance(obj, ExperimentProgram): event = "program_committed"
            elif isinstance(obj, PredictionSpec): event = "prediction_precommitted"
            elif isinstance(obj, Observation): event = "observation_acquired"
            elif isinstance(obj, PredictionEvaluation): event = "frozen_prediction_evaluated"
            elif isinstance(obj, DecisionContext) and ("competition:" in obj.object_id or obj.object_id.endswith("initial-competition")): event = "all_mechanisms_refitted"
            elif isinstance(obj, DecisionContext) and obj.object_id.endswith("confirmation-result"): event = "independent_confirmation_decided"
            elif getattr(obj, "schema_version", "") == "research-method-asset.v1": event = "method_asset_registered"
            if event:
                timeline.append({"sequence": first_sequences[obj.content_hash], "event": event, "object_ref": obj.ref().model_dump(mode="json"), "input_refs": [ref.model_dump(mode="json") for ref in obj.input_refs]})
        timeline.sort(key=lambda row: (row["sequence"], row["object_ref"]["object_id"]))
        atomic_write_json(output / "case_timeline.json", {"case_id": report["run_id"], "events": timeline, "acquisition_index": report["acquisition_index"], "scope": "one uninterrupted synthetic case; no cross-run result stitching"})
        atomic_write_json(output / "material_manifest.json", {"case_id": report["run_id"], "source_result": report["result"]["content_hash"], "files": ["01_frozen_predictions.png", "01_frozen_predictions.svg", "02_refits_and_action_cost.png", "02_refits_and_action_cost.svg", "action_benefit_cost.csv", "case_timeline.json"], "plot_only_forward_calls": forward.prediction_calls, "plotting_evidence_role": "replays initial fitted curves from public models; no new observations or likelihood terms"})
    finally:
        repository.close()
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    print(export_diagnostic_artifacts(args.run))


if __name__ == "__main__":
    main()
