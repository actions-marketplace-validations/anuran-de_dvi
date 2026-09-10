"""The `dvi` command-line entrypoint.

`dvi analyze --config dvi.toml --output-dir <dir>`:
load + validate config, analyze the before/after snapshot, render the Markdown
and JSON reports, and return an exit code the CI gate reads (0 clean/below
threshold, 1 gate tripped, 2 could-not-run).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from dvi.incidents import Incident
from dvi.store import SqliteIncidentStore

from .config import DviConfig, DviError, StoreConfig, load_config, normalized_assets
from .gate import gate_failed, worst_severity
from .render import render_json, render_markdown, render_multi_json, render_multi_markdown
from .sources import analyze_one, build_shared_context


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dvi", description="Data Versioning Intelligence")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="Analyze a before/after snapshot and report.")
    analyze.add_argument("--config", default="dvi.toml", help="Path to the dvi.toml config.")
    analyze.add_argument("--output-dir", default=".", help="Directory for report artifacts.")
    analyze.add_argument("--source-before", help="Override the file source 'before' path.")
    analyze.add_argument("--source-after", help="Override the file source 'after' path.")
    return parser


def _apply_overrides(config: DviConfig, args: argparse.Namespace) -> DviConfig:
    if args.source_before is None and args.source_after is None:
        return config
    if config.is_multi_asset:
        raise DviError(
            "--source-before/--source-after apply only to a single-asset "
            "file config, not [[assets]]"
        )
    if config.source is None or config.source.kind != "file":
        raise DviError("--source-before/--source-after apply only to a file source")
    if args.source_before is not None:
        config.source.before = args.source_before
    if args.source_after is not None:
        config.source.after = args.source_after
    return config


def _record_incident(store: StoreConfig | None, asset: str, incident: Incident) -> None:
    """Persist the incident when a `[store]` is configured (opt-in, deterministic).

    The run timestamp is the incident's ``detected_at`` (anchored to declared
    change timestamps, never the wall clock), so re-running the same snapshot
    upserts onto the same row instead of piling up duplicates.
    """
    if store is None:
        return
    run_at = incident.detected_at or incident.change_at
    if run_at is None:  # an incident always carries a detection time; guard anyway
        return
    try:
        with SqliteIncidentStore(store.path) as s:
            s.record(incident, asset=asset, run_at=run_at)
    except sqlite3.Error as e:
        raise DviError(f"could not record incident to {store.path}: {e}") from e


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        config = _apply_overrides(config, args)
        specs = sorted(normalized_assets(config), key=lambda s: s.name)
        shared = build_shared_context(config)
        results = [analyze_one(spec, shared) for spec in specs]

        worst = worst_severity(
            r.incident.severity if r.incident else None for r in results
        )
        failed = gate_failed(worst, config.gate.fail_on)

        if config.is_multi_asset:
            markdown = render_multi_markdown(
                results, fail_on=config.gate.fail_on, gate_failed=failed
            )
            payload = render_multi_json(
                results, fail_on=config.gate.fail_on, gate_failed=failed,
                worst_severity=worst, generated_at=datetime.now(UTC),
            )
        else:
            r = results[0]
            if r.error is not None:  # legacy: one asset errored → exit 2
                raise DviError(r.error)
            markdown = render_markdown(
                r.incident, asset=r.name, fail_on=config.gate.fail_on, gate_failed=failed
            )
            payload = render_json(
                r.incident, asset=r.name, fail_on=config.gate.fail_on,
                gate_failed=failed, generated_at=datetime.now(UTC),
            )

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "dvi-report.md").write_text(markdown, encoding="utf-8")
        (out_dir / "dvi-report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

        for r in results:
            if r.error is None and r.incident is not None:
                _record_incident(config.store, r.name, r.incident)

        errored = any(r.error is not None for r in results)
    except DviError as e:
        print(f"dvi: error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"dvi: error: could not write report to {args.output_dir}: {e}", file=sys.stderr)
        return 2

    print(markdown)
    if failed:
        return 1
    return 2 if errored else 0


if __name__ == "__main__":
    sys.exit(main())
