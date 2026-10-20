"""quality-gate: hold the Python Claude writes to ruff, complexipy and pyright.

    python quality_gate --hook           # after Edit, Write or MultiEdit: the call's JSON on stdin
    python quality_gate --shell-before   # before Bash: record the uncommitted Python
    python quality_gate --shell-after    # after Bash: gate the Python the command wrote
    python quality_gate --guard          # before Edit, Write or MultiEdit: keep the agent out
                                         # of the user's approvals
    python quality_gate --stop           # at Stop: hold the agent back, once, over what it
                                         # leaves undone
    python quality_gate FILE ...         # check files by hand; exits 1 on findings in new code
    python quality_gate --outdated       # each pinned checker against its latest release
    python quality_gate propose ID CASE  # the agent's case for keeping a size finding
    python quality_gate approve [ID]     # the user's approval of a case; none: list them

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
import keep
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


def answer(payload: dict, paths: list[Path]) -> dict | None:
    outcomes = check_all(paths)
    if not outcomes:
        return None
    with keep.session(payload.get("session_id")) as memory:
        triages = keep.triage(outcomes, memory)
        keep.remember(triages, memory)
    return report.hook_output(triages, payload.get("hook_event_name") or "PostToolUse")


def by_hand(files: list[str]) -> int:
    outcomes = []
    for name in files:
        outcome = gate.check(Path(name))
        if outcome is None:
            print(f"{name}: out of scope (outside git, ignored or excluded)")
        else:
            outcomes.append(outcome)
    with keep.session(None) as memory:
        triages = keep.triage(outcomes, memory)
        keep.remember(triages, memory)
    for result in triages:
        print(report.text(result) or f"{result.outcome.rel}: clean")
    blocked = sum(len(result.fix) + len(result.keep) for result in triages)
    if blocked:
        print(f"\n{blocked} finding(s) in new code.")
        print("\n".join(report.advice(triages)))
    return 1 if blocked else 0


def _edit(payload: dict) -> dict | None:
    path = edited_file(payload)
    return answer(payload, [path] if path else [])


def _shell_before(payload: dict) -> dict | None:
    denial = keep.deny_bash(payload)
    if denial is None:
        shell.before(payload)
    return denial


def _shell_after(payload: dict) -> dict | None:
    return answer(payload, shell.written(payload))


def _stop(payload: dict) -> dict | None:
    if not payload.get("stop_hook_active"):
        answer(payload, keep.files_left(payload.get("session_id")))
    left = keep.unfinished(payload)
    return {"decision": "block", "reason": report.stop_reason(left)} if left else None


HOOKS = {
    "edit": _edit,
    "shell_before": _shell_before,
    "shell_after": _shell_after,
    "guard": keep.deny_edit,
    "stop": _stop,
}


def run_hook(mode: str) -> int:
    output = HOOKS[mode](json.load(sys.stdin))
    if output:
        print(json.dumps(output))
    return 0


def words(argv: list[str]) -> int:
    """`propose` and `approve`: a case for keeping a size finding, and its approval."""
    parser = argparse.ArgumentParser(prog="quality_gate")
    commands = parser.add_subparsers(dest="word", required=True)
    proposing = commands.add_parser("propose", help="the agent's case for keeping a finding")
    proposing.add_argument("id")
    proposing.add_argument("case")
    approving = commands.add_parser("approve", help="the user's approval; none: list them")
    approving.add_argument("id", nargs="?")
    args = parser.parse_args(argv)
    if args.word == "propose":
        return keep.propose(args.id, args.case)
    return keep.approve(args.id) if args.id else keep.pending()


def main(argv: list[str]) -> int:
    if argv[:1] in (["propose"], ["approve"]):
        return words(argv)
    parser = argparse.ArgumentParser(
        prog="quality_gate", description="Hold Python to ruff, complexipy and pyright."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--hook", action="store_const", const="edit", dest="mode")
    modes.add_argument("--shell-before", action="store_const", const="shell_before", dest="mode")
    modes.add_argument("--shell-after", action="store_const", const="shell_after", dest="mode")
    modes.add_argument("--guard", action="store_const", const="guard", dest="mode")
    modes.add_argument("--stop", action="store_const", const="stop", dest="mode")
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
