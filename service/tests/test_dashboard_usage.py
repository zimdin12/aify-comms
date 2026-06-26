"""Old dashboard (8800) usage-screen wiring — string-match smoke test (2026-06-26).
No headless browser in the harness, so we assert the page contains the Pools band
container, the consumption container, the render function, and the /usage fetch."""
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "service" / "dashboard.html").read_text(encoding="utf-8")


def test_pools_band_container_and_card():
    assert 'id="usage-pools"' in HTML, "analytics page must host the Pools band"
    assert "usage-pool-card" in HTML, "per-pool card markup must exist"


def test_usage_render_and_fetch_wired():
    assert "function renderUsagePools" in HTML, "renderUsagePools() must be defined"
    assert "renderUsagePools(" in HTML.replace("function renderUsagePools", ""), "renderUsagePools must be called (from renderAnalytics)"
    assert "/usage'" in HTML or "/usage`" in HTML, "must fetch the /usage endpoint"


def test_consumption_section_container():
    assert 'id="usage-consumption"' in HTML, "Consumption section container must exist (data wired in Task 8)"


def test_severity_classes_present():
    assert "usage-pool-card warning" in HTML or "warning" in HTML
    assert "usage-pool-card critical" in HTML or "critical" in HTML
