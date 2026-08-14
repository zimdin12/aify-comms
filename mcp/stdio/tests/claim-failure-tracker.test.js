// Real tests for the control-claim failure tracker, extracted from server.js in v0.5.4.
//
// This is the code that decides what an operator SEES when a control loop cannot reach the service. Get it
// wrong in one direction and a transient blip screams; wrong in the other and a bridge that has been
// unreachable for an hour says nothing at all. server.js is imported by no test, so none of it was covered.
//
// THE PER-LABEL KEYING IS THE MAIN CLAIM. Two loops report through here — "environment controls" and
// "terminal controls". A single shared counter would let one loop's outage inflate the other's count, warn
// about a loop that is perfectly healthy, and be cleared by the wrong recovery.
//
// SEALING. `AIFY_DEBUG` is read from the process environment at CALL time, and the operator running this
// suite may well have it set — so it is saved, forced, and restored around every test that depends on it,
// and the default state is asserted rather than assumed. `console.error`/`console.debug` are captured the
// same way.

import assert from "node:assert/strict";
import test from "node:test";

import {
  CONTROL_CLAIM_FAILURES, noteControlClaimFailure, noteControlClaimSuccess,
  noteSpawnClaimFailure, noteSpawnClaimSuccess,
} from "../claim-failure-tracker.mjs";

const ENV = "AIFY_DEBUG";

function capture({ debug = "" } = {}, run) {
  const hadEnv = Object.prototype.hasOwnProperty.call(process.env, ENV);
  const prevEnv = process.env[ENV];
  const errors = [];
  const debugs = [];
  const realError = console.error;
  const realDebug = console.debug;
  if (debug === "") delete process.env[ENV];
  else process.env[ENV] = debug;
  console.error = (...a) => errors.push(a.join(" "));
  console.debug = (...a) => debugs.push(a.join(" "));
  try {
    run();
  } finally {
    console.error = realError;
    console.debug = realDebug;
    if (hadEnv) process.env[ENV] = prevEnv;
    else delete process.env[ENV];
  }
  return { errors, debugs };
}

const err = (message, extra = {}) => Object.assign(new Error(message), extra);

test.beforeEach(() => CONTROL_CLAIM_FAILURES.clear());

test("the first failure warns nobody — it is a blip until it is not", () => {
  // warnAfter is 3. A single failed poll happens whenever the service restarts.
  const { errors } = capture({}, () => noteControlClaimFailure("environment controls", err("ECONNREFUSED")));
  assert.deepEqual(errors, []);
  assert.equal(CONTROL_CLAIM_FAILURES.get("environment controls").count, 1, "but it IS counted");
});

test("the THIRD consecutive failure warns once, naming the loop and the count", () => {
  const { errors } = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteControlClaimFailure("environment controls", err("ECONNREFUSED"));
  });
  assert.equal(errors.length, 1, "exactly one warning, on the third");
  assert.match(errors[0], /environment controls unavailable \(3 consecutive\)/);
  assert.match(errors[0], /ECONNREFUSED/, "the reason must be in the line, not just the count");
});

test("a FOURTH and fifth failure stay quiet — one line per outage, not one per poll", () => {
  // These loops poll continuously. Without this a five-minute outage produces hundreds of identical lines
  // and buries whatever else went wrong.
  const { errors } = capture({}, () => {
    for (let i = 0; i < 6; i += 1) noteControlClaimFailure("environment controls", err("ECONNREFUSED"));
  });
  assert.equal(errors.length, 1, "still one line after six failures");
});

// NOT COVERED: the 30-SECOND RE-WARN, and the `lastLogAt` bookkeeping that drives it.
//
// The policy re-warns once an outage has been quiet for `repeatMs`, which is what stops a genuinely long
// outage going silent forever. Reaching it needs control of the clock, and the tracker calls
// `claimFailureDecision(state)` without passing `now` — so the policy's own `Date.now()` default applies
// and a test cannot advance it. (`mock.timers` does not help: it does not move `Date.now` for a module
// that already captured nothing, and it is the thing that broke the HTTP client elsewhere in this suite.)
//
// I found this by mutating `state.lastLogAt = decision.nextLastLogAt` to `= 0` and watching all 13 tests
// stay green. The test above still passes because `firstSustained` fires only at EXACTLY the threshold, so
// failures four through six are silent whether or not `lastLogAt` was stored. The assertion is true; it
// simply does not prove what its old name claimed. Making this testable means injecting a clock — a
// signature change, not a relocation, so it is out of scope for this slice.

test("EACH LOOP IS COUNTED SEPARATELY — one loop's outage cannot warn about the other", () => {
  const { errors } = capture({}, () => {
    for (let i = 0; i < 2; i += 1) noteControlClaimFailure("environment controls", err("boom"));
    noteControlClaimFailure("terminal controls", err("boom"));
  });
  assert.deepEqual(errors, [], "two plus one is not three when they are different loops");
  assert.equal(CONTROL_CLAIM_FAILURES.get("environment controls").count, 2);
  assert.equal(CONTROL_CLAIM_FAILURES.get("terminal controls").count, 1);
});

test("recovery CLEARS the entry, so the next outage counts from one again", () => {
  capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteControlClaimFailure("environment controls", err("boom"));
    noteControlClaimSuccess("environment controls");
  });
  assert.equal(CONTROL_CLAIM_FAILURES.has("environment controls"), false,
    "a lingering entry would make the next single failure look like the fourth");
});

test("recovery is announced only if the outage was ever announced", () => {
  // Symmetry with the warning: reporting "recovered after 1 failure(s)" for a blip nobody was told about
  // is noise that reads like something happened.
  const quiet = capture({}, () => {
    noteControlClaimFailure("terminal controls", err("boom"));
    noteControlClaimSuccess("terminal controls");
  });
  assert.deepEqual(quiet.errors, []);

  const loud = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteControlClaimFailure("terminal controls", err("boom"));
    noteControlClaimSuccess("terminal controls");
  });
  assert.equal(loud.errors.length, 2, "the warning and then the recovery");
  assert.match(loud.errors[1], /terminal controls recovered after 3 failure\(s\)/);
});

test("recovery for a loop that never failed is a no-op", () => {
  // Called on EVERY successful poll, which is the overwhelmingly common case.
  const { errors } = capture({}, () => noteControlClaimSuccess("environment controls"));
  assert.deepEqual(errors, []);
  assert.equal(CONTROL_CLAIM_FAILURES.size, 0);
});

test("the debug line needs BOTH the first failure and AIFY_DEBUG=1", () => {
  // Two independent gates. Neither alone should produce it.
  const off = capture({}, () => noteControlClaimFailure("environment controls", err("boom")));
  assert.deepEqual(off.debugs, [], "first failure, but debug is not enabled");

  CONTROL_CLAIM_FAILURES.clear();
  const on = capture({ debug: "1" }, () => noteControlClaimFailure("environment controls", err("boom")));
  assert.equal(on.debugs.length, 1);
  assert.match(on.debugs[0], /transient failure/);

  // Second failure with debug on: `decision.debug` is `failures === 1`, so it must NOT fire again.
  const second = capture({ debug: "1" }, () => noteControlClaimFailure("environment controls", err("boom")));
  assert.deepEqual(second.debugs, [], "only the FIRST failure is debug-logged");
});

test("AIFY_DEBUG=0 or 'true' does not enable debug — the check is the literal '1'", () => {
  for (const value of ["0", "true", "yes", " "]) {
    CONTROL_CLAIM_FAILURES.clear();
    const { debugs } = capture({ debug: value }, () => noteControlClaimFailure("x", err("boom")));
    assert.deepEqual(debugs, [], `AIFY_DEBUG=${JSON.stringify(value)} must not enable debug output`);
  }
});

test("the error's own serverUrl WINS, so the line names the host actually contacted", () => {
  // A bridge can be pointed at a different service than the ambient one; blaming the ambient URL sends
  // the operator to check a host that was never involved.
  const { errors } = capture({}, () => {
    for (let i = 0; i < 3; i += 1) {
      noteControlClaimFailure("environment controls", err("boom", { serverUrl: "http://elsewhere:9999" }));
    }
  });
  assert.match(errors[0], /against http:\/\/elsewhere:9999/);
});

test("the reason DEDUPES message, cause.code and cause.message", () => {
  // Node's fetch commonly gives all three the same text; printing "ECONNREFUSED: ECONNREFUSED:
  // ECONNREFUSED" reads like three separate problems.
  const cause = Object.assign(new Error("ECONNREFUSED"), { code: "ECONNREFUSED" });
  const { errors } = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteControlClaimFailure("x", err("ECONNREFUSED", { cause }));
  });
  const occurrences = errors[0].split("ECONNREFUSED").length - 1;
  assert.equal(occurrences, 1, `the reason must appear once, got ${occurrences}`);
});

test("distinct causes are all kept — dedupe must not collapse real detail", () => {
  const cause = Object.assign(new Error("socket hang up"), { code: "UND_ERR_SOCKET" });
  const { errors } = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteControlClaimFailure("x", err("request failed", { cause }));
  });
  for (const part of ["request failed", "UND_ERR_SOCKET", "socket hang up"]) {
    assert.match(errors[0], new RegExp(part.replace(/ /g, "\\s")));
  }
});

test("an error with no message at all still produces a usable line", () => {
  const { errors } = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteControlClaimFailure("x", {});
  });
  assert.match(errors[0], /x unavailable \(3 consecutive\)/, "the count and loop survive an empty error");
});

// --- the SPAWN claim ------------------------------------------------------
//
// Same subject, deliberately different shape: one spawn loop, so a plain counter rather than a keyed Map.
//
// Its counter is module-scope and NOT exported (unlike the control Map, which is exported so a test can
// prove an entry is cleared). So these tests reset it the only way a consumer can — by recording a
// success — and the first test proves that reset actually works, which is what makes the rest trustworthy.

function resetSpawn() {
  const realError = console.error;
  console.error = () => {};
  try {
    noteSpawnClaimSuccess();
  } finally {
    console.error = realError;
  }
}

test("a recorded success resets the counter, so the next failure counts from one", () => {
  // The fixture the tests below depend on, proven rather than assumed: after three failures and a
  // success, three MORE failures must warn again — which can only happen from a zeroed counter.
  const first = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure(new Error("boom"));
  });
  assert.equal(first.errors.length, 1);
  resetSpawn();

  const second = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure(new Error("boom"));
  });
  assert.equal(second.errors.length, 1, "the third failure after a reset warns again");
  resetSpawn();
});

test("the spawn claim is silent for two failures and warns on the third", () => {
  const { errors } = capture({}, () => {
    noteSpawnClaimFailure(new Error("ECONNREFUSED"));
    noteSpawnClaimFailure(new Error("ECONNREFUSED"));
  });
  assert.deepEqual(errors, [], "two failures is a blip");

  const third = capture({}, () => noteSpawnClaimFailure(new Error("ECONNREFUSED")));
  assert.equal(third.errors.length, 1);
  assert.match(third.errors[0], /spawn claim failed \(3 consecutive\)/);
  assert.match(third.errors[0], /keep retrying/, "it must say the bridge is not giving up");
  resetSpawn();
});

test("the spawn counter is INDEPENDENT of the control loops' counters", () => {
  // They are different subjects sharing a module. If they shared a counter, two control failures plus one
  // spawn failure would warn about a spawn claim that had failed once.
  // TWO control failures then ONE spawn failure. One-and-one was too weak: a mutant that added the
  // control count to the spawn count reached only 2 and stayed under the threshold, so the test passed
  // while the counters were fused. At two-and-one the fused count is exactly 3 and warns.
  CONTROL_CLAIM_FAILURES.clear();
  const { errors } = capture({}, () => {
    noteControlClaimFailure("environment controls", new Error("boom"));
    noteControlClaimFailure("environment controls", new Error("boom"));
    noteSpawnClaimFailure(new Error("boom"));
  });
  assert.deepEqual(errors, []);
  resetSpawn();
  CONTROL_CLAIM_FAILURES.clear();
});

test("recovery is announced only after a sustained outage, and not for a blip", () => {
  const quiet = capture({}, () => {
    noteSpawnClaimFailure(new Error("boom"));
    noteSpawnClaimSuccess();
  });
  assert.deepEqual(quiet.errors, []);

  const loud = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure(new Error("boom"));
    noteSpawnClaimSuccess();
  });
  assert.equal(loud.errors.length, 2);
  assert.match(loud.errors[1], /spawn claim recovered after 3 failure\(s\)/);
});

test("a success when nothing has failed logs nothing — it runs on every poll", () => {
  const { errors } = capture({}, () => { noteSpawnClaimSuccess(); noteSpawnClaimSuccess(); });
  assert.deepEqual(errors, []);
});

test("a non-Error rejection still yields a readable reason", () => {
  // `error?.message || String(error || "unknown error")`. Rejections here are not always Errors, and
  // "[object Object]" in this line would waste an operator's time.
  const { errors } = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure("plain string failure");
  });
  assert.match(errors[0], /plain string failure/);
  resetSpawn();

  const nothing = capture({}, () => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure(undefined);
  });
  assert.match(nothing.errors[0], /unknown error/, "even a bare rejection must say something");
  resetSpawn();
});

test("the debug line obeys the same two gates as the control one", () => {
  const off = capture({}, () => noteSpawnClaimFailure(new Error("boom")));
  assert.deepEqual(off.debugs, []);
  resetSpawn();

  const on = capture({ debug: "1" }, () => noteSpawnClaimFailure(new Error("boom")));
  assert.equal(on.debugs.length, 1);
  assert.match(on.debugs[0], /spawn claim transient failure/);
  resetSpawn();
});
