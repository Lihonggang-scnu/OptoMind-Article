"""Agent Skill packaging tests (skills/veritmm-tmm + skill-path/install-skill).

The skill is the agent-facing product surface: its frontmatter must satisfy
the Agent Skills specification, its content must stay inside the MCP-exposed
capability set (MCP-first, CLI fallback), and the installation commands must
be safe (fixed source, explicit overwrite confirmation, no symlink targets).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tmm_engine.cli import main
from tmm_engine.skill_packaging import (
    SKILL_DIR_NAME,
    SkillInstallError,
    install_skill,
    skill_manifest,
    skill_source_dir,
)

MCP_TOOLS = {
    "describe",
    "schema",
    "examples",
    "preflight",
    "run",
    "history",
    "inspect",
    "lineage",
    "compare",
    "verify_run",
    "veritmm_discover",
    "veritmm_catalog_get",
    "veritmm_material_search",
    "veritmm_material_explain",
    "veritmm_stack_validate",
    "veritmm_simulate_spectrum",
    "veritmm_simulate_batch",
    "veritmm_gradient_compute",
    "veritmm_sensitivity_analyze",
    "veritmm_optimize_problem",
    "veritmm_verify_run",
    "veritmm_verify_explain",
    "veritmm_job_status",
    "veritmm_job_result",
    "veritmm_intent_compile",
}

# V-11 brought the gradient/sensitivity/batch/intent/design surfaces into the
# MCP namespace (veritmm_* tools), so they are no longer "outside MCP"; the
# sweep/tolerance contracts and operator escapes still are.
CAPABILITIES_OUTSIDE_MCP = (
    "sweep",
    "tolerance",
    "skip_certificate",
    "physics_python",
)


def test_skill_source_contains_the_full_package() -> None:
    source = skill_source_dir()
    assert (source / "SKILL.md").is_file()
    for name in ("protocol", "artifacts", "certificate", "boundaries"):
        assert (source / "references" / f"{name}.md").is_file()
    assert (source / "scripts" / "example_task.json").is_file()
    assert (source / "scripts" / "preflight_run_verify.py").is_file()


def test_skill_frontmatter_satisfies_the_specification() -> None:
    text = (skill_source_dir() / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md must open with a YAML frontmatter block"
    frontmatter = match.group(1)
    name = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    description = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    assert name and name.group(1) == SKILL_DIR_NAME
    assert re.fullmatch(r"[a-z0-9-]{1,64}", SKILL_DIR_NAME)
    assert description and 0 < len(description.group(1)) <= 1024
    assert "verify_run" in description.group(1)


def test_skill_content_prefers_mcp_and_exposes_no_extra_capabilities() -> None:
    skill_text = (skill_source_dir() / "SKILL.md").read_text(encoding="utf-8")
    for tool in MCP_TOOLS:
        assert tool in skill_text, f"SKILL.md must document MCP tool {tool}"

    # The usable-surface sections (tool table + call order) must not offer any
    # capability outside the MCP set; the boundaries section may name them in
    # an exclusion context.
    usable = "\n".join(
        section
        for section in skill_text.split("\n## ")
        if section.startswith(("Preferred surface", "Call order"))
    )
    for capability in CAPABILITIES_OUTSIDE_MCP:
        if capability == "run":
            continue
        assert capability not in usable.lower(), (
            f"the usable-surface sections must not offer {capability}"
        )
    # The skill must state the MCP-first, CLI-fallback order explicitly.
    assert "Preferred surface" in skill_text
    assert "CLI fallback" in skill_text


def test_skill_scripts_compile(tmp_path: Path) -> None:
    import py_compile

    script = skill_source_dir() / "scripts" / "preflight_run_verify.py"
    py_compile.compile(str(script), cfile=str(tmp_path / "compiled.pyc"), doraise=True)


def test_skill_path_cli_reports_the_packaged_directory(capsys: pytest.CaptureFixture) -> None:
    assert main(["skill-path", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["skill_name"] == SKILL_DIR_NAME
    assert Path(payload["skill_path"]) == skill_source_dir()
    assert "SKILL.md" in payload["files"]

    assert main(["skill-path"]) == 0
    plain = capsys.readouterr().out.strip()
    assert Path(plain) == skill_source_dir()


def test_install_skill_round_trip_into_a_host_layout(tmp_path: Path) -> None:
    host_skills = tmp_path / "host" / ".claude" / "skills"
    result = install_skill(host_skills)
    installed = host_skills / SKILL_DIR_NAME
    assert result["destination"] == str(installed)
    assert result["overwritten"] is False
    # Hosts discover a folder named veritmm-tmm containing SKILL.md.
    assert (installed / "SKILL.md").is_file()
    assert (installed / "references" / "certificate.md").is_file()
    source_manifest = skill_manifest()
    assert sorted(result["files"]) == sorted(source_manifest["files"])
    # Relative targets resolve against the working directory.
    relative = install_skill(tmp_path / "relative-host")
    assert (Path(relative["destination"]) / "SKILL.md").is_file()


def test_install_skill_requires_explicit_overwrite(tmp_path: Path) -> None:
    host_skills = tmp_path / "skills"
    install_skill(host_skills)
    with pytest.raises(SkillInstallError, match="--force"):
        install_skill(host_skills)
    assert (host_skills / SKILL_DIR_NAME / "SKILL.md").is_file()
    overwritten = install_skill(host_skills, force=True)
    assert overwritten["overwritten"] is True


def test_install_skill_refuses_symlinked_targets(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:  # pragma: no cover - Windows without symlink privilege
        pytest.skip("symbolic links are not available on this host")
    with pytest.raises(SkillInstallError, match="symbolic link"):
        install_skill(link)


def test_install_skill_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    host_skills = tmp_path / "skills"
    assert main(["install-skill", str(host_skills), "--json"]) == 0
    conflict = main(["install-skill", str(host_skills), "--json"])
    assert conflict == 2
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "--force" in payload["error"]
    assert main(["install-skill", str(host_skills), "--force", "--json"]) == 0
