"""Real-data evaluation harness: specificity + injected recall on real datasets."""
from __future__ import annotations

from .runner import RealEvalReport, render_report, run_registry

__all__ = ["RealEvalReport", "render_report", "run_registry"]
