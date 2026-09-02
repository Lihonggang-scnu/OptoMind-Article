"""Export the formal OptoMind replay as a compact GitHub Pages site.

The source archives contain numerical arrays and optimizer state that are useful
for full reproduction but unnecessary for browser replay.  This exporter writes
the same read-only projection used by the local server plus every text artifact
linked by the interface.  It never calls an LLM, literature service, optimizer,
or VeriTMM.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODE_ROOT = PROJECT_ROOT / "code"
UI_ROOT = CODE_ROOT / "replay_ui"
FORMAL_OUTPUT_ROOT = CODE_ROOT / "outputs" / "tmm_research_harness"
STATIC_REPLAY_MODULE = CODE_ROOT / "optomind_optics" / "harness" / "static_replay.py"


def _load_replay_catalog_type() -> type[Any]:
    spec = importlib.util.spec_from_file_location(
        "optomind_static_replay_export", STATIC_REPLAY_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入静态回放模块：{STATIC_REPLAY_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ReplayCatalog


def _json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _artifact_paths(run: Mapping[str, Any]) -> list[str]:
    paths: set[str] = {"FINAL_ANSWER.md"}
    for stage in run.get("evidence") or []:
        if not isinstance(stage, Mapping):
            continue
        for item in stage.get("files") or []:
            if isinstance(item, Mapping) and item.get("path"):
                paths.add(str(item["path"]))
    for route in run.get("routes") or []:
        if not isinstance(route, Mapping):
            continue
        for round_row in route.get("rounds") or []:
            if not isinstance(round_row, Mapping):
                continue
            for item in round_row.get("raw_files") or []:
                if isinstance(item, Mapping) and item.get("path"):
                    paths.add(str(item["path"]))
    return sorted(paths)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _prepare_output(output_dir: Path) -> Path:
    resolved = output_dir.expanduser().resolve()
    forbidden = {
        PROJECT_ROOT.resolve(),
        CODE_ROOT.resolve(),
        UI_ROOT.resolve(),
        FORMAL_OUTPUT_ROOT.resolve(),
        Path(resolved.anchor).resolve(),
    }
    if resolved in forbidden or PROJECT_ROOT.resolve().is_relative_to(resolved):
        raise RuntimeError(f"拒绝清理过宽的静态站点目录：{resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    return resolved


def build_site(*, output_dir: Path, custom_domain: str = "") -> dict[str, Any]:
    ReplayCatalog = _load_replay_catalog_type()
    catalog = ReplayCatalog(FORMAL_OUTPUT_ROOT)
    run_ids = catalog.discover_run_ids()
    if not run_ids:
        raise RuntimeError(f"没有在 {FORMAL_OUTPUT_ROOT} 发现正式运行")

    target = _prepare_output(output_dir)
    (target / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(UI_ROOT / "index.html", target / "index.html")
    shutil.copy2(UI_ROOT / "index.html", target / "404.html")
    shutil.copy2(UI_ROOT / "assets" / "app.js", target / "assets" / "app.js")
    shutil.copy2(UI_ROOT / "assets" / "styles.css", target / "assets" / "styles.css")
    (target / "assets" / "config.js").write_text(
        "\n".join(
            [
                '"use strict";',
                "window.OPTOMIND_PORTAL_CONFIG = Object.freeze({",
                '  mode: "static",',
                '  catalogUrl: "data/catalog.json",',
                '  runUrlTemplate: "data/runs/{run_id}.json",',
                '  artifactBase: "artifacts",',
                "  liveEnabled: false,",
                "});",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (target / ".nojekyll").write_text("", encoding="utf-8")
    if custom_domain.strip():
        (target / "CNAME").write_text(custom_domain.strip() + "\n", encoding="utf-8")

    catalog_payload = catalog.catalog()
    _json(target / "data" / "catalog.json", catalog_payload)
    copied_artifacts = 0
    missing_artifacts: list[str] = []
    for run_id in run_ids:
        run = catalog.get_run(run_id)
        _json(target / "data" / "runs" / f"{run_id}.json", run)
        for relative in _artifact_paths(run):
            try:
                source = catalog.resolve_artifact(run_id, relative)
            except FileNotFoundError:
                missing_artifacts.append(f"{run_id}/{relative}")
                continue
            destination = target / "artifacts" / run_id / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied_artifacts += 1
    if missing_artifacts:
        preview = "、".join(missing_artifacts[:8])
        raise RuntimeError(
            f"静态回放引用了 {len(missing_artifacts)} 个不存在的只读产物：{preview}"
        )

    manifest_files = []
    total_bytes = 0
    for path in _files(target):
        relative = path.relative_to(target).as_posix()
        size = path.stat().st_size
        total_bytes += size
        manifest_files.append(
            {"path": relative, "bytes": size, "sha256": _hash_file(path)}
        )
    manifest = {
        "schema_version": "optomind-github-pages-replay.v1",
        "mode": "read_only_static_evidence",
        "formal_runs": run_ids,
        "run_count": len(run_ids),
        "copied_artifacts": copied_artifacts,
        "missing_artifacts": [],
        "total_bytes_before_manifest": total_bytes,
        "files": manifest_files,
    }
    _json(target / "STATIC_SITE_MANIFEST.json", manifest)
    manifest["output_dir"] = str(target)
    manifest["total_bytes"] = total_bytes + (target / "STATIC_SITE_MANIFEST.json").stat().st_size
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "_site",
        help="Generated GitHub Pages directory",
    )
    parser.add_argument(
        "--custom-domain",
        default="",
        help="Optional custom domain written to CNAME",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = build_site(
        output_dir=args.output_dir,
        custom_domain=str(args.custom_domain),
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "output_dir": manifest["output_dir"],
                "run_count": manifest["run_count"],
                "copied_artifacts": manifest["copied_artifacts"],
                "total_bytes": manifest["total_bytes"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
