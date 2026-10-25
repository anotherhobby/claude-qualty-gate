"""The checkers the gate runs over one file, and what each reported.

ruff's size and shape rules and complexipy hold how big and how tangled a function may
get; ruff's wider rule set holds style and likely bugs; pyright holds types.

Configuration is the project's where it has its own and the gate's baseline where it has
none, looked for from the file's directory up to the repository root: a project's ruff
configuration replaces the baseline whole, and can `extend` it to keep most of it. The
size and shape rules are always run, and run with inline suppressions ignored, so a
`# noqa` cannot silence them: a limit that is wrong is changed in configuration, where the
change is seen. The limits they run at are the configuration's.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from placement import Span, function_line
from tools import ToolError, uvx

GATE_RULES = ",".join((
    "C901",  # cyclomatic complexity
    "PLR0911",  # too many returns
    "PLR0912",  # too many branches
    "PLR0913",  # too many arguments
    "PLR0915",  # too many statements
    "PLR0917",  # too many positional arguments
    "PLR1702",  # too many nested blocks
    "PLR5501",  # else: if -> elif
    "RET",  # no else after return, raise, continue or break
    "SIM",  # simplifications
))
COGNITIVE_RULE = "cognitive-complexity"
# Cognitive complexity per function, the metric complexipy measures: stricter than the
# limit of 15 in SonarSource's rule S3776.
DEFAULT_COGNITIVE_LIMIT = 10
BASELINE = Path(__file__).resolve().parent.parent / "baseline" / "ruff.toml"
# The first run of a pinned checker downloads it.
TIMEOUT_S = 150


@dataclass(frozen=True)
class Finding:
    tool: str
    rule: str
    line: int
    message: str


@dataclass(frozen=True)
class Target:
    """The file being checked, and the root its checkers run from."""

    path: Path
    root: Path


def _dirs_up(target: Target) -> Iterator[Path]:
    """The file's directory and each parent, up to and including the root."""
    current = target.path.parent
    while True:
        yield current
        if current in (target.root, current.parent):
            return
        current = current.parent


def _tool_table(pyproject: Path, name: str) -> dict | None:
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data.get("tool", {}).get(name)


def _has_ruff_config(directory: Path) -> bool:
    if (directory / "ruff.toml").is_file() or (directory / ".ruff.toml").is_file():
        return True
    return _tool_table(directory / "pyproject.toml", "ruff") is not None


def has_own_ruff_config(target: Target) -> bool:
    return any(_has_ruff_config(directory) for directory in _dirs_up(target))


def cognitive_limit(target: Target) -> int:
    """The nearest `[tool.complexipy] max-complexity-allowed`, or the default."""
    for directory in _dirs_up(target):
        table = _tool_table(directory / "pyproject.toml", "complexipy") or {}
        if "max-complexity-allowed" in table:
            return int(table["max-complexity-allowed"])
    return DEFAULT_COGNITIVE_LIMIT


def touched_functions_block(target: Target) -> bool:
    """Whether old functions a change touches block, from the nearest
    `[tool.quality-gate] touched-functions`: "block", the default, or "report". Any other
    value blocks."""
    for directory in _dirs_up(target):
        table = _tool_table(directory / "pyproject.toml", "quality-gate") or {}
        if "touched-functions" in table:
            return table["touched-functions"] != "report"
    return True


def _run(cmd: list[str], cwd: Path, allowed: tuple[int, ...] = (0, 1)) -> str:
    name = cmd[1].split("@")[0]
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{name} did not finish in {TIMEOUT_S} s") from None
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise ToolError(f"{name} exited {result.returncode}: {detail}")
    return result.stdout


def _ruff(target: Target) -> list[str]:
    # No cache: a cache folder left in every project the gate visits is litter.
    cmd = [*uvx("ruff"), "check", "--force-exclude", "--no-cache", "--output-format", "json"]
    if not has_own_ruff_config(target):
        cmd += ["--config", str(BASELINE)]
    return cmd


def in_scope(target: Target) -> bool:
    """Whether the configuration's `exclude` leaves the file in."""
    listed = _run([*_ruff(target), "--show-files", str(target.path)], target.root)
    return bool(listed.strip())


def _from_ruff(item: dict) -> Finding:
    rule = item.get("code") or "syntax-error"
    return Finding("ruff", rule, item["location"]["row"], item["message"])


def ruff_general(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """Every rule the configuration selects except the size and shape rules."""
    cmd = [*_ruff(target), "--ignore", GATE_RULES, str(target.path)]
    return [_from_ruff(item) for item in json.loads(_run(cmd, target.root) or "[]")]


def ruff_gate(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """The size and shape rules, whatever the configuration selects, `noqa` ignored."""
    cmd = [
        *_ruff(target),
        "--preview",
        "--config",
        "lint.explicit-preview-rules = true",
        "--ignore-noqa",
        "--select",
        GATE_RULES,
        str(target.path),
    ]
    return [_from_ruff(item) for item in json.loads(_run(cmd, target.root) or "[]")]


def complexipy(target: Target, functions: tuple[Span, ...]) -> list[Finding]:
    limit = cognitive_limit(target)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "complexipy.json"
        cmd = [
            *uvx("complexipy"),
            "--failed",
            "--no-ignore",
            "--quiet",
            "--max-complexity-allowed",
            str(limit),
            "--cache-dir",
            str(Path(tmp) / "cache"),
            "--output-format",
            "json",
            "--output",
            str(out),
            str(target.path),
        ]
        _run(cmd, target.root)
        items = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    findings = []
    for item in items:
        name = item["function_name"].replace("::", ".")
        message = f"{name} has cognitive complexity {item['complexity']} (limit {limit})"
        line = function_line(functions, name)
        findings.append(Finding("complexipy", COGNITIVE_RULE, line, message))
    return findings


def pyright(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """pyright's errors, with the project's own `.venv` resolving imports when it has one."""
    cmd = [*uvx("pyright"), "--outputjson"]
    venv_python = target.root / ".venv" / "bin" / "python"
    if venv_python.exists():
        cmd += ["--pythonpath", str(venv_python)]
    out = _run([*cmd, str(target.path)], target.root)
    diagnostics = json.loads(out or "{}").get("generalDiagnostics", [])
    return [
        Finding(
            "pyright",
            item.get("rule") or "error",
            item["range"]["start"]["line"] + 1,
            item["message"].splitlines()[0],
        )
        for item in diagnostics
        if item.get("severity") == "error"
    ]


Checker = Callable[[Target, tuple[Span, ...]], list[Finding]]
CHECKERS: tuple[Checker, ...] = (ruff_general, ruff_gate, complexipy, pyright)
