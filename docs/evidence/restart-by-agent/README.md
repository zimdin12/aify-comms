# Restart by agent

Candidate base: `c5068ec7e663f0e704f78530d5581e1f88595d71`.

Both `comms_restart` transports now post to `/agents/{agentId}/control` without listing or selecting session rows. The existing lifecycle delegate queues restart; recreate queues restart with `fresh_context=true`. Its existing definition, owner, authorization, lifetime and transaction rules remain in force. The new admission callback rereads the agent and asks the uncached operational view, refusing `starting` with a retry instruction before any request is written. It does not change historical session-row admission.

Both queued-message helpers point at `GET /api/v1/agent-lifecycle-requests/{id}`. Restart requires a nonempty queued receipt and no longer reports the worker stopped or restarted from a nonqueued response.

## Evidence level and scope

PASSES IN TESTS on synthetic, isolated database/home fixtures. No live restart, install, deployment, provider call or additional E1 was performed. The incident prerequisite was met separately by the retained private read-only log/SQLite evidence; private identities and rows are not copied here.

Final service selection: **239 passed, 88 subtests passed, zero failed**, one existing Starlette deprecation warning, 49.58 seconds. Final bridge selection: **178 passed, zero failed or skipped**, six files. These are focused and governing checks, not a full-suite claim. The Python transport cases run through the actual `/mcp/http` mount. The JS cases run through the SDK's in-memory transport and a loopback HTTP service. Both verify the exact outbound method, agent path, caller, restart/recreate action, polling path and rendered reply.

The route fixtures cover tied stopped-history timestamps, a newer ended row absent from the active list, a live worker, no history, fresh context, and undefined/resident refusals. The starting witness publishes a current host report after the route's first read, while a cached word still says online. It asserts both HTTP 409 and an empty lifecycle table, then permits the online control. The unchanged historical-session tests independently prove stale rows remain refused and current rows remain admitted.

The JavaScript status-twin ledger drops only the deleted session-selection set and explains why it no longer exists.

## Reproduce

Run in an isolated checkout/home with agent, lease, Herdr and credential environment variables removed. Development dependencies must match the package lock. The exact commands and sanitized environments are the first JSON line in each raw receipt.

```text
python -m pytest service/tests/test_legacy_routes_delegate_defined_agents.py service/tests/test_a_superseded_session_controls_nothing_newer.py service/tests/test_tool_replies_match_the_js.py service/tests/test_claiming_the_operator_on_a_control_route_requires_the_key.py service/tests/test_js_status_set_twins_are_frozen.py service/tests/test_session_restart_refusal_names_the_real_cause.py service/tests/test_sessions_list_is_current_not_history.py service/tests/test_sse_tools_are_registered.py service/tests/test_no_new_oversized_source_file.py service/tests/test_leaves_do_not_import_the_carrier.py service/tests/test_no_import_cycles.py service/tests/test_the_env_plugin_addresses_routes_this_service_serves.py service/tests/test_no_mcp_tool_reports_an_outage_as_a_fact.py -q
node --test tests/lifecycle-tools.test.js tests/lifecycle-tools-say-queued.test.js tests/tool-replies.test.js tests/transport-parity.test.js tests/tool-surface-ratchet.test.js tests/http-body-parity.test.js
```

Node runs from `mcp/stdio`. The recorded run also used an external preload to select source-only env `37c3d1f30389efdcaaaa8067b21138f97ae60d37` and wrapper `8c7d8f43c92d21440e582368240e4bd9bb79dfcd`. Windows identity metadata and private conventional AppData paths were supplied by the isolated runner, not host credential directories.

## Retained receipts

The declared persistent evidence directory is `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`.

- `restart-route-red.txt`: first fixture run. Three history arms initially lacked the required environment ID. This is a setup error, not a credited regression witness.
- `restart-route-red-corrected.txt`: corrected fixtures against unchanged product reach the unsupported restart-action refusal.
- `restart-node-red.txt`: unchanged transport fails history-independent routing and fail-closed receipt assertions; removal controls pass.
- `restart-python-green.txt`: initial composed run is RED, 172 pass and one starting failure. The synthetic host had no current report cadence, so the operational view returned unknown. No product guard was weakened.
- `restart-starting-fresh-report.txt`: the corrected current-report fixture passes.
- `restart-starting-mutant-red.txt`: removing only the starting guard produces HTTP 200 and a stored lifecycle row, failing the named combined refusal/nonmutation assertion. Two other route tests and six subtests pass as controls.
- `restart-python-final.txt` and `restart-node-final.txt`: final actual exit-zero runs quoted above.
- `restart-tested.patch`: exact seven-file product/test diff exercised by those final runs. Its SHA256 is `cf67ae1534b1f64dcedbcf47d22b2182be65d6b7771953387bdcc6370feec845`. It was byte-compared again after the runs. This README is not part of that execution input.

`session_control.py` and `session_currentness.py` were compared with the candidate base and are unchanged. No caller-exhaustiveness or runtime-adoption claim is made.

An additional documentation selection is RED: 13 passed and one failed. The existing `docs/evidence/issue49-e1-consent/README.md` section names the moved `context-window` and `claude-login` checks without naming aify-env in that section. The unchanged c5068ec7 rollout checkout reproduces the same failure, with four controls passing, in `restart-docs-base-control.txt`. `restart-docs-final.txt` preserves the candidate run. This pre-existing documentation issue is reported for disposition and is not fixed or waived by the restart change. The restart-specific README is not an offender. Do not describe all governing checks as green.

The local unlazy ledger is not an automatic test certificate. Its CHECK commands were not approved or executed by the gate runner; direct isolated receipts remain separate. Publication waits for the lead's exact-candidate review.
