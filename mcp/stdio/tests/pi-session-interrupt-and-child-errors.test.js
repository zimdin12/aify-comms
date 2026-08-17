// The pi session's interrupt, and the error it hands an operator when `omp` will not execute.
//
// Twenty-sixth cluster off the V8-coverage census: `pi-session.js`'s `get state`, `_interruptTurn` and
// `_onChildError`. (`interrupt` is the turn handle's closure around `_interruptTurn`, built inside `runTurn`, so
// it needs a running turn — its body is what this covers.) The constructor only assigns fields, so all of it runs
// on a directly-constructed session with a fake child process; nothing spawns `omp`.
//
// TWO PROPERTIES THAT ONLY MATTER WHEN SOMETHING IS ALREADY WRONG:
//
//   * An interrupt names the turn it is cancelling, and a stale one is IGNORED. Controls arrive over HTTP and a
//     turn can finish while one is in flight, so an interrupt that skipped the check would abort whatever turn
//     happens to be running now — the next dispatch, cancelled by the previous operator's Stop.
//   * An ENOENT on the child is the single most common pi failure (a broken shebang, a missing exec bit, a stale
//     symlink), and `spawn ENOENT` on its own tells the operator nothing they can act on. This path replaces it
//     with the command the bridge actually resolved, the cwd it used, and the env var that overrides it.
//
// AIFY_PI_COMMAND / PI_COMMAND are sealed, because the enriched message reads them: unsealed, the assertion
// would depend on whatever the operator has configured.
//
// THREE MUTATIONS SURVIVE, and each is a duplicate rather than a gap. Marking `this._activeTurn.interrupted`
// instead of `turn.interrupted` is the SAME object once the guard above has run. The interrupt's `try/catch`
// around the abort is doubled by `_send`'s own, and `_failTurnAndChild`'s `_state = "dead"` is doubled by
// `_teardownChild`'s. The harness carries the paired removals for the last two, and both pairs ARE caught, so
// the properties are covered rather than merely double-guarded.
//
// AN OBSERVATION, MEASURED WHILE WRITING THIS AND NOT PINNED AS A CONTRACT: `new PiSession({ agentId })` with no
// `agentInfo` THROWS. The constructor normalises `this.agentInfo = agentInfo || {}` and then, one line later,
// calls `idleTimeoutFor(agentInfo)` with the RAW argument, which reads `.runtimeConfig` off undefined. Both
// sibling sessions (codex, hermes) resolve their idle timeout LAZILY from `this.agentInfo`, so neither has the
// problem. It is latent rather than live — `acquirePiSession` forwards whatever its caller passed, and every
// current caller passes an object — so this file supplies `agentInfo: {}` the way real callers do rather than
// asserting a crash nobody intends.

import assert from "node:assert/strict";
import test from "node:test";

import { PiSession } from "../pi-session.js";

const SEALED = ["AIFY_PI_COMMAND", "PI_COMMAND"];

function withSealedPiCommand(value, run) {
  const saved = new Map(SEALED.map((key) => [key, process.env[key]]));
  process.env.AIFY_PI_COMMAND = value;
  delete process.env.PI_COMMAND;
  assert.equal(process.env.AIFY_PI_COMMAND, value, "the AIFY_PI_COMMAND seal did not take");
  assert.equal(process.env.PI_COMMAND, undefined, "PI_COMMAND was left set");
  try {
    return run();
  } finally {
    for (const [key, previous] of saved) {
      if (previous === undefined) delete process.env[key];
      else process.env[key] = previous;
    }
  }
}

// A session with a child whose stdin records what was written. No pid, so even the escalation path's
// terminateProcessTree is a no-op on this object — nothing of the operator's can be signalled from here.
function session({ sent = [] } = {}) {
  const s = new PiSession({ agentId: "pi-session-agent", agentInfo: {} });
  s._proc = { stdin: { writable: true, destroyed: false, write: (line) => { sent.push(JSON.parse(line)); } } };
  s._launcher = { command: "C:/tools/omp.cmd", args: [] };
  s._cwd = "C:/work/project";
  return s;
}

const fakeTurn = () => {
  const turn = { interrupted: false, rejected: null };
  turn.reject = (error) => { turn.rejected = error; };
  return turn;
};

// ── state ───────────────────────────────────────────────────────────────────

test("state is readable and starts idle", () => {
  const s = new PiSession({ agentId: "pi-session-agent", agentInfo: {} });
  assert.equal(s.state, "idle");
  s._state = "ready";
  assert.equal(s.state, "ready", "the getter does not read the session's own state");
});

// ── interrupt ───────────────────────────────────────────────────────────────

test("interrupting the ACTIVE turn asks pi to abort and tells the console", async () => {
  const sent = [];
  const s = session({ sent });
  const turn = fakeTurn();
  s._activeTurn = turn;

  await s._interruptTurn(turn);

  assert.equal(turn.interrupted, true, "the turn was not marked interrupted");
  assert.equal(sent.length, 1, "no abort was sent to pi");
  assert.equal(sent[0].type, "abort");
  assert.match(String(sent[0].id), /^aify-abort-/, "the abort carries no correlatable request id");

  // The operator pressed Stop; the console has to show that something happened even before pi reacts.
  const frames = [];
  s.attachTerminalSink((text) => { frames.push(text); });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.match(frames.join(""), /interrupt requested/, "the console was never told about the interrupt");
});

test("interrupting a turn that is no longer active does NOTHING", async () => {
  // The dangerous case. A Stop for a finished turn arriving while the next one runs must not abort the new turn.
  const sent = [];
  const s = session({ sent });
  const finishedTurn = fakeTurn();
  const runningTurn = fakeTurn();
  s._activeTurn = runningTurn;

  await s._interruptTurn(finishedTurn);

  assert.deepEqual(sent, [], "a stale interrupt reached pi and would have aborted the running turn");
  assert.equal(finishedTurn.interrupted, false);
  assert.equal(runningTurn.interrupted, false, "the running turn was marked interrupted by someone else's Stop");
});

test("an interrupt does not kill the child immediately", async () => {
  // There is a grace window: pi is asked to abort first, and only a turn still active after INTERRUPT_GRACE_MS
  // escalates to killing the process tree. Killing straight away would discard the session for a cancel pi was
  // about to honour.
  //
  // A REAL child stands in for `omp`, because `terminateProcessTree` returns immediately for an object with no
  // pid — with a pid-less fake this test passed against a version that killed the tree on the spot. The pid is
  // ours; nothing is chosen by heuristic.
  //
  // NOT ASSERTED HERE: the escalation itself. INTERRUPT_GRACE_MS is a module constant with no seam, so reaching
  // it would mean a five-second test; it needs a clock injection to cover honestly.
  const { spawn } = await import("node:child_process");
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"],
    { stdio: ["pipe", "ignore", "ignore"], windowsHide: true });
  const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };

  try {
    const sent = [];
    const s = session({ sent });
    s._proc = { pid: child.pid, stdin: { writable: true, destroyed: false, write: (line) => { sent.push(JSON.parse(line)); } } };
    const turn = fakeTurn();
    s._activeTurn = turn;

    await s._interruptTurn(turn);
    await new Promise((resolve) => setTimeout(resolve, 300));

    assert.equal(alive(child.pid), true, "the child was killed instead of being asked to abort");
    assert.equal(sent.length, 1, "the abort was not sent");
    assert.equal(s._activeTurn, turn, "the turn was discarded rather than given its grace window");
  } finally {
    child.kill("SIGKILL");
  }
});

test("an interrupt survives a child whose stdin write THROWS", async () => {
  // A turn interrupted just as the child dies is the normal race. The write has to be attempted and its failure
  // contained — a fixture whose stdin merely reports `writable: false` never reaches the throw, which is how the
  // first version of this test passed without exercising either guard.
  const s = session();
  s._proc = { stdin: { writable: true, destroyed: false, write: () => { throw new Error("EPIPE"); } } };
  const turn = fakeTurn();
  s._activeTurn = turn;

  await assert.doesNotReject(() => s._interruptTurn(turn));
  assert.equal(turn.interrupted, true, "the turn was left unmarked because the child was gone");
});

test("an interrupt survives a child that is already gone", async () => {
  const s = session();
  s._proc = null;
  const turn = fakeTurn();
  s._activeTurn = turn;

  await assert.doesNotReject(() => s._interruptTurn(turn));
  assert.equal(turn.interrupted, true);
});

// ── child errors ────────────────────────────────────────────────────────────

test("an ENOENT is replaced with something the operator can act on", () => {
  withSealedPiCommand("C:/tools/omp.cmd", () => {
    const s = session();
    const turn = fakeTurn();
    s._activeTurn = turn;

    const error = new Error("spawn C:/tools/omp.cmd ENOENT");
    error.code = "ENOENT";
    s._onChildError(error);

    const message = String(turn.rejected?.message || "");
    assert.match(message, /C:\/tools\/omp\.cmd/, "the message does not name the command the bridge resolved");
    assert.match(message, /C:\/work\/project/, "the message does not name the cwd it tried to use");
    assert.match(message, /AIFY_PI_COMMAND/, "the message does not name the override that fixes it");
    // Each cause named separately: an `or`-joined regex passed while two of the three had been deleted, because
    // one surviving clause satisfied the whole alternation.
    assert.match(message, /exec bit/i, "the message does not mention a missing exec bit");
    assert.match(message, /shebang/i, "the message does not mention a broken shebang interpreter");
    assert.match(message, /symlink/i, "the message does not mention a stale symlink");
    // The enriched TEXT is what reaches the operator, and it is the same object the session records.
    assert.equal(s._lastError?.message, message, "the session recorded a different error than the turn got");

    // FINDING, pinned not fixed. `_onChildError` builds its enriched Error and attaches `.code` and
    // `.originalError` to it — then passes only `enriched.message` to `_failTurnAndChild`, which constructs a
    // FRESH Error. Both fields are therefore dead writes, and nothing in the bridge reads either one off this
    // path (`_lastError` has a single reader, which uses its message). Harmless today; it reads as though a
    // caller could branch on ENOENT here, and none can. Carrying the object instead of its message changes a
    // failure-path signature, so it is a reviewer's edit rather than a test slice's.
    assert.equal(s._lastError?.code, undefined,
      "the error code now survives — if that is the FIX, this assertion is what to update");
    assert.equal(s._lastError?.originalError, undefined, "originalError now survives — see the note above");
    assert.match(message, /spawn "C:\/tools\/omp\.cmd" ENOENT/,
      "the original failure is not even quoted in the enriched text, so nothing preserves it");
  });
});

test("the enriched ENOENT is still recorded when no turn is waiting for it", () => {
  // A spawn failure between turns has nobody to reject. It must still land on the session, because the next
  // ensureStarted reports `_lastError` rather than repeating the spawn.
  withSealedPiCommand("omp", () => {
    const s = session();
    s._activeTurn = null;
    const error = new Error("spawn omp ENOENT");
    error.code = "ENOENT";

    assert.doesNotThrow(() => s._onChildError(error));
    assert.match(String(s._lastError?.message || ""), /AIFY_PI_COMMAND/);
  });
});

test("a NON-ENOENT child error is reported as itself", () => {
  // Only ENOENT gets the launcher essay. Wrapping every failure in it would send the operator hunting a PATH
  // problem for an out-of-memory kill.
  withSealedPiCommand("omp", () => {
    const s = session();
    const turn = fakeTurn();
    s._activeTurn = turn;

    s._onChildError(new Error("EACCES: permission denied"));
    const message = String(turn.rejected?.message || "");
    assert.match(message, /EACCES: permission denied/);
    assert.doesNotMatch(message, /shebang/, "an unrelated failure was dressed up as a launcher problem");
  });
});

test("a child error with no Error object still fails the turn readably", () => {
  withSealedPiCommand("omp", () => {
    const s = session();
    const turn = fakeTurn();
    s._activeTurn = turn;
    s._onChildError("just a string");
    assert.match(String(turn.rejected?.message || ""), /just a string/);
  });
});

test("a child error takes the session out of service", () => {
  // `dead` is what stops the pool handing this session to the next dispatch. Leaving it `ready` would route work
  // to a child that cannot run.
  withSealedPiCommand("omp", () => {
    const s = session();
    s._state = "ready";
    const turn = fakeTurn();
    s._activeTurn = turn;

    s._onChildError(new Error("boom"));
    assert.equal(s.state, "dead", "the session stayed startable after its child failed");
    assert.equal(s._activeTurn, null, "the failed turn was left attached to the session");
  });
});
