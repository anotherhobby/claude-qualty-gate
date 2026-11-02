"""The Swift tools the gate runs, where it gets each, and what each is pinned to.

- SwiftLint is downloaded on first use at its pinned version, the official portable
  binary, checked against its SHA-256 and kept in the user's cache: pinned like the
  Python checkers, with nothing to install.
- The syntax helper (swift/) is built on first use, in the background since the build
  takes a few minutes, at the swift-syntax version its Package.resolved pins, and
  rebuilt whenever its source or the toolchain changes.
- swift-format and the compiler are the active Xcode toolchain's, pinned by it.

Nothing is written into the projects checked: builds go to the cache too.
"""

from __future__ import annotations

import fcntl
import functools
import hashlib
import io
import json
import os
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tools import ToolError, is_behind

SWIFTLINT_VERSION = "0.65.1"
SWIFTLINT_SHA256 = "c1e429b0599cf1b516f369a2d9ec04eaf0e436f3c12b637df8851fa52ff694d0"
SWIFTLINT_URL = (
    "https://github.com/realm/SwiftLint/releases/download/{version}/portable_swiftlint.zip"
)
HELPER_SOURCE = Path(__file__).resolve().parent.parent / "swift"
HELPER_INPUTS = ("Package.swift", "Package.resolved", "Sources")
# A download or a lint; a build at Stop has its own limit (swift_build.py).
TIMEOUT_S = 150
EXECUTABLE = 0o755
# How much of a failed helper build's log is shown.
LOG_TAIL = 1500


def cache() -> Path:
    """Where the gate keeps what it downloads and builds: the user's cache, never a
    project's."""
    configured = os.environ.get("QUALITY_GATE_CACHE")
    if configured:
        return Path(configured)
    return Path.home() / "Library" / "Caches" / "claude-quality-gate"


@contextmanager
def _locked(name: str) -> Iterator[None]:
    """One process at a time: hooks for parallel tool calls run at once."""
    cache().mkdir(parents=True, exist_ok=True)
    with (cache() / f"{name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def run(
    cmd: list[str],
    cwd: Path,
    *,
    stdin: str | None = None,
    allowed: tuple[int, ...] = (0,),
    timeout: int = TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """`cmd`'s result, or ToolError when it does not finish or exits outside `allowed`."""
    name = Path(cmd[0]).name
    try:
        result = subprocess.run(
            cmd, cwd=cwd, input=stdin, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{name} did not finish in {timeout} s") from None
    except OSError as error:
        raise ToolError(f"{name} did not run: {error}") from None
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise ToolError(f"{name} exited {result.returncode}: {detail}")
    return result


# SwiftLint ---------------------------------------------------------------------------------


def swiftlint() -> Path:
    """The pinned SwiftLint, downloaded the first time it is needed."""
    home = cache() / "swiftlint" / SWIFTLINT_VERSION
    exe = home / "swiftlint"
    if not exe.is_file():
        with _locked("swiftlint"):
            if not exe.is_file():
                _download_swiftlint(home)
    return exe


def _download_swiftlint(home: Path) -> None:
    url = SWIFTLINT_URL.format(version=SWIFTLINT_VERSION)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as reply:
            data = reply.read()
    except OSError as error:
        raise ToolError(f"could not download SwiftLint {SWIFTLINT_VERSION}: {error}") from None
    if hashlib.sha256(data).hexdigest() != SWIFTLINT_SHA256:
        raise ToolError(f"SwiftLint {SWIFTLINT_VERSION}'s download does not match its pinned hash")
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as tmp, zipfile.ZipFile(io.BytesIO(data)) as archive:
        exe = Path(archive.extract("swiftlint", tmp))
        exe.chmod(EXECUTABLE)
        exe.replace(home / "swiftlint")


# The toolchain -----------------------------------------------------------------------------


@functools.cache
def xcrun_find(tool: str) -> Path:
    """`tool` in the active Xcode toolchain."""
    found = run(["xcrun", "--find", tool], Path.cwd()).stdout.strip()
    if not found:
        raise ToolError(f"xcrun cannot find {tool}: is Xcode installed and selected?")
    return Path(found)


@functools.cache
def toolchain_version() -> str:
    """The first line of `swift --version`: what pins swift-format and the compiler."""
    out = run([str(xcrun_find("swift")), "--version"], Path.cwd()).stdout
    return next(iter(out.splitlines()), "unknown")


# The syntax helper -------------------------------------------------------------------------


def _helper_key() -> str:
    """Changes with the helper's source and with the toolchain that builds it."""
    digest = hashlib.sha256(toolchain_version().encode())
    for name in HELPER_INPUTS:
        root = HELPER_SOURCE / name
        for path in sorted([root] if root.is_file() else root.rglob("*.swift")):
            digest.update(path.relative_to(HELPER_SOURCE).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def helper() -> Path:
    """The built syntax helper. While it is not built yet, starts its build in the
    background and raises ToolError, so the checks that need it fail open meanwhile."""
    scratch = cache() / "gate-syntax" / _helper_key()
    exe = scratch / "release" / "gate-syntax"
    if exe.is_file():
        return exe
    with _locked("gate-syntax"):
        if exe.is_file():
            return exe
        failed = _start_build(scratch)
    detail = f" The last attempt failed:\n{failed}" if failed else ""
    raise ToolError(
        "the Swift syntax helper is being built, which takes a few minutes once per version; "
        "spans, cognitive complexity and the gate's own Swift rules resume when it is ready."
        + detail
    )


def _alive(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text(encoding="utf-8")), 0)
    except (OSError, ValueError):
        return False
    return True


def _start_build(scratch: Path) -> str:
    """Starts the helper's build unless one is running. Returns the tail of the last
    attempt's log when that attempt failed, or ""."""
    pid_file, log = scratch.with_suffix(".pid"), scratch.with_suffix(".log")
    if _alive(pid_file):
        return ""
    failed = log.read_text(encoding="utf-8")[-LOG_TAIL:] if log.is_file() else ""
    scratch.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(xcrun_find("swift")),
        "build",
        "-c",
        "release",
        "--product",
        "gate-syntax",
        "--force-resolved-versions",
        "--package-path",
        str(HELPER_SOURCE),
        "--scratch-path",
        str(scratch),
    ]
    with log.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(
            cmd, cwd=scratch.parent, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
        )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    return failed


# Versions ----------------------------------------------------------------------------------


def _latest_github(repo: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=10) as reply:
            return json.load(reply)["tag_name"]
    except (OSError, ValueError, KeyError):
        return None


def _pinned_swift_syntax() -> str:
    try:
        resolved = json.loads((HELPER_SOURCE / "Package.resolved").read_text(encoding="utf-8"))
        return resolved["pins"][0]["state"]["version"]
    except (OSError, ValueError, KeyError, IndexError):
        return "unknown"


def outdated_lines() -> list[str]:
    """Each Swift pin against its latest release, in tools.outdated()'s form."""
    lines = []
    for tool, repo, pinned in (
        ("swiftlint", "realm/SwiftLint", SWIFTLINT_VERSION),
        ("swift-syntax", "swiftlang/swift-syntax", _pinned_swift_syntax()),
    ):
        latest = _latest_github(repo)
        if latest is None:
            lines.append(f"  ?  {tool} {pinned}: no answer from GitHub")
            continue
        marker = "UP " if is_behind(pinned, latest) else "ok "
        lines.append(f"  {marker}{tool} {pinned}: latest {latest}")
    try:
        lines.append(f"  --  swift-format, swiftc: {toolchain_version()}")
    except ToolError as error:
        lines.append(f"  ?  swift-format, swiftc: {error}")
    return lines
