# 8801 Dashboard — button & console audit (2026-06-18)

Two fresh-eye subagent audits traced every interactive control end-to-end
(control → handler → API/effect) and judged correctness + corner cases. This
records the findings and what was fixed. All file refs are to
`service/new_dashboard/` and `service/routers/api_v2.py`.

## Console / Chat (audit 1)

| # | Sev | Finding | Resolution |
|---|-----|---------|-----------|
| C1 | high | "Start fresh" was identical to "Start console" for every non-pi runtime — `freshContext` is sent but never reaches the adapter, which always `--resume`s the stored handle. Only the toast text differed. The truly-fresh path is Reset (`recreate`). | Dropped the second button. A single **Start console** is shown; **Start fresh console** appears only for pi without a saved handle (the one case it's meaningful). Truly-fresh = the **Reset** lifecycle action. |
| C2 | med | Both start buttons rendered side-by-side; old dashboard showed exactly one. | Single button now (see C1). |
| C3 | high | "Start console" was offered for **resident** agents (e.g. resident claude). Clicking it spawned a managed `--resume` PTY **alongside** the operator's own CLI → conflicting second process / orphan. | `canStartConsole` now excludes `sessionMode==='resident'`. Resident agents instead get a clear note: *"X is resident — its terminal is the CLI you launched, not a dashboard console. Switch it to managed to get one,"* with the switch chip. |
| C4 | high | The chat **Messenger\|Console** toggle was shown for resident DMs and funnelled into the orphan path / a confusing empty state. | Toggle kept (operator wants terminal access), but the resident path is now graceful: the Console view shows the switch-to-managed note (managed agents still get the real inline terminal). No orphan path remains. |
| C5 | med | The console header card duplicated the Details-drawer lifecycle buttons (Restart/Reset/Stop/Switch/Message-in-Chat). | When the console is embedded in **Chat** (`opts.source==='chat'`), the header now shows only console-scoped actions (hermes "Open in new tab" / codex "Connect live console"); lifecycle lives in the chat header + Details drawer. The **Sessions** page keeps the full control set. |

## Sessions / Environments / Diagnostics / Settings / Runs / Files (audit 2)

| # | Sev | Finding | Resolution |
|---|-----|---------|-----------|
| O1 | high | "Restart bridge" environment button always errored — the backend `control_environment` only accepts `stop`/`forget`. It fired with no confirm and 100% failed. | Removed the dead button. Online environments keep **Stop bridge**; offline keep **Forget**. |
| O2 | med | The run-inspector **Retry** confirm read *"will kill 1 active run + N pending controls"* — copied from Interrupt. Retry only sends a new follow-up; it kills nothing. | Confirm now reads *"A new follow-up request will be sent to {target} (queued if busy). It does not interrupt anything running."* |
| O3 | med | Bulk **Remind work** silently no-op'd when the selection was runs-only: it cleared the selection with no reminder + no feedback. | Now toasts *"No reply-contract items in the selection to remind"* and keeps the selection; on success toasts the count. |
| O4 | low | Session **Restart/Reset** stay enabled on terminal (stopped/failed) sessions. | Left as-is — on the Sessions page these legitimately cold-start/revive a stopped session; only the duplicated chat-side copy was removed (C5). |
| O5 | low | File upload ignored the configured **Max shared file size (MB)** on both ends. | Enforced server-side in `share_artifact` (413 over the cap, file + content paths) and pre-checked client-side before POST. |
| O6 | low | **Steer** accepted a whitespace-only body (`uiPrompt` returns raw string). | Both steer entry points now guard `!body.trim()`. |

Controls verified correct (no change needed): run-inspector capability gating
(steer/interrupt only for claimed/running, defense-in-depth 409s, close gated
to non-terminal, open-console gated on a resolvable session); every Settings
key persists + round-trips; spawn-form validation + unavailable-runtime gating;
all destructive actions confirm; checkbox-vs-row-select ordering; contract
close/remind/inspect endpoints.
