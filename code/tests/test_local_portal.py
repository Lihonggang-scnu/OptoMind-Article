from __future__ import annotations

from pathlib import Path

import pytest

from optomind_portal.local_runtime import LocalRuntimeController, serve_local_portal


def _controller(tmp_path: Path) -> LocalRuntimeController:
    return LocalRuntimeController(
        project_root=tmp_path,
        code_root=tmp_path / "code",
        key_dir=tmp_path / "keys",
        local_run_root=tmp_path / "runs",
        runtime_preparer=lambda: Path("python"),
        credential_resolver=lambda _path: (Path("qwen.txt"), Path("s2.txt")),
        light_command_builder=lambda *_args, **_kwargs: ["python"],
        full_command_builder=lambda *_args, **_kwargs: ["python"],
    )


def test_real_question_is_locked_before_diagnostics(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    snapshot = controller.snapshot()

    assert snapshot["live_enabled"] is True
    assert snapshot["diagnostics"]["ready"] is False
    with pytest.raises(RuntimeError, match="尚未全部通过"):
        controller.start_run(
            question="请设计一个具有两个目标波段的平面多层介质膜。",
            profile="quick",
        )


def test_public_listener_is_rejected_before_server_creation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="只能监听本机回环地址"):
        serve_local_portal(
            formal_output_root=tmp_path / "formal",
            local_output_root=tmp_path / "local",
            ui_root=tmp_path / "ui",
            controller=_controller(tmp_path),
            host="0.0.0.0",
            open_browser=False,
        )
