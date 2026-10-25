from __future__ import annotations

from pathlib import Path

from checks import Finding, Target
from gate import Outcome
from keep import Triage, Unfinished, triage
from placement import CHANGED, ELSEWHERE, NEW
from report import FIX_ADVICE, LISTED, TOUCHED_ADVICE, hook_output, stop_reason, text


def outcome(placed: dict[str, list[Finding]] | None = None) -> Outcome:
    result = Outcome(Target(Path("/p/src/mod.py"), Path("/p")), functions=(("solve", 1, 99),))
    result.placed.update(placed or {})
    return result


def finding(line: int, rule: str = "E501") -> Finding:
    return Finding("ruff", rule, line, f"message {line}")


def arguments(count: int) -> Finding:
    message = f"Too many arguments in function definition ({count} > 8)"
    return Finding("ruff", "PLR0913", 1, message)


def triaged(*outcomes: Outcome) -> list[Triage]:
    return triage(list(outcomes), {"told": {}, "files": {}, "pushed": []})


def test_nothing_to_say_says_nothing():
    assert hook_output(triaged(outcome()), "PostToolUse") is None


def test_new_code_findings_block_with_every_finding_listed():
    answer = hook_output(triaged(outcome({NEW: [finding(n) for n in range(30)]})), "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    assert "Fix:\n" in answer["reason"]
    assert "src/mod.py:29: E501 message 29" in answer["reason"]
    assert FIX_ADVICE in answer["reason"]
    assert TOUCHED_ADVICE in answer["reason"]
    assert "propose" not in answer["reason"]


def test_a_size_finding_blocks_with_its_id_and_the_way_to_keep_it():
    answer = hook_output(triaged(outcome({NEW: [arguments(11)]})), "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    reason = answer["reason"]
    assert "Fix, or make the case to keep [id]:" in reason
    assert "PLR0913 Too many arguments in function definition (11 > 8) [" in reason
    assert "propose <id>" in reason
    assert "Only the user can approve" in reason
    assert FIX_ADVICE not in reason


def test_untouched_old_code_is_context_and_never_blocks():
    answer = hook_output(triaged(outcome({ELSEWHERE: [finding(9)]})), "PostToolUseFailure")
    assert answer is not None
    assert "decision" not in answer
    assert answer["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    context = answer["hookSpecificOutput"]["additionalContext"]
    assert "untouched old code: 1 (1 E501)" in context


def test_an_old_function_the_change_touched_blocks_like_new_code():
    answer = hook_output(triaged(outcome({CHANGED: [finding(3)]})), "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    assert "Fix:\n  src/mod.py:3: E501" in answer["reason"]
    assert TOUCHED_ADVICE in answer["reason"]
    assert "not required" not in answer["reason"]


def test_a_size_finding_in_a_touched_old_function_can_be_kept():
    answer = hook_output(triaged(outcome({CHANGED: [arguments(11)]})), "PostToolUse")
    assert answer is not None
    assert "Fix, or make the case to keep [id]:" in answer["reason"]


def reporting(placed: dict[str, list[Finding]]) -> Outcome:
    result = outcome(placed)
    result.touched_block = False
    return result


def test_a_project_that_only_reports_touched_functions_lists_them_as_context():
    answer = hook_output(triaged(reporting({CHANGED: [finding(3)]})), "PostToolUse")
    assert answer is not None
    assert "decision" not in answer
    context = answer["hookSpecificOutput"]["additionalContext"]
    assert "not blocked in this project:\n  src/mod.py:3: E501" in context


def test_reported_touched_functions_are_listed_up_to_the_limit_then_counted():
    (result,) = triaged(reporting({CHANGED: [finding(n) for n in range(LISTED + 4)]}))
    body = text(result)
    assert f"src/mod.py:{LISTED - 1}:" in body
    assert f"src/mod.py:{LISTED}:" not in body
    assert "... and 4 more" in body


def test_new_code_in_a_reporting_project_blocks_without_the_touched_advice():
    answer = hook_output(triaged(reporting({NEW: [finding(1)]})), "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    assert TOUCHED_ADVICE not in answer["reason"]


def test_several_files_block_once_with_the_advice_once():
    first, second = outcome({NEW: [finding(1)]}), outcome({NEW: [finding(2)]})
    answer = hook_output(triaged(first, second), "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    assert answer["reason"].count(FIX_ADVICE) == 1
    assert "src/mod.py:2: E501" in answer["reason"]


def test_a_checker_that_did_not_run_is_said():
    result = outcome()
    result.errors.append("pyright exited 3: bad config")
    answer = hook_output(triaged(result), "PostToolUse")
    assert answer is not None
    assert "pyright exited 3" in answer["hookSpecificOutput"]["additionalContext"]


def test_the_stop_reason_gives_each_case_its_approve_command():
    case = {
        "id": "a1b2c3",
        "rel": "src/mod.py",
        "rule": "PLR0913",
        "function": "solve",
        "case_value": 11,
        "case": "every parameter is a physical constant",
    }
    reason = stop_reason(Unfinished(cases=[case]))
    assert 'src/mod.py: PLR0913 in solve (11): "every parameter is a physical constant"' in reason
    assert "approve a1b2c3" in reason
