// The pi timing helpers, CALLED — not just compared against their three siblings.
//
// `deferred-agreement.test.js` proves these four agree (or deliberately differ) with the codex and hermes
// copies by reading source. That is a comparison, not an exercise: it would pass just as happily if
// `idleTimeoutFor` returned the wrong number in every one of the four. This file calls them.
//
// WHY THE NUMBERS MATTER. `idleTimeoutFor` decides when a live pi child is reaped for inactivity. Too
// small and a working agent is killed mid-task; too large and dead children accumulate. The precedence —
// config over env over default — is what lets an operator override per agent without touching the
// process, so getting the ORDER wrong is a silent misconfiguration rather than an error.

import assert from "node:assert/strict";
import test from "node:test";

// Sealed before the import: these helpers read process.env at CALL time, and an operator value in the
// ambient environment would otherwise decide the assertions.
const SEALED = ["AIFY_PI_IDLE_TIMEOUT_MS", "AIFY_PI_STARTUP_TIMEOUT_MS"];
const saved = {};
for (const name of SEALED) {
  saved[name] = process.env[name];
  delete process.env[name];
}

const { createDeferred, idleTimeoutFor, startupTimeoutFor, timeoutFor } =
  await import("../pi-session-timeouts.mjs");

function assertSealed() {
  for (const name of SEALED) {
    assert.equal(process.env[name], undefined, `${name} must stay unset for this test to mean anything`);
  }
}

test.afterEach(() => {
  for (const name of SEALED) delete process.env[name];
});

test("idleTimeoutFor prefers runtimeConfig, then env, then the 24h default", () => {
  assertSealed();
  assert.equal(idleTimeoutFor({}), 24 * 60 * 60 * 1000, "no config and no env means the default");

  process.env.AIFY_PI_IDLE_TIMEOUT_MS = "5000";
  assert.equal(idleTimeoutFor({}), 5000, "env is used when there is no config");

  assert.equal(
    idleTimeoutFor({ runtimeConfig: { piIdleTimeoutMs: 9000 } }),
    9000,
    "config WINS over env — an operator override per agent must not be overridden by the process",
  );
});

test("idleTimeoutFor rejects every non-positive and non-finite value", () => {
  // Each of these would otherwise reap a live child instantly or never.
  for (const bad of [0, -1, "", "abc", NaN, Infinity, -Infinity, null]) {
    assert.equal(
      idleTimeoutFor({ runtimeConfig: { piIdleTimeoutMs: bad } }),
      24 * 60 * 60 * 1000,
      `config ${JSON.stringify(bad)} must fall through to the default`,
    );
  }
  for (const bad of ["0", "-5", "not-a-number", ""]) {
    process.env.AIFY_PI_IDLE_TIMEOUT_MS = bad;
    assert.equal(idleTimeoutFor({}), 24 * 60 * 60 * 1000, `env ${JSON.stringify(bad)} must be ignored`);
  }
});

test("startupTimeoutFor has its own key and default, and does not read the idle one", () => {
  assertSealed();
  assert.equal(startupTimeoutFor({}), 45000);
  process.env.AIFY_PI_IDLE_TIMEOUT_MS = "1234";
  assert.equal(startupTimeoutFor({}), 45000, "the idle env var must not move the startup timeout");
  process.env.AIFY_PI_STARTUP_TIMEOUT_MS = "7000";
  assert.equal(startupTimeoutFor({}), 7000);
  assert.equal(startupTimeoutFor({ runtimeConfig: { startupTimeoutMs: 8000 } }), 8000, "config wins");
});

test("timeoutFor falls back to 12h and reads no env at all", () => {
  assertSealed();
  assert.equal(timeoutFor({}), 12 * 60 * 60 * 1000);
  assert.equal(timeoutFor({ runtimeConfig: { timeoutMs: 60000 } }), 60000);
  process.env.AIFY_PI_IDLE_TIMEOUT_MS = "1";
  assert.equal(timeoutFor({}), 12 * 60 * 60 * 1000, "timeoutFor is config-only by design");
});

test("createDeferred resolves through the promise it hands back", async () => {
  const d = createDeferred();
  d.resolve("value");
  assert.equal(await d.promise, "value");
});

test("createDeferred's rejection reaches a REAL awaiter — the guard must not swallow", async () => {
  const d = createDeferred();
  d.reject(new Error("boom"));
  await assert.rejects(() => d.promise, /boom/);
});

test("a rejected Deferred with NO awaiter does not kill the process", async () => {
  // The guard this file's sibling agreement test exists for. pi was the only one of the four session
  // modules missing `promise.catch(() => {})`, so a rejection nobody awaited became an unhandled
  // rejection — a warning by default and a PROCESS KILL under --unhandled-rejections=strict.
  const seen = [];
  const onUnhandled = (reason) => seen.push(reason);
  process.on("unhandledRejection", onUnhandled);
  try {
    createDeferred().reject(new Error("nobody is listening"));
    // Two turns: the rejection is reported after the microtask queue drains.
    await new Promise((r) => setTimeout(r, 20));
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
  assert.deepEqual(seen, [], "an unawaited rejection must be absorbed by the no-op catch");
});

test("each Deferred is independent", () => {
  // Anti-vacuity for the settle cases: a `createDeferred` returning one shared promise would satisfy
  // every assertion above.
  const a = createDeferred();
  const b = createDeferred();
  assert.notEqual(a.promise, b.promise);
  assert.notEqual(a.resolve, b.resolve);
});

console.log("pi-session-timeouts.test.js: all assertions passed");
