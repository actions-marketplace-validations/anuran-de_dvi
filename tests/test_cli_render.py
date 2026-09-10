from datetime import UTC, datetime

from dvi.cli.render import render_json, render_markdown, render_multi_json, render_multi_markdown
from dvi.cli.sources import AssetResult
from dvi.incidents import BusinessImpact, Incident
from dvi.lineage import Criticality, Exposure
from dvi.rca import ChangeEvent, RootCauseCandidate


def _incident():
    change = ChangeEvent(
        id="pr-1",
        timestamp=datetime(2026, 8, 30, tzinfo=UTC),
        targets=["model.shop.stg_orders"],
        label="rename country codes",
    )
    return Incident(
        title="Semantic change in country - rename country codes",
        severity="high",
        summary="Suspected data incident from change 'rename country codes'.",
        primary_cause=RootCauseCandidate(change=change, score=1.0),
        affected_assets={"model.shop.fct_orders"},
        evidence=["country: UK -> GB (40 rows)"],
        confidence=0.87,
    )


def test_markdown_incident_has_verdict_and_marker():
    md = render_markdown(_incident(), asset="model.shop.fct_orders",
                         fail_on="high", gate_failed=True)
    assert "High-severity semantic change detected" in md
    assert "country: UK -> GB (40 rows)" in md
    assert "`model.shop.fct_orders`" in md
    assert "87%" in md               # confidence rendered
    assert "FAILED" in md            # gate line
    assert md.rstrip().endswith("<!-- dvi-report -->")


def test_markdown_no_incident_is_green_report():
    md = render_markdown(None, asset="model.shop.fct_orders",
                         fail_on="high", gate_failed=False)
    assert "No semantic change detected" in md
    assert "<!-- dvi-report -->" in md
    assert "FAILED" not in md


def test_json_incident_schema():
    js = render_json(_incident(), asset="model.shop.fct_orders",
                     fail_on="high", gate_failed=True,
                     generated_at=datetime(2026, 8, 30, tzinfo=UTC))
    assert js["asset"] == "model.shop.fct_orders"
    assert js["severity"] == "high"
    assert js["gate"] == {"fail_on": "high", "failed": True}
    assert js["incident"]["title"].startswith("Semantic change in country")
    assert js["incident"]["affected_assets"] == ["model.shop.fct_orders"]
    assert js["incident"]["confidence"] == 0.87
    assert js["generated_at"] == "2026-08-30T00:00:00+00:00"


def _incident_with_business_impact():
    exposure = Exposure(
        unique_id="exposure.shop.revenue_dashboard",
        name="Revenue Dashboard",
        type="dashboard",
        criticality=Criticality.HIGH,
        owner="data-team",
        url="",
        depends_on=frozenset(),
    )
    impact = BusinessImpact(
        exposures=(exposure,),
        by_type={"dashboard": [exposure]},
        max_criticality=Criticality.HIGH,
    )
    inc = _incident()
    inc.business_impact = impact
    return inc


def test_markdown_renders_business_impact():
    md = render_markdown(_incident_with_business_impact(), asset="model.shop.fct_orders",
                         fail_on="high", gate_failed=True)
    assert "Business impact:" in md
    assert "Revenue Dashboard" in md
    assert "HIGH" in md


def test_json_renders_business_impact():
    js = render_json(_incident_with_business_impact(), asset="model.shop.fct_orders",
                     fail_on="high", gate_failed=True,
                     generated_at=datetime(2026, 8, 30, tzinfo=UTC))
    business = js["incident"]["business_impact"]
    assert isinstance(business, dict)
    assert business["max_criticality"] == "HIGH"
    assert business["exposures"] == [{
        "name": "Revenue Dashboard",
        "type": "dashboard",
        "criticality": "HIGH",
        "owner": "data-team",
    }]


def test_json_no_incident_is_null():
    js = render_json(None, asset="a", fail_on="high", gate_failed=False,
                     generated_at=datetime(2026, 8, 30, tzinfo=UTC))
    assert js["incident"] is None
    assert js["severity"] is None
    assert js["gate"]["failed"] is False


def test_multi_markdown_marker_first_and_summary_rows():
    results = [
        AssetResult("model.a", _incident(), None),
        AssetResult("model.b", None, None),
        AssetResult("model.c", None, "warehouse database not found: x.duckdb"),
    ]
    md = render_multi_markdown(results, fail_on="high", gate_failed=True)
    assert md.splitlines()[0] == "<!-- dvi-report -->"
    assert "| Asset | Result |" in md
    assert "`model.a`" in md and "High" in md
    assert "`model.b`" in md and "clean" in md
    assert "ERRORED" in md and "x.duckdb" in md
    assert "## model.a" in md          # drill-down heading
    assert "FAILED" in md              # gate line present


def test_multi_json_sorted_assets_and_worst_severity():
    results = [
        AssetResult("model.a", _incident(), None),
        AssetResult("model.b", None, "boom"),
    ]
    js = render_multi_json(results, fail_on="high", gate_failed=True,
                           worst_severity="high",
                           generated_at=datetime(2026, 8, 30, tzinfo=UTC))
    assert [a["asset"] for a in js["assets"]] == ["model.a", "model.b"]
    assert js["assets"][0]["incident"] is not None
    assert js["assets"][0]["severity"] == "high"
    assert js["assets"][1]["error"] == "boom"
    assert js["assets"][1]["incident"] is None
    assert js["gate"]["worst_severity"] == "high"
    assert js["gate"]["failed"] is True
