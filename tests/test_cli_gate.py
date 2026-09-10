# tests/test_cli_gate.py
import pytest

from dvi.cli.gate import SEVERITY_LEVELS, exit_code, gate_failed, worst_severity


@pytest.mark.parametrize(
    "severity,fail_on,expected",
    [
        ("high", "high", True),
        ("critical", "high", True),
        ("medium", "high", False),
        ("low", "low", True),
        ("medium", "low", True),
        (None, "low", False),
        (None, "high", False),
        ("low", "critical", False),
    ],
)
def test_gate_failed_matrix(severity, fail_on, expected):
    assert gate_failed(severity, fail_on) is expected


def test_exit_code_maps_gate():
    assert exit_code("high", "high") == 1
    assert exit_code("medium", "high") == 0
    assert exit_code(None, "high") == 0


def test_severity_levels_ordered():
    assert SEVERITY_LEVELS == ("low", "medium", "high", "critical")


def test_worst_severity_picks_highest_rank():
    assert worst_severity(["low", "critical", "high", None]) == "critical"


def test_worst_severity_all_none_is_none():
    assert worst_severity([None, None]) is None
    assert worst_severity([]) is None
