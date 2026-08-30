"""Run the verifier-first TMM research harness from one natural-language request."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERITMM_ROOT = PROJECT_ROOT.parent / "veritmm"
# Keep this copy runnable without depending on an unrelated editable install.
# The code tree contains a legacy namespace with material CSVs, while the
# executable VeriTMM package lives in the sibling ``veritmm`` tree.
for import_root in (VERITMM_ROOT, PROJECT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from optomind_optics.harness.method_research import (  # noqa: E402
    QwenMethodFindingSynthesizer,
    TMMMethodResearchAdapter,
    discover_review_kb_paths,
)
from optomind_optics.harness.problem_analyzer import (  # noqa: E402
    ArticlePlusQwenClient,
    QwenTMMProblemAnalyzer,
)
from optomind_optics.harness.research_orchestrator import (  # noqa: E402
    TMMResearchHarness,
    TMMResearchHarnessConfig,
)
from optomind_optics.harness.strategy_planner import QwenTMMStrategyPlanner  # noqa: E402
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler  # noqa: E402


def _slug(value: str) -> str:
    ascii_words = re.findall(r"[A-Za-z0-9]+", value.lower())[:6]
    return "-".join(ascii_words) or "optical-task"


def _question(args: argparse.Namespace) -> str:
    if args.question_file:
        return Path(args.question_file).read_text(encoding="utf-8").strip()
    if args.question:
        return str(args.question).strip()
    if args.resume_from:
        request_path = Path(args.resume_from) / "REQUEST.json"
        if request_path.exists():
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            return str(payload.get("question") or "").strip()
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze, research, design, execute and revise a planar multilayer "
            "task with qwen3.7-flash and the deterministic TMM runtime."
        )
    )
    parser.add_argument("question", nargs="?", help="Natural-language optical task")
    parser.add_argument("--question-file", help="UTF-8 text file containing the task")
    parser.add_argument(
        "--resume-from",
        help=(
            "Resume a completed/paused harness output directory into a new "
            "output directory; literature routes below the round cap are "
            "reactivated by default"
        ),
    )
    parser.add_argument(
        "--resume-route",
        action="append",
        default=[],
        help="Explicit route ID to reactivate during resume; repeat as needed",
    )
    parser.add_argument("--output-dir", help="New or empty run directory")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--kb-sqlite",
        action="append",
        default=[],
        help="Optional ReviewKnowledgeBase SQLite path; repeat as needed",
    )
    parser.add_argument(
        "--online-method-research",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use S2 first and OpenAlex only as a complement (default: enabled)",
    )
    parser.add_argument(
        "--qwen-method-synthesis",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use one bounded qwen3.7-flash call to synthesize literature methods",
    )
    parser.add_argument("--maximum-iterations", type=int, default=6)
    parser.add_argument("--maximum-initial-routes", type=int, default=5)
    parser.add_argument(
        "--route-planning-maximum-routes",
        type=int,
        default=4,
        help="Maximum number of literature-planned routes before adding the control arm (default: 4)",
    )
    parser.add_argument(
        "--max-rounds-per-route",
        type=int,
        default=6,
        help="Independent hard cap for each route lineage (default: 6)",
    )
    parser.add_argument(
        "--minimum-rounds-before-llm-stop",
        type=int,
        default=2,
        help=(
            "Minimum executed rounds before an explicit LLM no-benefit stop "
            "can terminate a route (default: 2)"
        ),
    )
    parser.add_argument(
        "--control-route",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Add one parallel planner route using model memory only, without "
            "S2 or method-research input (default: enabled)"
        ),
    )
    parser.add_argument("--maximum-refinement-rounds", type=int, default=1)
    parser.add_argument("--maximum-method-research-rounds", type=int, default=2)
    parser.add_argument(
        "--method-research-wall-time-seconds",
        type=float,
        default=360.0,
        help="Maximum online-method-research time per round before graceful fallback",
    )
    parser.add_argument(
        "--s2-request-budget-seconds",
        type=float,
        default=75.0,
        help="Maximum elapsed time for one Semantic Scholar request including retries",
    )
    parser.add_argument("--wall-time-seconds", type=float, default=3600.0)
    parser.add_argument(
        "--task-compiler-tier",
        choices=("turbo", "plus"),
        default="turbo",
        help=(
            "Qwen tier that translates a route into a TMM task. turbo is the "
            "shipped default; plus costs more per compile and is worth trying "
            "when a compile encodes a direction backwards"
        ),
    )
    parser.add_argument(
        "--force-mock",
        action="store_true",
        help="Forward the repository mock flag to Qwen calls (diagnostic only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    question = _question(args)
    if not question:
        raise SystemExit("A question or --question-file is required.")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id.strip() or f"tmm-research-{timestamp}-{_slug(question)}"
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(args.resume_from).resolve().with_name(
            f"{Path(args.resume_from).name}-resume-{timestamp}"
        )
        if args.resume_from
        else PROJECT_ROOT / "outputs" / "tmm_research_harness" / run_id
    )
    synthesis = (
        QwenMethodFindingSynthesizer(force_mock=True if args.force_mock else None)
        if args.qwen_method_synthesis
        else None
    )
    review_kb_paths = (
        [Path(item) for item in args.kb_sqlite]
        if args.kb_sqlite
        else list(discover_review_kb_paths(PROJECT_ROOT, question=question))
    )
    researcher = TMMMethodResearchAdapter(
        review_kb_paths=review_kb_paths,
        online_enabled=bool(args.online_method_research),
        require_method_guidance=True,
        synthesis_callback=synthesis,
        max_queries=6,
        local_top_k=6,
        online_limit=6,
        online_wall_time_seconds=args.method_research_wall_time_seconds,
        s2_request_budget_seconds=args.s2_request_budget_seconds,
    )
    config = TMMResearchHarnessConfig(
        maximum_iterations=args.maximum_iterations,
        maximum_initial_routes=args.maximum_initial_routes,
        route_planning_maximum_routes=args.route_planning_maximum_routes,
        max_rounds_per_route=args.max_rounds_per_route,
        minimum_rounds_before_llm_stop=args.minimum_rounds_before_llm_stop,
        maximum_refinement_rounds=args.maximum_refinement_rounds,
        maximum_method_research_rounds=args.maximum_method_research_rounds,
        wall_time_seconds=args.wall_time_seconds,
        online_method_research=bool(args.online_method_research),
        use_qwen_policy_inside_tmm=False,
        qwen_force_mock=True if args.force_mock else None,
        control_route_enabled=bool(args.control_route),
    )
    task_compiler = QwenTMMTaskCompiler(
        client=ArticlePlusQwenClient(role=args.task_compiler_tier)
    )
    harness = TMMResearchHarness(
        output_dir,
        run_id=run_id,
        problem_analyzer=QwenTMMProblemAnalyzer(),
        method_researcher=researcher,
        strategy_planner=QwenTMMStrategyPlanner(),
        task_compiler=task_compiler,
        config=config,
    )
    result = (
        harness.resume_from_checkpoint(
            args.resume_from,
            question=question,
            route_ids=args.resume_route,
        )
        if args.resume_from
        else harness.run(question)
    )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "stage": result.stage,
                "output_dir": str(output_dir.resolve()),
                "telemetry": result.telemetry,
                "final_answer": str((output_dir / "FINAL_ANSWER.md").resolve())
                if result.final_answer is not None
                else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
