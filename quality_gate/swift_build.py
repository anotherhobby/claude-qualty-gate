"""The compiler, at Stop: a Swift package does not build halfway through a multi-file
edit, so its types are checked once the agent is done rather than after every write.

Each SwiftPM package the session wrote Swift in is built, its tests included, in the
gate's cache rather than the package's own `.build`. Every error is a finding wherever it
is, since the build is broken; every warning in the package's own files is one too, placed
like any other finding, which holds the package to warnings as errors without forcing the
flag on its dependencies. An Xcode project is not built.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import defaultdict
from pathlib import Path

import placement
import swift_tools
from checks import BY_BUILD, BY_LINE, Finding, Target, dirs_up
from placement import Span
from tools import ToolError

# A clean build of a package with dependencies can take longer; it fails open, and the
# next Stop carries on from where it stopped.
TIMEOUT_S = 150
# `path:line:column: error: message`, as swiftc writes each diagnostic.
_DIAGNOSTIC = re.compile(r"^(/.+?):(\d+):\d+: (error|warning): (.*)$", re.MULTILINE)

_lock = threading.Lock()
# Each package's diagnostics by file, or why it did not build: built once per Stop.
_built: dict[Path, dict[Path, list[Finding]] | ToolError] = {}


def package_root(path: Path, root: Path) -> Path | None:
    """The nearest directory holding a Package.swift, up to the repository root."""
    found = (d for d in dirs_up(Target(path, root)) if (d / "Package.swift").is_file())
    return next(found, None)


def _diagnostics(package: Path, output: str) -> dict[Path, list[Finding]]:
    found: dict[Path, set[Finding]] = defaultdict(set)
    for path_text, line, kind, message in _DIAGNOSTIC.findall(output):
        path = Path(path_text).resolve()
        if package in path.parents:
            scope = BY_BUILD if kind == "error" else BY_LINE
            found[path].add(Finding("swiftc", kind, int(line), message, scope))
    return {path: sorted(items, key=lambda f: (f.line, f.message)) for path, items in found.items()}


def _build(package: Path) -> dict[Path, list[Finding]]:
    name = hashlib.sha256(str(package).encode()).hexdigest()[:16]
    cmd = [str(swift_tools.xcrun_find("swift")), "build", "--build-tests"]
    cmd += ["--package-path", str(package)]
    cmd += ["--scratch-path", str(swift_tools.cache() / "build" / name)]
    result = swift_tools.run(cmd, package, allowed=(0, 1), timeout=TIMEOUT_S)
    return _diagnostics(package, result.stdout + result.stderr)


def build(package: Path) -> dict[Path, list[Finding]]:
    """`package` built with its tests, once per process: its diagnostics by file. A build
    that did not run raises ToolError, once per process too."""
    with _lock:
        if package not in _built:
            try:
                _built[package] = _build(package)
            except ToolError as error:
                _built[package] = error
        built = _built[package]
    if isinstance(built, ToolError):
        raise built
    return built


def stop_paths(paths: list[Path]) -> list[Path]:
    """Every file with a diagnostic in the packages `paths` are in, so that an error the
    session caused in a file it never opened is found too."""
    packages = set()
    for path in paths:
        root = placement.repo_root(path.parent)
        package = package_root(path, root) if root else None
        if package is not None:
            packages.add(package)
    found: set[Path] = set()
    for package in packages:
        try:
            found.update(build(package))
        except ToolError:
            continue  # reported by the compiler checker on each file of the package
    return sorted(found)


def compiler(target: Target, _functions: tuple[Span, ...]) -> list[Finding]:
    """The compiler's diagnostics for `target`, from its package's build."""
    package = package_root(target.path, target.root)
    return [] if package is None else build(package).get(target.path, [])
