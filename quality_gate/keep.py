"""Size findings a function may keep: the agent makes its case, and the user approves it.

The size rules measure, and some code is the right size as written. A size finding in new
code, or in an old function the change touched, blocks until it is fixed or the agent
records its case with `propose`, after which the agent carries on. Only the user approves a
case, with `approve` in their own terminal: the hooks deny the agent that command and any
edit under the user's gate directory. Lint, type and style findings are never kept.

What is kept, and where:
- approvals, in the user's home directory and never in a repository, so projects shared
  with others are left alone. An approval names its function and rule, not a line, and
  covers that function up to the value approved.
- open size findings and the cases made for them, in temp, cleared after a week unseen. A
  case not approved by then is gone; the finding is reported again while the change still holds it.
- each session's memory, in temp: what it has been told, so a finding blocks once per
  session, and what each file it checked last held, which the Stop hook reads.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import placement
from checks import COGNITIVE_RULE, Finding
from gate import Outcome
from placement import CHANGED

STATE = Path(tempfile.gettempdir()) / "claude-quality-gate"
# Open findings, cases and session memories unseen this long are cleared.
STALE_S = 7 * 24 * 3600
LOG = "approved.jsonl"
GATE_DIR = Path(__file__).resolve().parent

# The rules that measure size. Every other finding is fixed, never kept.
SIZE_RULES = frozenset({
    "C901",
    "PLR0911",
    "PLR0912",
    "PLR0913",
    "PLR0915",
    "PLR0917",
    "PLR1702",
    COGNITIVE_RULE,
})
# ruff says "(11 > 8)"; the gate's complexipy finding says "cognitive complexity 19".
_MEASURE = re.compile(r"\((\d+) > \d+\)|complexity (\d+)")
_ID = re.compile(r"[0-9a-f]{6}")
_APPROVE = re.compile(r"quality[_-]gate['\"]?\s+approve\b")

DENIED = (
    "Approving a kept finding is the user's decision, made in their own terminal. Give the "
    "user your case and the command; do not run it."
)

Key = tuple[str, str, str, str]
Memory = dict


def home() -> Path:
    """Where approvals are kept: the user's own, never a repository's."""
    configured = os.environ.get("QUALITY_GATE_HOME")
    return Path(configured) if configured else Path.home() / ".claude" / "quality-gate"


def command(*args: str) -> str:
    """The shell command that runs this gate with `args`."""
    runner = ["uv", "run", "--no-config", "--no-project", "--quiet", "--python", ">=3.12", "python"]
    return shlex.join([*runner, str(GATE_DIR), *args])


def describe(rel: str, finding: Finding) -> str:
    return f"{rel}:{finding.line}: {finding.rule} {finding.message}"


@dataclass(frozen=True)
class Size:
    """A size finding, named by its function rather than its line, so it outlives edits."""

    finding: Finding
    root: str
    rel: str
    function: str
    value: int

    @property
    def key(self) -> Key:
        return (self.root, self.rel, self.function, self.finding.rule)

    @property
    def id(self) -> str:
        return hashlib.sha256("\0".join(self.key).encode()).hexdigest()[:6]

    @property
    def name(self) -> str:
        return f"{self.finding.rule} in {self.function} ({self.value})"


def size_of(outcome: Outcome, finding: Finding) -> Size | None:
    """`finding` as a size finding, or None when it is one to fix."""
    if finding.rule not in SIZE_RULES:
        return None
    span = placement.innermost(outcome.functions, finding.line)
    measured = _MEASURE.search(finding.message)
    if span is None or measured is None:
        return None
    value = int(measured[1] or measured[2])
    return Size(finding, str(outcome.target.root), outcome.rel, span[0], value)


@dataclass
class Triage:
    """One file's findings, sorted by what the agent is to do about each."""

    outcome: Outcome
    # New or touched code, to fix: never kept.
    fix: list[Finding] = field(default_factory=list)
    # New or touched code, to fix or make the case for.
    keep: list[Size] = field(default_factory=list)
    # Of those, the ones grown past an approval: id to the value the user approved.
    worse: dict[str, int] = field(default_factory=dict)
    # Ones the session has already been told about, with no case yet.
    open: list[Size] = field(default_factory=list)
    # A case made, awaiting the user: (finding, case).
    cased: list[tuple[Size, str]] = field(default_factory=list)
    # Approved by the user.
    approved: list[tuple[Size, dict]] = field(default_factory=list)
    # Old functions this change touched, in a project that only reports them.
    reported: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return bool(self.fix or self.keep)


def _covers(record: dict | None, value: int, field_name: str) -> bool:
    return record is not None and value <= record[field_name]


def _covering(size: Size | None, approvals: dict[Key, dict]) -> dict | None:
    """The user's approval that covers `size`, or None."""
    approval = approvals.get(size.key) if size else None
    return approval if size and _covers(approval, size.value, "value") else None


def _sort_open(result: Triage, size: Size, approval: dict | None, told: dict) -> None:
    case = _case(size.id)
    if case is not None and _covers(case, size.value, "case_value"):
        result.cased.append((size, case["case"]))
    elif told.get(size.id, -1) >= size.value:
        result.open.append(size)
    else:
        result.keep.append(size)
        if approval:
            result.worse[size.id] = approval["value"]


def _triage(outcome: Outcome, approvals: dict[Key, dict], told: dict) -> Triage:
    result = Triage(outcome)
    for finding in outcome.blocking:
        size = size_of(outcome, finding)
        if size is None:
            result.fix.append(finding)
        elif approval := _covering(size, approvals):
            result.approved.append((size, approval))
        else:
            _sort_open(result, size, approvals.get(size.key), told)
    if not outcome.touched_block:
        _sort_reported(result, approvals)
    return result


def _sort_reported(result: Triage, approvals: dict[Key, dict]) -> None:
    for finding in result.outcome.placed[CHANGED]:
        size = size_of(result.outcome, finding)
        if size and (approval := _covering(size, approvals)):
            result.approved.append((size, approval))
        else:
            result.reported.append(finding)


def triage(outcomes: list[Outcome], memory: Memory) -> list[Triage]:
    approvals = _approvals()
    return [_triage(outcome, approvals, memory["told"]) for outcome in outcomes]


def remember(triages: list[Triage], memory: Memory) -> None:
    """Record each open size finding, so `propose` can find it by id, and what the
    session has been told."""
    _clear_stale()
    for result in triages:
        sizes = [*result.keep, *result.open, *(size for size, _ in result.cased)]
        for size in sizes:
            _record(size)
        for size in result.keep:
            memory["told"][size.id] = size.value
        memory["files"][str(result.outcome.target.path)] = {
            "fix": [describe(result.outcome.rel, f) for f in result.fix],
            "sizes": [[s.id, s.value, describe(s.rel, s.finding)] for s in sizes],
        }


# Stores ----------------------------------------------------------------------------------


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(data), encoding="utf-8")
    partial.replace(path)


def _clear_stale() -> None:
    cutoff = time.time() - STALE_S
    for old in [*STATE.glob("open/*"), *STATE.glob("sessions/*")]:
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)


def _open_path(finding_id: str) -> Path:
    return STATE / "open" / f"{finding_id}.json"


def _record(size: Size) -> None:
    path = _open_path(size.id)
    record = _read(path) or {}
    record.update(
        id=size.id,
        root=size.root,
        rel=size.rel,
        function=size.function,
        rule=size.finding.rule,
        value=size.value,
    )
    _write(path, record)


def _case(finding_id: str) -> dict | None:
    """The open finding `finding_id` when a case has been made for it."""
    if not _ID.fullmatch(finding_id):
        return None
    record = _read(_open_path(finding_id))
    return record if record and record.get("case") else None


def _approvals() -> dict[Key, dict]:
    """The user's approvals, the latest for each function and rule."""
    try:
        lines = (home() / LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found: dict[Key, dict] = {}
    for line in lines:
        try:
            entry = json.loads(line)
            found[entry["root"], entry["rel"], entry["function"], entry["rule"]] = entry
        except (ValueError, KeyError, TypeError):
            continue
    return found


def _session_path(session_id: str) -> Path:
    name = hashlib.sha256(session_id.encode()).hexdigest()[:32]
    return STATE / "sessions" / f"{name}.json"


@contextmanager
def session(session_id: str | None) -> Iterator[Memory]:
    """The session's memory, saved on leaving; without a session, a memory nothing keeps."""
    if not session_id:
        yield {"told": {}, "files": {}, "pushed": []}
        return
    path = _session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        memory = _read(path) or {"told": {}, "files": {}, "pushed": []}
        yield memory
        _write(path, memory)


# Commands --------------------------------------------------------------------------------


def propose(finding_id: str, case: str) -> int:
    """Record the agent's case for keeping an open size finding."""
    record = _read(_open_path(finding_id)) if _ID.fullmatch(finding_id) else None
    if record is None:
        print(f"No open size finding {finding_id}: use an id the quality gate reported.")
        return 1
    if not case.strip():
        print("Give the case: the refactor you rejected, and why the code is better as written.")
        return 1
    record.update(case=case.strip(), case_value=record["value"], proposed_at=time.time())
    _write(_open_path(finding_id), record)
    print(f"Recorded {finding_id}. For the user to approve, in their own terminal:")
    print(f"  {command('approve', finding_id)}")
    return 0


def approve(finding_id: str) -> int:
    """The user's approval of a case: kept in their home directory from now on."""
    case = _case(finding_id)
    if case is None:
        print(f"No case awaits approval as {finding_id}. To list those that do:")
        print(f"  {command('approve')}")
        return 1
    entry = {name: case[name] for name in ("root", "rel", "function", "rule")}
    entry |= {"value": case["case_value"], "case": case["case"]}
    entry["approved"] = date.today().isoformat()
    log = home() / LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry) + "\n")
    _open_path(finding_id).unlink(missing_ok=True)
    print(f"Approved {entry['rule']} in {entry['function']} in {entry['rel']}, up to")
    print(f"{entry['value']}. Recorded in {log}.")
    return 0


def pending() -> int:
    """Every case awaiting the user."""
    cases = [c for path in sorted(STATE.glob("open/*.json")) if (c := _case(path.stem))]
    if not cases:
        print("No kept findings await approval.")
        return 0
    for case in cases:
        where = f"{Path(case['root']).name}/{case['rel']}"
        print(f"{case['id']}  {where}: {case['rule']} in {case['function']} ({case['case_value']})")
        print(f"        {case['case']}")
    print(f"\nTo approve one: {command('approve')} <id>")
    return 0


# Hooks -----------------------------------------------------------------------------------


def _deny() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": DENIED,
        }
    }


def deny_bash(payload: dict) -> dict | None:
    """A denial when the agent's command would approve a kept finding."""
    text = (payload.get("tool_input") or {}).get("command") or ""
    return _deny() if _APPROVE.search(text) else None


def deny_edit(payload: dict) -> dict | None:
    """A denial when the agent's edit would write under the user's gate directory."""
    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        return None
    path = Path(payload.get("cwd") or ".", raw).resolve()
    gate_home = home().resolve()
    return _deny() if path == gate_home or gate_home in path.parents else None


@dataclass
class Unfinished:
    """What a session leaves undone, each told to the agent once."""

    # Lines describing findings to fix.
    must_fix: list[str] = field(default_factory=list)
    # (id, line) of size findings with neither a fix nor a case.
    need_case: list[tuple[str, str]] = field(default_factory=list)
    # Cases made, for the agent's summary.
    cases: list[dict] = field(default_factory=list)

    def keys(self) -> set[str]:
        return {
            *self.must_fix,
            *(f"open:{finding_id}" for finding_id, _ in self.need_case),
            *(f"case:{case['id']}" for case in self.cases),
        }

    def __bool__(self) -> bool:
        return bool(self.must_fix or self.need_case or self.cases)

    def without(self, told: set[str]) -> Unfinished:
        """What is left once everything already in `told` is dropped."""
        return Unfinished(
            [line for line in self.must_fix if line not in told],
            [(found, line) for found, line in self.need_case if f"open:{found}" not in told],
            [case for case in self.cases if f"case:{case['id']}" not in told],
        )


def _unfinished(memory: Memory) -> Unfinished:
    files = list(memory["files"].values())
    found = Unfinished([line for file in files for line in file["fix"]])
    for finding_id, value, line in (size for file in files for size in file["sizes"]):
        case = _case(finding_id)
        if case is not None and _covers(case, value, "case_value"):
            found.cases.append(case)
        else:
            found.need_case.append((finding_id, line))
    return found.without(set(memory["pushed"]))


def _texts(entry: dict) -> list[str]:
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [block.get("text", "") for block in content if block.get("type") == "text"]


def _final_text(transcript: str | None) -> str:
    """What the agent has said since the last thing it was told: its summary, when it is
    stopping. Empty when the transcript cannot be read."""
    try:
        lines = Path(transcript or "").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    said: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "user":
            said = []
        elif entry.get("type") == "assistant":
            said += _texts(entry)
    return "\n".join(said)


def files_left(session_id: str | None) -> list[Path]:
    """The files the session last left with findings to fix, to check again at Stop:
    another file's edit may have fixed them. Files since deleted are forgotten."""
    if not session_id or not _session_path(session_id).is_file():
        return []
    with session(session_id) as memory:
        files = memory["files"]
        for gone in [name for name in files if not Path(name).is_file()]:
            del files[gone]
        return [Path(name) for name, file in files.items() if file["fix"] or file["sizes"]]


def unfinished(payload: dict) -> Unfinished | None:
    """At Stop: what the session leaves undone that the agent has not yet been told, or
    None. The agent is held back once: not again while it answers this hook."""
    session_id = payload.get("session_id")
    if not session_id or not _session_path(session_id).is_file():
        return None
    with session(session_id) as memory:
        found = _unfinished(memory)
        memory["pushed"] = sorted({*memory["pushed"], *found.keys()})
    if payload.get("stop_hook_active"):
        return None
    said = _final_text(payload.get("transcript_path"))
    found.cases = [case for case in found.cases if case["id"] not in said]
    return found or None
