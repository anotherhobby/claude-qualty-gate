from __future__ import annotations

from pathlib import Path

from checks import Finding, Target
from gate import Outcome
from placement import CHANGED, ELSEWHERE, NEW
from report import ADVICE, LISTED, hook_output, text


def outcome(placed: dict[str, list[Finding]] | None = None) -> Outcome:
    result = Outcome(Target(Path("/p/src/mod.py"), Path("/p")))
    result.placed.update(placed or {})
    return result


def finding(line: int, rule: str = "C901") -> Finding:
    return Finding("ruff", rule, line, f"message {line}")


def test_nothing_to_say_says_nothing():
    assert hook_output([outcome()], "PostToolUse") is None


def test_new_code_findings_block_with_every_finding_listed():
    answer = hook_output([outcome({NEW: [finding(n) for n in range(30)]})], "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    assert "src/mod.py:29: C901 message 29" in answer["reason"]


def test_old_code_is_context_and_never_blocks():
    old = outcome({CHANGED: [finding(3)], ELSEWHERE: [finding(9)]})
    answer = hook_output([old], "PostToolUseFailure")
    assert answer is not None
    assert "decision" not in answer
    assert answer["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    context = answer["hookSpecificOutput"]["additionalContext"]
    assert "src/mod.py:3: C901" in context
    assert "untouched old code: 1 (1 C901)" in context


def test_old_code_touched_is_listed_up_to_the_limit_then_counted():
    body = text(outcome({CHANGED: [finding(n) for n in range(LISTED + 4)]}))
    assert f"src/mod.py:{LISTED - 1}:" in body
    assert f"src/mod.py:{LISTED}:" not in body
    assert "... and 4 more" in body


def test_several_files_block_once_with_the_advice_once():
    first, second = outcome({NEW: [finding(1)]}), outcome({CHANGED: [finding(2)]})
    answer = hook_output([first, second], "PostToolUse")
    assert answer is not None
    assert answer["decision"] == "block"
    assert answer["reason"].count(ADVICE) == 1
    assert "src/mod.py:2: C901" in answer["reason"]


def test_a_checker_that_did_not_run_is_said():
    result = outcome()
    result.errors.append("pyright exited 3: bad config")
    answer = hook_output([result], "PostToolUse")
    assert answer is not None
    assert "pyright exited 3" in answer["hookSpecificOutput"]["additionalContext"]
