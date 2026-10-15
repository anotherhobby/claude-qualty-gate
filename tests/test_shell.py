from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit

import shell

GATE_DIR = Path(__file__).resolve().parent.parent / "quality_gate"
CLEAN = "def double(value: int) -> int:\n    return value * 2\n"
SIX_POSITIONAL = "\n\ndef mix(a, b, c, d, e, f):\n    return a + b + c + d + e + f\n"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "STATE", tmp_path / "state")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def payload(repo: Path, call: str = "toolu_1") -> dict:
    return {"cwd": str(repo), "tool_use_id": call, "tool_name": "Bash"}


def test_only_what_the_command_wrote_is_attributed_to_it(repo: Path):
    commit(repo, {"clean.py": CLEAN, "touched.py": CLEAN, "dirty.py": CLEAN})
    (repo / "dirty.py").write_text(CLEAN + "# edited before the command\n")
    (repo / "already_new.py").write_text(CLEAN)
    shell.before(payload(repo))
    (repo / "touched.py").write_text(CLEAN + SIX_POSITIONAL)
    (repo / "copied_in.py").write_text(CLEAN)
    assert shell.written(payload(repo)) == [repo / "copied_in.py", repo / "touched.py"]


def test_a_file_already_dirty_that_the_command_changes_again_is_attributed(repo: Path):
    commit(repo, {"dirty.py": CLEAN})
    (repo / "dirty.py").write_text(CLEAN + "# first\n")
    shell.before(payload(repo))
    (repo / "dirty.py").write_text(CLEAN + "# second\n")
    assert shell.written(payload(repo)) == [repo / "dirty.py"]


def test_deleted_ignored_and_non_python_files_are_not(repo: Path):
    commit(repo, {".gitignore": "build/\n", "gone.py": CLEAN})
    shell.before(payload(repo))
    (repo / "gone.py").unlink()
    (repo / "build").mkdir()
    (repo / "build" / "generated.py").write_text(CLEAN)
    (repo / "notes.md").write_text("# notes\n")
    assert shell.written(payload(repo)) == []


def test_without_a_record_nothing_is_attributed(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    (repo / "mod.py").write_text(CLEAN + SIX_POSITIONAL)
    assert shell.written(payload(repo)) == []


def test_a_record_is_used_once(repo: Path):
    commit(repo, {"mod.py": CLEAN})
    shell.before(payload(repo))
    (repo / "mod.py").write_text(CLEAN + SIX_POSITIONAL)
    assert shell.written(payload(repo)) == [repo / "mod.py"]
    assert shell.written(payload(repo)) == []


def test_the_project_directory_is_watched_as_well_as_the_cwd(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    commit(repo, {"mod.py": CLEAN})
    shell.before(payload(elsewhere))
    (repo / "mod.py").write_text(CLEAN + SIX_POSITIONAL)
    assert shell.written(payload(elsewhere)) == [repo / "mod.py"]


def run(mode: str, body: dict, state: Path) -> subprocess.CompletedProcess:
    # The real entry point, in a child, with TMPDIR pointing its record at the test's.
    return subprocess.run(
        [sys.executable, str(GATE_DIR), mode],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": shell.os.environ["PATH"], "TMPDIR": str(state)},
    )


def test_a_failed_command_that_wrote_python_is_still_gated(repo: Path, tmp_path: Path):
    commit(repo, {"mod.py": CLEAN})
    state = tmp_path / "tmp"
    state.mkdir()
    body = payload(repo, "toolu_failed")
    assert run("--shell-before", {**body, "hook_event_name": "PreToolUse"}, state).stdout == ""
    (repo / "mod.py").write_text(CLEAN + SIX_POSITIONAL)
    ran = run("--shell-after", {**body, "hook_event_name": "PostToolUseFailure"}, state)
    answer = json.loads(ran.stdout)
    assert answer["decision"] == "block"
    assert "mod.py:5: PLR0917" in answer["reason"]


def test_a_command_that_wrote_no_python_says_nothing(repo: Path, tmp_path: Path):
    commit(repo, {"mod.py": CLEAN})
    state = tmp_path / "tmp"
    state.mkdir()
    body = payload(repo, "toolu_quiet")
    run("--shell-before", body, state)
    (repo / "notes.md").write_text("# notes\n")
    ran = run("--shell-after", {**body, "hook_event_name": "PostToolUse"}, state)
    assert (ran.returncode, ran.stdout) == (0, "")
