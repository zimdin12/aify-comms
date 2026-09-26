# G: comms-senior-dev, independent whole-release review of 0.7.0

Saved verbatim from two comms messages, 2026-09-26 (1790387169179-8d677374, partial;
1790387906588-8330688d, final). Pins: aify-comms v0.6.22 b7fde7c8 -> v0.7.0 2daa3026, aify-env
9a8642ae -> c4f4608f, aify-wrapper 1946a9c -> 34b2a95. It did not read the other reports in this folder.
RAN = offline witness; READ = source traced.

Verdict: REVISE for 0.7.1.

## Ship-blocking / high impact

- **W01 P1, introduced.** service/terminal_write_queue.py:224-230,564-589; service/main.py:481-485.
  `flush_terminal` removes a pending batch BEFORE its database write, and `flush_all` returns whenever
  pending keys are empty, ignoring `_flush_tasks`. Shutdown reports a successful drain and can close the
  pool while an acknowledged write is still in flight; pool `aclose` does not await checked-out
  operations. RAN isolated blocked-write probe: pending=0, inflight=true, drain=true. Fix: await both
  pending and tracked in-flight flushes within the timeout; test a blocked write through real lifespan
  shutdown. The new shutdown test stubs `flush_all`, so cannot catch this.
- **W02 P1, inherited.** service/reconcilers/dispatch_queue.py:294-300; service/api_core/recovery_writes.py:216-223;
  service/reconcilers/claim_receipts.py:129-136. Claim at t1 stamps a read receipt; requeue clears
  claimed_at but keeps the receipt; reclaim t2 uses INSERT OR IGNORE; failed-run cleanup matches t2 and
  deletes zero t1 receipts. The undelivered source stays read. RAN in-memory claim/requeue/reclaim/fail.
  Fix: on both requeue paths, atomically remove claim-created receipts using the OLD claimed_at before
  clearing it; keep any genuinely user-read receipt.
- **W03 P1, inherited credential boundary.** service/new_dashboard/api-origin.mjs:46-56; api-client.mjs:69-71.
  A link with `?apiOrigin=https://receiver...` persists that origin; a dashboard with a stored service
  key then sends X-API-Key to it. RAN stubbed fetch. Fix: origin-bound credentials; an unconfirmed
  URL-origin change never reuses the old origin's stored key.
- **W04 P1 on Windows, introduced cross-repo.** aify-wrapper wrappers/claude-aify.sh.in:468,
  codex-aify.sh.in:425, hermes-aify.sh.in:402; aify-comms install.sh:28-29,507-510. The resume helper runs
  native Node on the raw rendered `@@BRIDGE_DIR@@` (`/c/...` under Git Bash); install.sh uses
  `path_for_node` elsewhere but not for this substitution. With MSYS path conversion disabled the lookup
  silently fails. RAN native Node `/c/...` absent vs `C:/...` present. Fix: render a Node-native helper
  path; test the installed wrapper with conversion disabled.
- **W05 P2, introduced.** mcp/stdio/agent-for-handle.mjs:17-29 skips `loadSettingsEnv()`, so a
  settings.local.json-only key fails the lookup. READ. Fix: load settings first.
- **W06 P1/P2, introduced.** install.sh:18,91-96,1455. A direct non-interactive reinstall without
  --env-endpoint resets a baked custom aify-env endpoint to loopback; redeploy.sh forwards it, direct
  install does not; v0.6.22 read the installed endpoint. READ. Fix: recover it before rewrite unless
  overridden; cover direct install as well as redeploy.

## Other functional defects

- **W07 P2, introduced.** messages.py:108-118; send_nonce.py:38-53,65-70. A retry of the same nonce and
  toRole after membership changes recomputes recipients, then 409s instead of returning the first id.
  RAN. Fix: identify the nonce by request intent before role/parent resolution.
- **W08 P2, introduced.** inbox.py:47-48,74-79,103-109,132-133. Offset paging on a default (non-peek)
  unread read marks the first page read, so offset two skips still-unread rows. RAN: 6,5 -> 2,1,
  skipping 4,3. Fix: reject a nonzero offset on a mutating unread read.
- **W09 P2, inherited.** messages.py:189,303-305; send_nonce.py:58-70. A fan-out acknowledges an
  unsuffixed msg_id, stores only suffixed rows, and a retry returns a suffixed id. RAN. Fix: return one
  stable logical id both times.
- **W10 P2, inherited.** registry-credential.mjs:88-104; aify-service-endpoint.mjs:139-145. A registry key
  bound to https://host/aify also resolves for https://host/other. RAN. Fix: bind the base path.
- **W11 P2, inherited.** doctor-api-key.mjs:96-114; doctor.js:107,129-131. The doctor prefers the
  checkout .env key over the endpoint-bound registry key. RAN. Fix: bind the .env key to the endpoint.
- **W12 P2, inherited.** aify-env lib/dashboard.mjs:518-529. Close/reopen the start list while an old
  fetch is pending: the old answer fills the new list; Enter starts a stale agent. RAN mocked. Fix: an
  opening-generation token.
- **W13 P2, inherited.** aify-env lib/output-follower.mjs:451-456; console-session.mjs:683-685. An
  AbortError not caused by a local stop leaves status connecting/streaming, so reconnect (FAILED only)
  never runs. RAN. Fix: ignore only when stopped, else FAILED.
- **W14 P2, introduced.** aify-env lib/attach-screen.mjs:40-42; bin/aify-env-attach.mjs:118. Detach
  always emits CSI < u, which can pop a parent terminal's keyboard-protocol level. READ. Fix: pop only
  what was pushed.
- **W15 P2, inherited.** routers/settings.py:106; agents/turn_boundaries.py:50-54. Malformed PUT
  /settings and array POST turn-start still return 500. RAN. Fix with route-level tests.

## Coverage / retirement obligations

- **T01 P2.** The bridge-current tests supply synthetic builds; no test captures each production sender
  (server.js:240, auto-registration.mjs:161,282, sidecars).
- **T02 P2.** test_the_roster_asks_for_live_workers_once.py calls a helper, not GET /agents.
- **T03 P2.** test_redeploy_keeps_the_installed_env_endpoint.py builds a Git-Bash PATH with `;`, and never
  proves the claude stub ran. Also: stamp.sh writes dirty:false when git fails; mark unknown instead.
- **T04 P2, conditional.** Deleted v1->v2 migration scripts and add-service.sh / SUB_SERVICES drop an
  upgrade path and custom compose configuration. State them unsupported, or keep them.
- **T05 P2.** Deleted free-names.test.mjs narrowed coverage. The environments-panel copy command runs
  `aify-env doctor` then `aify-env`, which starts/supersedes an idle incumbent (not a P1: aify-env refuses
  a takeover with processes unless --force).
- **T06 P3.** aify-env splits an all-navigation read, so a pasted `jj` navigates; bracketed paste is
  needed before claiming every paste is inert.

## Earlier partial (W07-D1)

- **P2, inherited.** service/new_dashboard/change-refresh.mjs:190-200,119-126. A full refresh started on
  old messages completes after a partial refresh applied newer ones and overwrites them; the pending
  entry is already gone, so nothing replays. RAN with the production ChangeDrivenRefresh class. Fix:
  serialize full against partial, or generation-gate slice writes.

Checked sound (its words): primary nonce uniqueness index; orphan claim CAS/liveness; peek=true inbox
tie-break and 21+ notify paging; the wrapper resume helper with manual redirects; registry key withheld
from different origins; production status gate limits a wrong start.
