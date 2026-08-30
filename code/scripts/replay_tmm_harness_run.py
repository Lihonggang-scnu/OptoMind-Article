"""Fresh-process replay and scientific comparison for one completed TMM run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optomind_optics.harness.replay import replay_completed_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--replay-subdir", default="fresh_replay")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    manifest = replay_completed_run(
        args.source_run,
        replay_subdir=args.replay_subdir,
        replace_existing=bool(args.replace),
    )
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if manifest.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
