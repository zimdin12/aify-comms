# Plan 2 — Runtime Capabilities + Pi Delivery Flip Design Spec

**Status:** Draft — pending operator review
**Author:** comms-senior-dev (Claude Opus 4.7) + operator
**Date:** 2026-05-25
**Related branch:** `feature/dashboard-console-mode`
**Builds on:** [Plan 1 spec](2026-05-25-runtime-adapter-design.md), [Plan 1 implementation plan](../plans/2026-05-25-plan1-runtime-adapter-session-handle.md)

## Goal

Fill in the capability flag layer of the `RuntimeAdapter` contract — every Plan-1-stubbed method that throws `"not yet implemented: Plan 2"` becomes a concrete value driven by the runtime's actual delivery surface in aify-comms. With that foundation, route pi delivery away from `pi-session-resume`'s spawn-fresh-worker pattern (the architectural mismatch that's caused recurring `--model unknown` / missing-API-key failures) and into `managed_via_wrapper` — the same pattern claude/hermes/codex already use.

Also: introduce a Python-side `RuntimeAdapter` package mirroring the JS one, so Plan 3 can be a pure migration of consumers rather than a mixed "introduce abstraction + migrate" plan.

The user-visible outcome: pi agents auto-migrate on next launch from "spawn a fresh `pi --mode rpc --resume X --model Y` worker per inbound message" to "deliver into the bridge-spawned `pi-aify` PTY backing." No more "`Model 'unknown' not found`" failures. No more "API key not inherited" failures. Pi finally fits the same shape as the other multi-client runtimes (claude/hermes via gateway, codex via app-server).

## Why

Operator-reported pain (2026-05-25) traced to the spawn-fresh-worker pattern:

| Incident | Root cause |
|---|---|
| sc-omp-test-1 ping failed with `Model "unknown" not found` | Pi worker spawned without inheriting the operator's interactive pi's model |
| sc-omp-test-2 reply failed with `No API key for cloudflare-ai-gateway` | Pi worker spawn-time env didn't inherit the interactive shell's API key |
| pi resident registration tries to wake but spawns yet another pi process | Single-client RPC mutex means the worker can't even attach to the operator's live pi |

The architectural mismatch is **structural** — `omp --mode rpc` is single-client stdio. Without a multi-client gateway like hermes's `tui_gateway` or codex's `app-server`, there's no way for an external dispatch worker to "inject" into the operator's running pi. So today's `pi-session-resume` path forks a brand-new pi process per inbound message and runs the prompt there — fragile, slow, and the worker needs its own model + API config every time.

The fix is to declare pi `supportsResident=false` and route all pi delivery through `managed_via_wrapper` (which already works for claude/hermes/codex). The bridge spawns the wrapper PTY, the wrapper IS the backing, the operator can attach the dashboard Console to that same PTY. Same shape as the rest.

## Scope

### In scope (Plan 2)

1. **Adapter capability methods** for both JS (`mcp/stdio/adapters/`) and Python (`service/runtimes/`, new package). Plan 2 implements the six capability getters in both languages.
2. **Pi delivery flip**: `_managed_via_wrapper_for_runtime` returns True for pi; pi resident agents auto-migrate to managed on next launch with graceful drain (waits for active runs to complete before flipping).
3. **Consumer migration** for the call sites Plan 2 touches: `_managed_via_wrapper_for_runtime`, `_default_capabilities_for`, `defaultCapabilitiesForRuntime`, `controlCapabilitiesForRuntime`, `_agent_execution_mode` (pi branch). Other consumers wait for Plan 3.
4. **Removal of `pi-session-resume` from the resident path** in `mcp/stdio/runtimes.js`. The persistent `PiSession` pool in `pi-session.js` stays — it's how the wrapper-PTY's pi process is managed for steering/interrupt; it just isn't the front-door delivery path anymore.
5. **Cross-language consistency test** ensuring JS and Python adapters report identical capability values per runtime.
6. **Docs**: DECISIONS.md entry, README.md mention of `service/runtimes/`.

### Out of scope (Plan 3)

- Filling in the `consoleCommand` / `injectMessage` / `interrupt` / `steer` methods on the adapter. Plan 3.
- Migrating the per-runtime branches inside `_default_console_command`, dispatch dispatcher, delivery shims. Plan 3.
- Wiring opencode through `opencode serve` for multi-client capability. Separate follow-up.
- Implementing the runtime-ready event hook + new `ready` status taxonomy. Plan 4 (tracked).

## Architecture

### Capability contract (both languages, identical surface)

Six capability properties added to the existing `RuntimeAdapter` contract:

| Property | Type | Semantics |
|---|---|---|
| `supportsResident` | bool | aify-comms can deliver resident dispatches (live agent injection) for this runtime today. Static per-runtime declaration; does NOT depend on per-agent state. |
| `supportsManaged` | bool | aify-comms can spawn a managed/headless dispatch worker for this runtime. |
| `supportsSteering` | bool | aify-comms can mid-turn steer (append context to an active turn). For runtimes that distinguish "resident steering" vs "managed steering," this is the OR of both — i.e. true if any aify-comms delivery mode supports steering. |
| `supportsInterrupt` | bool | aify-comms can interrupt an active dispatch. |
| `supportsMultiClient` | bool | The runtime exposes a multi-client communication surface aify-comms can attach to (claude notifications, codex app-server WS, hermes gateway WS). False when the runtime is single-client (pi RPC mutex). |
| `preferredDeliveryMode` | str enum | `"resident"` \| `"managed"` \| `"managed-via-wrapper"`. The default delivery shape the dispatch dispatcher picks when no explicit mode is requested. |

### Per-runtime capability matrix

| Runtime | resident | managed | steering | interrupt | multiClient | preferredDeliveryMode | Notes |
|---|:---:|:---:|:---:|:---:|:---:|---|---|
| claude-code | ✓ | ✓ | ✓ | ✓ | ✓ | `managed-via-wrapper` | claude-channel.js notifications path is multi-client; wrapper-backing proven in Plan 1 era |
| codex | ✓ | ✓ | ✓ | ✓ | ✓ | `managed-via-wrapper` | App-server JSON-RPC `turn/steer` + `turn/interrupt` confirmed multi-client |
| hermes | ✓ | ✓ | ✓ | ✓ | ✓ | `managed-via-wrapper` | Gateway URL gates per-agent availability; capability flag declares runtime can do it |
| pi | ✗ | ✓ | ✓ | ✓ | ✗ | `managed-via-wrapper` | Single-client RPC mutex; steering/interrupt only meaningful in managed delivery |
| opencode | ✗ | ✓ | ✗ | ✓ | ✗ | `managed` | aify-comms doesn't wire `opencode serve` today; tracked as separate follow-up |

`supportsResident` for hermes is a static `true` — the adapter declares "this runtime CAN do resident." The server separately checks per-agent state (`runtimeConfig.gatewayUrl` non-empty) before routing a resident dispatch. This matches the existing `hermes-missing-handle` wakeMode logic.

`supportsSteering` for pi is `true` because the persistent PiSession's `command:steer` path works in managed delivery. Resident pi doesn't exist post-flip, so the steering-in-resident question is moot.

### File layout

#### JS additions

```
mcp/stdio/adapters/
├── base.js                      # extend: capability getters become abstract (throw without override)
├── claude.js                    # extend: capability getter overrides
├── codex.js                     # extend
├── hermes.js                    # extend
├── pi.js                        # extend
├── opencode.js                  # extend
└── index.js                     # unchanged

mcp/stdio/scripts/
└── dump-capabilities.mjs        # NEW — prints all adapters' capabilities as JSON for cross-language consistency test

mcp/stdio/tests/adapters/
├── contract.test.js             # extend: Plan 2 capability assertions now expect concrete values (no longer "throws")
├── claude.test.js               # extend: pin capability values
├── codex.test.js                # extend
├── hermes.test.js               # extend
├── pi.test.js                   # extend
└── opencode.test.js             # extend
```

#### Python additions

```
service/runtimes/
├── __init__.py                  # NEW — adapter_for(name) factory + supported_runtimes()
├── base.py                      # NEW — RuntimeAdapter ABC + capability properties (abstract) + normalize_session_handle + normalize_model_override + HANDLE_PLACEHOLDERS + MODEL_PLACEHOLDERS
├── claude.py                    # NEW — ClaudeAdapter
├── codex.py                     # NEW — CodexAdapter
├── hermes.py                    # NEW — HermesAdapter
├── pi.py                        # NEW — PiAdapter
└── opencode.py                  # NEW — OpencodeAdapter

service/tests/runtimes/
├── test_base.py                 # NEW — abstract contract checks (raises NotImplementedError, normalizer behavior)
├── test_per_adapter.py          # NEW — per-adapter capability assertions pinned
└── test_factory.py              # NEW — adapter_for() + alias resolution + supported_runtimes()

service/tests/
└── test_runtime_adapter_consistency.py  # NEW — cross-language consistency: runs `node mcp/stdio/scripts/dump-capabilities.mjs` and compares to Python adapter values
```

#### Consumer changes (Plan 2 migration)

```
service/routers/api_v2.py
  - _managed_via_wrapper_for_runtime   : drop pi exclusion; consult adapter.preferred_delivery_mode
  - _default_capabilities_for          : build from adapter.supports_* getters
  - _agent_execution_mode (pi branch)  : pi resident → managed-via-wrapper migration logic
  - new: _pi_resident_pending_flip helper that gates the flip on dispatch_state.has_active_run

mcp/stdio/runtimes.js
  - defaultCapabilitiesForRuntime  : build from adapterFor(runtime).supports* getters
  - controlCapabilitiesForRuntime  : same
  - createPiController             : the resident-path branch becomes dead code; route to managed-via-wrapper
  - pi-session-resume delivery path: removed from the dispatch dispatcher entry table
```

#### Database

No schema changes needed. The pi flip rewrites `agents.session_mode` from `"resident"` to `"managed"` for affected pi rows; existing `session_handle` is preserved.

### Pi flip mechanics (graceful drain)

1. **Detection.** When the bridge calls `comms_register` for a pi agent, the server side checks: is this a pi agent currently registered as `sessionMode=resident`?

2. **Pending-flip flag.** If so, set `agents.runtime_state.pi_resident_pending_flip = true`. Dashboard reads this to display "migrating to managed" badge briefly.

3. **Drain.** A small server-side helper `_drain_and_flip_pi_resident_agents` runs periodically (~every 5 seconds) and processes any pi agent with `pi_resident_pending_flip = true`:
   - If `dispatch_state.has_active_run == false` AND there are no queued runs: flip.
     - `session_mode = "managed"`
     - `runtime_state.pi_resident_pending_flip = false`
     - `runtime_state.flipped_at = now()`
     - `capabilities` recomputed from `adapter_for("pi").supports_*`
     - Existing `session_handle` preserved (the wrapper will resume that session)
   - Else: wait until next tick.

4. **Front-door safety net.** During the pending-flip window, any new resident pi dispatch attempt is rejected with a 409 explaining "this agent is migrating from resident to managed; retry in a few seconds." Existing in-flight resident runs complete via the legacy path.

5. **Bridge-side.** After the flip, the bridge stops claiming resident pi runs entirely. Existing PiSession pool stays (used as backing for the managed-via-wrapper PTY's pi process).

### Failure modes

| Failure | Behavior |
|---|---|
| Operator manually re-registers a pi agent as `sessionMode=resident` after flip | Server returns 400: "pi does not support resident delivery in aify-comms; use sessionMode=managed (auto-resolved from preferredDeliveryMode). To restore the legacy spawn-worker behavior, see DECISIONS.md (intentionally removed in Plan 2)." |
| Pi agent has stuck active run blocking flip | Operator sees the `pi_resident_pending_flip = true` badge for as long as the run hangs. Existing run-stuck mitigations (manual interrupt via dashboard) unchanged. Worst case, operator interrupts the run, drain completes, flip lands. |
| `service/runtimes/` import path fails inside container | Bridge errors out clearly with `ImportError: cannot import name 'adapter_for' from 'service.runtimes'`. Service container is rebuilt every plan; Dockerfile already COPYs `service/`. |
| JS adapter and Python adapter disagree on a capability value | `test_runtime_adapter_consistency.py` fails CI and surfaces the diff. |
| Node not on PATH when Python runs consistency test | `shutil.which("node")` returns None; test skips with `pytest.skip("node not available")`. CI environment guarantees node; operator's WSL Ubuntu + native Windows both have it. |

### Cross-language consistency test

`mcp/stdio/scripts/dump-capabilities.mjs`:
```javascript
import { adapterFor, supportedRuntimes } from "../adapters/index.js";
const out = {};
for (const name of supportedRuntimes()) {
  const a = adapterFor(name);
  out[name] = {
    supportsResident: a.supportsResident,
    supportsManaged: a.supportsManaged,
    supportsSteering: a.supportsSteering,
    supportsInterrupt: a.supportsInterrupt,
    supportsMultiClient: a.supportsMultiClient,
    preferredDeliveryMode: a.preferredDeliveryMode,
  };
}
process.stdout.write(JSON.stringify(out, null, 2));
```

`service/tests/test_runtime_adapter_consistency.py`:
```python
def test_js_and_python_adapters_agree_on_capabilities():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH — cross-language consistency check skipped")
    proc = subprocess.run([node, "mcp/stdio/scripts/dump-capabilities.mjs"],
                          cwd=ROOT, capture_output=True, text=True, check=True)
    js_caps = json.loads(proc.stdout)
    for name, js_values in js_caps.items():
        py_adapter = adapter_for(name)
        py_values = {
            "supportsResident": py_adapter.supports_resident,
            "supportsManaged": py_adapter.supports_managed,
            "supportsSteering": py_adapter.supports_steering,
            "supportsInterrupt": py_adapter.supports_interrupt,
            "supportsMultiClient": py_adapter.supports_multi_client,
            "preferredDeliveryMode": py_adapter.preferred_delivery_mode,
        }
        assert py_values == js_values, f"capability drift for {name}: JS={js_values}, Py={py_values}"
```

Naming convention: JS uses `camelCase` (`supportsResident`), Python uses `snake_case` (`supports_resident`). The consistency test maps between them.

### Backwards compatibility

- **Existing pi resident agents:** auto-migrate on next bridge launch (graceful drain). No data loss; `session_handle` is preserved.
- **Existing dashboard Console launches:** unchanged. Plan 1's `_default_console_command` already routes pi managed correctly.
- **Existing operator code calling `comms_register(runtime="pi", sessionMode="resident")`:** post-flip, server returns 400 with explanation. Operator updates their tooling to omit `sessionMode` (server auto-resolves from `preferredDeliveryMode`) or passes `sessionMode="managed"` explicitly.
- **Plan 1's RuntimeAdapter tests:** still pass; Plan 2's changes are additive to the contract suite.

## Migration plan summary (rollout order)

1. Land the Python adapter package (Plan 2 Task 1-8). Cross-language consistency test passes from day 1.
2. Land JS capability overrides (Plan 2 Task 9-14). Both languages agree.
3. Land consumer migration in `api_v2.py` and `runtimes.js` (Plan 2 Task 15-18).
4. Land the pi flip helper + `_drain_and_flip_pi_resident_agents` (Plan 2 Task 19-21).
5. Remove `pi-session-resume` from the dispatch entry table (Plan 2 Task 22).
6. Docs + smoke + push (Plan 2 Task 23-24).

## Open questions

None at design time — all 4 of the operator's brainstorm answers are integrated above.

## Success criteria

- All Plan 1 + Plan 2 tests green: `node --test mcp/stdio/tests/` and `python -m pytest service/tests/`.
- `adapter_for("pi").supports_resident == false` in both languages.
- `_managed_via_wrapper_for_runtime("pi") == True` in `api_v2.py`.
- Cross-language consistency test passes.
- Manual e2e: register a pi agent. Wait. Pi resident is rejected with a clean explanation; pi managed delivers messages through the wrapper PTY. The `comms-senior-dev-pi` failure pattern (`Model "unknown" not found`) doesn't reproduce.
- Existing pi resident agents auto-migrate. Their `session_handle` is preserved; their dashboard listing flips from "resident" to "managed" within ~5s of the bridge picking up the deregistration → re-registration cycle (or whenever the drain helper runs).
- The codex follow-up (#118 — `--remote` + `resume` ordering) is unblocked by Plan 2 since Plan 2 doesn't touch the codex-aify wrapper.

## Why this design over alternatives

- **vs. hardcoding capabilities in both languages without an adapter** (option A from brainstorm): violates "clean architecture always" by leaving two sources of truth that drift. The consistency test would have to verify hardcoded constants against each other — same problem.
- **vs. shared JSON descriptor** (option B): introduces a config file format we don't need; adapter classes become anaemic shells around a JSON load.
- **vs. soft-deprecating pi-session-resume** (option B from pi-flip brainstorm): keeps the broken code path alive longer, costs maintenance, and contradicts the operator's stated direction of "drop harnesses that can't do clean injection."
- **vs. interrupting in-flight pi runs at flip time** (alternative to option 3 from brainstorm): risks losing work product. Graceful drain is the safer default; the worst case (stuck run) is the same problem operator has today with stuck runs in general — orthogonal to this plan.

## References

- Plan 1 spec: `docs/superpowers/specs/2026-05-25-runtime-adapter-design.md`
- Plan 1 implementation: `docs/superpowers/plans/2026-05-25-plan1-runtime-adapter-session-handle.md`
- Operator's "clean architecture always" feedback: `~/.claude/projects/.../memory/feedback-clean-architecture-always.md`
- DECISIONS.md entry for pi single-client RPC mutex (predates this plan)
- Codex app-server docs: https://developers.openai.com/codex/app-server
- OpenCode server docs: https://opencode.ai/docs/server/
