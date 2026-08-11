"""The service's one UTC timestamp format.

Extracted for v0.5 slice 1a. `_now()` lived in `api_v2.py` and is needed by reconcilers moving out
of it — importing it back from the router would create exactly the cycle the extraction exists to
remove, so it lands in a module with no dependencies of its own.

Two lines, no imports beyond `time`, no state. Deliberately not a "utils" module: this file holds
the wall-clock format and nothing else, so nothing acquires a dependency on the router by
association.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

ISO_SECONDS = "%Y-%m-%dT%H:%M:%SZ"


def now() -> str:
    """UTC, second resolution, `Z`-suffixed — the format every timestamp column in this service
    stores and every comparison assumes. Changing it is a data migration, not a formatting choice:
    stored timestamps are compared LEXICALLY in SQL throughout."""
    return time.strftime(ISO_SECONDS, time.gmtime())


def iso_to_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0
