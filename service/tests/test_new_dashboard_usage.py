"""New dashboard (8801) usage-screen wiring — string-match smoke test (2026-06-26)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "service" / "new_dashboard"
APP = (ROOT / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "styles.css").read_text(encoding="utf-8")


def test_containers_present():
    assert 'id="usage-pools"' in HTML
    assert 'id="usage-consumption"' in HTML


def test_render_fns_and_fetch():
    # render-only (read from state.analytics.usage/consumption); fetch happens in loadAnalytics
    assert "function renderUsagePools" in APP
    assert "function renderUsageConsumption" in APP
    assert "api('/usage')" in APP
    assert "api('/usage/consumption')" in APP
    # called from the analytics page render
    assert "renderUsagePools();" in APP and "renderUsageConsumption();" in APP
    # the fetch is throttled + in-flight-guarded (no per-render storm)
    assert "state.analytics.loading) return" in APP
    assert "state.analytics.usage" in APP


def test_five_hour_label_uses_five_hour_reset():
    # A weekly reset must not be rendered beside the 5h label when the 5h
    # window is absent (the live OpenAI response currently has that shape).
    assert "const fiveHourReset = f.resets_at" in APP
    assert "fiveHourReset ? ' · ' + esc(fiveHourReset)" in APP


def test_css_present():
    assert ".usage-pool-card" in CSS
    assert ".usage-consumption-table" in CSS
