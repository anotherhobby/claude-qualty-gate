"""The languages the gate holds, each a backend: the files it owns, how it reads their
functions, and the checkers it runs as a file is written and at Stop.

Everything else (placement against HEAD, keeping a size finding, session memory, the
hooks and what they say) is the same for every language.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import checks
import placement
import swift
import swift_build
from checks import Checker, Target

PYTHON_SIZE_RULES = frozenset({
    "C901",
    "PLR0911",
    "PLR0912",
    "PLR0913",
    "PLR0915",
    "PLR0917",
    "PLR1702",
    checks.COGNITIVE_RULE,
})


def _always(_target: Target) -> bool:
    return True


@dataclass(frozen=True)
class Language:
    name: str
    suffixes: frozenset[str]
    spans: placement.SpansOf
    checkers: tuple[Checker, ...]
    # The rules that measure size, which may be kept with a case.
    size_rules: frozenset[str]
    # Whether the file is left in by the language's own configuration.
    in_scope: Callable[[Target], bool] = _always
    # Checked at Stop, once the agent is done: a whole-program check such as a build.
    stop_checkers: tuple[Checker, ...] = ()
    # The files to check at Stop beyond those the session wrote, given those.
    stop_paths: Callable[[list[Path]], list[Path]] | None = None
    # How to fix a tool's findings mechanically, by tool: the command, given the file.
    fixers: tuple[tuple[str, Callable[[Target], str]], ...] = ()


PYTHON = Language(
    name="Python",
    suffixes=frozenset({".py"}),
    spans=placement.spans,
    checkers=checks.CHECKERS,
    size_rules=PYTHON_SIZE_RULES,
    in_scope=checks.in_scope,
)

SWIFT = Language(
    name="Swift",
    suffixes=frozenset({".swift"}),
    spans=swift.spans,
    checkers=(swift.swiftlint, swift.syntax, swift.swift_format, swift.manifest),
    size_rules=swift.SIZE_RULES,
    stop_checkers=(swift_build.compiler,),
    stop_paths=swift_build.stop_paths,
    fixers=(("swift-format", swift.format_command),),
)

LANGUAGES = (PYTHON, SWIFT)
SIZE_RULES = frozenset().union(*(language.size_rules for language in LANGUAGES))


def for_path(path: Path | str) -> Language | None:
    """The language that holds `path`, or None when the gate does not check it."""
    suffix = Path(path).suffix
    return next((language for language in LANGUAGES if suffix in language.suffixes), None)
