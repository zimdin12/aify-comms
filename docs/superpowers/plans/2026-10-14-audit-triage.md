# 2026-10-14 audit: findings, triage, release mapping

Five read-only audits at v0.9.4 (f91fbbbf): dashboard API wiring, dashboard settings and actions, a live
dashboard pass, docs/skills/install, and separation of concerns. Each finding below was re-checked by the
lead. **live** means it was reproduced against the running service, **code** means both ends were read, and
**suspected** means it is not proven yet and will not be fixed until it is. Triage: **fix** (defect),
**cosmetic** (a small mistake, fixed by judgment), **leave** (with a reason), **Steven** (needs his decision).

## 0.9.5: the dashboard does what it says

| # | Finding | Evidence | Triage |
|---|---|---|---|
| D1 | Channel Delete always 400: no `requestedBy` | live: 400 without, 404 with | fix |
| D2 | Shared-file Delete always 400: no `requestedBy`; its test fakes success | live: 400 without, 200 with | fix + real-shape test |
| D3 | Edit → Save always re-posts the current environment; for an undefined agent that marks running sessions `lost` and sets it offline, even when only the description changed | code: `inspector-forms.mjs:41` preselects, `agent-session-actions.mjs:110`, `environment_assignment.py:128-140` has no unchanged check | fix: post only when changed (dashboard), and make an unchanged assignment a no-op (service) |
| D4 | Close on a dashboard-started run stores "Closed from run inspector by dashboard operator." as a reply from the agent | code: `run-inspector.mjs:307`, `dispatch_run_settlement.py:68`, `dashboard_run_report.py:214` (sender = target_agent) | fix: an operator close mirrors nothing |
| D5 | A queued run's blocker renders "[object Object]" | code: `records.py:285` is a dict, `status.js:161,182` stringifies it | fix |
| D6 | Cancelling a queued run (0.9.4) sends no live update; open dashboards show it queued | code: `controls.py:86-91` has no broadcast | fix |
| D7 | Start/Restart/Reset offered for agents no host defines; the service always 409s; `definitionState` is never read | code: grep 0 readers (control: `sessionHandle` found); `records.py:433` | fix: disable with the reason |
| D8 | Session-handle edit toasts "Agent updated" when the service refused it as a collision | code: `session_handle_change.py:209`, result discarded | fix |
| D9 | Session-mode switch ignores `changed:false`, `warning`, `resumeCommand`; always "Switched" | code: `session_mode.py:119-131` | fix |
| D10 | Remind on a contract that is not due does nothing; bulk says "N reminded" regardless | code: `dispatch_sweeps.py` returns reminded/skipped | fix: show what happened |
| D11 | Message Run chip, "awaiting" badge and Run row never render: messages carry no run fields | live: inbox record keys | fix: remove the dead reads, or carry the run id (decide with dev) |
| D12 | Two environment badges (`unknownProcesses`, env `bridgeBuild`) have no producer | code: 0 producers (control: `terminalReason` found in aify-env) | cosmetic: remove |
| D13 | Labels that misdescribe: `contract_stale_hours`, `agent_liveness_seconds`, the model/effort hint, Remove agent, Unsend, the Overdue tile's "30 min" against a default of 10, the Failed tile counting cancelled runs, settings clamped silently | code (subagent; lead to spot-check each before editing) | cosmetic |
| D14 | `/health` names whether the operator gate is on (promised to dashboard-manager) | n/a | feature, small |
| D15 | Live pass: missing favicon (404), and the sign-in page's own 401 logged as a console error on every visit | live | cosmetic |

### Live pass (added after the first triage message)

| # | Finding | Evidence | Triage |
|---|---|---|---|
| L1 | One open tab refetches six panels on every `data_changed`; `agents` is listed as a real change about 1.3 times a second, so the tab makes about 8 API requests a second | live: 29 `data_changed` in 20 s, 26 naming `agents` as a table, not liveness (own websocket count) | fix: find the `agents` write outside the liveness columns; the dashboard should also coalesce |
| L2 | Opening Sessions auto-mounts a managed console and focuses its input; a stray key goes to a live agent. The resize is by design (the console is the managed TUI; resident consoles are excluded, `xterm-mount.mjs:376`) | live (subagent network log) + code | discuss: no auto-focus |
| L3 | A hermes gateway token is shown in plain text: a spawn request's stored `error` embeds the gateway JSON (`spawn_1790779653349_394e088b`, 10-01) | live: API scan, 1 of 500 rows | fix: redact at write and at display |
| L4 | Successful spawns are recorded as failed: 89 of the last 120 read "Abandoned: claimed by a live bridge but never settled" | live: `spawn_1791918337937_17723494` failed while its session `sess_1791918337967_e0b2ac6f` is `running` and golf-senior-dev is working | fix: trace who should settle a started spawn (comms or aify-env) |
| L5 | Analytics "Live agents" 42 against Chat's 28: it counts offline agents, but its tooltip says "live worker + fresh heartbeat" | code: `analytics.py:179-182` (subagent) | cosmetic |
| L6 | "Worked 0m ago" for idle agents: last worked is `MAX(COALESCE(finished_at, now))` over every claimed run, so any stale unfinished run reads as now | code: `analytics.py:369-371` | fix |
| L7 | Smaller display contradictions: leaderboard completed/total against its rate, message tile 1629 against the chart's 1617, a completed run's elapsed still counting, "triggered" showing the claim time, a session "online" in the list and "running" in its header, a doubled full stop, the quota tile's "5h — left" | live (subagent) | cosmetic, each checked when fixed |

## 0.9.6: docs, installer and labels tell the truth

| # | Finding | Evidence | Triage |
|---|---|---|---|
| X1 | `install.hermes.md:149` still says the notify hook installs | code | fix |
| X2 | UNINSTALL misses the Claude `SessionStart startup\|resume\|clear` hook and the Codex `SessionStart` hook | code: `install.sh:1757,1531` | fix |
| X3 | DECISIONS.md:1204,1283 say an operator key is always generated | code: `config.py:82` defaults empty | fix |
| X4 | KNOWN_ISSUES lists the `strictMcp` bug as open; it is fixed (a620a102) | code | fix |
| X5 | Hermes `--with-hook` examples in install.sh usage, README, the install skill and HERMES_INTEGRATION | code | fix |
| X6 | comms' own launcher renderer lacks `shopt -u patsub_replacement`: an `&` in a value corrupts the launcher | live: reproduced on bash 5.3.9 | fix: render through aify-wrapper's `render.sh` and delete the copy |
| X7 | Minor drift: HERMES_INTEGRATION's PYTHONPATH line, TARGET_ARCHITECTURE missing `aify-env agents`, the skill's removal scope, quoted error strings, AIFY_ENV_BOUNDARY saying aify-env must not derive status, the roadmap's "Finished" | code | cosmetic |

## 0.9.7: separation held where it does not need Steven

| # | Finding | Evidence | Triage |
|---|---|---|---|
| S1 | With `/settings` unreadable, the bridge's wrapper cache is empty, so it spawns runtimes itself | code: `managed-wrapper-cache.mjs:15,25` | fix: fail closed (no spawn when unknown) |
| S2 | "Routers hold HTTP only" has no test and is broken: `environments.py` heartbeat 333 lines, `analytics.py` 220 | code | move the two largest into api_core; add the rule's test with a frozen list |
| S3 | Screen rules duplicated in comms (`console_working.py` vs `terminal_text.py`), already drifted | code | fix: one owner |
| S4 | Dead: `scripts/normalize_machine_id_casing.py`, `AIFY_COMMS_DELEGATE_SPAWNS`, test-only modules in the bridge copy | code | remove |
| S5 | The reconciler-has-a-caller test counts comments | code | tighten |

## Needs Steven (last)

- **P7 switch and P8 deletions** of the 0.9 agent-state plan have not started and are not recorded as slipped. When and whether to do them.
- **Host work out of comms** (hermes gateway host, bridge spawns and kills, launchers calling bridge files by path, aify-env core naming the comms route): a multi-repo move, not a fix.
- **Delete the deprecated pi runtime** (`pi-session.js` is 994 lines, 6 under the gate) and the **template leftover `service/containers/`** (the routes 503 here).
- **Files over the limit by design**: `install.sh` at 2187 lines; `hermes-aify.sh.in` at 1172, which no gate measures.
- Earlier: worktree and binding-file cleanup; relaunching agents.

## Left, with reasons

- The 8800 and 8811 LAN binding: the operator decided this on 2026-09-25.
- Delete session leaving "active" terminal rows: suspected only (the route requires an ended session).
