"""The Swift checkers: SwiftLint, the gate's syntax helper and swift-format as each file is
written, and the compiler at Stop (swift_build.py).

SwiftLint holds size, the constructs never allowed and likely bugs, at the gate's own
configuration (baseline/swiftlint.yml), on a copy of the file with every `swiftlint:`
comment stripped so that none can silence it. The helper holds what SwiftLint cannot:
cognitive complexity, a view's body, `try?`, `@unchecked Sendable`, `nonisolated(unsafe)`,
commented-out code and the `swiftlint:` comments themselves, and reads each file's spans
for placement. swift-format holds style, at the project's `.swift-format` or the gate's
baseline. A package manifest is also held to Swift 6 language mode.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import swift_tools
from checks import BY_FILE, BY_LINE, COGNITIVE_RULE, Finding, Target, dirs_up
from placement import Span
from tools import ToolError

BASELINE = Path(__file__).resolve().parent.parent / "baseline"
SWIFTLINT_CONFIG = BASELINE / "swiftlint.yml"
# Laid over the configuration for test files, which compare against literal values.
SWIFTLINT_TESTS = BASELINE / "swiftlint-tests.yml"
SWIFT_FORMAT_CONFIG = BASELINE / "swift-format.json"
COGNITIVE_LIMIT = 10
VIEW_BODY_LIMIT = 30
VIEW_BODY_RULE = "view-body-length"
LANGUAGE_MODE_RULE = "swift-language-mode"
SWIFT_6 = 6

# The rules that measure size, which may be kept with a case. Every other finding is fixed.
SIZE_RULES = frozenset({
    "cyclomatic_complexity",
    "function_body_length",
    "closure_body_length",
    "type_body_length",
    "file_length",
    "function_parameter_count",
    COGNITIVE_RULE,
    VIEW_BODY_RULE,
})
_NUMBER = re.compile(r"\d+")
# `path:line:column: warning: [Rule] message`, as swift-format writes each finding.
_FORMAT = re.compile(r"^.+?:(\d+):\d+: (?:warning|error): \[(\w+)\] (.*)$", re.MULTILINE)


def is_test(target: Target) -> bool:
    rel = target.path.relative_to(target.root)
    return "Tests" in rel.parts[:-1] or target.path.stem.endswith("Tests")


def _from_swiftlint(item: dict) -> Finding:
    """A SwiftLint violation; a size one says how far over it is, `(52 > 40)`, as ruff's
    do, so that it can be kept up to that value."""
    rule, message = item["rule_id"], item["reason"]
    numbers = _NUMBER.findall(message)
    if rule in SIZE_RULES and len(numbers) > 1:
        message = f"{message} ({numbers[-1]} > {numbers[0]})"
    scope = BY_FILE if rule == "file_length" else BY_LINE
    return Finding("swiftlint", rule, item.get("line") or 1, message, scope)


def swiftlint(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """SwiftLint at the gate's configuration, with `swiftlint:` comments stripped."""
    source = target.path.read_text(encoding="utf-8").replace("swiftlint:", "swiftlint-stripped:")
    cmd = [str(swift_tools.swiftlint()), "lint", "--use-stdin", "--quiet", "--no-cache"]
    cmd += ["--reporter", "json", "--config", str(SWIFTLINT_CONFIG)]
    if is_test(target):
        cmd += ["--config", str(SWIFTLINT_TESTS)]
    out = swift_tools.run(cmd, target.root, stdin=source, allowed=(0, 2)).stdout
    return [_from_swiftlint(item) for item in json.loads(out or "[]")]


def syntax(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """The gate's syntax helper: the rules SwiftLint does not hold."""
    limits = [str(COGNITIVE_LIMIT), str(VIEW_BODY_LIMIT)]
    cmd = [str(swift_tools.helper()), "check", *limits, str(target.path)]
    report = json.loads(swift_tools.run(cmd, target.root).stdout)
    return [
        Finding("gate-syntax", item["rule"], item["line"], item["message"])
        for item in report["findings"]
    ]


def spans(source: str) -> tuple[Span, ...]:
    """Every function, type, accessor and closure in `source`, or none while the helper
    is not built (its checker says so)."""
    try:
        out = swift_tools.run([str(swift_tools.helper()), "spans"], Path.cwd(), stdin=source)
    except ToolError:
        return ()
    return tuple((span["name"], span["first"], span["last"]) for span in json.loads(out.stdout))


def format_config(target: Target) -> Path:
    """The nearest `.swift-format` up to the repository root, or the gate's baseline."""
    found = (directory / ".swift-format" for directory in dirs_up(target))
    return next((path for path in found if path.is_file()), SWIFT_FORMAT_CONFIG)


def swift_format(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    config = format_config(target)
    cmd = [str(swift_tools.xcrun_find("swift-format")), "lint", "--configuration", str(config)]
    result = swift_tools.run([*cmd, str(target.path)], target.root, allowed=(0, 1))
    return [
        Finding("swift-format", rule, int(line), message)
        for line, rule, message in _FORMAT.findall(result.stderr)
    ]


def format_command(target: Target) -> str:
    """The command that fixes swift-format's findings in `target`."""
    config = format_config(target)
    return f"xcrun swift-format format -i --configuration {config} {target.path}"


def _language_modes(manifest: dict) -> list[str]:
    """What keeps a package from building in Swift 6 language mode, if anything."""
    problems = []
    tools_version = manifest["toolsVersion"]["_version"]
    if int(tools_version.split(".")[0]) < SWIFT_6:
        problems.append(f"swift-tools-version {tools_version} builds in Swift 5 language mode")
    for target in manifest.get("targets", []):
        for setting in target.get("settings", []):
            mode = (setting.get("kind") or {}).get("swiftLanguageMode")
            if mode and mode.get("_0") != str(SWIFT_6):
                problems.append(f"target {target['name']} builds in Swift {mode['_0']} mode")
    return problems


def manifest(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """A package manifest that does not build in Swift 6 language mode."""
    if target.path.name != "Package.swift":
        return []
    scratch = swift_tools.cache() / "manifests"
    cmd = [str(swift_tools.xcrun_find("swift")), "package", "--scratch-path", str(scratch)]
    cmd += ["--package-path", str(target.path.parent), "dump-package"]
    dumped = json.loads(swift_tools.run(cmd, target.root).stdout)
    return [
        Finding("swift", LANGUAGE_MODE_RULE, 1, f"{problem}; the gate holds Swift to Swift 6")
        for problem in _language_modes(dumped)
    ]
