"""Render an Incident (or its absence) into a Markdown PR comment and JSON.

Both artifacts come from the same Incident, so the human and machine views can
never disagree. The Markdown always ends with an HTML marker so the GitHub
Action can find and update the same sticky comment on each run.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from dvi.incidents import Incident, render_business_impact

if TYPE_CHECKING:
    from .sources import AssetResult

MARKER = "<!-- dvi-report -->"
_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🔴"}


def _incident_detail_lines(incident: Incident) -> list[str]:
    lines: list[str] = []
    emoji = _EMOJI.get(incident.severity, "🔴")
    lines.append(
        f"{emoji} **{incident.severity.capitalize()}-severity "
        f"semantic change detected**"
    )
    lines.append("")
    lines.append(f"### {incident.title}")
    lines.append("")
    lines.append(incident.summary)
    if incident.confidence is not None:
        lines.append("")
        lines.append(f"**Confidence:** {incident.confidence:.0%}")
    if incident.evidence:
        lines.append("")
        lines.append("**Evidence:**")
        lines.extend(f"- {e}" for e in incident.evidence)
    if incident.affected_assets:
        rendered = ", ".join(f"`{a}`" for a in sorted(incident.affected_assets))
        lines.append("")
        lines.append(f"**Affected downstream assets:** {rendered}")
    if incident.business_impact is not None:
        lines.append("")
        lines.extend(bl.strip() for bl in render_business_impact(incident.business_impact))
    return lines


def render_markdown(
    incident: Incident | None,
    *,
    asset: str,
    fail_on: str,
    gate_failed: bool,
) -> str:
    lines: list[str] = []
    if incident is None:
        lines.append("✅ **No semantic change detected**")
        lines.append("")
        lines.append(f"Asset: `{asset}`")
    else:
        lines.extend(_incident_detail_lines(incident))
    lines.append("")
    lines.append(f"_Gate: fail_on=`{fail_on}` — {'FAILED' if gate_failed else 'passed'}_")
    lines.append("")
    lines.append(MARKER)
    return "\n".join(lines)


def _incident_json(incident: Incident) -> dict:
    business = None
    if incident.business_impact is not None:
        impact = incident.business_impact
        business = {
            "exposures": [
                {
                    "name": e.name,
                    "type": e.type,
                    "criticality": e.criticality.name,
                    "owner": e.owner,
                }
                for e in impact.exposures
            ],
            "max_criticality": (
                impact.max_criticality.name if impact.max_criticality else None
            ),
        }
    return {
        "title": incident.title,
        "severity": incident.severity,
        "summary": incident.summary,
        "confidence": incident.confidence,
        "affected_assets": sorted(incident.affected_assets),
        "evidence": list(incident.evidence),
        "business_impact": business,
    }


def render_json(
    incident: Incident | None,
    *,
    asset: str,
    fail_on: str,
    gate_failed: bool,
    generated_at: datetime,
) -> dict:
    inc: dict | None = None
    if incident is not None:
        inc = _incident_json(incident)
    return {
        "asset": asset,
        "severity": incident.severity if incident else None,
        "incident": inc,
        "gate": {"fail_on": fail_on, "failed": gate_failed},
        "generated_at": generated_at.isoformat(),
    }


def _summary_cell(result: AssetResult) -> str:
    if result.error is not None:
        # Keep the error on one table cell: collapse whitespace/newlines and
        # escape pipes so a multi-line or pipe-bearing error can't break the row.
        safe = " ".join(result.error.split()).replace("|", r"\|")
        return f"⚠️ ERRORED — {safe}"
    if result.incident is None:
        return "✅ clean"
    emoji = _EMOJI.get(result.incident.severity, "🔴")
    return f"{emoji} {result.incident.severity.capitalize()}"


def render_multi_markdown(
    results: list[AssetResult], *, fail_on: str, gate_failed: bool
) -> str:
    lines: list[str] = [MARKER, ""]
    lines.append(f"## DVI report — {len(results)} assets")
    lines.append("")
    lines.append("| Asset | Result |")
    lines.append("| --- | --- |")
    for r in results:
        lines.append(f"| `{r.name}` | {_summary_cell(r)} |")
    lines.append("")
    lines.append(f"_Gate: fail_on=`{fail_on}` — {'FAILED' if gate_failed else 'passed'}_")
    for r in results:
        lines.append("")
        lines.append(f"## {r.name}")
        lines.append("")
        if r.error is not None:
            lines.append(f"⚠️ **ERRORED** — {r.error}")
        elif r.incident is None:
            lines.append("✅ No semantic change detected")
        else:
            lines.extend(_incident_detail_lines(r.incident))
    return "\n".join(lines)


def render_multi_json(
    results: list[AssetResult], *, fail_on: str, gate_failed: bool,
    worst_severity: str | None, generated_at: datetime,
) -> dict:
    assets = []
    for r in results:
        inc = None
        if r.incident is not None:
            inc = _incident_json(r.incident)
        assets.append({
            "asset": r.name,
            "severity": r.incident.severity if r.incident else None,
            "incident": inc,
            "error": r.error,
        })
    return {
        "assets": assets,
        "gate": {"fail_on": fail_on, "failed": gate_failed,
                 "worst_severity": worst_severity},
        "generated_at": generated_at.isoformat(),
    }
