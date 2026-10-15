"""quality-gate: hold the Python Claude writes to ruff, complexipy and pyright.

    python quality_gate --hook           # after Edit, Write or MultiEdit: the call's JSON on stdin
    python quality_gate --shell-before   # before Bash: record the uncommitted Python
    python quality_gate --shell-after    # after Bash: gate the Python the command wrote
    python quality_gate FILE ...         # check files by hand; exits 1 on findings in new code
    python quality_gate --outdated       # each pinned checker against its latest release

Run as a directory, so this folder is the first entry on the import path and a project's
own modules can never shadow the gate's.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gate
import report
import shell
import tools

# Files checked at once after a command that wrote many; each runs four checkers.
FILES_AT_ONCE = 4


def edited_file(payload: dict) -> Path | None:
    """The Python file a tool call wrote, or None when it wrote none."""
    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or ".") / path
    return path if path.suffix == ".py" and path.is_file() else None


def check_all(paths: list[Path]) -> list[gate.Outcome]:
    with ThreadPoolExecutor(max_workers=FILES_AT_ONCE) as pool:
        return [outcome for outcome in pool.map(gate.check, paths) if outcome is not None]


def answer(payload: dict, paths: list[Path]) -> int:
    event = payload.get("hook_event_name") or "PostToolUse"
    output = report.hook_output(check_all(paths), event) if paths else None
    if output:
        print(json.dumps(output))
    return 0


def by_hand(files: list[str]) -> int:
    blocked = 0
    for name in files:
        outcome = gate.check(Path(name))
        if outcome is None:
            print(f"{name}: out of scope (outside git, ignored or excluded)")
            continue
        print(report.text(outcome) or f"{outcome.rel}: clean")
        blocked += len(outcome.blocking)
    if blocked:
        print(f"\n{blocked} finding(s) in new code. {report.ADVICE}")
    return 1 if blocked else 0


def run_hook(mode: str) -> int:
    payload = json.load(sys.stdin)
    if mode == "shell_before":
        shell.before(payload)
        return 0
    if mode == "shell_after":
        return answer(payload, shell.written(payload))
    path = edited_file(payload)
    return answer(payload, [path] if path else [])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="quality_gate", description="Hold Python to ruff, complexipy and pyright."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--hook", action="store_const", const="edit", dest="mode")
    modes.add_argument("--shell-before", action="store_const", const="shell_before", dest="mode")
    modes.add_argument("--shell-after", action="store_const", const="shell_after", dest="mode")
    modes.add_argument("--outdated", action="store_const", const="outdated", dest="mode")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    if args.mode == "outdated":
        return tools.outdated()
    if args.mode:
        return run_hook(args.mode)
    if not args.files:
        parser.error("name files to check, or give a mode")
    return by_hand(args.files)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
