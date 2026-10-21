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

No `agent_stopped` event was captured. That is not proof of absence: no run here shows the probe capturing an
`agent_stopped` event when one exists. That the cancel path never ran rests on source: `control_agent` delegates a
defined agent's stop and returns before `_apply_agent_stop_or_resume`. The writer that was observed is the one emitting
`terminal_closed`, which only `service/reconcilers/terminal_runs.py` does. Which of its three sites wrote it is ASSUMED to
be the queued-run sweep, because the seed was never claimed.
