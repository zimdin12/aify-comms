#!/usr/bin/env node
// What each runtime tells the dashboard it can do — `runtimeCapability`, which no test named.
//
// This is the row the dashboard draws a spawn target from. Getting it wrong does not raise: a
// runtime advertises a capability it does not have and the spawn fails later for an unrelated-looking
// reason, or advertises none and simply stops being offered.
//
// THE ONE DISCRIMINATION IN IT is `nativeResume`, true for codex/hermes/opencode/pi and false for
// claude-code. Everything else in the capabilities block is a constant today. A list like that is
// exactly what drifted in `channel_delivery.py`'s four runtime sets, so it is asserted here against
// `ENVIRONMENT_RUNTIME_IDS` rather than restated — a runtime added to the advertised set without a
// resume decision fails this file instead of quietly defaulting.
//
// `availabilityFor` is an injected probe, so nothing here touches the real PATH or the operator's
// installed runtimes. That is the whole reason this function is testable and, say, `inspectShebang`
// (which short-circuits to null on win32) is not testable on this machine at all.

import assert from "node:assert/strict";

import {
  ENVIRONMENT_RUNTIME_IDS,
  advertisedEnvironmentRuntimes,
  runtimeCapability,
} from "../environment-runtimes.js";

const AVAILABLE = () => ({ available: true, message: "" });
const UNAVAILABLE = (message) => () => ({ available: false, message });

// ── availability comes from the probe, never from the runtime name ───────────────────────────
{
  const row = runtimeCapability("codex", { availabilityFor: AVAILABLE });
  assert.equal(row.runtime, "codex");
  assert.equal(row.available, true);
  assert.equal(row.unavailableReason, "", "an available runtime carries no reason");
  assert.deepEqual(row.modes, ["managed-warm"]);

  const missing = runtimeCapability("codex", { availabilityFor: UNAVAILABLE("codex CLI not on PATH") });
  assert.equal(missing.available, false);
  assert.equal(missing.unavailableReason, "codex CLI not on PATH",
    "the probe's own message is passed through — an operator needs to know WHICH thing is missing");
}

{
  // A probe that reports unavailable WITHOUT a message still yields a reason, because the dashboard
  // renders this string and an empty one reads as "no problem".
  const blank = runtimeCapability("codex", { availabilityFor: () => ({ available: false }) });
  assert.equal(blank.available, false);
  assert.equal(blank.unavailableReason, "runtime launcher unavailable");

  for (const shape of [{ available: false, message: "" }, { available: false, message: null }]) {
    assert.equal(
      runtimeCapability("codex", { availabilityFor: () => shape }).unavailableReason,
      "runtime launcher unavailable",
      JSON.stringify(shape),
    );
  }
}

{
  // `available` is coerced to a real boolean: the dashboard branches on it, and a truthy string
  // would render a runtime as available while `unavailableReason` said otherwise.
  const truthy = runtimeCapability("codex", { availabilityFor: () => ({ available: "yes" }) });
  assert.equal(truthy.available, true);
  assert.equal(typeof truthy.available, "boolean");
  const falsy = runtimeCapability("codex", { availabilityFor: () => ({ available: 0, message: "nope" }) });
  assert.equal(falsy.available, false);
  assert.equal(falsy.unavailableReason, "nope");
}

// ── the runtime name is normalised before anything else ──────────────────────────────────────
{
  for (const [given, expected] of [["claude", "claude-code"], ["claude-code", "claude-code"], ["CODEX", "codex"]]) {
    const row = runtimeCapability(given, { availabilityFor: AVAILABLE });
    assert.equal(row.runtime, expected, `${given} must normalise to ${expected}`);
  }

  // The probe is called with the NORMALISED name, not the raw one — otherwise an alias would be
  // probed under a name no launcher knows and every aliased runtime would read as unavailable.
  let sawName = null;
  runtimeCapability("claude", {
    availabilityFor: (name) => {
      sawName = name;
      return { available: true };
    },
  });
  assert.equal(sawName, "claude-code");
}

// ── nativeResume: the one real discrimination ────────────────────────────────────────────────
{
  const nativeResumeFor = (runtime) =>
    runtimeCapability(runtime, { availabilityFor: AVAILABLE }).capabilities.nativeResume;

  assert.equal(nativeResumeFor("claude-code"), false,
    "claude-code resumes through the bridge, not natively — this is the discrimination");
  for (const runtime of ["codex", "hermes", "opencode", "pi"]) {
    assert.equal(nativeResumeFor(runtime), true, `${runtime} resumes natively`);
  }

  // Held against the advertised set rather than restated, so adding a runtime to
  // ENVIRONMENT_RUNTIME_IDS without deciding its resume story fails HERE rather than defaulting.
  const advertisedWithout = ENVIRONMENT_RUNTIME_IDS.filter((r) => !nativeResumeFor(r));
  assert.deepEqual(advertisedWithout, ["claude-code"],
    `runtimes without native resume changed to ${JSON.stringify(advertisedWithout)} — that is a `
    + "capability decision, not a typo");

  // An unknown runtime does NOT get native resume. Failing toward the bridge path is the safe
  // direction: a bridge resume that is not needed costs a round trip, a native resume that does not
  // exist loses the session.
  assert.equal(nativeResumeFor("some-future-runtime"), false);
}

// ── the constant half, pinned so a change to it is deliberate ────────────────────────────────
{
  const { capabilities } = runtimeCapability("codex", { availabilityFor: AVAILABLE });
  assert.deepEqual(capabilities, {
    persistent: true,
    nativeResume: true,
    bridgeResume: true,
    cliAttach: false,
    interrupt: true,
    streaming: true,
    tokenTelemetry: false,
    costTelemetry: false,
    contextReset: true,
  });

  // Availability must not leak into the capability block: what a runtime CAN do does not change
  // because its launcher is missing right now.
  const offline = runtimeCapability("codex", { availabilityFor: UNAVAILABLE("gone") });
  assert.deepEqual(offline.capabilities, capabilities);
}

// ── advertisedEnvironmentRuntimes ────────────────────────────────────────────────────────────
{
  const rows = advertisedEnvironmentRuntimes({ availabilityFor: AVAILABLE });
  assert.deepEqual(rows.map((r) => r.runtime), [...ENVIRONMENT_RUNTIME_IDS],
    "every advertised runtime appears, in order, and none is invented");
  assert.equal(rows.length, new Set(rows.map((r) => r.runtime)).size, "no duplicates");
  assert.ok(rows.every((r) => r.available === true));

  // The options object is forwarded — without that, the advertised list would probe the real PATH
  // and this test would depend on what happens to be installed.
  const none = advertisedEnvironmentRuntimes({ availabilityFor: UNAVAILABLE("nothing installed") });
  assert.ok(none.every((r) => r.available === false && r.unavailableReason === "nothing installed"));

  assert.ok(Object.isFrozen(ENVIRONMENT_RUNTIME_IDS), "the advertised set is frozen against mutation");
}

console.log("environment-runtime-capability.test.js: all assertions passed");
