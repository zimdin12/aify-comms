// Real tests for the terminal activity pulses, extracted from server.js in v0.5.4.
//
// Both pulses are rate-limited AND self-clearing, and both properties matter in a way a source read cannot
// show. A pulse that re-emitted on every frame would flood the service during a noisy turn; one that
// latched instead of expiring would leave an agent permanently "busy" after a single burst of output, and
// dispatch would stop delivering to it.
//
// server.js is imported by no test, so none of this had coverage.
//
// A REAL HTTP SERVER on 127.0.0.2 — `httpCall` and `reportTurnBusy` are imported bindings that cannot be
// monkey-patched. One server for the file: a per-test server plus a cache-busted import does NOT bust
// `aify-service-endpoint.mjs`, which resolves its target once at load. Routes are matched on the path with
// `/api/v1` stripped, because httpCall adds that prefix.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

// The REAL timers, captured before any test enables `mock.timers`. The watchdog below must not be the
// thing being mocked: my first version used the global setTimeout, so once timers were faked the
// watchdog could never fire and a hang stalled the whole file instead of failing it.
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;

const REQUESTS = [];
const WAITERS = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ url: req.url.replace(/^\/api\/v1/, ""), body: body ? JSON.parse(body) : null });
    res.writeHead(200, { "content-type": "application/json" });
    res.end("{}");
    for (let i = WAITERS.length - 1; i >= 0; i -= 1) {
      if (REQUESTS.length >= WAITERS[i].n) WAITERS.splice(i, 1)[0].resolve();
    }
  });
});

const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const m = await import("../terminal-manager.mjs");

test.after(() => SERVER.close());

// The pulses fire their HTTP calls without awaiting them (`.catch(() => {})`), so a test has to wait for
// the request to LAND rather than for a couple of event-loop turns. Two setImmediates was my first
// version: it happened to work for the fast cases and lost the race once `mock.timers` was enabled, which
// reads as the pulse not firing at all. Waiting on the server's own callback cannot race.
// COUNT-BASED, and it checks the count FIRST. My previous version only resolved on a FUTURE request, so
// a test awaiting two requests hung forever when both had already landed before the second wait was
// registered — the suite blocked rather than failing. The timeout turns any remaining hang into a
// readable failure instead of a stalled run.
function arrived(n = REQUESTS.length + 1) {
  if (REQUESTS.length >= n) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const t = realSetTimeout(() => reject(new Error(`timed out waiting for request #${n}; saw ${REQUESTS.length}`)), 4000);
    WAITERS.push({ n, resolve: () => { realClearTimeout(t); resolve(); } });
  });
}

// For the negative assertions — "no SECOND request" — there is nothing to wait for, so give the event loop
// a few turns and then assert nothing showed up.
const quiet = async () => { for (let i = 0; i < 5; i += 1) await new Promise((r) => setImmediate(r)); };

function reset() {
  REQUESTS.length = 0;
  m.CONSOLE_WORKING_TIMERS.clear();
  for (const entry of m.TERMINAL_TURN_BUSY_TIMERS.values()) if (entry.timer) clearTimeout(entry.timer);
  m.TERMINAL_TURN_BUSY_TIMERS.clear();
}

test("neither pulse fires without an agent id", async () => {
  reset();
  for (const blank of ["", "   ", null, undefined]) {
    m.pulseConsoleWorking("t1", blank);
    m.pulseTerminalTurnBusy("t1", blank);
  }
  await quiet();
  assert.deepEqual(REQUESTS, [], "a terminal with no bound agent has nobody to report about");
});

test("console-working is rate-limited per terminal", async () => {
  reset();
  m.pulseConsoleWorking("t1", "coder");
  m.pulseConsoleWorking("t1", "coder");
  m.pulseConsoleWorking("t1", "coder");
  await arrived();
  await quiet();
  const posts = REQUESTS.filter((r) => r.url.includes("/console-working"));
  assert.equal(posts.length, 1, "a noisy turn must not become one POST per output frame");
  assert.equal(posts[0].url, "/agents/coder/console-working");
});

test("…but the limit is PER TERMINAL, not global", async () => {
  // Two agents producing output at once must both be reported; a global throttle would silence one.
  reset();
  m.pulseConsoleWorking("t1", "coder");
  m.pulseConsoleWorking("t2", "tester");
  await arrived(2);
  assert.equal(REQUESTS.filter((r) => r.url.includes("/console-working")).length, 2);
});

test("the subagents flag travels with the pulse", async () => {
  reset();
  m.pulseConsoleWorking("t1", "coder", true);
  await arrived();
  assert.equal(REQUESTS[0].body.subagents, true);
});

// REAL TIME, not faked timers. Mocking setTimeout/Date breaks the HTTP client these pulses call through --
// with timers faked the request never lands, which surfaced as "timed out waiting for request #1; saw 0".
// The quiet window is 8s, so these two tests cost ~16s between them, and they exercise the actual
// constants rather than a fake clock's idea of them.
const sleep = (ms) => new Promise((r) => realSetTimeout(r, ms));

test("turn-busy reports busy:true once, then clears itself after the quiet window", async () => {
  // The self-clear is the important half. Without it one burst of output marks the agent busy forever and
  // dispatch stops delivering to it.
  reset();
  m.pulseTerminalTurnBusy("t1", "coder");
  await arrived(1);
  let beats = REQUESTS.filter((r) => r.url.includes("/heartbeat"));
  assert.equal(beats.length, 1);
  assert.equal(beats[0].body.turnBusy, true);

  // A second pulse inside the 5s re-emit window must not repeat the report.
  m.pulseTerminalTurnBusy("t1", "coder");
  await quiet();
  assert.equal(REQUESTS.filter((r) => r.url.includes("/heartbeat")).length, 1,
    "re-emitting on every frame would flood the heartbeat endpoint");

  await sleep(m.TERMINAL_TURN_BUSY_QUIET_MS + 400);
  beats = REQUESTS.filter((r) => r.url.includes("/heartbeat"));
  assert.equal(beats.length, 2, "the quiet window must release the agent");
  assert.equal(beats[1].body.turnBusy, false);
  assert.equal(m.TERMINAL_TURN_BUSY_TIMERS.has("t1"), false, "and forget the terminal");
});

test("continued output KEEPS the agent busy rather than releasing it mid-turn", async () => {
  reset();
  m.pulseTerminalTurnBusy("t1", "coder");
  await arrived(1);

  // Pulse again before the window expires: the release must be pushed back, not fire.
  await sleep(m.TERMINAL_TURN_BUSY_QUIET_MS - 1500);
  m.pulseTerminalTurnBusy("t1", "coder");
  await sleep(2000);

  // The second pulse lands OUTSIDE the 5s re-emit window, so it legitimately reports busy:true again —
  // my first version asserted a single heartbeat and was simply wrong about the constants. What must NOT
  // have happened is a release: the quiet timer was reset, so the agent is still held busy.
  const beats = REQUESTS.filter((r) => r.url.includes("/heartbeat"));
  assert.ok(beats.length >= 1);
  assert.deepEqual(beats.filter((b) => b.body.turnBusy === false), [],
    "a still-producing terminal must not be reported idle");
  assert.equal(m.TERMINAL_TURN_BUSY_TIMERS.has("t1"), true, "the release timer must have been pushed back");
});

test("the two pulse families keep separate books", async () => {
  // They answer different questions — "is output happening" vs "is the agent busy" — and sharing a timer
  // map would make one silence the other.
  reset();
  assert.notEqual(m.CONSOLE_WORKING_TIMERS, m.TERMINAL_TURN_BUSY_TIMERS);
  assert.ok(m.CONSOLE_WORKING_REMIT_MS < m.TERMINAL_TURN_BUSY_QUIET_MS,
    "the console re-emit window must be shorter than the busy release, or busy would clear mid-turn");
});
