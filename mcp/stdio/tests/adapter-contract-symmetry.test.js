#!/usr/bin/env node
// Task 2.2 (2026-05-30 runtime-symmetry plan): the REGISTRY-DRIVEN symmetry guard.
//
// Task 2.1's adapter-resume-contract.test.js asserts sessionIdSource +
// resumeCommand against a HARDCODED list of the 5 adapter classes. This guard
// is different on purpose: it enumerates the registry (adapters/index.js) so a
// newly-registered runtime is AUTOMATICALLY covered, and it asserts the FULL
// symmetric contract — the four capability flags, discoverSessionId,
// sessionIdSource, and resumeCommand. A future harness that registers an adapter
// missing any contract member fails here loudly rather than silently shipping an
// asymmetric runtime.
//
// Contract surface (per the design spec "The symmetric contract (the triad)"):
//   - supportsManaged / supportsResident / supportsInterrupt / supportsSteering
//     → booleans (capability flags; branches key off these, not `runtime == x`)
//   - discoverSessionId → async function (runtime-native id discovery)
//   - sessionIdSource ∈ {"pinned","captured","resume"}
//   - resumeCommand(sessionId) → non-empty string embedding the id

import assert from "node:assert/strict";
import { test } from "node:test";

import { supportedRuntimes, adapterFor } from "../adapters/index.js";

const VALID_SOURCES = new Set(["pinned", "captured", "resume"]);
const CAPABILITY_FLAGS = [
  "supportsManaged",
  "supportsResident",
  "supportsInterrupt",
  "supportsSteering",
];

// Enumerate the registry — NOT a hardcoded list. supportedRuntimes() returns the
// canonical keys; adapterFor() instantiates each. A new entry in the registry's
// Map is picked up here with zero test changes.
const RUNTIMES = supportedRuntimes();

// ─────────────────── meta: the enumeration is non-empty ───────────────────
// Guards against a refactor that makes the registry silently empty (which would
// otherwise let the per-adapter loop "pass" by iterating zero times).

test("registry enumerates more than one adapter (no silent empty pass)", () => {
  assert.ok(
    Array.isArray(RUNTIMES),
    `supportedRuntimes() must return an array, got ${typeof RUNTIMES}`,
  );
  assert.ok(
    RUNTIMES.length > 1,
    `expected the registry to enumerate >1 adapter, got ${RUNTIMES.length}: ${JSON.stringify(RUNTIMES)}`,
  );
});

// ─────────────────── full symmetric contract, per registered adapter ───────

for (const runtime of RUNTIMES) {
  test(`adapter "${runtime}" satisfies the full symmetric contract`, () => {
    const a = adapterFor(runtime);

    // Capability flags must be present AND strictly boolean. A flag that throws
    // (base stub) or is non-boolean is an incomplete adapter.
    for (const flag of CAPABILITY_FLAGS) {
      let value;
      assert.doesNotThrow(
        () => { value = a[flag]; },
        `${runtime}.${flag} must be implemented (base stub throws)`,
      );
      assert.equal(
        typeof value,
        "boolean",
        `${runtime}.${flag} must be a boolean, got ${typeof value} (${value})`,
      );
    }

    // discoverSessionId must be a function (runtime-native id discovery hook).
    assert.equal(
      typeof a.discoverSessionId,
      "function",
      `${runtime}.discoverSessionId must be a function`,
    );

    // sessionIdSource must be one of the contract enum values.
    let source;
    assert.doesNotThrow(
      () => { source = a.sessionIdSource; },
      `${runtime}.sessionIdSource must be implemented (base stub throws)`,
    );
    assert.ok(
      VALID_SOURCES.has(source),
      `${runtime}.sessionIdSource must be one of {pinned,captured,resume}, got ${JSON.stringify(source)}`,
    );

    // resumeCommand must be a function returning a non-empty string that embeds
    // the supplied id (so the dashboard/FSM rejection surfaces a usable command).
    assert.equal(
      typeof a.resumeCommand,
      "function",
      `${runtime}.resumeCommand must be a function`,
    );
    const sampleId = "sample-session-id";
    let cmd;
    assert.doesNotThrow(
      () => { cmd = a.resumeCommand(sampleId); },
      `${runtime}.resumeCommand must be implemented (base stub throws)`,
    );
    assert.equal(
      typeof cmd,
      "string",
      `${runtime}.resumeCommand must return a string, got ${typeof cmd}`,
    );
    assert.ok(
      cmd.length > 0,
      `${runtime}.resumeCommand must return a non-empty string`,
    );
    assert.ok(
      cmd.includes(sampleId),
      `${runtime}.resumeCommand must embed the session id, got ${JSON.stringify(cmd)}`,
    );
  });
}
