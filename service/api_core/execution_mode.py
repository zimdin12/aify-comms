"""Dispatch execution and its compatibility entry point."""
from typing import Any, Optional

from service.api_core.delivery_policy import execution_mode


def _agent_execution_mode(row, requested_runtime: Optional[str] = None, settings: Optional[dict[str, Any]] = None) -> tuple[Optional[str], Optional[str]]:
    return execution_mode(row, requested_runtime, settings)


async def _auto_return_resident_to_managed_if_possible(
    db,
    row,
    *,
    settings: dict[str, Any],
    force: bool = False,
    reason: str = "resident_lease_expired",
):
    # Manual ownership model: resident<->managed changes happen only through
    # PATCH /agents/{id}/session-mode. Keep the helper as a compatibility
    # no-op for older call sites while the automatic paths are removed.
    return row, ""
