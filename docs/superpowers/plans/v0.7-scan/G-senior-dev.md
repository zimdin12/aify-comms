# G: comms-senior-dev (independent lens)

Its reply to the lens request, 2026-09-25, saved verbatim. It read aify-comms at `b7fde7c8` and aify-env at `9a8642ae`.

- **G1. P1 M** `service/reconcilers/dispatch_queue.py:254-295`: the orphan-claim requeue can overwrite a run that was delivered or completed concurrently, and cause duplicate execution.
  - The stale candidate is SELECTed before awaits, then the UPDATE is by id alone: no status, claim-bridge or absence-of-delivered-event CAS. `sweep.py:225` commits only after this helper.
  - Fix: guard the UPDATE with the selected state and owner, and check rowcount before the event or notification.
  - This is a source race and was not reproduced live.
- **G2. P1 M** `service/routers/dispatch_messages/messages.py:106-123`: reusing a nonce with a different recipient, body or trigger silently drops the new send. The response still says ok:true/replayed:true and returns the old messageId.
  - The fast-path lookup keys only on (from_agent, client_nonce) and never compares the payload.
  - Isolated FastAPI probe: the first ordinary send returned 200. A second send with the same nonce, to another recipient, as a triggered request, returned 200/replayed:true with dispatchRuns=[] and 0 new-body rows.
  - Fix: reject a conflicting payload, and cover the raced insert path at :187-219 too.
- **G3. P2 S** `service/status_engine.py:185-194`: a late turn_end for an older run clears a newer run's in_turn and turn_run_id, so status can read online mid-turn.
  - `_on_turn_end` ignores event.runId, and `status_events.py:25-63` persists the fold with no ordering or identity guard.
  - Pure-function probe: start(new), end(old) gives in_turn=0.
  - Fix: preserve the new turn when the IDs are both non-empty and differ, and test the stale-end order.
- **G4. P3 S** `service/status_engine.py:183`: a duplicate, dead EVENT_KINDS tuple can be deleted.
  - `_EVENT_HANDLERS` at :209-216 owns the vocabulary and KNOWN_EVENT_KINDS derives it.
  - A repository search found only an unused import, at `service/tests/test_status_engine.py:90`.
  - Fix: remove both, so there are not two event lists to drift apart.

**Checked clean within this scope:**
- **External keys and the service key.**
  - `external_keys.py:71-105` validates labels and keys, and rejects reserved or duplicate keys.
  - `main.py:214-288` compares the service key, restricts external keys to marked routes, and does not give them a dashboard cookie.
  - `send_message:86-88` rejects local-agent impersonation under external keys.
- **Operator key.** `operator_authz.py:60-119` requires a separately proven operator key, and `main.py:379` resolves or generates it.
- **aify-env claim and control loops.**
  - `claim.mjs:75,97-160` reports starting/running or logs a refusal.
  - `index.mjs:194-319` keeps the claim and control loops separate and alive through exceptions.
  - `api.mjs:106-139` reads credentials per call, refuses to follow redirects, and bounds requests.
- **Spawn env vars.**
  - aify-env `terminal-controls.mjs:267-273,420-437` consumes launch.env.
  - On the aify-comms side, `spawn_env.py` and `test_a_spawns_env_vars_reach_its_worker.py` bind stored envVars to that launch.
- **Status cache.** `status_cache.py:52-99` expires the derived cache rather than dropping it, and the read-gate tests cover a vanished wrapper terminal.
- **Tests run.** `service/tests/test_message_idempotency.py`: 6/6 pass.

**Unverified:**
- live fleet behaviour
- every rotation variant
- the exact interleaving of the requeue race under production load
- the full suite and cross-repo integration
- the rest of the service/ ponytail population, beyond the one duplicate constant
