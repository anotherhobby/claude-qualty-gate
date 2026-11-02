"""Check one file with every checker of its language and place each finding against HEAD."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import checks
import languages
import placement
from checks import BY_BUILD, BY_FILE, Checker, Finding, Target
from placement import CHANGED, ELSEWHERE, NEW
from tools import ToolError


@dataclass
class Outcome:
    target: Target
    placed: dict[str, list[Finding]] = field(
        default_factory=lambda: {NEW: [], CHANGED: [], ELSEWHERE: []}
    )
    errors: list[str] = field(default_factory=list)
    # Every function in the file as it is on disk.
    functions: tuple[placement.Span, ...] = ()
    # False when the project only reports old functions the change touched.
    touched_block: bool = True

    @property
    def rel(self) -> str:
        return self.target.path.relative_to(self.target.root).as_posix()

    @property
    def blocking(self) -> list[Finding]:
        """Findings in new code and, unless the project only reports them, in every old
        function this change touched."""
        touched = self.placed[CHANGED] if self.touched_block else []
        return sorted([*self.placed[NEW], *touched], key=lambda f: (f.line, f.rule))


def _collect(
    target: Target, functions: tuple[placement.Span, ...], checkers: tuple[Checker, ...]
) -> tuple[list, list]:
    """Every checker at once: the slowest alone takes most of the time."""
    findings: list[Finding] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(checkers)) as pool:
        running = [pool.submit(checker, target, functions) for checker in checkers]
        for future in running:
            try:
                findings.extend(future.result())
            except ToolError as error:
                errors.append(str(error))
    return findings, errors


def _place(finding: Finding, file: placement.History) -> str:
    if finding.scope == BY_BUILD:
        return NEW
    if finding.scope == BY_FILE:
        return placement.place_file(file)
    return placement.place(finding.line, file)


def check(path: Path, *, stop: bool = False) -> Outcome | None:
    """`path` checked and placed, or None when it is out of scope: in no language the
    gate holds, outside a git repository, where scratch files live and nothing can be told
    new, ignored by git, or excluded by its language's configuration. At Stop, `stop`, the
    language's whole-program checks run too."""
    language = languages.for_path(path)
    path = path.resolve()
    root = placement.repo_root(path.parent)
    if language is None or root is None or placement.is_ignored(path, root):
        return None
    target = Target(path, root)
    outcome = Outcome(target)
    try:
        if not language.in_scope(target):
            return None
    except ToolError as error:
        outcome.errors.append(str(error))
        return outcome
    outcome.touched_block = checks.touched_functions_block(target)
    file = placement.history(path, root, language.spans)
    outcome.functions = file.functions
    checkers = language.checkers + (language.stop_checkers if stop else ())
    findings, outcome.errors = _collect(target, file.functions, checkers)
    for finding in sorted(findings, key=lambda f: (f.line, f.rule)):
        outcome.placed[_place(finding, file)].append(finding)
    return outcome
