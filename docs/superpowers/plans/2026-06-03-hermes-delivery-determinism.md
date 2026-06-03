# Hermes Delivery Determinism — Implementation Plan

> **For agentic workers:** execute task-by-task; review between tasks. Steps use `- [ ]`.

**Goal:** Make inter-agent messages to managed AND resident hermes agents actually deliver and render in the visible TUI, reliably, and produce a real reply — restoring the determinism that worked at `ac585ab` without reintroducing the synthetic `aify-<agentId>` session name.

**Architecture:** Keep native real session ids (symmetric with claude/codex). Fix the service claim gate so the channel-sidecar can claim resident hermes runs. Restore a deterministic guarantee that the visible TUI attaches to the SAME gateway host the delivery loop polls, in a session that is known/pre-seeded, resolved by real id with a deterministic fallback. Fix duplicate-session proliferation and the regressed bypass flag.

**Tech stack:** Python FastAPI (`service/`), Node host bridges (`mcp/stdio/`), bash wrapper (`install.sh`), hermes plugin (`integrations/hermes-aify-plugin`).

---

## Root causes (evidence)

- **RC1 (mp: queued, never claimed):** `service/routers/api_v2.py:2311` `_bridge_claim_block_reason` — `bridge_not_current` guard blocks a `channel-sidecar` claim for a resident agent whose `runtime_state.bridgeInstanceId` is set. Carve-out exists only inside `if session_mode == "managed":` (2320). `is_channel_sidecar_claim` already computed at 2287.
- **RC2 (ci: requeue forever / not in TUI):** the visible TUI is not attached to the loop's gateway host (`session.active_list` empty on ci's gateway 9136); `waitForActiveSession` returns null → requeue forever. Underlying: native-id reconciliation is non-deterministic (stale marker, no pre-seed, TUI may run its own tui_gateway instead of attaching to the host). Regression vs `ac585ab` (deterministic stable key + pre-seed).
- **RC3 (duplicate sessions):** relaunch creates a second resident `agent_sessions`/console row instead of superseding the prior one (mp-senior-dev, mp-manager each show 2 `resident_*` sessions).
- **RC4 (--yolo lost):** hermes-aify regenerated without the `--yolo`/`HERMES_YOLO_MODE=1` bypass default.

---

## Task 1: Service claim-gate carve-out for resident channel-sidecar (RC1)

**Files:** Modify `service/routers/api_v2.py` (~2311); Test `service/tests/test_api_v2_regressions.py`.

- [ ] Add `and not is_channel_sidecar_claim` to the `bridge_not_current` condition at 2311 so a declared channel-sidecar claim bypasses the one-current-bridge guard on the resident path (mirroring the managed carve-outs).
- [ ] Test: a resident hermes agent with `runtime_state.bridgeInstanceId` set; a `channel-sidecar` claim for an `execution_mode='resident'` run is NOT blocked; a non-sidecar foreign bridge IS still blocked.
- [ ] Rebuild container; verify mp's queued run claims.

## Task 2: Deterministic visible-TUI ↔ gateway-host convergence (RC2)

**Files:** `install.sh` (hermes wrapper), `mcp/stdio/hermes-managed-host.js`, `mcp/stdio/hermes-endpoint.js`, `integrations/hermes-aify-plugin/aify_hermes_plugin/patches.py`.

Guarantee, deterministically, that: (a) the visible `hermes --tui` attaches to the per-agent gateway HOST (via `HERMES_TUI_GATEWAY_URL`) — never spins up its own tui_gateway; (b) the session it resumes EXISTS and is attached on that host before the loop delivers (pre-seed / ensure-session by REAL id); (c) the marker, active-session file, wrapper resume, and loop target all reference that one real id; (d) the loop resolves by real id with a deterministic fallback to the gateway's freshest attached session (already present) AND a bounded "no TUI attached" failure with an actionable message instead of infinite requeue.

- [ ] Verify/repair gateway-env wiring on ALL hermes launch paths (managed, resident, explicit `--resume`): `AIFY_HERMES_GATEWAY_URL` + `HERMES_TUI_GATEWAY_URL` exported so the TUI attaches to the host.
- [ ] Re-introduce a deterministic session guarantee using the REAL id: at launch, resolve the real id (explicit resume → marker → create fresh + capture), ensure that session is attached on the gateway host (ensure-session/pre-seed equivalent), and write it atomically to marker + active-file before the loop can claim.
- [ ] Bounded requeue: after N empty-active_list polls, fail the run with "no visible TUI attached to gateway <url>" (surfaced to sender) instead of requeue-forever.
- [ ] Tests in `mcp/stdio/tests/` for: real-id primary match, fallback to freshest attached, bounded-fail on no-attach.

## Task 3: Stop duplicate resident sessions on relaunch (RC3)

**Files:** `service/routers/api_v2.py` (resident `agent_sessions`/console upsert + supersession).

- [ ] On resident re-register/relaunch, supersede/replace the prior resident `agent_sessions` + console row for the same `(agent_id, machine_id, runtime, session_mode)` instead of inserting a sibling.
- [ ] Test: two sequential resident registers for one agent → one live session row, prior marked superseded/stopped.

## Task 4: Restore --yolo bypass default for hermes-aify (RC4)

**Files:** `install.sh` (hermes wrapper + PS1).

- [ ] Ensure `HERMES_AUTO=true` default → `--yolo` / `HERMES_YOLO_MODE=1`, opt-out via `--safe`/`--no-auto`; mirror in the PS1 wrapper. Add a guard test/grep.

## Task 5: Review + docs + skills

- [ ] Adversarial review of Tasks 1–4 diffs.
- [ ] Update `AGENTS.md` realization matrix, `KNOWN_ISSUES.md`, `DECISIONS.md` (delivery model = native id + deterministic gateway-host attach).
- [ ] Update `.claude/skills/aify-comms-debug/SKILL.md` + `.agents` mirror with the claim-gate + convergence fixes.

## Task 6: End-to-end live test (clean room)

- [ ] Kill the user's messy bridges; run a fresh `aify-comms` env bridge.
- [ ] Spawn/launch two hermes agents; `comms_send` one→other; verify the message renders in the target's visible TUI and a real reply threads back. Verify dashboard delivery state = completed + reply.

## Task 7: Commit + push

- [ ] Stage all, commit with a descriptive message, push to origin/main.
