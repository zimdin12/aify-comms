# clear

Clear messages, shared files, or all data using comms_clear.

## Arguments
- `$ARGUMENTS` — What to clear: "inbox", "shared", "agents", "all". Optionally add hours: "inbox 24" to clear messages older than 24h.

## Instructions
Parse arguments. First word = target. Second word (if number) = olderThanHours. Call comms_clear.
