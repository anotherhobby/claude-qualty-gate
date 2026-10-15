"""The gate end to end: the pinned checkers run for real, through uvx, on scratch repos.

The first run downloads the checkers; after that each test takes about as long as
pyright does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from conftest import commit

from gate import check
from placement import CHANGED, ELSEWHERE, NEW

GATE_DIR = Path(__file__).resolve().parent.parent / "quality_gate"

CLEAN = textwrap.dedent(
    """\
    def double(value: int) -> int:
        return value * 2
    """
)

# Nine branches nested five deep: over the cognitive limit and the nesting limit.
TANGLED = textwrap.dedent(
    """\


    def tangled(a: int, b: int, c: int) -> int:
        total = 0
        for i in range(a):
            if i % 2:
                for j in range(b):
                    if j % 3:
                        while c > 0:
                            if c % 5:
                                total += 1
                            elif c % 7:
                                total -= 1
                            c -= 1
        return total
    """
)

NOQA_ON_A_SIZE_RULE = textwrap.dedent(
    """\


    def many(a, b, c, d, e, f, g, h, i):  # noqa: PLR0913, PLR0917
        return a, b, c, d, e, f, g, h, i
    """
)


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_a_clean_file_has_nothing(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert not any(outcome.placed.values())
    assert outcome.errors == []


def test_a_new_tangled_function_blocks(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + TANGLED)
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert {"cognitive-complexity", "PLR1702"} <= rules(outcome.blocking)
    assert outcome.errors == []


def test_the_same_function_already_at_head_is_reported_not_blocked(repo: Path):
    commit(repo, {"mod.py": CLEAN + TANGLED})
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert outcome.blocking == []
    assert "cognitive-complexity" in rules(outcome.placed[ELSEWHERE])


def test_touching_an_old_function_reports_it_as_changed(repo: Path):
    commit(repo, {"mod.py": CLEAN + TANGLED})
    edited = (CLEAN + TANGLED).replace("total = 0", "total = 1")
    (repo / "mod.py").write_text(edited)
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert outcome.blocking == []
    assert "cognitive-complexity" in rules(outcome.placed[CHANGED])


def test_noqa_does_not_silence_a_size_rule(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + NOQA_ON_A_SIZE_RULE)
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert {"PLR0913", "PLR0917"} <= rules(outcome.blocking)


def test_noqa_still_silences_other_rules(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + "\n\nimport os  # noqa: F401, E402\n")
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert "F401" not in rules(outcome.blocking)


def test_pyright_errors_in_new_code_block(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    unchecked = "\n\ndef half(x: int | None) -> float:\n    return x / 2\n"
    (repo / "mod.py").write_text(CLEAN + unchecked)
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert "reportOptionalOperand" in rules(outcome.blocking)


def test_the_projects_own_limits_apply(repo: Path):
    config = "[tool.ruff.lint.pylint]\nmax-args = 12\nmax-positional-args = 12\n"
    commit(repo, {"mod.py": CLEAN, "pyproject.toml": config})
    (repo / "mod.py").write_text(CLEAN + NOQA_ON_A_SIZE_RULE)
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert not {"PLR0913", "PLR0917"} & rules(outcome.blocking)


def test_a_gitignored_file_is_out_of_scope(repo: Path):
    commit(repo, {".gitignore": "scratch/\n"})
    (repo / "scratch").mkdir()
    (repo / "scratch" / "tmp.py").write_text(TANGLED)
    assert check(repo / "scratch" / "tmp.py") is None


def test_a_file_the_configuration_excludes_is_out_of_scope(repo: Path):
    commit(repo, {"pyproject.toml": "[tool.ruff]\nextend-exclude = ['vendor']\n"})
    (repo / "vendor").mkdir()
    (repo / "vendor" / "lib.py").write_text(TANGLED)
    assert check(repo / "vendor" / "lib.py") is None


def test_the_baseline_spares_tests_magic_values_only(repo: Path):
    body = "\n\ndef check(x: int) -> bool:\n    return x == 42\n"
    commit(repo, {"keep.py": CLEAN})
    for rel in ("src/mod.py", "tests/test_mod.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(CLEAN + body)
    src, tests = check(repo / "src" / "mod.py"), check(repo / "tests" / "test_mod.py")
    assert src is not None
    assert tests is not None
    assert "PLR2004" in rules(src.blocking)
    assert "PLR2004" not in rules(tests.blocking)


def test_a_file_outside_git_is_out_of_scope(tmp_path: Path):
    path = tmp_path / "loose.py"
    path.write_text(CLEAN + TANGLED)
    assert check(path) is None


def test_no_cache_is_left_in_the_project(repo: Path):
    commit(repo, {"mod.py": CLEAN + TANGLED})
    check(repo / "mod.py")
    assert sorted(p.name for p in repo.iterdir()) == [".git", "mod.py"]


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE_DIR), "--hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_hook_blocks_through_its_json_answer(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + TANGLED)
    ran = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(repo / "mod.py")}})
    assert ran.returncode == 0, ran.stderr
    answer = json.loads(ran.stdout)
    assert answer["decision"] == "block"
    assert "mod.py:5: cognitive-complexity" in answer["reason"]


def test_the_hook_ignores_files_that_are_not_python(repo: Path):
    commit(repo, {"notes.md": "# notes\n"})
    ran = run_hook({"tool_name": "Write", "tool_input": {"file_path": str(repo / "notes.md")}})
    assert (ran.returncode, ran.stdout) == (0, "")


def test_the_hook_resolves_a_relative_path_against_the_sessions_cwd(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + TANGLED)
    ran = run_hook({"cwd": str(repo), "tool_input": {"file_path": "mod.py"}})
    assert json.loads(ran.stdout)["decision"] == "block"


def test_new_findings_are_placed_new(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + TANGLED)
    outcome = check(repo / "mod.py")
    assert outcome is not None
    assert outcome.placed[NEW] == outcome.blocking
