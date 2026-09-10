# src/dvi/cli/gate.py
"""The CI gate: does the incident's severity meet the fail threshold?

The severity ladder mirrors dvi.incidents.impact's ordering. Kept here as an
explicit public tuple so the CLI contract (exit codes) does not depend on a
private name in another package.
"""

from __future__ import annotations

from collections.abc import Iterable

SEVERITY_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")


def gate_failed(severity: str | None, fail_on: str) -> bool:
    """True when a detected incident is severe enough to block the PR."""
    if severity is None:
        return False
    return SEVERITY_LEVELS.index(severity) >= SEVERITY_LEVELS.index(fail_on)


def exit_code(severity: str | None, fail_on: str) -> int:
    """1 when the gate trips, else 0. (Error exit 2 is handled by the CLI.)"""
    return 1 if gate_failed(severity, fail_on) else 0


def worst_severity(severities: Iterable[str | None]) -> str | None:
    """Highest severity by SEVERITY_LEVELS rank; None if none present."""
    present = [s for s in severities if s is not None]
    if not present:
        return None
    return max(present, key=SEVERITY_LEVELS.index)
