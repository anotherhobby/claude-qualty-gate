"""The checkers the gate runs, the versions they are pinned to, and whether newer ones exist.

Every checker runs through `uvx` at an exact version, never from a project's own
environment: the gate then holds every project to the same rules, and a project needs
nothing installed for it. Pins move by hand; `--outdated` says when there is a reason to.
"""

from __future__ import annotations

import json
import shutil
import urllib.request

PINS = {
    "complexipy": "8.0.1",
    "pyright": "1.1.414",
    "ruff": "0.17.1",
}
LEGEND = "UP = newer release available, ? = can't check"


class ToolError(Exception):
    """A checker did not run, so its silence proves nothing about the file."""


def uvx(tool: str) -> list[str]:
    """The command prefix that runs `tool` at its pinned version."""
    exe = shutil.which("uvx")
    if exe is None:
        raise ToolError("uvx is not on PATH, and every checker runs through it")
    return [exe, f"{tool}@{PINS[tool]}"]


def version_tuple(version: str) -> tuple[int, ...] | None:
    """`1.1.414` -> (1, 1, 414); None for anything that is not dotted digits."""
    parts = version.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def is_behind(pinned: str, latest: str) -> bool:
    old, new = version_tuple(pinned), version_tuple(latest)
    return old is not None and new is not None and old < new


def latest_release(tool: str) -> str | None:
    """The newest version PyPI lists for `tool`, or None when PyPI does not answer."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{tool}/json", timeout=10) as reply:
            return json.load(reply)["info"]["version"]
    except (OSError, ValueError, KeyError):
        return None


def report_line(tool: str, pinned: str, latest: str | None) -> str:
    if latest is None:
        return f"  ?  {tool} {pinned}: no answer from PyPI"
    marker = "UP " if is_behind(pinned, latest) else "ok "
    return f"  {marker}{tool} {pinned}: latest {latest}"


def outdated() -> int:
    """Print each pin against its latest release. Informational: always 0."""
    print(LEGEND)
    for tool, pinned in sorted(PINS.items()):
        print(report_line(tool, pinned, latest_release(tool)))
    return 0
