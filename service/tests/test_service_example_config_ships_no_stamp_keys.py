"""The shipped example config must not carry a key the loader refuses.

`setup.sh` creates `config/service.json` from `config/service.example.json`, and every install guide
now tells an operator to run `setup.sh` — so whatever is in the example lands on every new host.

The example shipped `"version": "4.0.0"`. That key is stamp-owned: the loader refuses it, precisely
because a stale `version` in a hand-edited service.json once made an instance report 3.6.6 while
running 0.5.4. So the value was inert, and shipping it anyway is a trap with a delay on it — the next
person to read the example learns that setting a version there is a thing you do, and the only reason
it does no harm is a guard they have not read.

The set is DERIVED from the loader rather than restated, so a sixth stamp-owned field cannot be added
in one place and left unguarded in the other.
"""

import json
from pathlib import Path

from service.config import _STAMP_OWNED_KEYS

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "config" / "service.example.json"


def test_the_example_exists_and_is_a_json_object():
    """Anti-vacuity: setup.sh copies this file, so an unreadable one is its own bug and would make the
    check below pass by having nothing to look at."""
    assert EXAMPLE.exists(), "setup.sh copies this file; it must be there"
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data, "the example must be a non-empty JSON object"


def test_the_owned_set_is_not_empty():
    """If the loader ever stopped declaring the set, the assertion below would pass vacuously."""
    assert _STAMP_OWNED_KEYS, "the loader declares no stamp-owned keys; this gate would prove nothing"


def test_the_example_sets_no_stamp_owned_key():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    present = sorted(k for k in data if k in _STAMP_OWNED_KEYS)
    assert present == [], (
        f"config/service.example.json sets {present}, which the loader refuses. Every new host gets "
        "this file from setup.sh, so it teaches a setting that cannot work."
    )
