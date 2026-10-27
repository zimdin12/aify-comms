"""How a comms tool describes an agent's runtime and wake path (port of `mcp/stdio/agent-summary.mjs`).

The bridge classified the wake path itself because its local mode had no service to ask. The service puts
`wakeMode` on every agent record it serves (`service/api_core/records.py`), so this reads it, and the
normalizers are the service's own.

The bridge filled a missing `machineId` with the machine the bridge ran on. The service is not on the
agent's machine, so a missing one prints `?`: unknown, said as unknown.
"""

from __future__ import annotations

from service.api_core.runtime import _normalize_runtime, _normalize_session_mode


def runtime_summary(info: dict) -> str:
    return f"{_normalize_runtime(info.get('runtime'))} @ {info.get('machineId') or '?'} ({_normalize_session_mode(info.get('sessionMode'))})"


def wake_mode_summary(info: dict) -> str:
    return str(info.get("wakeMode") or "").strip() or "?"
