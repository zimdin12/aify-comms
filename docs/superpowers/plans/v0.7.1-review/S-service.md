# v0.7.1 review — S: the Python service

Lens: `service/` (not `service/new_dashboard/`), `mcp/sse_server.py`, `config/`, `Dockerfile`,
`docker-compose.yml`. Range `v0.6.22..v0.7.0` (44 commits touch the lens). Read at tag `v0.7.0`
(`git archive v0.7.0`), repros run against that extract with host fastapi 0.139.2 / starlette 1.6.0
(the container's fastapi pin; the container itself was not touched).

Evidence: **RAN** = a repro executed and the result observed; **READ** = from source only.

Count: P1 0 · P2 2 · P3 9.

---

## P2

### S1 — Boot fails on a database whose `dispatch_controls` predates `source_message_id` (introduced by 0.7.0, a78846c7)

- **Where:** `service/schema.py:240` adds
  `CREATE INDEX IF NOT EXISTS idx_dispatch_controls_source_message ON dispatch_controls(source_message_id)`
  to `SCHEMA`. `init_db` runs `executescript(SCHEMA)` at `service/db.py:529`, **before**
  `_migrate_dispatch_controls_table` (`db.py:324`) adds that column (`DISPATCH_CONTROL_MIGRATIONS`,
  `db.py:108`). It is the only index in `SCHEMA` on a migration-added column.
- **Scenario:** a DB whose `dispatch_controls` was created 2026-04-14..04-23 (table in e9cacb22, column
  added by migration in 24082d0b) and not booted since. `init_db` raises
  `OperationalError: no such column: source_message_id`; the service does not start. Exposure is narrow
  (any DB booted on any build since 04-23 already has the column), but the pattern is a trap for every
  future index on a migrated column, and nothing tests an old-shape DB.
- **Evidence — RAN:** fresh v0.7.0 DB boots (control); drop the index and the column to mimic the
  pre-04-23 shape, re-run v0.7.0 `init_db` → `OperationalError no such column: source_message_id`. Same
  old-shape DB on a v0.6.22 extract → boots.
- **Fix (S):** move that one `CREATE INDEX` out of `SCHEMA` into `_migrate_dispatch_controls_table`, after
  the column loop (the pattern `_migrate_messages_table` already uses for `idx_messages_nonce_reservation`).
  Test: build a DB, `ALTER TABLE dispatch_controls DROP COLUMN source_message_id`, `init_db` must succeed.

### S2 — The `/listen` disconnect fix never fires in production; a gone caller's messages are still marked read (fix claimed by 540324e1 + d59172ae; behaviour pre-existing)

- **Where:** `service/routers/agents/listen.py:65` and `:103` call `request.is_disconnected()`. The app is
  wrapped in `BaseHTTPMiddleware` subclasses unconditionally (`CrossSiteBrowserMiddleware`,
  `RequestTimingMiddleware`, plus `APIKeyMiddleware` when a key is set; `service/main.py` ~530-573).
  Under `BaseHTTPMiddleware`, `is_disconnected()`'s pre-cancelled scope cancels the middleware's
  `receive_or_disconnect` task group before it reads anything, so it always returns False.
- **Scenario:** `comms_listen` long-poll; the caller goes (tool call cancelled, bridge dies, fetch
  aborted) while parked; a message arrives → the handler fetches it, writes `read_receipts`, commits and
  returns to nobody. The agent's unread no longer shows it. The v0.7 plan's backlog line "d59172ae ...
  narrows the window" is false: it narrows nothing.
- **Evidence — RAN:** drove `GET /api/v1/agents/lc-listener/listen?timeout=1` through `service.main.app`
  (full middleware stack, no lifespan, temp DB) with an ASGI `receive` that answers `http.disconnect`
  after the request message. Connected control: 200, 1 message, receipt `m-1`. Disconnected: 200,
  1 message, receipt `m-1`; tracing `is_disconnected` showed `False, False`. A minimal FastAPI app shows
  the mechanism: 3 successive `is_disconnected()` calls give `[False, True, True]` without middleware and
  `[False, False, False]` with one pass-through `BaseHTTPMiddleware`, while a real
  `await request.receive()` returns `http.disconnect` in both.
- **Why the tests are green:** both new tests in `test_listen_long_poll.py` call the handler function
  directly with a hand-built `Request` / `SimpleNamespace`, bypassing the middleware.
- **Fix:** (M) a watcher task in the handler that awaits `request.receive()` (real await, no cancelled
  scope) and sets a flag on `http.disconnect`; check the flag before committing receipts, and let it end
  the wait early. Or (L) convert the three middlewares to pure ASGI. Either way, test through
  `service.main.app`, not the bare handler. `comms_listen` is deprecated, which bounds the impact; correct
  the backlog/KNOWN_ISSUES wording either way.

---

## P3

### S3 — Turn-start / turn-end still 500 on a non-object JSON body (0.7.0 converted the sibling routes and missed these)

- `service/routers/agents/turn_boundaries.py:51` reads `await request.json()` then `(body or {}).get(...)`.
  b6e4963c made hook-posted routes lenient via `json_object_body(request, lenient=True)`; these two are the
  most hook-posted of all. **RAN:** body `[1]` → `turn-end` 500, `turn-start` 500; `heartbeat` (control,
  converted) 200. Fix (S): `body = await json_object_body(request, lenient=True)` in
  `_posted_by_a_superseded_bridge`.

### S4 — The late-`turn_end` guard (307c14a8) is unreachable: no producer sends a run id on `turn_end`

- `service/status_engine.py:195` ignores a `turn_end` whose `runId` differs from the open turn's. Every
  producer passes `runId: ""`: `turn_boundaries.py:235`, `api_core/turn_busy_signal.py:102`; the only
  bridge poster, `mcp/stdio/agent-state-event.mjs`, posts `/turn-end` with `{}` and `/status-event` with
  `{kind}` only (grepped aify-env and aify-wrapper too: no other `status-event` poster). The commit's claim
  ("a late turn_end for an older run no longer closes a newer turn") describes a path that never runs.
  **READ.** Harmless as written; either drop it or make the turn-end detector send its run id. Its test
  (`test_status_engine.py:99`) exercises the state machine only.

### S5 — Shutdown drain aborts the rest of shutdown on a write error, and its docstring says otherwise (f66fddf3)

- `service/terminal_write_queue.py:577-590` catches only `TimeoutError`. `flush_terminal` re-raises a
  failed write (`:276-283`), so `drain_terminal_output_writes()` raises out of the lifespan `finally`
  (`service/main.py:482`), skipping `CONNECTION_POOL.aclose()`, `CHANGE_FEED.detach()` and
  `container_manager.shutdown()`. The docstring says a failing terminal "would hold shutdown open for
  ever"; it raises instead. **READ.** Fix (S): `except Exception: logger.warning(...); return False`.
  The call-site test (`test_the_lifespan_drains_before_the_pool_closes`) is a source-text order pin and
  cannot see this.

### S6 — A legitimate `clientNonce` retry can get 409 when recipients resolve differently (d6adaa90)

- `service/routers/dispatch_messages/messages.py:111-113` fingerprints the *resolved* recipient set.
  For a `toRole` send, `_resolve_recipient_ids` (`routers/dispatch_messages/shared.py:168`) selects every
  agent with that role, so an agent registered with the role between the first attempt and the retry
  makes the retry a 409 ("already used for a different message") although the first send succeeded. Same
  if the reply parent is deleted between attempts. **READ.** Rare (retries are seconds apart). Fix (S):
  fingerprint the ask (`to`, `toRole`, raw `inReplyTo`) rather than the resolution, or exclude recipients
  for `toRole` sends.

### S7 — `test_deleting_messages_uses_indexes.py` tests a copy of the SQL, on a fresh DB only

- Its `STATEMENTS` list re-types the six statements of `_delete_messages_by_ids`
  (`service/api_core/message_store.py:33-41`); they match today, but a change to the real SQL is
  invisible to it, and a fresh `init_db` is exactly the case that hides S1. **READ.** Fix (S): add the
  old-shape boot test from S1; optionally capture the real statements with a `set_trace_callback`.

### S8 — Deletion policy added to routers rather than `api_core/`

- `/clear` (`service/routers/maintenance.py:45-130`, rewritten in a13b9e8f: per-target cutoff rules,
  unlink-after-commit), `GET /bridges`' liveness query (`service/routers/agents/bridges.py`, new), and
  `_posted_by_a_superseded_bridge` (`turn_boundaries.py:42`, a DB query) are behaviour in routers,
  contrary to docs/ARCHITECTURE.md's layering. **READ.** Fix (S each): move to `api_core/clear.py`,
  `api_core/live_bridges.py`, and next to `bridge_supersede.py`.

### S9 — `service/routers/containers.py` lost its module docstring (f104e0ee)

- `import asyncio` was inserted as line 1, above the docstring, so it is now a bare expression.
  **RAN:** `service.routers.containers.__doc__` is `None` (control: `service.routers.usage.__doc__` set).
  Fix (S): move the import below the docstring.

### S10 — Stale or misformatted text left by 0.7.0 deletions

- `service/api_core/capabilities.py:188` says the argv comes from `adapter.console_command(...)`; b2451d86
  deleted `console_command`. `service/terminal_diagnostics.py:44` reads `_CTRL_RE =re.compile(`
  (3bea61ce). **READ.** Fix (S): reword; reformat.

### S11 — `/analytics/pulse` can 500 on an unparseable `finished_at` (pre-existing, kept by 8886eb79)

- `service/routers/analytics.py:344-348`: `f = _iso_to_epoch(..., default=None)` then `if f <= s` →
  `TypeError` when `f` is None. The pre-0.7 `_ep` had the same hole. The service writes `finished_at` with
  `clock.now()`, so it is latent. **READ.** Fix (S): `if s is None or f is None: continue`.

---

## Checked and sound

- **Nonce reservation (4af0b0ab):** the `nonce_primary` column is added before
  `idx_messages_nonce_reservation` in `_migrate_messages_table`; old rows default 0 so the index builds on
  an existing DB; `INSERT OR IGNORE` + `break` on the lost reservation writes nothing; every
  `send_fingerprint` field exists on `MessageSend` (RAN: field check).
  `test_message_idempotency.py` green (RAN).
- **Orphan requeue CAS (816ae235, 05749aa9):** the UPDATE re-checks status, claim bridge, `claimed_at`
  and bridge freshness with the same predicate as the SELECT; `rowcount` gates the event and invalidation.
- **Console input merge (d95b9ce6):** the merged function is equivalent to both twins; `source` defaults
  to "dispatch" and the send path passes "message_send".
- **Ended-status unification (a944050c):** `_TERMINAL_END_STATUSES` equals both old hand-typed sets (same
  six); the prune now also releases `lost`/`completed` output, as intended.
- **Roster one-query gate (76344f83):** `_agents_with_live_terminal_sessions` uses the same filter and
  `vterm_` exclusion as `_has_live_terminal_session`; `shell` is an idle-at-prompt status, so gating it is
  right.
- **`/clear` (a13b9e8f):** cutoffs compare ISO_SECONDS strings against columns written by `clock.now()`;
  `logger` and `Path` are bound in `maintenance.py`; the bridge's `comms_clear` enum
  (`inbox|shared|agents|all`) is a subset of the new `Literal`; the dashboard does not call `/clear`.
- **Shared-artifact takeover (075a49c7):** `authorize_operator`/`operator_key_from` are imported; the
  check matches DELETE's. (Sender names are self-asserted everywhere, so this stops accidents, not a
  hostile agent; that is the platform's trust model, backlog H-A3.)
- **Terminal listing (d5fbaa96):** `_LISTING_COLUMNS` exist in the table (RAN: compared against
  `PRAGMA table_info` of a fresh DB); the only omitted columns (`activity_*`, `start_intent`) were never
  read by `_terminal_session_to_dict`.
- **`agent_live_state` drop (9497dcb0):** no remaining reader (grep, with the drop statement itself as the
  positive hit); no table references it.
- **Runtime adapter deletions (b2451d86):** every method still called (`resume_command`, `console_argv`,
  `is_resident_ready`, `supports_resident`) remains on the adapters.
- **Undefined names:** `scripts/undefined_name_sweep.py` over all non-test `service/` and `mcp/` Python
  at v0.7.0: CLEAN across 254 files (RAN; control file with an undefined name: 1 finding).
- **`python-dotenv` removal:** nothing imports `dotenv` (control: `httpx` imports found).
- **Safety header:** `service/sse/rendering.py` matches `mcp/stdio/tool-response-format.mjs`
  (`SAFETY_HEADER` = prefix + `TRUST_RULE`) byte for byte.
- **Docker SDK off the loop (f104e0ee):** every blocking call in `ContainerManager` except startup
  `_reconcile_existing` runs via `to_thread`/executor.
- **Bridge build (e9c12c87, 164ac617):** registration and every liveness beat store it; `GET /bridges`
  filters superseded rows and uses the same ISO_SECONDS format its writers use. The non-hex-tag gap is
  already in the plan backlog.
- **Request bodies (74624e35):** `json_object_body` never hands back a non-dict; strict routes now 400
  where they used to 500.
- **Usage age (e0fd85ad), channel create (A15), stamp `dirty`/`sha_from_env`:** as the commits describe.
- **Focused tests:** `test_listen_long_poll.py`, `test_deleting_messages_uses_indexes.py`,
  `test_message_idempotency.py`: 26 passed (RAN). Being green does not help S1 or S2 (see above).

Not checked: whether the bridge actually sends `liveness: true` + `bridgeBuild` on each beat (bridge
lens), and what the bridge does with a 409 from S6.
