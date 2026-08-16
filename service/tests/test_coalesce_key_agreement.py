"""The notification coalescing key, and the fact that two languages compute it independently.

`service/ntfy.py` builds a coalescing key for push alerts; `service/new_dashboard/notify.mjs` builds
one for desktop notifications. The Python docstring says "Same key as notify.mjs" — and nothing
checked it. Two hand-written implementations of one rule, in two languages, with the agreement
recorded only in prose, is the forked-constant class this series has been removing; it is just harder
to see because the fork is a function rather than a literal.

WHAT DRIFT WOULD COST. The key exists to collapse a two-agent ping-pong burst into one alert. If the
two sides disagree — one lowercases and the other does not, one falls back to `channel` and the other
does not — an operator watching both surfaces gets the burst collapsed on one and spammed on the
other, with nothing in either suite failing. Neither implementation can be wrong on its own terms;
they can only be wrong relative to each other.

The Python side additionally had no test of its own. Both halves are here: the rules, and the
agreement, checked BEHAVIOURALLY over a shared corpus rather than by comparing source text — a
regex over two languages proves the code was written a certain way, not that it computes the same
answer.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from service.ntfy import coalesce_key

REPO = pathlib.Path(__file__).resolve().parents[2]
NOTIFY_MJS = REPO / "service" / "new_dashboard" / "notify.mjs"

# Every case is (event, data). Chosen to cover each branch of the rule and each way the two
# implementations could plausibly diverge: the fallback chain, casing, trimming, and absent fields.
CORPUS = [
    ("message_sent", {"from": "manager-bot", "subject": "Ship the thing"}),
    ("message_sent", {"from": "MANAGER-BOT", "subject": "SHIP THE THING"}),
    ("message_sent", {"from": "  manager-bot  ", "subject": "  Ship the thing  "}),
    ("message_sent", {"from": "manager-bot", "subject": ""}),
    ("message_sent", {"from": "", "subject": "no sender"}),
    ("message_sent", {"subject": "no from key at all"}),
    ("channel_message", {"channel": "team-alpha", "subject": "standup"}),
    ("channel_message", {"from": "manager-bot", "channel": "team-alpha", "subject": "both"}),
    ("channel_message", {"channel": "TEAM-ALPHA", "subject": "casing"}),
    ("message_sent", {}),
    ("message_sent", {"from": None, "subject": None}),
    ("message_sent", {"from": "manager-bot", "subject": "pipes | in | subject"}),
    ("message_sent", {"from": "a|b", "subject": "sender with a pipe"}),
    ("", {"from": "manager-bot", "subject": "empty event"}),
]


# ── the rule, on the Python side ─────────────────────────────────────────────────────────────
def test_the_key_is_sender_and_subject_not_the_message_id():
    """THE WHOLE POINT. The id is unique per message, so keying on it would coalesce nothing during
    exactly the burst this exists to collapse."""
    first = coalesce_key("message_sent", {"id": "m1", "from": "x", "subject": "s"})
    second = coalesce_key("message_sent", {"id": "m2", "from": "x", "subject": "s"})
    assert first == second


def test_the_event_is_part_of_the_key():
    """Two different events from one sender about one subject are two alerts, not one."""
    data = {"from": "x", "subject": "s"}
    assert coalesce_key("message_sent", data) != coalesce_key("channel_message", data)


def test_casing_and_whitespace_do_not_split_a_burst():
    canonical = coalesce_key("message_sent", {"from": "manager-bot", "subject": "ship it"})
    assert coalesce_key("message_sent", {"from": "MANAGER-BOT", "subject": "Ship It"}) == canonical
    assert coalesce_key("message_sent", {"from": " manager-bot ", "subject": "\tship it\n"}) == canonical


def test_the_channel_is_the_fallback_when_there_is_no_sender():
    """A channel message has no `from`, and keying every one of them under the same placeholder would
    collapse unrelated channels into one alert."""
    assert coalesce_key("channel_message", {"channel": "alpha", "subject": "s"}) != coalesce_key(
        "channel_message", {"channel": "beta", "subject": "s"}
    )
    assert coalesce_key("channel_message", {"from": "x", "channel": "alpha", "subject": "s"}) == (
        coalesce_key("channel_message", {"from": "x", "channel": "beta", "subject": "s"})
    ), "`from` wins when both are present, so the same sender coalesces across channels"


def test_a_missing_sender_and_channel_collapse_to_a_placeholder():
    assert coalesce_key("message_sent", {"subject": "s"}) == "message_sent|?|s"
    assert coalesce_key("message_sent", {}) == "message_sent|?|"
    assert coalesce_key("message_sent", None) == "message_sent|?|"


def test_none_values_are_treated_as_absent_not_as_the_string_none():
    """A JSON payload carrying explicit nulls must key the same as one omitting the fields."""
    assert coalesce_key("message_sent", {"from": None, "subject": None}) == coalesce_key("message_sent", {})


# ── the agreement ────────────────────────────────────────────────────────────────────────────
def test_python_and_javascript_compute_the_same_key():
    """BEHAVIOURAL, not textual. A regex over two languages proves the code was written a certain
    way; running the same corpus through both proves they compute the same answer, which is the only
    thing an operator watching two surfaces experiences."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH — cross-language agreement check skipped")

    script = (
        f"import {{ coalesceKey }} from {json.dumps(NOTIFY_MJS.as_uri())};\n"
        "let input = '';\n"
        "process.stdin.on('data', (d) => { input += d; });\n"
        "process.stdin.on('end', () => {\n"
        "  const cases = JSON.parse(input);\n"
        "  process.stdout.write(JSON.stringify(cases.map(([e, d]) => coalesceKey(e, d))));\n"
        "});\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(CORPUS),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"

    js_keys = json.loads(proc.stdout)
    py_keys = [coalesce_key(event, data) for event, data in CORPUS]

    assert len(js_keys) == len(CORPUS), "the node side did not answer for every case"
    mismatches = [
        (case, py, js) for case, py, js in zip(CORPUS, py_keys, js_keys) if py != js
    ]
    assert not mismatches, (
        "coalesce_key (service/ntfy.py) and coalesceKey (service/new_dashboard/notify.mjs) disagree. "
        "An operator watching both surfaces would see one burst collapsed and the other spammed.\n  "
        + "\n  ".join(f"{case!r}: python={py!r} js={js!r}" for case, py, js in mismatches)
    )


def test_the_corpus_actually_discriminates():
    """Anti-vacuity: a corpus whose cases all produce the same key would let any two implementations
    agree. It must exercise the branches the two could differ on."""
    keys = [coalesce_key(event, data) for event, data in CORPUS]
    assert len(set(keys)) >= 8, f"only {len(set(keys))} distinct keys — the corpus is too narrow"
    assert any(k.endswith("|") for k in keys), "an empty-subject case is covered"
    assert any("|?|" in k for k in keys), "a no-sender case is covered"
