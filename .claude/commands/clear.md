# clear

Clear messages, shared files, or all data using comms_clear.

## Arguments
- `$ARGUMENTS` — What to clear: "inbox", "shared", "agents", "all". Optionally add hours: "inbox 24" to clear messages older than 24h.

## Instructions
Parse arguments. First word = target; second word (if a number) = olderThanHours. Call comms_clear.
For `inbox`, pass `agentId` = your registered agent ID; for `agents`, pass the one agent the user names.
Without `agentId`, `inbox` deletes every agent's direct messages and `agents` removes every identity on
the service; `shared` and `all` always span every team. Confirm with the user before any call that is
not scoped to one `agentId`.
Every target honours `olderThanHours`. Under a cutoff, `all` leaves sessions, spawn records and environments alone, since they have no single age.
