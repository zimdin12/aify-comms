"""Statuses an OPERATOR set by hand, which derivation must never override.

`stopped` is the whole set today, and the reason it needs an owner at all is that it is the one input the
status engine is not allowed to argue with. An operator stopping an agent is a DECISION, not an observation:
no amount of live evidence — a fresh heartbeat, an open terminal, a running turn — may promote it back to
`online`, because the operator's intent outlives the evidence.

WHY IT IS NOT IN `vocabulary.py`: that module is the single Python owner of the CROSS-LANGUAGE vocabulary
and loads every word from `service/contracts/vocabulary.json`, so a hardcoded constant there would
contradict the thing it exists to guarantee. Putting this word in the JSON would be a contract change with a
JS agreement test on the other side — a decision, not a side effect of a move.

WHY IT IS NOT IN `status_decision.py`, which is where the subject argues it belongs: that module imports
`managed_env`, which imports `records`, which needs this constant. Placing it there closes a real import
cycle — `records -> status_decision -> managed_env -> records` — and I found that by running the import
rather than by reading the direct imports, which showed no cycle at one level of depth.

A leaf: standard library only, no service imports, so it can never join a cycle itself.
"""

from __future__ import annotations

_MANUAL_STATUSES = {"stopped"}
