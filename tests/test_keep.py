"""Keeping a size finding: the agent's case, the user's approval, and what each session is
told. The checkers do not run here; findings are made by hand."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import keep
from checks import COGNITIVE_RULE, Finding, Target
from gate import Outcome
from keep import Triage
from placement import CHANGED, NEW

TARGET = Target(Path("/p/src/solver.py"), Path("/p"))
# solve holds a nested helper on lines 5 to 8.
FUNCTIONS = (("solve", 1, 20), ("solve.step", 5, 8))


def arguments(count: int, line: int = 1) -> Finding:
    message = f"Too many arguments in function definition ({count} > 8)"
    return Finding("ruff", "PLR0913", line, message)


def fresh() -> keep.Memory:
    return {"told": {}, "files": {}, "pushed": []}


def check(memory: keep.Memory, *findings: Finding, where: str = NEW) -> Triage:
    """One check of the file, as a hook makes it: triaged, then remembered."""
    outcome = Outcome(TARGET, functions=FUNCTIONS)
    outcome.placed[where] += findings
    (result,) = keep.triage([outcome], memory)
    keep.remember([result], memory)
    return result


def only_id(result: Triage) -> str:
    (size,) = result.keep
    return size.id


def approved_by_hand(memory: keep.Memory, count: int) -> str:
    finding_id = only_id(check(memory, arguments(count)))
    assert keep.propose(finding_id, "every parameter is a physical constant") == 0
    assert keep.approve(finding_id) == 0
    return finding_id


def test_a_size_finding_is_named_by_its_function_and_measured():
    size = keep.size_of(Outcome(TARGET, functions=FUNCTIONS), arguments(11))
    assert size is not None
    assert (size.function, size.value, size.name) == ("solve", 11, "PLR0913 in solve (11)")


def test_the_innermost_function_owns_a_finding():
    size = keep.size_of(Outcome(TARGET, functions=FUNCTIONS), arguments(9, line=6))
    assert size is not None
    assert size.function == "solve.step"


def test_cognitive_complexity_is_measured_from_the_gates_own_message():
    message = "solve has cognitive complexity 19 (limit 15)"
    finding = Finding("complexipy", COGNITIVE_RULE, 1, message)
    size = keep.size_of(Outcome(TARGET, functions=FUNCTIONS), finding)
    assert size is not None
    assert size.value == 19


@pytest.mark.parametrize(
    "finding",
    [
        Finding("ruff", "SIM108", 2, "Use ternary operator"),
        Finding("pyright", "reportArgumentType", 2, "Argument of type ..."),
        Finding("ruff", "PLR1702", 30, "Too many nested blocks (4 > 3)"),
    ],
    ids=["style", "types", "outside every function"],
)
def test_only_a_size_finding_in_a_function_can_be_kept(finding: Finding):
    assert keep.size_of(Outcome(TARGET, functions=FUNCTIONS), finding) is None


def test_a_size_finding_blocks_once_per_session_until_it_grows():
    memory = fresh()
    assert check(memory, arguments(11)).blocking
    again = check(memory, arguments(11))
    assert not again.blocking
    assert [size.value for size in again.open] == [11]
    assert check(memory, arguments(12)).blocking
    assert check(fresh(), arguments(12)).blocking


def test_a_finding_to_fix_blocks_every_time():
    memory = fresh()
    style = Finding("ruff", "SIM108", 3, "Use ternary operator")
    assert check(memory, style).fix == [style]
    assert check(memory, style).fix == [style]


def test_a_case_needs_an_open_finding_and_words(capsys: pytest.CaptureFixture[str]):
    assert keep.propose("abcdef", "reason") == 1
    assert keep.propose("../../etc", "reason") == 1
    finding_id = only_id(check(fresh(), arguments(11)))
    assert keep.propose(finding_id, "   ") == 1
    assert keep.propose(finding_id, "every parameter is a physical constant") == 0
    assert f"approve {finding_id}" in capsys.readouterr().out


def test_a_case_stops_the_block_until_the_function_grows_past_it():
    memory = fresh()
    finding_id = only_id(check(memory, arguments(11)))
    keep.propose(finding_id, "every parameter is a physical constant")
    later = fresh()
    kept = check(later, arguments(11))
    assert not kept.blocking
    assert [(size.id, case) for size, case in kept.cased] == [
        (finding_id, "every parameter is a physical constant")
    ]
    assert check(later, arguments(13)).blocking


def test_approval_needs_a_case():
    finding_id = only_id(check(fresh(), arguments(11)))
    assert keep.approve(finding_id) == 1
    assert not (keep.home() / keep.LOG).exists()


def test_an_approval_is_kept_in_the_users_home_and_outlives_the_case():
    finding_id = approved_by_hand(fresh(), 11)
    (entry,) = [json.loads(line) for line in (keep.home() / keep.LOG).read_text().splitlines()]
    assert entry["function"] == "solve"
    assert entry["rule"] == "PLR0913"
    assert entry["value"] == 11
    assert entry["case"] == "every parameter is a physical constant"
    assert keep.approve(finding_id) == 1


def test_an_approved_function_is_cited_in_new_and_old_code():
    approved_by_hand(fresh(), 11)
    for where in (NEW, CHANGED):
        result = check(fresh(), arguments(10), where=where)
        assert not result.blocking
        ((size, approval),) = result.approved
        assert (size.value, approval["value"]) == (10, 11)


def test_an_approved_function_grown_past_its_approval_blocks_again():
    approved_by_hand(fresh(), 11)
    result = check(fresh(), arguments(13))
    assert result.blocking
    assert result.worse == {only_id(result): 11}


def test_pending_lists_each_case(capsys: pytest.CaptureFixture[str]):
    assert keep.pending() == 0
    assert "No kept findings" in capsys.readouterr().out
    finding_id = only_id(check(fresh(), arguments(11)))
    keep.propose(finding_id, "every parameter is a physical constant")
    capsys.readouterr()
    keep.pending()
    listed = capsys.readouterr().out
    assert f"{finding_id}  p/src/solver.py: PLR0913 in solve (11)" in listed
    assert "every parameter is a physical constant" in listed


def test_a_session_is_remembered_and_no_session_is_not():
    with keep.session("s1") as memory:
        check(memory, arguments(11))
    with keep.session("s1") as memory:
        assert not check(memory, arguments(11)).blocking
    with keep.session(None) as memory:
        check(memory, arguments(11))
    with keep.session(None) as memory:
        assert check(memory, arguments(11)).blocking


def stop(transcript: Path | None = None, *, active: bool = False) -> keep.Unfinished | None:
    payload = {"session_id": "s1", "stop_hook_active": active}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    return keep.unfinished(payload)


def test_stop_holds_the_agent_back_once_for_a_finding_with_no_case():
    with keep.session("s1") as memory:
        finding_id = only_id(check(memory, arguments(11)))
    left = stop()
    assert left is not None
    assert [found for found, _ in left.need_case] == [finding_id]
    assert stop() is None


def test_stop_asks_once_for_each_case_in_the_summary():
    with keep.session("s1") as memory:
        finding_id = only_id(check(memory, arguments(11)))
    keep.propose(finding_id, "every parameter is a physical constant")
    left = stop()
    assert left is not None
    assert [case["id"] for case in left.cases] == [finding_id]
    assert left.need_case == []
    assert stop() is None


def test_stop_does_not_ask_while_the_agent_answers_it():
    with keep.session("s1") as memory:
        check(memory, arguments(11))
    assert stop(active=True) is None
    assert stop() is None


def test_stop_does_not_ask_for_a_case_the_summary_already_gives(tmp_path: Path):
    with keep.session("s1") as memory:
        finding_id = only_id(check(memory, arguments(11)))
    keep.propose(finding_id, "every parameter is a physical constant")
    transcript = tmp_path / "transcript.jsonl"
    said = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": finding_id}]}},
    ]
    transcript.write_text("\n".join(json.dumps(entry) for entry in said))
    assert stop(transcript) is None


def test_stop_has_nothing_to_say_without_a_session():
    assert keep.unfinished({}) is None
    assert stop() is None


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("uv run --no-project python /x/quality_gate approve a1b2c3", True),
        ("uv run python '/x y/quality_gate' approve", True),
        ("uv run python /x/quality_gate propose a1b2c3 'reason'", False),
        ("git commit -m 'approve the plan'", False),
    ],
)
def test_the_agent_is_denied_the_approve_command(command: str, *, denied: bool):
    answer = keep.deny_bash({"tool_input": {"command": command}})
    assert (answer is not None) == denied
    if answer:
        assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_agent_is_denied_edits_to_the_users_approvals(tmp_path: Path):
    log = keep.home() / keep.LOG
    assert keep.deny_edit({"tool_input": {"file_path": str(log)}}) is not None
    relative = os.path.relpath(log, tmp_path)
    assert keep.deny_edit({"cwd": str(tmp_path), "tool_input": {"file_path": relative}})
    assert keep.deny_edit({"tool_input": {"file_path": str(tmp_path / "mod.py")}}) is None
