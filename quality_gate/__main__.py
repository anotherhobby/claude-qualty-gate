"""quality-gate: hold the Python and Swift Claude writes to their checkers.

    python quality_gate --hook           # after Edit, Write or MultiEdit: the call's JSON on stdin
    python quality_gate --shell-before   # before Bash: record the uncommitted code
    python quality_gate --shell-after    # after Bash: gate the code the command wrote
    python quality_gate --guard          # before Edit, Write or MultiEdit: keep the agent out
                                         # of the user's approvals
    python quality_gate --stop           # at Stop: hold the agent back, once, over what it
                                         # leaves undone
    python quality_gate FILE ...         # check files by hand; exits 1 on findings in new or
                                         # touched code
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
import languages
import report
import shell
import swift_tools
import tools

# Files checked at once after a command that wrote many; each runs four checkers.
FILES_AT_ONCE = 4


def edited_file(payload: dict) -> Path | None:
    """The file in a language the gate holds that a tool call wrote, or None."""
    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or ".") / path
    return path if languages.for_path(path) and path.is_file() else None


def check_all(paths: list[Path], *, stop: bool = False) -> list[gate.Outcome]:
    def one(path: Path) -> gate.Outcome | None:
        return gate.check(path, stop=stop)

    with ThreadPoolExecutor(max_workers=FILES_AT_ONCE) as pool:
        return [outcome for outcome in pool.map(one, paths) if outcome is not None]


def answer(payload: dict, paths: list[Path], *, stop: bool = False) -> dict | None:
    outcomes = check_all(paths, stop=stop)
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
        print(f"\n{blocked} finding(s) in new or touched code.")
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


def stop_paths(session_id: str | None) -> list[Path]:
    """What to check at Stop: the files the session left with findings, another file's
    edit may have fixed them; every file in a language with whole-program checks, which
    run only now; and the files those checks find more in."""
    paths = set(keep.files_left(session_id))
    for language in languages.LANGUAGES:
        if not language.stop_checkers:
            continue
        seen = [p for p in keep.files_seen(session_id) if languages.for_path(p) is language]
        paths.update(seen)
        if language.stop_paths and seen:
            paths.update(language.stop_paths(seen))
    return sorted(paths)


def _stop(payload: dict) -> dict | None:
    if not payload.get("stop_hook_active"):
        answer(payload, stop_paths(payload.get("session_id")), stop=True)
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
        prog="quality_gate", description="Hold Python and Swift to their checkers."
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
        tools.outdated()
        print("\n".join(swift_tools.outdated_lines()))
        return 0
    if args.mode:
        return run_hook(args.mode)
    if not args.files:
        parser.error("name files to check, or give a mode")
    return by_hand(args.files)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
