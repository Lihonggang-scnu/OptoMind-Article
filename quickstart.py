"""Minimal evaluator entry points for OptoMind-Article.

Static replay uses only the Python standard library.  A real lightweight run
checks local credential files, prepares an isolated environment when needed,
and then calls the shipped research harness without placing secrets on the
command line or copying them into the repository.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CODE_ROOT = ROOT / "code"
REPLAY_SCRIPT = CODE_ROOT / "scripts" / "run_static_replay_ui.py"
HARNESS_SCRIPT = CODE_ROOT / "scripts" / "run_tmm_research_harness.py"
DEFAULT_KEY_DIR = CODE_ROOT / "api_keys"
DEFAULT_QUESTION_FILE = CODE_ROOT / "examples" / "evaluator_quick_test_question.txt"
OUTPUT_ROOT = CODE_ROOT / "outputs" / "tmm_research_harness"
LOCAL_RUN_ROOT = ROOT / "local_runs"
REQUIREMENTS = CODE_ROOT / "requirements-evaluator.txt"
VENV_DIR = ROOT / ".venv"

RUNTIME_IMPORTS = (
    "numpy",
    "pydantic",
    "scipy",
    "matplotlib",
    "PIL",
    "fitz",
    "tmm",
    "torch",
    "openai",
    "ftfy",
)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def _run(command: list[str], *, env: dict[str, str] | None = None) -> int:
    printable = " ".join(f'"{item}"' if " " in item else item for item in command)
    print(f"\n执行：{printable}\n", flush=True)
    return subprocess.call(command, cwd=ROOT, env=env)


def _python_in_venv() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _runtime_ready(python: str | Path) -> bool:
    probe = "; ".join(f"import {name}" for name in RUNTIME_IMPORTS)
    probe += "; import numpy as np; assert hasattr(np, 'trapezoid')"
    probe += "; from pydantic import BaseModel; assert hasattr(BaseModel, 'model_validate')"
    completed = subprocess.run(
        [str(python), "-c", probe],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _prepare_runtime(*, use_current_env: bool = False) -> Path:
    current = Path(sys.executable).resolve()
    if _runtime_ready(current):
        print(f"运行环境已就绪：{current}")
        return current
    if use_current_env:
        raise RuntimeError(
            "当前 Python 缺少运行依赖。请去掉 --use-current-env，让程序自动创建 .venv。"
        )

    venv_python = _python_in_venv()
    if not venv_python.is_file():
        print("首次运行：正在创建项目隔离环境 .venv……")
        # Reuse compatible machine-wide wheels when available; pip still
        # installs or upgrades every missing/incompatible pinned requirement.
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(VENV_DIR)
    if not _runtime_ready(venv_python):
        print("首次运行：正在安装科研链路依赖；PyTorch 下载可能需要数分钟……")
        code = _run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(REQUIREMENTS),
            ]
        )
        if code != 0 or not _runtime_ready(venv_python):
            raise RuntimeError("依赖安装未完成，请检查网络后再次运行同一个入口。")
    print(f"运行环境已就绪：{venv_python}")
    return venv_python


def _credential_file(key_dir: Path, filename: str) -> Path:
    path = (key_dir / filename).resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(
            f"缺少非空密钥文件：{path}\n"
            "请把主办方私发的 api_keys 文件夹整体替换到 code/api_keys 后重试。"
        )
    return path


def credential_paths(key_dir: Path) -> tuple[Path, Path]:
    """Validate credential presence without reading or printing secret values."""

    resolved = key_dir.expanduser().resolve()
    qwen = _credential_file(resolved, "qwen-api-key.txt")
    semantic_scholar = _credential_file(resolved, "semantic-scholar-api-key.txt")
    return qwen, semantic_scholar


def build_light_test_command(
    python: str | Path,
    *,
    question_file: Path,
    output_dir: Path,
) -> list[str]:
    """Build the bounded real-chain command used by the evaluator shortcut."""

    return [
        str(python),
        "-u",
        str(HARNESS_SCRIPT),
        "--question-file",
        str(question_file.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--run-id",
        output_dir.name,
        "--maximum-iterations",
        "1",
        "--maximum-initial-routes",
        "1",
        "--route-planning-maximum-routes",
        "1",
        "--max-rounds-per-route",
        "1",
        "--minimum-rounds-before-llm-stop",
        "1",
        "--no-control-route",
        "--maximum-refinement-rounds",
        "0",
        "--maximum-method-research-rounds",
        "1",
        "--method-research-wall-time-seconds",
        "180",
        "--s2-request-budget-seconds",
        "45",
        "--wall-time-seconds",
        "1800",
        "--task-compiler-tier",
        "turbo",
    ]


def build_full_test_command(
    python: str | Path,
    *,
    question_file: Path,
    output_dir: Path,
) -> list[str]:
    """Build the current default full-chain command for the local portal."""

    return [
        str(python),
        "-u",
        str(HARNESS_SCRIPT),
        "--question-file",
        str(question_file.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--run-id",
        output_dir.name,
        "--maximum-iterations",
        "6",
        "--maximum-initial-routes",
        "5",
        "--route-planning-maximum-routes",
        "4",
        "--max-rounds-per-route",
        "6",
        "--minimum-rounds-before-llm-stop",
        "2",
        "--wall-time-seconds",
        "10800",
    ]


def run_replay(*, port: int = 8765, no_open: bool = False) -> int:
    command = [sys.executable, "-u", str(REPLAY_SCRIPT), "--port", str(port)]
    if no_open:
        command.append("--no-open")
    print("正在启动六组完整记录的只读回放台；此功能不需要密钥。")
    return _run(command)


def run_portal(args: argparse.Namespace) -> int:
    if str(CODE_ROOT) not in sys.path:
        sys.path.insert(0, str(CODE_ROOT))
    from optomind_portal.local_runtime import (  # noqa: PLC0415
        LocalRuntimeController,
        serve_local_portal,
    )

    key_dir = Path(args.key_dir).expanduser().resolve()
    controller = LocalRuntimeController(
        project_root=ROOT,
        code_root=CODE_ROOT,
        key_dir=key_dir,
        local_run_root=LOCAL_RUN_ROOT,
        runtime_preparer=lambda: _prepare_runtime(
            use_current_env=bool(args.use_current_env)
        ),
        credential_resolver=credential_paths,
        light_command_builder=build_light_test_command,
        full_command_builder=build_full_test_command,
    )
    serve_local_portal(
        formal_output_root=OUTPUT_ROOT,
        local_output_root=LOCAL_RUN_ROOT,
        ui_root=CODE_ROOT / "replay_ui",
        controller=controller,
        port=int(args.port),
        open_browser=not bool(args.no_open),
    )
    return 0


def run_light_test(args: argparse.Namespace) -> int:
    question_file = Path(args.question_file).expanduser().resolve()
    if not question_file.is_file() or not question_file.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"测试题文件不存在或为空：{question_file}")

    qwen_key, s2_key = credential_paths(Path(args.key_dir))
    python = _prepare_runtime(use_current_env=bool(args.use_current_env))
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = LOCAL_RUN_ROOT / f"evaluator-smoke-{timestamp}"

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "QWEN_API_KEY_FILE": str(qwen_key),
            "DASHSCOPE_API_KEY_FILE": str(qwen_key),
            "SEMANTIC_SCHOLAR_API_KEYS_FILE": str(s2_key),
            "QWEN_HTTP_TIMEOUT_SEC": env.get("QWEN_HTTP_TIMEOUT_SEC", "60"),
        }
    )
    print("密钥文件已就绪（只传递文件路径，不输出密钥内容）。")
    print("轻量连通性测试采用 1 条路线、1 轮的有界参数，不改动六组正式回放记录。")
    print(f"测试题：{question_file}")
    print(f"新产物目录：{output_dir}")

    code = _run(
        build_light_test_command(
            python,
            question_file=question_file,
            output_dir=output_dir,
        ),
        env=env,
    )
    print(f"\n研究进程退出码：{code}")
    print(f"完整运行记录：{output_dir}")
    if code != 0:
        print("测试未形成 completed 终态；请保留目录并查看 RESEARCH_EVENTS.jsonl。")
        return code
    if args.no_replay:
        return 0
    print("测试完成，正在打开独立保存的六组正式静态回放。")
    return run_replay(port=int(args.port), no_open=False)


def run_doctor(args: argparse.Namespace) -> int:
    formal_runs = [
        "e2e-methane-swir-window-20260828-default4-w10800",
        "e2e-uav-swir-window-20260829-default4-w10800",
        "e2e-space-qkd-cband-window-20260829-default4-w10800",
        "e2e-solarblind-uv-window-20260829-default4-w10800",
        "e2e-fifth-dualgas-mwir-20260829-default4-w10800",
        "e2e-sixth-combustion-co-20260829-default4-w10800",
    ]
    missing = [name for name in formal_runs if not (OUTPUT_ROOT / name).is_dir()]
    if missing:
        print("缺少正式回放目录：" + "、".join(missing))
        return 2
    print("静态回放资产：6/6 组就绪。")
    try:
        credential_paths(Path(args.key_dir))
    except RuntimeError as exc:
        print(f"真实测试密钥：未就绪。{exc}")
    else:
        print("真实测试密钥：Qwen 与 Semantic Scholar 文件均非空（未读取内容）。")
    print(
        "当前 Python 运行依赖："
        + ("已就绪。" if _runtime_ready(sys.executable) else "未安装；首次真实测试将自动安装。")
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OptoMind-Article 评审快捷入口")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay", help="无需密钥，打开六组静态回放")
    replay.add_argument("--port", type=int, default=8765, help="监听端口；0 表示自动选择")
    replay.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    portal = subparsers.add_parser(
        "ui",
        aliases=["portal"],
        help="打开统一前端：静态回放与检查后激活的真实提问",
    )
    portal.add_argument("--key-dir", default=str(DEFAULT_KEY_DIR), help="密钥文件夹")
    portal.add_argument("--use-current-env", action="store_true", help="不自动创建隔离环境")
    portal.add_argument("--port", type=int, default=8765, help="监听端口；0 表示自动选择")
    portal.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    test = subparsers.add_parser("test", help="使用本地密钥执行轻量真实链路")
    test.add_argument("--key-dir", default=str(DEFAULT_KEY_DIR), help="密钥文件夹")
    test.add_argument("--question-file", default=str(DEFAULT_QUESTION_FILE), help="UTF-8 测试题")
    test.add_argument("--use-current-env", action="store_true", help="不自动创建隔离环境")
    test.add_argument("--no-replay", action="store_true", help="完成后不打开回放台")
    test.add_argument("--port", type=int, default=8765, help="完成后回放台端口")

    doctor = subparsers.add_parser("doctor", help="检查回放资产、密钥文件和运行依赖")
    doctor.add_argument("--key-dir", default=str(DEFAULT_KEY_DIR), help="密钥文件夹")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "replay":
            return run_replay(port=int(args.port), no_open=bool(args.no_open))
        if args.command in {"ui", "portal"}:
            return run_portal(args)
        if args.command == "test":
            return run_light_test(args)
        return run_doctor(args)
    except KeyboardInterrupt:
        print("\n已停止。")
        return 130
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(f"\n无法继续：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
