from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import keep

GitRepo = Callable[..., None]


@pytest.fixture(autouse=True)
def isolated_keep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Approvals, cases and session memories in scratch places, here and in every gate
    a test runs as a subprocess, never in the user's own."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setenv("QUALITY_GATE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(keep, "STATE", scratch / "claude-quality-gate")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty git repository with an identity, so commits work anywhere."""
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "gate@example.invalid")
    git(root, "config", "user.name", "gate tests")
    git(root, "config", "commit.gpgsign", "false")
    return root.resolve()


def commit(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
