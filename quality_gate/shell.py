"""Python that reaches a repository through the shell rather than through Edit or Write:
`sed -i`, a heredoc, a `cp` or `mv` from somewhere else, a code generator, a formatter.

Before each Bash command the gate records the content hash of every uncommitted Python
file in the repositories the session works in; after it, every Python file whose hash
differs, or that the record lacks, is one the command wrote. Those are gated. Comparing
against the record rather than against HEAD means a command is answerable only for what
it wrote, never for edits made, and gated, before it ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import placement

STATE = Path(tempfile.gettempdir()) / "claude-quality-gate"
# A record whose command never reported back (refused, interrupted) is cleared after this.
STALE_S = 24 * 3600

# Each `git status --porcelain` entry is two status letters and a space, then the path.
STATUS_WIDTH = 3

# {repository root: {path relative to it: content hash}}
Record = dict[str, dict[str, str]]


def roots(payload: dict) -> set[Path]:
    """The repositories the session works in: its current directory's and its project's."""
    places = [payload.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR")]
    found = (placement.repo_root(Path(p)) for p in places if p and Path(p).is_dir())
    return {root for root in found if root is not None}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def uncommitted_python(root: Path) -> dict[str, str]:
    """Every Python file git shows as changed or untracked, with its content hash.
    Deleted files are left out, and so are ignored ones, which git does not list."""
    out = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    entries = out.split("\0")
    rels = (entry[STATUS_WIDTH:] for entry in entries if len(entry) > STATUS_WIDTH)
    python = (rel for rel in rels if rel.endswith(".py") and (root / rel).is_file())
    return {rel: _hash(root / rel) for rel in python}


def _record_path(payload: dict) -> Path:
    name = hashlib.sha256(str(payload.get("tool_use_id", "")).encode()).hexdigest()[:32]
    return STATE / f"{name}.json"


def _clear_stale() -> None:
    cutoff = time.time() - STALE_S
    for old in STATE.glob("*.json"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)


def before(payload: dict) -> None:
    """Record the uncommitted Python of every repository the command could write to."""
    STATE.mkdir(parents=True, exist_ok=True)
    _clear_stale()
    record: Record = {str(root): uncommitted_python(root) for root in roots(payload)}
    _record_path(payload).write_text(json.dumps(record), encoding="utf-8")


def written(payload: dict) -> list[Path]:
    """The Python files the command wrote. With no record (the gate was installed while
    the command ran) nothing can be attributed to it, so nothing is."""
    path = _record_path(payload)
    try:
        record: Record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    path.unlink(missing_ok=True)
    files = []
    for root_name, seen in record.items():
        root = Path(root_name)
        now = uncommitted_python(root)
        files += [root / rel for rel, digest in sorted(now.items()) if seen.get(rel) != digest]
    return files
