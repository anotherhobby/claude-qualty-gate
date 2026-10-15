"""What the gate says about checked files: to Claude through a hook, or to a terminal."""

from __future__ import annotations

from collections import Counter

from checks import Finding
from gate import Outcome
from placement import CHANGED, ELSEWHERE

# Old code is listed up to this many findings and counted past it.
LISTED = 15

ADVICE = (
    "Fix these before moving on. Refactor rather than suppress: guard clauses and early "
    "returns instead of nesting, helpers instead of long functions, keyword-only "
    "parameters instead of long positional lists. An inline noqa does not silence the "
    "size and shape rules; a limit that seems wrong is the user's to change in the "
    "project's configuration."
)


def _lines(rel: str, findings: list[Finding], limit: int | None = None) -> list[str]:
    shown = findings if limit is None else findings[:limit]
    lines = [f"  {rel}:{f.line}: {f.rule} {f.message}" for f in shown]
    if len(findings) > len(shown):
        lines.append(f"  ... and {len(findings) - len(shown)} more")
    return lines


def _counts(findings: list[Finding]) -> str:
    by_rule = Counter(f.rule for f in findings)
    return ", ".join(f"{n} {rule}" for rule, n in by_rule.most_common())


def text(outcome: Outcome) -> str:
    """Everything worth saying about one file, or "" when there is nothing."""
    rel, placed, parts = outcome.rel, outcome.placed, []
    if outcome.blocking:
        parts.append(f"quality gate: {len(outcome.blocking)} finding(s) in new code in {rel}.")
        parts += _lines(rel, outcome.blocking)
    if placed[CHANGED]:
        parts.append(f"In old code this change touched in {rel} (cleanup welcome, not required):")
        parts += _lines(rel, placed[CHANGED], LISTED)
    if elsewhere := placed[ELSEWHERE]:
        parts.append(
            f"Elsewhere in {rel}, untouched old code: {len(elsewhere)} ({_counts(elsewhere)})."
        )
    if outcome.errors:
        parts.append(f"quality gate: some checkers did not run on {rel}, so their silence")
        parts.append("proves nothing about it:")
        parts += [f"  {error}" for error in outcome.errors]
    return "\n".join(parts)


def hook_output(outcomes: list[Outcome], event: str) -> dict | None:
    """The answer to a hook `event`: a block when any new code has findings, context
    otherwise, None when there is nothing to say."""
    bodies = [body for outcome in outcomes if (body := text(outcome))]
    if not bodies:
        return None
    if any(outcome.blocking for outcome in outcomes):
        return {"decision": "block", "reason": "\n".join([*bodies, ADVICE])}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(bodies)}}
