# Which writer fails the queued seed when a defined agent is stopped (2026-10-21)

This is a development run, not qualification. Gate `87f83a97` (probe records `summary`, `error_text` and dispatch events),
pair comms `78270d30` + aify-env `f704bcdf`, `--scenarios=stop`. The console ends `case stop-restart: FAIL`, cleanup
complete, exit 1.

What happened to the seed run `run_1792598417783_01e4b167`, from `stop.evidence.json`:

- `queued` at 16:00:17Z;
- `terminal_closed` at 16:00:27Z, with body "Terminal was KILLED by 1 before an explicit reply was recorded.
  terminalId=term_…". The run ended `failed` with that text as its `summary` and `error_text`;
- `handoff` at 16:00:44Z, "Auto-mirrored missing handoff to gate-sender": the sender was sent a fallback message about
  the failed run.

There is no `agent_stopped` event, so `_apply_agent_stop_or_resume`'s cancel never ran for this agent. The writer is the
terminal-close path whose event type is `terminal_closed` (`service/reconcilers/terminal_runs.py`).
