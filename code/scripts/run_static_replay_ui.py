"""Launch the read-only OptoMind static evidence replay."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_REPLAY_MODULE = PROJECT_ROOT / "optomind_optics" / "harness" / "static_replay.py"


def _load_serve_static_replay():
    """Load the standard-library replay module without importing the harness."""

    spec = importlib.util.spec_from_file_location(
        "optomind_static_replay_launcher", STATIC_REPLAY_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入静态回放模块：{STATIC_REPLAY_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.serve_static_replay


def _default_port() -> int:
    try:
        return int(os.environ.get("PORT", "8765"))
    except ValueError:
        return 8765


def _default_output_root() -> Path:
    configured = str(os.environ.get("OPTOMIND_OUTPUT_ROOT") or "").strip()
    return (
        Path(configured)
        if configured
        else PROJECT_ROOT / "outputs" / "tmm_research_harness"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=_default_output_root(),
        help="Directory containing the completed research-run archives",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("OPTOMIND_HOST", "127.0.0.1"),
        help="Listening address; the default exposes the replay only locally",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_default_port(),
        help="Listening port; 0 selects an available port",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a local browser automatically",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    host = str(args.host)
    serve_static_replay = _load_serve_static_replay()
    serve_static_replay(
        output_root=args.output_root,
        ui_root=PROJECT_ROOT / "replay_ui",
        host=host,
        port=int(args.port),
        open_browser=not bool(args.no_open)
        and host in {"127.0.0.1", "localhost", "::1"},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
