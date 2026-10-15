"""Where a finding sits against HEAD: in new code, in old code this change touched, or in
old code elsewhere in the file.

New code is held to every rule. Old code is reported and never blocked on, so editing an
old file does not become a forced rewrite of it. A function is new when HEAD's copy of
the file has no function of that qualified name, and a finding outside every function is
new when its line changed. The innermost function around a line places it, so a function
added inside an old one is new.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

NEW = "new"
CHANGED = "changed"
ELSEWHERE = "elsewhere"

_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)

# (qualified name, first line, last line)
Span = tuple[str, int, int]


@dataclass(frozen=True)
class History:
    """What placing a finding needs to know about its file."""

    functions: tuple[Span, ...]
    # None when HEAD has no copy of the file, which makes all of it new.
    old_names: frozenset[str] | None
    changed: frozenset[int]


def spans(source: str) -> tuple[Span, ...]:
    """Every function in `source`. A decorator is part of its function, and methods are
    named through their class. Source that does not parse has none."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    found: list[Span] = []
    _walk(tree, "", found)
    return tuple(found)


def _walk(node: ast.AST, prefix: str, found: list[Span]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNCTION):
            first = min([child.lineno, *(d.lineno for d in child.decorator_list)])
            found.append((prefix + child.name, first, child.end_lineno or child.lineno))
        if isinstance(child, (*_FUNCTION, ast.ClassDef)):
            _walk(child, f"{prefix}{child.name}.", found)
        else:
            _walk(child, prefix, found)


def function_line(functions: tuple[Span, ...], name: str) -> int:
    """The first line of the function called `name`, or 1 when there is none."""
    return next((first for found, first, _ in functions if found == name), 1)


def _git(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def repo_root(directory: Path) -> Path | None:
    """The top of the git work tree holding `directory`, or None outside one."""
    out = _git(directory, "rev-parse", "--show-toplevel")
    return Path(out.strip()).resolve() if out else None


def is_ignored(path: Path, root: Path) -> bool:
    """Whether git ignores `path`. ruff's `--force-exclude` does not apply .gitignore to a
    file named on its command line, so the gate asks git itself."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(path)], cwd=root, capture_output=True, check=False
    )
    return result.returncode == 0


def changed_lines(diff: str) -> frozenset[int]:
    """The lines of the new copy a `-U0` diff touches. A pure deletion marks the line it
    follows, so removing lines from a function still touches it."""
    lines: set[int] = set()
    for match in _HUNK.finditer(diff):
        start, count = int(match[1]), int(match[2] or 1)
        lines.update(range(start, start + count) if count else [start])
    return frozenset(lines)


def history(path: Path, root: Path) -> History:
    """`path` on disk against its copy at HEAD in the repository at `root`."""
    rel = path.relative_to(root).as_posix()
    functions = spans(path.read_text(encoding="utf-8"))
    before = _git(root, "show", f"HEAD:{rel}")
    if before is None:
        return History(functions, None, frozenset())
    diff = _git(root, "diff", "--no-ext-diff", "--no-color", "-U0", "HEAD", "--", rel)
    old_names = frozenset(name for name, _, _ in spans(before))
    return History(functions, old_names, changed_lines(diff or ""))


def place(line: int, file: History) -> str:
    """NEW, CHANGED or ELSEWHERE for a finding on `line`."""
    if file.old_names is None:
        return NEW
    inside = [span for span in file.functions if span[1] <= line <= span[2]]
    if not inside:
        return NEW if line in file.changed else ELSEWHERE
    name, first, last = max(inside, key=lambda span: span[1])
    if name not in file.old_names:
        return NEW
    return CHANGED if any(first <= n <= last for n in file.changed) else ELSEWHERE
