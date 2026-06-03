# Comprehensive Cleanup — Lifecycle, Switch Safety, State Model

> Built from a 4-agent deep audit (lifecycle actions, resident↔managed switch, feature wiring, state model). Execute in phases; review + commit between; tests each phase.

**Goal:** clean, consistent lifecycle verbs; safe resident↔managed switching for every harness; and ONE coherent state model so the dashboard can't show contradictions ("Stopped but running", "stale but has session", duplicates, mode-lag). Features themselves are already fully wired (audit C found no missing/dead features).

---

## Phase 1 — Switch safety (HIGH; data-loss + footgun)

**Files:** `service/routers/api_v2.py`, `service/dashboard.html`, `service/runtimes/*.py`.

- [ ] **G1 — resident→managed must carry the native session handle.** `_coldstart_spawn_request_for_dispatch` (~api_v2.py:6126/6262) INSERTs `spawn_requests` with `session_handle=''`, so the managed worker starts a FRESH native session (loses codex thread / hermes gateway / claude transcript). The dedicated restart path (~10503) carries `session["session_handle"]`. Fix: add `session_handle` to the coldstart INSERT, sourced from the agent's current `session_handle`. Verify the claim payload (~4829) then carries it to the bridge (server.js:2508). Test: a coldstart spawn_request for a runtime with a stored handle carries that handle.
- [ ] **G2 — block managed→resident for runtimes that don't support resident.** `switch_agent_session_mode` (~12488) has no `supports_resident` guard → flipping pi/opencode to resident yields `presence-only` + every dispatch rejected. Fix: (server) reject `mode='resident'` when the runtime adapter `supports_resident is False` (actionable 409; allow `force` only with a clear warning); (dashboard) `agentModeSwitchAction` (~4779) returns no "Switch to resident" for pi/opencode (disabled tooltip "resident not supported for this runtime"). Test: switch pi→resident is rejected.
- [ ] **G3 — warn on binding a native handle already owned by another live agent** (lc-coder + lc-tech-lead share a codex thread live). Add a uniqueness check/warning in the coldstart + handle-set path.
- [ ] **G4/G5 (UX)** — on the 409 active-run block, offer a confirm→`force=true` retry in `switchAgentSessionMode` (~dashboard 4791); add a derived hint when an agent is `session_mode=resident` with no live resident bridge + no candidate for >N s (switch limbo).

## Phase 2 — Lifecycle verb cleanup (MED; the operator's "what is restart/recover" ask)

**Files:** `service/routers/api_v2.py`, `service/dashboard.html`.

- [ ] **Collapse dead actions.** `recover` + `resume` on `POST /sessions/{id}/control` are byte-identical to `restart` and have NO UI caller — drop them from the allow-list (~api_v2.py:10401) and collapse the `next_status` map (~10547) to just `restart→restarting`. (Resident wake-resume stays on `/agents/{id}/control`.)
- [ ] **Rename for clarity.** Dashboard: "Recreate" → "Reset (fresh context)" (~3692/3780/7188); make resident Stop/Resume labels consistent ("Resume wake", ~7976).
- [ ] **Fix stale guidance** pointing to a non-existent Recover button: `dashboard.html:1874/3960/5953` + `api_v2.py:10603` status_note → say "Restart".
- [ ] **Danger tiering + dot colors:** distinct styling for reversible (Stop) vs context-destroying (Reset) vs identity-destroying (Remove); give `cli-takeover`/`recovering` a distinct chat-dot color from idle (~dashboard 470).
- [ ] Produce a canonical lifecycle table in docs (Phase 4).

## Phase 3 — State-model consolidation (HIGH value; the systemic root — careful)

**Files:** `service/routers/api_v2.py`, `service/main.py`, `service/dashboard.html`.

- [ ] **Latent bug first:** `_reconcile_dead_session_status` case (a) reads the FROZEN `agent_sessions.terminal_status` instead of joining live `terminal_sessions` → misses dead terminals (cms-manager/lc-coder/lc-tech-lead show running+attached while terminals are stopped/failed). Fix case (a) to join live `terminal_sessions`.
- [ ] **One liveness predicate.** Consolidate `_resident_bridge_is_fresh`, `_owner_bridge_is_fresh`, `_agent_has_fresh_bridge`, `_has_live_channel_sidecar`, `_has_live_managed_wrapper_child`, `_has_live_terminal_session` into a single `agent_liveness(db, agent_id) -> {worker_live, console_live, resident_bridge_fresh, sidecar_live}` computed once, used by both derivers. (Removes the window/kind asymmetry behind "available after working".)
- [ ] **Derive session status.** Add `_compute_session_display_status(session_row, db)` joining live terminal/bridge truth; call it in `_agent_session_to_dict` (~4886) so GET /sessions derives like GET /agents — the displayed session badge stops reading the frozen snapshot. This kills "Stopped/Stale but running" structurally.
- [ ] **One `LIVE_SESSION_STATUSES` constant** defined once server-side, embedded into the dashboard bootstrap, replacing the 4 divergent sets (api_v2 296 / 15493 / 3441; dashboard 3318).
- [ ] Have session mutators (`_reconcile_dead_session_status`, `_reconcile_duplicate_resident_sessions`) call `_invalidate_agent_live_state` so the dot refreshes same-pass.
- [ ] Keep the run-order in `main.py`; the well-targeted reconcilers (turn-busy clear, orphan requeue, reroute) stay.
- [ ] Once session status is derived, the display-layer band-aids (`sessionModeSummary`, `sessionPriorityScore` retired=0, `sessionPresenceForSession`) can stay as belt-and-suspenders.

## Phase 4 — Docs + skills

- [ ] `AGENTS.md` / `DECISIONS.md` / `KNOWN_ISSUES.md`: the cleaned lifecycle verb model + the canonical state model (derive session status; one liveness predicate) + the switch matrix (full-duplex claude/codex/hermes; managed-only pi/opencode).
- [ ] `.claude/skills/aify-comms{,-debug}/SKILL.md` (+ `.agents` mirrors): the lifecycle clarity table; the switch-safety notes (G1/G2); the state-model note ("session status is derived, no more stopped-but-running").

## Phase 5 — Push + integrations

- [ ] Rebuild container + reinstall bridges; run full JS + Python suites.
- [ ] Commit per phase; push to origin/main; rerun `install.sh` for the integrations.
