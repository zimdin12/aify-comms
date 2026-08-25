#!/usr/bin/env node
// A managed native-runtime agent claims its runs whether or not it carries `native-managed-run`.
//
// TWO PRODUCERS WRITE THAT CAPABILITY, WITH DIFFERENT VOCABULARIES.
//
//   * service/db.py backfills `native-managed-run` for managed codex/opencode/pi/hermes agents at
//     startup, deliberately skipping claude-code.
//   * runtimes.js defaultCapabilitiesForRuntime — the derivation a bridge sends on register — NEVER
//     emits it, for any runtime. It emits resident-run, managed-run, resume, interrupt, steer, spawn.
//
// Re-register is a full state refresh, so an agent that re-registers after the backfill LOSES the
// capability until the service restarts and backfills again. Measured on the live database
// 2026-08-25: of ten managed hermes agents, SIX carried it and FOUR did not — same runtime, both
// states, no other difference. codex 2/2 and pi 2/2 had it; claude-code 0/10 correctly did not.
//
// NOTHING IS BROKEN BY THAT, and this test is why. supportedExecutionModes reads
//
//     capabilities.includes("native-managed-run") || NATIVE_MANAGED_RUNTIMES.has(runtime)
//
// so the runtime check carries every agent the capability would have. The `||` is load-bearing and
// nothing said so. db.py's own docstring claims the opposite — that agents missing the capability
// have their managed runs refused and "queue forever" — which was true before the fallback existed
// and is not true now. A future reader trusting that docstring could remove the fallback as redundant
// and strand exactly the four agents measured above.
//
// This test fails if the fallback goes.

import assert from "node:assert/strict";

import { NATIVE_MANAGED_RUNTIMES, supportedExecutionModes } from "../dispatch-execution.js";

/** The shape supportedExecutionModes reads, with only what this test varies. */
function managedAgent(runtime, capabilities) {
  return { sessionMode: "managed", runtime, capabilities };
}

// ── the control ────────────────────────────────────────────────────────────────────────────────
{
  // An agent WITH the capability claims managed runs. If this ever stops being true the assertions
  // below prove nothing, because everything would be failing rather than falling back.
  const modes = supportedExecutionModes(managedAgent("hermes", ["managed-run", "native-managed-run"]));
  assert.ok(
    modes.includes("managed"),
    "an agent carrying native-managed-run no longer claims managed runs; this test is now vacuous",
  );
}

// ── the fallback, for every runtime the backfill covers ────────────────────────────────────────
{
  for (const runtime of NATIVE_MANAGED_RUNTIMES) {
    const withCap = supportedExecutionModes(managedAgent(runtime, ["managed-run", "native-managed-run"]));
    const without = supportedExecutionModes(managedAgent(runtime, ["managed-run"]));
    assert.deepEqual(
      without, withCap,
      `${runtime}: losing native-managed-run changed what it claims. The runtime fallback in `
        + "supportedExecutionModes is what makes the two producers' disagreement harmless; without it, "
        + "every agent that re-registers between service restarts stops claiming its managed runs.",
    );
    assert.ok(without.includes("managed"), `${runtime} does not claim managed runs at all`);
  }
}

// ── and it is scoped, not blanket ──────────────────────────────────────────────────────────────
{
  // The fallback must not hand "managed" to a runtime the backfill deliberately excludes. claude-code
  // is channel/wrapper-backed only, which is exactly why db.py skips it — if the fallback covered it
  // too, the environment bridge would claim runs the wrapper's own child bridge owns.
  assert.ok(!NATIVE_MANAGED_RUNTIMES.has("claude-code"), "claude-code joined the native-managed set");
  const modes = supportedExecutionModes(managedAgent("claude-code", ["managed-run"]));
  assert.ok(
    !modes.includes("managed"),
    "managed claude-code claims native managed runs, which the wrapper child bridge already owns",
  );
}

// ── the capability alone is still enough, for a runtime outside the set ────────────────────────
{
  // Belt and braces in the other direction: if a runtime is ever removed from NATIVE_MANAGED_RUNTIMES
  // but its agents still carry the capability, they keep working.
  const modes = supportedExecutionModes(managedAgent("some-future-runtime", ["native-managed-run"]));
  assert.ok(modes.includes("managed"), "the capability stopped being sufficient on its own");
}

console.log("native-managed-run-fallback-is-load-bearing.test.js: all assertions passed");
