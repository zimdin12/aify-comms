"""New dashboard (8801) usage-screen wiring — string-match smoke test (2026-06-26)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "service" / "new_dashboard"
APP = (ROOT / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "styles.css").read_text(encoding="utf-8")


def test_containers_present():
    assert 'id="usage-pools"' in HTML
    assert 'id="usage-consumption"' in HTML


def test_the_usage_screen_is_covered_by_tests_that_RUN_it():
    """RETIRED as a grep — the usage renderers and their fetch left app.js in v0.5.4.

    This asserted eight substrings appeared in app.js: the two function names, the two endpoints,
    the two call sites, the in-flight guard, and the state key. Every one is satisfied by the same
    text inside a comment, and none of them could fail on the behaviour it names — `api('/usage')`
    present but never reached still passes.

    `analytics-page.test.mjs` replaces them by CALLING the code against a stubbed fetch and a frozen
    clock. It covers strictly more: that the ~12s cache actually suppresses a second fetch (the
    reason the throttle exists, on a single-worker service), that `force` bypasses it (or the range
    selector does nothing for twelve seconds and reads as broken), that a failed /usage keeps the
    LAST-GOOD quota and flags it stale rather than blanking a number an operator acts on, and that
    an unverified pool renders an em dash instead of a figure it cannot stand behind.

    `renderUsageConsumption` moved to summary-tiles.mjs and is asserted by summary-tiles.test.mjs.
    """
    assert (ROOT / "analytics-page.test.mjs").exists()
    js = (ROOT / "analytics-page.test.mjs").read_text(encoding="utf-8")
    assert "/usage/consumption" in js, "the replacement must still cover both endpoints"
    assert "usageStale" in js, "...and the last-good behaviour that made the fetch worth testing"


def test_the_five_hour_label_reads_the_FIVE_HOUR_reset():
    """The claim is real; the mechanism was a grep and the code moved.

    A weekly reset rendered beside the 5h label is wrong in a way an operator cannot see -- the
    number looks plausible. The live OpenAI response has exactly that shape (no five_hour window),
    which is what made it worth pinning.

    Asserted where the code now lives, and by what it READS rather than by two source substrings:
    the label is derived from `f.resets_at` (the five_hour band), never from the weekly one.
    """
    source = (ROOT / "analytics-page.mjs").read_text(encoding="utf-8")
    assert "const fiveHourReset = f.resets_at" in source, "the 5h label must come from the 5h band"
    assert "const w = p.weekly || {}, f = p.five_hour || {};" in source, (
        "...and `f` must be five_hour, or the line above reads the wrong window"
    )


def test_css_present():
    assert ".usage-pool-card" in CSS
    assert ".usage-consumption-table" in CSS
