# Issues opened after the R2 freeze

The [issue map](2026-11-04-issue-map.md) is frozen at the 109 issues the owners approved.
Issues opened later are placed here, so the approved map and its hashes stay unchanged.
The owner confirms the placement when picking an issue up.

| Issue | Group | Placement and what must not regress |
|---|---|---|
| [aify-comms#55](https://github.com/zimdin12/aify-comms/issues/55) | G0, design first | In-session register for an unbound launcher session, lost when tools moved to `/mcp/http` in 0.9.11. Needs comms (bind the MCP session on an explicit register), wrapper and env (lifetime record, lease and hook identity follow the binding). Keep: the model never invents an identity; a bound connection cannot rebind; a live holder is replaced only on request. |
| [aify-comms#56](https://github.com/zimdin12/aify-comms/issues/56) | G0 | Dashboard resume command drops `--dangerously-skip-permissions` so the launcher's own default applies. Keep the hermes and codex commands unchanged. |
| [aify-wrapper#27](https://github.com/zimdin12/aify-wrapper/issues/27) | G0, wrapper | `claude-aify --aify-agent <id>` resumes the agent's pinned session. Keep: launches that must start fresh (managed spawns from a definition, handoff compaction, an explicit new session) still start fresh; a transcript missing on this host falls back with a visible line. Check every caller that passes `--aify-agent` before changing the default. |
| [aify-comms#57](https://github.com/zimdin12/aify-comms/issues/57) | G2, later | Fork an agent at spawn: copy a session under a new agent id. Keep: the source agent's session and identity are untouched; the fork is a new definition, not a rename. |
| [aify-comms#58](https://github.com/zimdin12/aify-comms/issues/58) | G2 | An agent that restarts itself resumes its pending task without the operator. Keep: a restart never replays a task twice; an operator stop still holds. |
| [aify-comms#60](https://github.com/zimdin12/aify-comms/issues/60) | G0 | Coded lifecycle conflicts show `detail.message`, not raw JSON. Delivered in a6573791 and d9197754; closes with that evidence. |
| [aify-env#33](https://github.com/zimdin12/aify-env/issues/33) | G0 | A node-pty whose native binding cannot load starts in piped mode. Delivered in 2fe275c; stays open until one live check on an installed host. |
| [aify-wrapper#28](https://github.com/zimdin12/aify-wrapper/issues/28) | G0 | `--check` exits before claiming a Herdr pane. Delivered in 580536a; stays open until one live check with the Hermes launcher refreshed. |
| [aify-wrapper#29](https://github.com/zimdin12/aify-wrapper/issues/29) | G0 | Warn when a forwarded Claude `--settings` may replace the wrapper's hooks. Delivered in 2c65a14; stays open until one live check. |
| Herdr order (no issue yet) | G2, env | aify-env's agent list can follow Herdr's workspace and tab order as one more mode on its existing order button. Herdr owns the order as a reported fact; unmapped rows keep their order; no polling interval. Built only if the installed Herdr reports a manual reorder. |
| Herdr status detail (proposal) | G2, env, after env#29 | One optional display detail per agent (for example "background shell running"), set and cleared by the event that causes it. Herdr's four reportable states stay as they are; nothing is inferred from elapsed time or terminal activity. |
| Legacy spawn queue (no issue yet) | G2, comms then env | Phase 1, the Environments table showing lifecycle spawn history, landed in db161189. Phase 2 moves cold dispatch of defined agents to a lifecycle `start`. Phase 3 retires env's legacy claim polling and the service's live writes once no legacy row is in flight. Legacy rows stay as history. |

## Order agreed 2026-11-21

Steven agreed this order after the triage of 67 open issues on 2026-11-20. It sequences the
groups above; it does not change any issue's owner or exit.

1. **Close what is finished.** Each owner checks an issue against its own acceptance criteria
   and posts the evidence before closing it. An issue that needs one live check stays open
   until that check runs.
2. **Replace time-based behaviour with state.** Close managed runs on aify-env's turn end
   (comms#31 with env#29), retire clock loops where events exist (comms#48 with env#30), and
   retire the legacy spawn queue, whose rows have no success state and are marked failed by the
   30-minute ceiling.
3. **Finish the spawn queue migration** (phases 2 and 3 above).
4. **Small service fixes**, built in parallel: comms#32, #33, #34, #37, #38, #45, the docs
   issues #39, #40 and #50, and #22, #25 and #29.
5. **One install path**, ahead of the work-PC install: comms#49, #51 and #52; env#12, #27 and
   #28; wrapper#19, #22, #23 and #24.
6. **Agent features:** comms#55, #57 and #58, "Make managed", and the Herdr items.

No fix in this programme may rest on a cap, a budget, a timeout or a time window, and none may
limit what agents can do. aify-wrapper#7, a fixed context budget, was closed as not planned on
2026-11-21 for that reason.

## Found in older documents, not yet filed

These came from KNOWN_ISSUES.md, TARGET_ARCHITECTURE.md and the 2026-11-08 scope decisions.
Each needs a decision to file or drop before work starts.

- A reused agent id inherits a removed Hermes agent's markers.
- A bridge whose key lookup fails once stays keyless until relaunched.
- An `execution-unknown` lifecycle action keeps the agent reserved with nothing to clear it.
- A Windows relaunch can kill a reused process id.
- The installer writes the Hermes config entry by splicing lines.
- Any caller can change a live dispatch run.
- The `aify-comms` verifier moves into the container; launcher version and fingerprint reach the
  service; usage readers move to the launchers.
- Dead code: the virtual-terminal control path and a usage collector with no caller.
- A supervised Windows pass of staging runs E2 to E4.
- `V0.2_PLAN.md` and `NOW.md` are stale and move to history under comms#39.
