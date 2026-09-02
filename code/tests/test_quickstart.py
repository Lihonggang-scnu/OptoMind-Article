from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("optomind_quickstart", ROOT / "quickstart.py")
assert SPEC is not None and SPEC.loader is not None
QUICKSTART = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUICKSTART)


def test_credential_paths_only_require_nonempty_expected_files(tmp_path: Path) -> None:
    (tmp_path / "qwen-api-key.txt").write_text("placeholder-qwen\n", encoding="utf-8")
    (tmp_path / "semantic-scholar-api-key.txt").write_text(
        "placeholder-s2\n", encoding="utf-8"
    )

    qwen, s2 = QUICKSTART.credential_paths(tmp_path)

    assert qwen.name == "qwen-api-key.txt"
    assert s2.name == "semantic-scholar-api-key.txt"


def test_credential_paths_reject_empty_file(tmp_path: Path) -> None:
    (tmp_path / "qwen-api-key.txt").write_text("", encoding="utf-8")
    (tmp_path / "semantic-scholar-api-key.txt").write_text("value\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="qwen-api-key.txt"):
        QUICKSTART.credential_paths(tmp_path)


def test_light_command_is_bounded_and_keeps_question_out_of_cli(tmp_path: Path) -> None:
    question = tmp_path / "question.txt"
    output = tmp_path / "output"

    command = QUICKSTART.build_light_test_command(
        "python", question_file=question, output_dir=output
    )

    assert "--question-file" in command
    assert str(question.resolve()) in command
    assert "--maximum-iterations" in command
    assert command[command.index("--maximum-iterations") + 1] == "1"
    assert command[command.index("--route-planning-maximum-routes") + 1] == "1"
    assert command[command.index("--max-rounds-per-route") + 1] == "1"
    assert "--no-control-route" in command
    assert not any("近红外" in item for item in command)


def test_full_command_uses_current_default_research_limits(tmp_path: Path) -> None:
    question = tmp_path / "question.txt"
    output = tmp_path / "output"

    command = QUICKSTART.build_full_test_command(
        "python", question_file=question, output_dir=output
    )

    assert command[command.index("--route-planning-maximum-routes") + 1] == "4"
    assert command[command.index("--maximum-initial-routes") + 1] == "5"
    assert command[command.index("--max-rounds-per-route") + 1] == "6"
    assert command[command.index("--minimum-rounds-before-llm-stop") + 1] == "2"
    assert command[command.index("--wall-time-seconds") + 1] == "10800"
    assert str(question.resolve()) in command
    assert not any("近红外" in item for item in command)
