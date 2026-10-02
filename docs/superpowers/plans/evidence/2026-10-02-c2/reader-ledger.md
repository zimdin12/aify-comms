# C2 reader ledger: every reader of status, readiness, liveness, eligibility and busy facts

Source census of comms `service/` and `mcp/stdio/` at 3811a66f (identical code on next/0.9-agent-state), 2026-10-02,
by read-only search and reading. Items marked UNCONFIRMED are inferred from code, not observed running. The
re-derivation patterns are at the end; P4 turns them into the discriminator test. The one claim re-read by hand
before use: C3 (`update_agent` writes any string, no gate), confirmed.

## A. Producing and serving status

| # | file :: function | reads | distinction | decides / shows |
|---|---|---|---|---|
| A1 | status_engine.py :: `derive` | `StatusInputs` (disabled, mode, env_reachable, worker_present, host_activity+fresh, in_turn, awaiting_input, alive, config_defect, console_booting, spawn_starting, bridge_stale, has_live_session, background_work) | all nine | the status word; the single authority |
| A2 | status_engine.py :: `is_live_agent_status` | word; prefix against `NON_LIVE_AGENT_STATUSES`; empty = not live | live vs non-live | predicate for analytics and the environment gate |
| A3 | api_core/status_inputs.py :: `_gather_status_inputs` / `engine_status` | `agent_status_state` in_turn/awaiting_input/turn_started_at (`_in_turn_survives`); raw stopped; launch_mode none; console-working lease; `_managed_env_reachable`; `_has_live_worker_for`; `_resident_bridge_is_fresh`; `_agent_awaiting_input`; `_managed_console_is_booting`; `_agent_config_defect`; `_managed_spawn_is_starting`; `host_activity_for`; wake mode missing-handle; `background_work_for` | every input | `StatusInputs` for push, reaper, engine; sets resident config_defect |
| A4 | api_core/status_inputs.py :: `_compute_live_status_cache` | A3's facts plus `agent_turn_state.turn_busy`, active run, channel run awaiting reply, env status, `_worker_liveness_for`, terminal prompt hint, subagents lease | legacy status, then inputs | cache entry (status, reason, refresh_after, inputs); does not set resident config_defect |
| A5 | api_core/status_decision.py :: `_decide_effective_status` | turn_busy, active_run, has_live_worker, terminal hint, env/bridge/session statuses, awaiting-reply run, no-sidecar/no-console | legacy cascade | reason text (statusNote); `awaiting_reply` (no reader found, UNCONFIRMED) |
| A6 | api_core/status_refresh.py :: `_refresh_agent_live_state` | cache vs `derive()`; `_MANUAL_STATUSES` | manual vs derived | stores derive(); blanks reason on disagreement |
| A7 | api_core/status_refresh.py :: `_compute_agent_status` | manual raw status; fresh cache; else refresh; db-less fallbacks | manual / cached / derived | word for sends, compaction, analytics, write responses; no read gates, no run promotion |
| A8 | api_core/status_refresh.py :: `_get_recipient_info` | cache or raw row through A10 | display | `recipientStatus` in send responses |
| A9 | api_core/records.py :: `_status_with_dispatch` | running active run; status not manual, non-live or blocked | may a running run promote | promotes to working |
| A10 | api_core/records.py :: `_agent_record_to_dict` | live_status, cache, raw; legacy maps; note; wake mode; capabilities; dispatchState | served shape | `status`, `statusRaw`, `statusNote`, `wakeMode`, `capabilities`, `dispatchState` |
| A11 | reconcilers/status_cache.py :: `_live_state_set` / `_live_state_fresh` | previous vs new; refresh_after | changed / expired | `agent_status_moved` push; serve or recompute |
| A12 | status_push.py :: `refresh_expired_statuses_once` | expired entries | expired | recompute |
| A13 | api_core/channel_delivery.py :: `_worker_liveness_for` / `_has_live_worker_for` | live session row; terminal status and command; sidecar and console (claude, hermes need both); channel flag; codex wrapper child | worker present | `has_live_worker`, no-sidecar / no-console |
| A14 | api_core/liveness.py :: `_agent_wake_mode` | launch_mode, session_mode, runtime, capabilities, handles | wake path | `wakeMode`; missing-handle sets bridge_stale and config_defect |
| A15 | api_core/liveness.py :: `_agent_config_defect` | mode, launchable runtime, missing handle | can ever start | config_defect, so misconfigured |
| A16 | api_core/liveness.py :: `_agent_liveness` | live terminal, sidecar, resident bridge fresh | worker / console / sidecar / resident live | liveness dict |
| A17 | api_core/capabilities.py :: `_managed_env_reachable` / `_row_capabilities` | effective env status or agent last_seen; runtime, mode, channel, gateway | env reachable; capabilities | env_reachable and every capability gate |

## B. Decision readers

| # | file :: function | reads | distinction | decides |
|---|---|---|---|---|
| B1 | api_core/registration_gates.py :: `_enforce_env_reachable_gate` | `is_live_agent_status`, managed, owning env status | live claim vs dead env | read-time override to offline (GET /agents only) |
| B2 | api_core/registration_gates.py :: `_enforce_live_worker_gate` | at-rest statuses, managed, live terminal | at rest vs no worker | downgrade to available |
| B3 | routers/agents/identity.py :: `list_agents` / `get_agent` | A10, then B2, B1 | served | the roster and agent record |
| B4 | api_core/send_preflight.py :: `_preflight_live_send_recipients` | resident bridge fresh; A7+A9 non-live; execution mode; env unavailable reason; active run + steer; queued runs | live; busy by runs (not turn_busy) | launchable vs notStarted; refuse / steer / queue |
| B5 | api_core/send_refusal.py :: `_refuse_send_to_unstartable_recipients` | A8 | display | refusal payload |
| B6 | routers/dispatch_messages/messages.py :: send | B4, A8 | startable | refuse a new send |
| B7 | routers/channel_send.py | B4, A8 | startable | wake launchable members only |
| B8 | routers/dispatch_messages/dispatch.py :: trigger | B4, A8 | display | recipientStatus |
| B9 | api_core/dispatch_launch.py :: `_launch_recipients_for_dispatch` | queueIfBusy: active run, queued runs, `_turn_busy_holds_delivery`; wrapper child; claimable spawn | busy; worker; can cold-start | queue vs deliver; cold-start; console backing |
| B10 | api_core/channel_coldstart.py :: `_coldstart_cold_channel_members` | managed, channel runtime, wrapper child | worker present | cold-start |
| B11 | api_core/dispatch_runs.py :: `_create_dispatch_runs` | steer + active run; A7+A9 when full | steerable and busy | steer vs new run |
| B12 | api_core/execution_mode.py :: `_agent_execution_mode` | capabilities, launch_mode, runtime, handles | can dispatch, how | channel / managed / resident or refusal |
| B13 | api_core/dispatch_hint.py :: `_dispatch_fix_hint` | resident-run, handle | fix path | hint text |
| B14 | api_core/channel_delivery.py :: `_apply_channel_routing_to_claude_runs` | sidecar | sidecar present | route to channel |
| B15 | api_core/dispatch_start.py :: `twin_refusal` | resident + live worker | live resident | refuse a managed twin |
| B16 | api_core/dispatch_start.py :: `_coldstart_spawn_request_for_dispatch` | env online, runtime, pending spawn | can cold-start | spawn request or refusal |
| B17 | dispatch_claim.py :: claim | raw stopped (not launch_mode none); driver state; owner bridge beat; console owner; `_turn_busy_holds_delivery` + steerable | stopped; busy; steerable | stopped / release / blockedBy / hold |
| B18 | api_core/claim_gating.py :: `_turn_busy_holds_delivery` | `agent_turn_state` busy, started, updated, bridge; `_turn_lease_is_renewable` | busy (1800 s from start, renewable) | hold delivery |
| B19 | api_core/claim_gating.py :: `_has_claimable_steerable_run` | steer, queued steer runs | steerable | bypass the hold |
| B20 | api_core/claim_run_selection.py :: `_select_claimable_run` | hold flag; capabilities | busy; capability | skip / cancel / fail |
| B21 | api_core/dispatch_sweeps.py :: `_run_contract_reminders_once` | A4 blocked + reason; active run; `_turn_busy_state` (120 s); B4 | blocked; busy | skip or send reminder |
| B22 | api_core/dispatch_sweeps.py :: `_mirror_missing_dispatch_handoff`; api_core/away_briefing.py :: `_send` | B4 | startable | dispatch / run |
| B23 | reconcilers/orphaned_managed_runs.py :: `_close_orphaned_managed_runs` | `engine_status` working/blocked; stale/offline/stopped | mid-turn vs gone | skip or fail; omits misconfigured, lists stale |
| B24 | reconcilers/undeliverable_queued_runs.py :: `_agent_has_live_claimer` | resident fresh; claimer lease; sidecar; bridge row | claimer present | bool |
| B25 | reconcilers/undeliverable_queued_runs.py :: `_reap_undeliverable_queued_runs` | B24; launchable; spawn | deliverable; can cold-start | skip / rescue / fail |
| B26 | reconcilers/dispatch_lifecycle.py :: `_fail_stranded_delivered_reply_runs` | turn_busy on the run; interrupt; B24 | busy on this run | skip or fail |
| B27 | reconcilers/dispatch_lifecycle.py :: `_clear_turn_busy_for_dead_bridges` | turn_busy, bridge not the hook marker, bridge stale | busy with dead owner | clear |
| B28 | reconcilers/managed_workers.py :: `_reconcile_managed_worker_hygiene` | sidecar, wrapper child, terminal | worker / orphan | reap ghost console |
| B29 | reconcilers/managed_workers.py :: `_repair_unusable_active_runs` | superseded bridge | usable run | discard |
| B30 | reconcilers/terminals.py :: `_reconcile_resurrected_managed_consoles` | raw not stopped, live terminal, output, sidecar | alive again | reactivate |
| B31 | reconcilers/terminals.py :: `_close_idle_virtual_rpc_workers` | open runs (not turn_busy), terminal updated | idle | auto-close |
| B32 | reconcilers/dispatch_queue.py :: reroute / requeue | sidecar; claim bridge | worker / claimer | reroute or requeue |
| B33 | reconcilers/sessions.py :: `_compute_session_display_status` | raw stopped; A16; terminal | session live | session display |
| B34 | reconcilers/dead_session_status.py :: `_reconcile_dead_session_status` | stopped; dead terminals | session dead | write session status |
| B35 | routers/analytics.py :: `get_analytics` | wake mode; A7+A2; working prefix | wake-capable; live; working | live / online / working counts |
| B36 | api_core/analytics_series.py :: `_build_online_agent_board` | A7+A2; working prefix | live; working | pulse board, utilization |
| B37 | api_core/compaction.py :: `decide_native_compact` | A7; at-rest; working; blocked | at prompt / mid-turn | allow or refuse compaction |
| B38 | api_core/session_mode_gates.py :: `_enforce_switch_not_blocked_by_active_run` | active run; supports_resident | busy (runs) | 409 / warning |
| B39 | routers/agents/session_ops.py :: start / stop-worker / resume | live sessions; active run; pending spawn | running; busy | alreadyRunning / refusals |
| B40 | routers/session_control.py | live sessions, dead terminals, run | session live | refuse restart / spawn |
| B41 | api_core/nested_session_handback.py :: `was_stopped` | raw stopped / launch_mode none | prior stop | preserve |
| B42 | routers/agents/liveness.py :: heartbeat | cached offline; turn flip | recovery | invalidate, push |
| B43 | routers/agents/turn_boundaries.py :: turn-end | turn_busy, in_turn, run; superseded bridge | which turn is open | ignore / clear |
| B44 | api_core/turn_busy_signal.py :: `_apply_turn_busy_signal` | previous busy, owner, run | owner may clear | set / clear |
| B45 | routers/agents/rename.py | A16 | live bridge | note text |
| B46 | routers/agents/{session_lease,session_handle,session_ops,session_mode}.py, api_core/session_handle_change.py | A7, A10 | display | response payload |

## C. Push and raw writes

| # | file :: function | reads | decides |
|---|---|---|---|
| C1 | api_core/status_broadcast.py :: `_broadcast_engine_status` | manual stop, else A3 | WS `agent_status` (no B1/B2, no A9) |
| C2 | api_core/status_broadcast.py :: `_broadcast_agent_status` | A4 + derive | WS `agent_status` |
| C3 | routers/agents/attributes.py :: `update_agent` | request status, lowercased, unvalidated, no gate | writes raw `agents.status` (`stopped` = manual stop); broadcasts it (confirmed by reading) |
| C4 | routers/dispatch_messages/dispatch.py :: run PATCH | `agentStatus` in VALID_STATUSES | writes raw `agents.status` (UNCONFIRMED that `stopped` is reachable) |

## D. Dashboard (service/new_dashboard)

| # | file :: function | reads | shows |
|---|---|---|---|
| D1 | status.js :: `resolveStatus`, `STATUS_KINDS` | word | chip, dot, tone; unknown fallback |
| D2 | status.js :: `NON_LIVE`, `LIVE`, `ACTIVE_AGENT_STATUSES` | vocabulary | shared sets |
| D3 | status.js :: why-context, chips, legend; boot-wiring.mjs | note, lastSeen | tooltip, filters, legend |
| D4 | summary-tiles.mjs :: `renderMetrics` | working, blocked, active | tiles |
| D5 | chat-select.mjs :: `chatRailItems` | live filter, set filter, rank | filter, sort |
| D6 | chat-render.mjs :: `railItemHtml` | blocked | badge |
| D7 | chat.js :: pulse loader | pulse status (B36) | overwrites roster status until next poll (UNCONFIRMED) |
| D8 | analytics.js | status, workingNow | pulse |
| D9 | agent-click-handlers.mjs :: `startOffer` | stopped/available start; starting/misconfigured reason | start offer |
| D10 | agent-drawer.mjs | status not in offline/stopped/available | Stop button (shown for misconfigured, starting) |
| D11 | start-dialog.mjs :: `actionFor` | D9, active | start / console / none |
| D12 | identity-directory.mjs | status, note | chip |
| D13 | sessions-list.mjs :: `sessionDisplayStatus` | session row, agent status | rail status |
| D14 | session-rail.mjs | D13; blocked | rows, badge |
| D15 | session-click-handlers.mjs | live set | preset |
| D16 | session-console.mjs | session status | stop / start |
| D17 | app.js session header | session or agent status | header chip |
| D18 | console-await.mjs :: `updateAwaitPill` | blocked or tail heuristic | awaiting pill |
| D19 | realtime-socket.mjs | WS `agent_status` | patches status, statusRaw, note |
| D20 | render-memo.mjs :: `_agentSig` | status, note | repaint gate |

The dashboard reads no `wakeMode` and no `dispatchState`.

## E. Bridge (mcp/stdio)

| # | file :: function | reads | shows / decides |
|---|---|---|---|
| E1 | agent-reporting-tools.mjs :: comms_agents / comms_agent_info | status (default idle), wakeMode, dispatchState | text |
| E2 | agent-summary.mjs :: `wakeModeSummary` | wakeMode or capabilities | text |
| E3 | tool-response-format.mjs :: `formatDispatchState` | activeRun, queuedRuns | text |
| E4 | send-tools.mjs | recipientStatus | text |
| E5 | dispatch-loop.mjs | resident + launch_mode none + stopped | terminate resident host |
| E6 | claude-channel.js | claim stopped / release | sidecar dormant |
| E7 | hermes-delivery-run.mjs | claim stopped / release | teardown |
| E8 | dispatch-loop.mjs (writer) | — | PATCH agentStatus working / idle |
| E9 | self-record-tools.mjs (writer) | — | comms_status, through C3 |

## Vocabulary and its special readers

`VALID_STATUSES`: working, shell, online, available, starting, blocked, offline, stopped, misconfigured (the order
differs between status_engine.py and contracts/vocabulary.json; status.js follows the engine).

| value | special readers |
|---|---|
| working | A4 clamp; A9 target; B23 skip; B35/B36 prefix; B37 mid-turn; D4, D5, D2 |
| shell | at-rest (B2, B37); D2; D1 |
| online | at-rest (B2, B37); legacy target in A7/A10; D2 |
| available | live in A2 so B4 cold-starts; B1 may override; A9 may promote; D9 start; D10 no Stop |
| starting | live; not active; D9; D10 offers Stop |
| blocked | excluded from A9; B37; B23 skip; B21 skip; D4, D6, D14, D18 |
| offline | non-live (B4 refuse); B1 writes; B42; B23 fail-fast; D10 |
| stopped | manual (A6, A7, C1, C2); A3 disabled; B17 stopped; B30, B33, B34, B41; E5; D9 |
| misconfigured | non-live (B4, B35, B36, A9); D9; missing from B23's fail-fast set and from D10's no-Stop list |
| legacy stale / idle / active / ready | mapped in A7, A10, D1; B23 still lists stale |

## UNCONFIRMED

- M1: `awaiting_reply` has no reader.
- M2: C1 pushes `misconfigured` where the next poll says `offline` for a resident with no wake handle, and skips
  B1, B2 and A9.
- M3: D7 overwrites a gated roster status with an ungated one until the next poll.
- M4: B4 and A8 skip B2 (the environment case is covered by B4's own check).
- M6: C4 would persist `stopped` as a manual stop; no bridge sends it.
- M7: B17 checks only raw stopped, while A3 also treats launch_mode none as disabled.
- M8: D1 `inputEnabled` and the vocabulary meanings have no reader outside tests.
- M9 not read in detail: routers/agents/config.py session-status set, registration handle-collision liveness,
  `_environment_has_live_bridge`.

## Re-derivation patterns

Over `service/` and `mcp/stdio/`, test files excluded:

- `is_live_agent_status|NON_LIVE_AGENT_STATUSES|WORKER_AT_REST_STATUSES|turn_busy|in_turn|awaiting_input|engine_status|_compute_agent_status|_worker_liveness_for|_LIVE_STATE_CACHE|derive\(|live_process_probes|spawn_starting|console_booting|env_reachable|config_defect|worker_present|has_live_worker|wakeMode|wake_mode|dispatchState`
- `_compute_agent_status\(|_live_state_get\(|_live_state_fresh\(|_get_recipient_info\(|engine_status\(|_gather_status_inputs\(|_agent_record_to_dict\(|_status_with_dispatch\(|is_live_agent_status\(`
- `_row_capabilities\(|_managed_env_reachable\(` and capability literals `"(steer|interrupt|resident-run|managed-run|resume|spawn)" (not )?in `
- `_agent_wake_mode\(|_agent_liveness\(|_has_live_worker_for\(|_resident_bridge_is_fresh\(|_has_live_terminal_session\(|_agent_has_live_terminal\(|_has_live_channel_sidecar\(|_has_live_claimer_lease\(|_claimer_lease_released_within\(|_has_live_managed_wrapper_child\(|_agents_with_live_terminal_sessions\(|_managed_spawn_is_starting\(|_managed_console_is_booting\(|_agent_config_defect\(|twin_refusal\(`
- `_turn_busy_holds_delivery|_has_claimable_steerable_run|_turn_busy_state\(|_status_turn_signals|_in_turn_survives`
- `_MANUAL_STATUSES|VALID_STATUSES|AGENT_STATUSES|AGENT_STATUS_MEANINGS`
- status-word literals: a single or double quote, then one of working, shell, online, available, blocked, offline,
  stopped, misconfigured or starting, then a quote
- `_preflight_live_send_recipients\(|_refuse_send_to_unstartable_recipients\(`
- dashboard: `resolveStatus\(|inputEnabled|LIVE_AGENT_STATUSES|ACTIVE_AGENT_STATUSES|NON_LIVE|statusNote|dispatchState|wakeMode|hasActiveRun|queuedRuns|statusRaw` and the status-word literals
- bridge: `\.stopped\b|\.release\b|blockedBy|agentStatus|turnBusy|statusNote|wakeMode|dispatchState|recipientStatus`
