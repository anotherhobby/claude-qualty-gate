"""What the gate says about checked files: to Claude through a hook, or to a terminal."""

from __future__ import annotations

from collections import Counter

import keep
from checks import Finding
from keep import Triage, Unfinished, describe
from placement import ELSEWHERE

# Old functions a project only reports are listed up to this many findings and counted past it.
LISTED = 15

TOUCHED_ADVICE = (
    "These findings are in code you wrote or edited: new code, and every old function you "
    "changed a line of. A function you edit must pass in full, including what was wrong "
    "with it before you arrived. This is not optional cleanup. Old functions you did not "
    "touch can stay as they are."
)

FIX_ADVICE = (
    'Findings under "Fix:" are lint, type and style. Fix all of them before moving on; none '
    "is ever kept."
)


def size_advice() -> str:
    case = "<the refactor you rejected, and why this is better>"
    propose = f'{keep.command("propose")} <id> "{case}"'
    return "\n".join([
        'Findings under "Fix, or make the case to keep" measure size. Fix these too, in nearly '
        "every case: guard clauses and early returns instead of nesting, helpers that mean "
        "something on their own, keyword-only parameters (a bare * in the signature) instead "
        "of long positional lists. An inline noqa does not silence them.",
        "",
        "Some code is the right size as written: a numeric routine that needs every one of its "
        "parameters, a state machine whose transitions belong together. Keep it only if you "
        "can name the refactor you rejected and say what it would make worse. Splitting a "
        "function into helpers that share all its state, packing arguments into a dict or "
        "**kwargs, or threading flags through to divide a branch is not a fix: it hides the "
        "size from the gate and makes the code worse.",
        "",
        "To keep one, record your case and carry on with your work:",
        f"  {propose}",
        "When you are done, list each kept finding in your summary to the user: where it is, "
        "your case, and the approve command that propose prints. Only the user can approve a "
        "kept finding. You cannot, so don't try.",
    ])


def _lines(rel: str, findings: list[Finding], limit: int | None = None) -> list[str]:
    shown = findings if limit is None else findings[:limit]
    lines = [f"  {describe(rel, f)}" for f in shown]
    if len(findings) > len(shown):
        lines.append(f"  ... and {len(findings) - len(shown)} more")
    return lines


def _counts(findings: list[Finding]) -> str:
    by_rule = Counter(f.rule for f in findings)
    return ", ".join(f"{n} {rule}" for rule, n in by_rule.most_common())


def _blocking(result: Triage) -> list[str]:
    rel = result.outcome.rel
    if not result.blocking:
        return []
    count = len(result.fix) + len(result.keep)
    parts = [f"quality gate: {count} finding(s) in code you wrote or edited in {rel}."]
    if result.fix:
        parts += ["Fix:", *_lines(rel, result.fix)]
    if result.keep:
        parts.append("Fix, or make the case to keep [id]:")
    for size in result.keep:
        parts.append(f"  {describe(rel, size.finding)} [{size.id}]")
        if (approved := result.worse.get(size.id)) is not None:
            parts.append(
                f"    The user approved {approved} here; it now measures {size.value}, so the "
                "approval no longer covers it."
            )
    return parts


def _notes(result: Triage) -> list[str]:
    rel = result.outcome.rel
    notes = [
        f"Still open in {rel}: {size.name} [{size.id}]. Fix it, or record your case: "
        f'{keep.command("propose", size.id)} "<case>"'
        for size in result.open
    ]
    notes += [
        f'Still kept in {rel}: {size.name}, your case: "{case}". Include it in your summary.'
        for size, case in result.cased
    ]
    notes += [
        f"Kept on purpose in {rel}: {size.name}, approved by the user {approval['approved']}: "
        f'"{approval["case"]}". Leave it as it is.'
        for size, approval in result.approved
    ]
    return notes


def _old(result: Triage) -> list[str]:
    rel, parts = result.outcome.rel, []
    if result.reported:
        parts.append(f"In old functions this change touched in {rel}, not blocked in this project:")
        parts += _lines(rel, result.reported, LISTED)
    if elsewhere := result.outcome.placed[ELSEWHERE]:
        parts.append(
            f"Elsewhere in {rel}, untouched old code: {len(elsewhere)} ({_counts(elsewhere)})."
        )
    return parts


def _errors(result: Triage) -> list[str]:
    if not result.outcome.errors:
        return []
    rel = result.outcome.rel
    return [
        f"quality gate: some checkers did not run on {rel}, so their silence",
        "proves nothing about it:",
        *(f"  {error}" for error in result.outcome.errors),
    ]


def text(result: Triage) -> str:
    """Everything worth saying about one file, or "" when there is nothing."""
    return "\n".join([*_blocking(result), *_notes(result), *_old(result), *_errors(result)])


def advice(triages: list[Triage]) -> list[str]:
    """What to do about blocking findings, once for every file checked together."""
    blocking = [result for result in triages if result.blocking]
    if not blocking:
        return []
    parts = ["", TOUCHED_ADVICE] if any(r.outcome.touched_block for r in blocking) else []
    if any(result.fix for result in triages):
        parts += ["", FIX_ADVICE]
    if any(result.keep for result in triages):
        parts += ["", size_advice()]
    return parts


def hook_output(triages: list[Triage], event: str) -> dict | None:
    """The answer to a hook `event`: a block when any new or touched code has findings to
    fix or to make the case for, context otherwise, None when there is nothing to say."""
    bodies = [body for result in triages if (body := text(result))]
    if not bodies:
        return None
    if any(result.blocking for result in triages):
        return {"decision": "block", "reason": "\n".join([*bodies, *advice(triages)])}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(bodies)}}


def stop_reason(left: Unfinished) -> str:
    """Why the agent is held back at Stop, once for each thing it leaves undone."""
    parts = ["Before you finish:"]
    if left.must_fix:
        parts.append("These findings in code you wrote or edited are not fixed. Each must be,")
        parts.append("and lint, type and style are never kept:")
        parts += [f"  {line}" for line in left.must_fix]
    if left.need_case:
        parts.append("These size findings in code you wrote or edited are neither fixed nor")
        parts.append("kept with a case.")
        parts.append("Fix each one, or record your case:")
        parts += [f"  {line} [{finding_id}]" for finding_id, line in left.need_case]
        parts.append(f'  {keep.command("propose")} <id> "<case>"')
        parts.append("Then, in your summary, give the user each kept finding with your case and")
        parts.append("its approve command.")
    if left.cases:
        parts.append("In your summary to the user, list each finding you kept: where it is, your")
        parts.append("case, and its approve command. Only the user can approve one:")
    for case in left.cases:
        what = f"{case['rule']} in {case['function']} ({case['case_value']})"
        parts.append(f'  {case["rel"]}: {what}: "{case["case"]}"')
        parts.append(f"    {keep.command('approve', case['id'])}")
    return "\n".join(parts)
