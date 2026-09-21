"""Run the registry, aggregate per-dataset + pooled metrics, render markdown.

The same runs feed three numbers at once: false-positive rate (real-vs-real),
recall (injected), and calibration (the pooled (confidence, label) pairs). The
report states the injected-label honesty boundary in prose so a reader cannot
mistake it for observed production incidents.
"""
from __future__ import annotations

from dataclasses import dataclass

from dvi.calibration.loader import load_model
from dvi.calibration.reliability import (
    ReliabilityReport,
    build_reliability_report,
    render_reliability,
)

from .experiments import (
    Pair,
    RealFpReport,
    RealRecallReport,
    injected_recall_report,
    real_vs_real_report,
)
from .registry import RealDataset, build_registry


@dataclass(frozen=True)
class DatasetResult:
    dataset_id: str
    domain: str
    fp: RealFpReport
    recalls: list[RealRecallReport]

    @property
    def fp_rate(self) -> float:
        return self.fp.false_positive_rate

    @property
    def recall(self) -> float:
        hits = sum(r.hits for r in self.recalls)
        trials = sum(r.trials for r in self.recalls)
        return hits / trials if trials else 0.0


@dataclass(frozen=True)
class RealEvalReport:
    datasets: list[DatasetResult]
    reliability: ReliabilityReport

    @property
    def pooled_fp_rate(self) -> float:
        fires = sum(d.fp.fires for d in self.datasets)
        checks = sum(d.fp.checks for d in self.datasets)
        return fires / checks if checks else 0.0

    @property
    def pooled_recall(self) -> float:
        hits = sum(r.hits for d in self.datasets for r in d.recalls)
        trials = sum(r.trials for d in self.datasets for r in d.recalls)
        return hits / trials if trials else 0.0

    @property
    def pooled_hits(self) -> int:
        return sum(r.hits for d in self.datasets for r in d.recalls)

    @property
    def pooled_positives(self) -> int:
        return sum(r.trials for d in self.datasets for r in d.recalls)


def _run_dataset(
    spec: RealDataset, *, n: int, trials: int, seed: int, model
) -> tuple[DatasetResult, list[Pair]]:
    frame = spec.load()
    # Cap n so a dataset never asks for more rows than it can split disjointly.
    max_n = frame.height // 2
    fp = real_vs_real_report(
        frame, columns=spec.fp_columns, n=min(n, max_n), trials=trials,
        seed=seed, model=model,
    )
    recalls: list[RealRecallReport] = []
    for recipe in spec.recipes:
        recalls.append(
            injected_recall_report(
                frame, family=recipe.family, column=recipe.column,
                inject=recipe.inject, n=min(recipe.n, max_n), trials=trials,
                seed=seed, model=model,
            )
        )
    pairs: list[Pair] = list(fp.pairs)
    for r in recalls:
        pairs.extend(r.pairs)
    return DatasetResult(spec.id, spec.domain, fp, recalls), pairs


def run_registry(
    *, n: int = 1000, trials: int = 30, seed: int = 0, committed_only: bool = False
) -> RealEvalReport:
    """Run both experiments over present datasets; pool the calibration pairs.

    committed_only skips local (git-ignored) datasets so CI and the test suite
    stay deterministic and fast regardless of what a developer has prepped.
    """
    model = load_model()
    results: list[DatasetResult] = []
    all_pairs: list[Pair] = []
    for spec in build_registry():
        if committed_only and not spec.committed:
            continue
        result, pairs = _run_dataset(spec, n=n, trials=trials, seed=seed, model=model)
        results.append(result)
        all_pairs.extend(pairs)
    reliability = build_reliability_report(all_pairs)
    return RealEvalReport(datasets=results, reliability=reliability)


def render_report(report: RealEvalReport) -> str:
    """A deterministic markdown report: per-dataset table + pooled + reliability."""
    lines = [
        "# DVI real-data evaluation",
        "",
        "Positives are **injected** into **real distributions** (real data "
        "underneath, ground-truth labels on top) — this is injected-label "
        "evaluation, not observed production incidents. Real-vs-real measures "
        "specificity on unchanged real data.",
        "",
        "| dataset | domain | recall | false-positive rate |",
        "|---------|--------|--------|---------------------|",
    ]
    for d in sorted(report.datasets, key=lambda x: x.dataset_id):
        lines.append(
            f"| {d.dataset_id} | {d.domain} | {d.recall:.3f} | {d.fp_rate:.3f} |"
        )
    lines.append(
        f"| **pooled** | — | {report.pooled_recall:.3f} | "
        f"{report.pooled_fp_rate:.3f} |"
    )
    lines.append("")
    lines.append(render_reliability(report.reliability))
    return "\n".join(lines)
