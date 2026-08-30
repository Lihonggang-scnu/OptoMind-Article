"""Generate and preserve one realistic user-style TMM test question.

This is a one-shot provenance tool for the article handoff branch.  The model
is asked to speak as a real optical-design user: the returned text must contain
only the external design need and constraints.  Harness internals such as
route planning, literature retrieval, feedback, robustness audits, and report
fields are deliberately excluded from the user-facing question.

The script writes the exact prompt, exact raw model content, and safe usage
metadata as UTF-8.  It does not modify the harness and does not run an E2E
experiment.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# When this file is executed directly, Python puts ``scripts`` rather than the
# repository's ``code`` root on sys.path.  Add the root before importing the
# project package; this keeps the invocation independent of the caller's cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from optomind_optics.harness.problem_analyzer import ArticlePlusQwenClient


PROMPT = """请模拟一名真实的光学器件研发用户，向一个能够承接自然语言光学设计需求的系统提出一个新的多层膜设计需求。用户只描述想实现的器件功能、使用场景和外部约束，不了解也不提及系统内部如何检索、规划、计算、迭代或报告结果。

请只生成一条真实用户会说的中文题面，使用自然的第一人称或直接需求口吻。题面应具有科研价值，适合平面多层光学薄膜设计，至少提出两个彼此有科学权衡的外部目标；每个目标都写明波长范围和最大化、最小化或数值阈值；同时写明入射介质、基底、总层数或总厚度等必要约束，角度和偏振在确实相关时再写明。

为了保证题面可执行，入射介质只能使用空气或水，基底只能使用熔融石英或硅；如果题面指定薄膜材料，只能使用以下本地材料目录中的名称：SiO2、TiO2、HfO2、Al2O3、MgF2、ZnS、Si3N4。不要使用 ZnSe、蓝宝石、CaF2、Ge、LaSFN9 或其它不在该集合中的材料，也不要要求复折射率、温度响应、相位、群延迟或全波仿真。

题面只写用户的需求、用途和约束，不写解法、材料配方之外的内部实现说明、文献、检索、路线、对照实验、反馈迭代、TMM、优化器、候选结构、评分、鲁棒性报告、能量守恒、日志、代码或提交格式。不要要求系统输出任何内部状态；这些事情由固定实验链路自行完成。不要给出预期分数、预期排名或暗示答案。

题面必须是一个全新的科学问题，不得复用或同义改写已知历史题型“近红外 800-1500nm 波段高透射、紫外 200-400nm 波段高反射、熔融石英基底、总层数不超过30层”的双波段需求。题面长度控制在450个汉字以内。

输出规则：只输出一段连续的中文题面，不要标题、编号、列表、解释、JSON、Markdown、引号或前后缀。"""


def _new_output_dir(root: Path) -> Path:
    stem = root / "qwen-real-user-question-20260828"
    candidate = stem
    index = 2
    while candidate.exists():
        candidate = root / f"qwen-real-user-question-20260828-v{index}"
        index += 1
    candidate.mkdir(parents=True)
    return candidate


def _safe_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "model_name",
        "mock_llm",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
    return {key: value.get(key) for key in sorted(allowed) if key in value}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "outputs"
        / "tmm_research_harness",
    )
    args = parser.parse_args()

    output_dir = _new_output_dir(args.output_root)
    client = ArticlePlusQwenClient(role="turbo")
    result = client.call(
        [{"role": "user", "content": PROMPT}],
        max_tokens=1800,
    )
    raw_content = str(result.get("content") or "")
    usage = _safe_usage(result.get("_llm_usage"))
    question = raw_content.strip()

    record = {
        "schema_version": "qwen-real-user-question.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": "qwen_generated",
        "model": client.model_name,
        "role": "turbo",
        "prompt": PROMPT,
        "raw_model_output": raw_content,
        "usage": usage,
        "question_candidate": question,
        "e2e_started": False,
        "approval_required": True,
    }
    (output_dir / "QWEN_CALL_RECORD.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "RAW_MODEL_OUTPUT.txt").write_text(
        raw_content,
        encoding="utf-8",
    )
    (output_dir / "PROMPT.txt").write_text(PROMPT, encoding="utf-8")

    # Keep stdout ASCII-only so PowerShell cannot corrupt the Chinese payload.
    print(
        json.dumps(
            {
                "record_dir": str(output_dir),
                "model": client.model_name,
                "usage": usage,
                "raw_model_output": raw_content,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
