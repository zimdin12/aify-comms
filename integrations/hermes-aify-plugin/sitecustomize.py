"""Python startup hook for hermes-aify.

This file is loaded automatically by Python when its directory is on
PYTHONPATH. The hermes-aify wrapper enables it with AIFY_HERMES_PLUGIN=1.
"""

from __future__ import annotations

import os
import sys


def _enabled() -> bool:
    value = os.environ.get("AIFY_HERMES_PLUGIN", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


if _enabled():
    try:
        from aify_hermes_plugin.bootstrap import install

        install()
    except Exception as exc:  # pragma: no cover - startup failure path
        print(
            f"[aify-hermes-plugin] startup failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        if os.environ.get("AIFY_HERMES_PLUGIN_STRICT", "").strip() == "1":
            raise
