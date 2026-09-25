# Lens A: the Python service (`service/`)

Scope: `service/**` plus `mcp/sse_server.py`, read at HEAD `b7fde7c8`. The repo was read-only
throughout. Every "PROVEN IN TESTS" below was run with the repo's own `FastApiTestCase` harness from
a scratch file outside the repo, never against the live service.

How the sweep was done, so a gap is visible:
- **Connection leaks**: an AST scan of every `X = await get_db()` for an `await X.close()` in a
  `finally`. It found 106 checkouts, all closed. Negative control: a synthetic leaky function was
  flagged, so the scan can report a leak.
- **Unawaited coroutines**: an AST scan of calls to any service `async def` that are not awaited or
  passed to a call. 1,473 async calls were seen. Every hit was a name collision (logger `info`,
  docker SDK `stop`, raw sqlite `rollback`). That scan led to A5.
- **Dead code**: an AST reference count of all 1,070 top-level defs and methods in `service/`,
  across every `.py/.js/.mjs/.html/.sh/.json` in the repo. Positive controls: `get_db` 107 uses,
  `_now` 137. I then grepped each of the 17 candidates with `grep -rnw` across the repo; most turned
  out to be imported under an alias.
- **Timestamps**: every raw `<`/`>` comparison on a `*_at`/`last_seen` column was checked against how
  its cutoff is built.
- **Hot-path statement counts**: measured by patching `aiosqlite.core.Connection.execute` around one
  `GET /agents`.

---

### A1. `/clear` ignores `olderThanHours` for every target except inbox messages, and accepts any target
- where: `service/routers/maintenance.py:43-121`; `service/models.py:659-662`; tool text at `mcp/stdio/lifecycle-tools.mjs:189,194` and `service/sse/management_tools.py:29`
- failure/cost: `cutoff` is applied only inside the `inbox` branch. `shared`, `agents`, `channels` and every `all`-only table (read_receipts, agent_sessions, spawn_requests, spawn_specs, environments) are deleted in full regardless of age. The tool tells agents to "prefer olderThanHours over a bare wipe" and describes it as "Only clear items older than N hours", so an agent following that advice still wipes everything. Also, `target` is a free `str`: an unknown value (`"bogus"`) returns 200 `ok: true`. The service also accepts `channels`, which the bridge enum does not offer. Shared files are unlinked from disk before the DB commit.
- evidence: PROVEN IN TESTS (scratch `test_clear_age.py`). I shared `fresh.txt` and registered `fresh-agent` seconds before. `POST /clear {"target":"all","olderThanHours":1}` returned `cleared: {files: 1, agents: 1}`. Afterwards `/shared` was empty and the agent returned 410. `{"target":"bogus"}` returned 200 `{"ok": true, ...zeros}`. No service test posts `/clear` with `olderThanHours` (grep of `service/tests` finds only the SSE argument-forwarding test).
- severity: P1 (irreversible data loss that contradicts the documented filter)
- effort: S
- fix sketch: Either apply the cutoff per target (`shared_at`, agent `last_seen`, channel message `timestamp`) or refuse with 400 when `olderThanHours` is combined with a target it cannot honour. Make `target` a `Literal[...]`.

### A2. Any agent can take over and then delete another agent's shared artifact, which defeats the owner check on delete
- where: `service/routers/shared.py:70-136` (`INSERT OR REPLACE ... from_agent`); owner check at `:161-209`
- failure/cost: `DELETE /shared/{name}` was deliberately hardened (the H4 ruling) so only the sharer or an operator can delete. But `POST /shared` overwrites any existing name with no owner check and rewrites `from_agent`, so the new writer becomes the owner and can then delete. `/clear target=shared|channels` also deletes everyone's artifacts and channels with no actor at all, which bypasses the same rule on `delete_channel` (`service/routers/channels.py:256-298`). `from_agent` on share, and `createdBy` on channel create, are never passed through `validate_sender`/`validate_name`.
- evidence: PROVEN IN TESTS (scratch `test_misc_probe.py`). `alice` shares `plan.md`. `mallory`'s DELETE gets 403. `mallory` POSTs the same name and gets 200, and GET now shows `from: mallory`. `mallory`'s DELETE then gets 200.
- severity: P2
- effort: S
- fix sketch: In `share_artifact`, if a row exists with a different `from_agent`, refuse with 409 unless `authorize_operator(...)`. Run `validate_sender` on `from_agent`/`createdBy`. Decide whether `/clear` needs `authorize_operator`.

### A3. Five routes turn a non-object or malformed JSON body into a 500
- where: `service/routers/agents/liveness.py:134-141,275-281`; `service/routers/usage.py:43-47,88-91`; `service/routers/dispatch_messages/inbox.py:339-341`; `service/routers/channel_membership.py:99-100`; `service/routers/agents/config.py:47-48`; `service/routers/agents/session_ops.py:226-229`; `service/routers/agents/turn_boundaries.py:86-89,186-189`
- failure/cost: 12 handlers read `await request.json()` raw. Only 2 check `isinstance(body, dict)`. A JSON array or string reaches `body.get(...)` and raises AttributeError. Malformed JSON on the handlers without a try raises JSONDecodeError. Both become 500s through `JsonApiRoute`'s catch-all, which also logs a full traceback each time. `post_usage_consumption` accepts any `rows` value. `POST /usage` accepts any number of client-chosen `source_id` keys into a process-global dict (`service/usage_cache.py:16`).
- evidence: PROVEN IN TESTS (scratch `test_misc_probe.py`). The heartbeat with `[1,2]` and with `"str"` returned 500 (`not json` returned 200, because heartbeat swallows the parse error). `/usage` with `[1]`, `/messages/x/read` with `[]`, and `/channels/c1/read` with `nope` all returned 500.
- severity: P3
- effort: S
- fix sketch: One helper, `json_object_body(request) -> dict`, that returns `{}` or raises 400. Better: give these routes Pydantic models, as the other body-taking routes have.

### A4. Missing indexes: every message delete does four full-table scans per 250-message chunk, under the writer lock
- where: `service/api_core/message_store.py:30-45`; indexes in `service/schema.py:224-243`
- failure/cost: `_delete_messages_by_ids` nulls `dispatch_runs.message_id`, `.in_reply_to` and `.result_message_id`, and `dispatch_controls.source_message_id`, for each chunk. None of those columns is indexed. Every caller pays this: hourly rotation, `/clear`, conversation clear, channel delete, unsend. `dispatch_runs` rows that touch a live agent are never pruned (`service/reconcilers/dispatch_lifecycle.py:364-369`), so the scanned table only grows.
- evidence: `EXPLAIN QUERY PLAN` on a scratch DB built by the real `init_db`: the three `dispatch_runs` updates each give `SCAN dispatch_runs`, and the `dispatch_controls` update gives `SCAN dispatch_controls`. The `messages.in_reply_to` update in the same helper uses `idx_messages_reply`, which is the control. The row count on the operator's DB was not read (ASSUMED large).
- severity: P3 (performance; worsens with history)
- effort: S
- fix sketch: Three partial indexes on `dispatch_runs(message_id|in_reply_to|result_message_id) WHERE col != ''` and one on `dispatch_controls(source_message_id)`, added in `schema.py`.

### A5. The container manager calls the blocking docker SDK inside async code on the single event loop
- where: `service/containers/manager.py:156-231` (`start_container`), `:253-255`, `:286-288` (`stop_container`, which runs `container.stop(timeout=30)` inline), `:316` (`get_container_logs`); the idle reaper at `:399-416` calls `stop_container`
- failure/cost: `docker.containers.run/stop/remove/get` are synchronous HTTP calls to the docker socket. `stop(timeout=30)` blocks the whole service (claims, heartbeats, dashboard) for up to 30 s per container, and the idle reaper does this unprompted. `containers.run` can pull an image implicitly, which blocks for minutes. `pull_image` at `:328` already uses `run_in_executor`, so the pattern is known and was simply not applied here.
- evidence: read the listed lines. The manager only starts when `config/service.json` has `containers.definitions` (`service/main.py:419-437`). `config/` on this host has no `service.json`, so this is latent here. Whether any operator enables it is ASSUMED unknown.
- severity: P2 (latent on this host)
- effort: S
- fix sketch: Wrap each docker SDK call in `await asyncio.to_thread(...)`. Otherwise, if no one uses containers, delete the subsystem (manager, proxy, gpu, `routers/containers.py`, `sse/container_tools.py`, roughly 950 lines). That is the operator's call.

### A6. Buffered terminal output is dropped on a graceful service restart: `flush_all` has no production caller
- where: `service/terminal_write_queue.py:564-573`; lifespan shutdown at `service/main.py:465-485`
- failure/cost: `POST /terminals/{id}/output` returns once the chunk is enqueued. The queue holds up to a 24 ms window, plus any batches re-queued after a lock error (`_requeue_front`, `:367`). Shutdown cancels the background tasks and closes the pool but never flushes the queue, so output a bridge was told was accepted is lost on every `docker compose up --build`. (`stopped`/`failed` statuses flush immediately at `:151-152`, so status transitions are mostly safe; output bytes and other statuses are not.)
- evidence: grep for `flush_all` outside tests finds only its definition and `flush_terminal_output_writes_for_tests` (`:576-577`). Nothing in `main.py` references `TERMINAL_OUTPUT_WRITES`.
- severity: P3
- effort: S
- fix sketch: `await asyncio.wait_for(TERMINAL_OUTPUT_WRITES.flush_all(), 5)` in the lifespan `finally`, before `CONNECTION_POOL.aclose()`. Bound it, because `flush_all` loops until the queue is empty and a lock-retrying terminal could keep it spinning.

### A7. `GET /agents` still issues one `terminal_sessions` COUNT per online agent
- where: `service/api_core/registration_gates.py:157-199` → `service/api_core/live_process_probes.py:40-62`, called per agent from `service/routers/agents/identity.py:118`
- failure/cost: the roster is polled by every dashboard tab. Everything else per-agent is either batched or capped at `LIST_AGENTS_REFRESH_LIMIT` (8). This one query scales linearly with the fleet. `_enforce_env_reachable_gate` (`registration_gates.py:111-114`) also re-selects the environment by id for each live managed agent, although the roster could pass an id-keyed map as it already does `environments_by_machine`.
- evidence: MEASURED (scratch `test_roster_count.py`, hermes managed agents with heartbeats). With 10 agents: 72 statements, 10 of them this COUNT. With 30 agents: 84 statements, 22 of them this COUNT. The other per-agent statements stayed at 8 (the refresh cap).
- severity: P3
- effort: S
- fix sketch: One `SELECT DISTINCT agent_id FROM terminal_sessions WHERE status IN {LIVE} AND id NOT LIKE 'vterm_%'` per roster call, passed into the gate the way `agent_row` already is.

### A8. The live-worker gate lists its statuses by hand, one of them nonexistent, and its docstring describes a writeback that was removed
- where: `service/api_core/registration_gates.py:186` (`if payload.get("status") not in {"online", "ready"}`), docstring `:163-184`
- failure/cost: `ready` is not in the status vocabulary (`service/contracts/vocabulary.json:24-34`). `shell` ("live worker at its prompt") is not covered, so a cached `shell` for a managed wrapper agent with no live terminal is never downgraded. The sibling env gate 40 lines above was fixed for exactly this (it went from a hand list to `is_live_agent_status` after external review finding 3, 2026-09-21). The docstring says "the writeback below keeps subsequent reads honest", but the body at `:195-202` says the writeback was REMOVED on 2026-06-18.
- evidence: read both gates. `grep "ready"` across `service/` finds this as the only status set containing it. Whether a cached `shell` actually survives terminal death is ASSUMED (not reproduced).
- severity: P3
- effort: S
- fix sketch: Gate on the statuses that assert a live worker, derived from the vocabulary module rather than listed. Delete the stale docstring sentence.

### A9. Terminal end-status set typed three times, and the output-retention sweep uses a fourth, narrower list
- where: `service/api_core/terminal_status.py:140`, `service/api_core/tuning.py:29`, `service/routers/sessions.py:97` (all `{"stopped","failed","lost","ended","completed","cancelled"}`); retention at `service/reconcilers/terminal_history.py:118-123` (`'stopped','failed','ended','cancelled'`)
- failure/cost: `sessions.py:100-102` says "BORROWED constant: one owner, never a copy -- a forked status set is finding N7" three lines below its own fork at `:97`. The retention list omits `lost` and `completed`, so a terminal ending in either keeps its `output` replay buffer forever. Today that is ASSUMED harmless: grep of `service/`, `mcp/stdio` and `~/projects/aify-env/lib` found no writer that sets a terminal to `lost`/`completed`. It becomes a real leak the day one does. `test_terminal_sql_compares_terminal_statuses.py` checks membership, not completeness, so it cannot catch this.
- evidence: read all four sites. No test references `ended_output_cleared`.
- severity: P3
- effort: S
- fix sketch: Import `_TERMINAL_END_STATUSES` at the two copies. Build the retention SQL from the same set, minus `stopping`.

### A10. `service/control_plane.py` is 897 lines with no code in it
- where: `service/control_plane.py` (whole file)
- failure/cost: by AST it contains 22 import statements, one docstring and nothing else: 0 functions, 0 classes, 0 constants. 500 of its lines are comments ("X moved to Y") and 305 are blank. Its own docstring (`:24-37`) says the imports "have no real reader" and that retiring them means re-aiming four witness gates. CLAUDE.md and `docs/ARCHITECTURE.md:88-89` still describe it as "shared helpers + the two queue classes", which is stale. It is not a regrowth risk; it is a tombstone that every reader must page through.
- evidence: `ast.parse` census: `{'Expr': 1, 'Import': 3, 'ImportFrom': 19}`, with code 92, comment 500, blank 305 lines. The first 45 lines were read. 20+ test files reference the module (`grep -rln service.control_plane service/tests`).
- severity: P3
- effort: M (the gates that use it as a witness need re-aiming: `test_leaves_do_not_import_the_carrier.py`, `test_moved_to_comments_are_true.py`, `test_no_orphaned_imports_in_control_plane.py`, `test_borrowed_accessors_return_the_owner.py` and others)
- fix sketch: Delete the file for 0.7.0. Move the "moved to" ledger into git history or a single doc section, and point the witness gates at the real owners.

### A11. Confirmed dead code in the service
- where: `service/api_core/terminal_tail_buffer.py:207` `held_count()`; `service/runtimes/base.py:136-145` `inject_message`/`interrupt`/`steer` ("Plan 3 — not yet implemented" stubs)
- failure/cost: `held_count` has zero references anywhere. The three adapter stubs are never called and never overridden. Their only reader is `service/tests/runtimes/test_base.py:111`, which asserts that they raise.
- evidence: `grep -rnw held_count` across the repo returns only the def. `grep -rnE "\.(interrupt|steer|inject_message)\(" service mcp/sse_server.py` returns nothing, while the same pattern for `.is_resident_ready(` finds `capabilities.py:115` (positive control). `grep -rnE "def (interrupt|steer|inject_message)" service` returns only `base.py`. Seven other scan candidates were rejected as aliased imports: `richest_recording`, `held_ids`, `is_known_event_kind`, `stored_log_is_partial`, `live_screen_seq`, `live_screen_reconstructed`, `forget_terminal`, `bind_app`, `version_text` all have live `as _alias` uses.
- severity: P3
- effort: S
- fix sketch: Delete the four methods/functions and the stub-asserting test.

### A12. The agent-addressed half of the WebSocket manager has never had a client
- where: `service/ws.py:10,16-22,27-28,69-99` (`_agents`, `online_agents`, `notify_agent`)
- failure/cost: the docstring at `:70-91` records that nothing has ever connected with `agent_id`, so `_agents` is permanently empty and `notify_agent`'s three callers send nothing. It was "kept" pending an operator call. For a finalization release it is dead flexibility, and it makes three call sites read as if they notify an agent when they do not.
- evidence: read the docstring's own measurement. It names `test_the_agent_addressed_websocket_half_has_no_client` as the tripwire.
- severity: P3
- effort: S
- fix sketch: Delete `_agents`, `online_agents`, `notify_agent` and the three calls, or get the operator's explicit "keep".

### A13. The deprecated `/agents/{id}/listen` long-poll ignores disconnects and can mark messages read for a caller that has gone
- where: `service/routers/agents/listen.py:37-109`
- failure/cost: it loops for up to 600 s, opening a DB connection every 2 s, and never checks `request.is_disconnected()` (the claim long-polls do, via `longpoll(is_disconnected=...)`). If the bridge dies mid-listen, the orphaned loop keeps running. When a message arrives it marks every unread message read (no LIMIT, one parent query per message) and returns them to nobody, so the agent's unread count drops for messages it never saw. It also writes `agents.status = 'idle'/'working'`, values the proof-based status vocabulary does not contain (they are legacy-mapped at `service/api_core/records.py:311`).
- evidence: read the handler. Its client is `mcp/stdio/inbox-tools.mjs:156`, which labels it "deprecated compatibility/debug long-polling". That the orphan loop consumes messages is ASSUMED from the code, not reproduced.
- severity: P3
- effort: S
- fix sketch: Check `await request.is_disconnected()` each iteration and drop the `agents.status` writes. Or delete the route with the tool, since it is already deprecated.

### A14. The usage endpoints: a POST with no producer, and a GET that re-stamps cached data as fresh
- where: `service/routers/usage.py:86-96`, `service/usage_cache.py:73-100`; `service/routers/usage.py:60-83`
- failure/cost: `POST /usage/consumption` has no caller in any of the three repos. `usage_cache.py:76-78` says so itself, and `collectConsumptionOnce` is "PARKED" (`mcp/stdio/usage-collector.js:3-13`). Its GET is read by `analytics-page.mjs:36`, which therefore always shows "unmeasured". `GET /usage` sets `fresh["updated_at"] = _now()` and `stale=False` on every read, even when the cached OpenAI reading is up to 120 s old (`_OPENAI_POOL_TTL_SECONDS`), so the age shown to the dashboard is always zero.
- evidence: `grep -rn "usage/consumption"` across the repo and `~/projects/aify-env`/`aify-wrapper` finds only the router, the dashboard GET and a fixture copy.
- severity: P3
- effort: S
- fix sketch: Stamp `updated_at` when the pool is fetched, not when it is read. Park or delete the consumption POST/GET pair together with its dashboard panel.

### A15. `create_channel` reports any insert failure as "already exists"
- where: `service/routers/channels.py:166-188`
- failure/cost: `except Exception: raise HTTPException(409, "already exists")` also catches `sqlite3.OperationalError: database is locked`. Because it is re-raised as an HTTPException, `JsonApiRoute`'s bounded lock retry (`service/api_core/routing.py:50-58`) never sees it. The result is a false 409 instead of a retried success.
- evidence: read both. (Not reproduced under contention.)
- severity: P3
- effort: S
- fix sketch: Catch `sqlite3.IntegrityError` only.

### A16. v1-to-v2 JSON migration tooling is still shipped in the service package
- where: `service/export_v1.py` (97 lines), `service/import_v2.py` (122 lines), `scripts/migrate-v1-to-v2.sh`
- failure/cost: these migrate the pre-SQLite JSON-file era (last touched by the rename commit, 2026-04-14). They are copied into every container image. `service/db.py:246` also carries a comment about `import_v2` rewriting settings.
- evidence: `grep -rln export_v1|import_v2` finds only the script and that comment. Whether any v1 install remains is ASSUMED no, and is the operator's call.
- severity: P3
- effort: S
- fix sketch: Delete all three for 0.7.0 and note it in the changelog.

### A17. Ten zero-argument `_borrowed_*` accessors that return a constant
- where: `service/routers/agents/shared.py:96-171` (7), `service/routers/sessions.py:103`, `service/api_core/reply_linking.py:35`, `service/api_core/status_broadcast.py:32`
- failure/cost: these are leftovers from the carrier era. Most return a name already imported at module level (for example `_borrowed_list_agents_refresh_limit` returns `LIST_AGENTS_REFRESH_LIMIT`), so there is no cycle for them to break. Each read costs a function call and a hop for the reader. `test_borrowed_accessors_return_the_owner.py` gates them.
- evidence: `grep "^def _borrowed_"`, then read each body.
- severity: P3
- effort: M (the call sites and the gate must change together)
- fix sketch: Inline to the owner import. Keep a function only where a function-scope import is genuinely breaking a cycle (`_borrowed_live_session_statuses`, `_borrowed_listen_events`, `_borrowed_manual_statuses`), and say so in one line.

### A18. The longest functions are still 200-457 lines, even though every file is now under the gate
- where: `service/api_core/status_inputs.py:312` `_compute_live_status_cache` (457), `service/dispatch_claim.py:81` `_claim_dispatch_once` (383), `service/reconcilers/sweep.py:57` `_run_dispatch_reconcile_once` (364), `service/routers/environments.py:296` `environment_heartbeat` (317), `service/api_core/dispatch_runs.py:93` (288), `service/reconcilers/managed_workers.py:126` (279)
- failure/cost: the file split moved these functions but did not decompose them, and they sit exactly on the status, claim and sweep paths where the repo records most of its incidents. Steven's own bar: "a long one is a structure you have not found yet". `environment_heartbeat` also does its supersede arbitration and metadata merge as an unlocked read-then-write (no `BEGIN IMMEDIATE` in `environments.py`). A manual-roots PATCH landing between its SELECT (`:360-372`) and its UPDATE can be overwritten (ASSUMED; the window is milliseconds).
- evidence: an AST length census of every function in `service/`.
- severity: P3
- effort: L (do not attempt in 0.7.0 beyond `environment_heartbeat`, which is the most self-contained)
- fix sketch: Split `environment_heartbeat` into pure steps (derive incoming metadata, arbitrate supersede, merge preserved keys, write), each taking a context object and returning a result.

### A19. Stale docstring: `validation.py` says a quirk is "preserved deliberately" that the code below it fixed
- where: `service/api_core/validation.py:10-15` against `:26-39`
- failure/cost: the module docstring says a trailing-newline name "is ACCEPTED today... pinned by a test". The regex now ends in `\Z` and the comment below explains the tightening. A reader of the first screen of a security-adjacent module learns the opposite of what the code does.
- evidence: read both passages.
- severity: P3
- effort: S
- fix sketch: Replace the quirk paragraph with one line pointing at the `\Z` comment.

### A20. Small duplicated time helpers
- where: `service/containers/manager.py:28` `_now()` (a datetime, not the service string); `service/ntfy.py:264` and `service/reconcilers/terminal_controls.py:100` re-inline `time.strftime(ISO_SECONDS, time.gmtime())` instead of calling `service.clock.now()`; `service/routers/analytics.py:227` nests its own `_iso_to_epoch` beside `service.clock.iso_to_epoch` (it returns None where the clock version returns 0.0)
- failure/cost: `clock.now()`'s docstring says changing the format is a data migration. Two copies of the format outside `clock.py` are two places a future change will miss.
- evidence: grep for `strftime(ISO_SECONDS` and `iso_to_epoch`.
- severity: P3
- effort: S
- fix sketch: Call `service.clock.now()`. Give `clock.iso_to_epoch` a `default` parameter and delete the nested copy.

---

## Checked and clean

- **Connection handling**: all 106 `get_db()` checkouts close in `finally` (AST scan with a negative control). The pool (`service/db_pool.py`) resets on the connection's own worker thread, retires any connection with changed state, and is opt-in for tests.
- **Timestamp comparisons**: every stored `*_at` value comes from `clock.now()`'s single format, and every raw lexical comparison checked (`stats.py:129-139`, `analytics.py:131-137`, `reply_linking.py:149-150`, `superseded_bridge_stops.py:55-68`, `spawn_request_state.py:59-66`) builds its cutoff in the same `ISO_SECONDS` format. `messages.timestamp` comparisons use integer ms on both sides. No `datetime('now')` defaults in the schema.
- **JSON in SQL**: the only two `json_extract` sites are both guarded by `json_valid` (`channel_delivery.py:173-176`, `spawn_requests.py:194-195`).
- **Unawaited coroutines**: none. All scan hits were name collisions.
- **Message rotation** (`reconcilers/message_rotation.py`): the `NOT IN` subquery filters out empty and NULL ids, so it cannot silently match nothing. Open runs are protected, and read receipts are deleted by id within the same transaction.
- **Background queues**: the ntfy relay is bounded (`asyncio.Queue(maxsize=...)`, drops on full). The long-poll waiters are removed in `finally` (`longpoll.py:_wait_once`).
- **Auth middleware skip list** (`main.py:185-187`): `startswith` matching, but no real route begins with a skipped prefix.
- **SSE API client** (`service/sse/api_client.py`): turns non-2xx and non-JSON responses into `detail` errors instead of confident empties.
- **Retry premise in `JsonApiRoute`**: the seven mutating handlers with two `commit()` calls commit once per branch (checked `control_environment`, `control_agent`), so a lock retry does not double-apply there. Commits inside helpers called from `send_message` were not traced; that path belongs to the delivery reviewer.
- **Status cache cold start**: the startup reconcile runs the unbounded live-state refresh (`reconcilers/sweep.py:343`) before serving, so the roster's `row["status"]` fallback (where `idle` maps to `online`) is not normally reached.
- **Docstring vs body scan**: no function whose docstring claims read-only or side-effect-free writes the database (both hits of an AST scan were false positives).
- **Duplicate function bodies**: no structurally identical helpers across modules (AST-dump hash), apart from the two trivial favicon routes.

Not covered by this lens: `dispatch_claim.py` CAS internals, the `status_inputs`/`status_decision` derivation, and the reconcile sweep's ordering. Those are with the delivery and status reviewer.
