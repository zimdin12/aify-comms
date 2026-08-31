// `resolve-session` — which hermes session an agent resumes, decided once at launch.
//
// Fifth cluster off the V8-coverage census. `runResolveSessionCli` and its
// `defaultWriteActiveSessionFile` default had a zero call count, and the decision they make is behind
// three named incidents recorded in the source:
//
//   * THE FRESH-SESSION-EVERY-RESTART BUG. `active_list` holds only CURRENTLY-LIVE sessions and is empty
//     after any gateway restart, so resolving a marker against it found "no live session" every launch,
//     cleared the marker, and minted a new session — the agent abandoned its history each time. The DB
//     (`session.list`) is consulted so a marker survives a restart, and is PREFERRED over the live list.
//   * THE 4007 "SESSION NOT FOUND" LOOP. `--resume` needs the DURABLE `session_key`, not the ephemeral
//     runtime id, so the matched ROW is resolved and its resume key persisted — even when the marker
//     held a stale ephemeral id that still matched a live row.
//   * THE STRANDED CONSOLE. An operator's explicit `--resume <id>` is authoritative ONLY when there is no
//     gateway to validate it against. With a gateway it becomes a candidate and is DB-validated, so a
//     dead id falls through to a clean fresh start instead of launching `hermes --resume <dead-id>`.
//
// AND THE CLEAR IS CONDITIONAL ON PROOF. The stale marker is cleared only when `session.list` SUCCEEDED
// and did not contain it. If the DB was unavailable the marker cannot be shown dead, and clearing it is
// the lost-history bug again — so a query failure keeps it.
//
// Every seam is already injectable, so this needs no network and no gateway: the module takes its client
// opener, its marker reader/writer/clearer, its active-file writer, its temp dir and its two streams.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { runResolveSessionCli } from "../hermes-active-session.mjs";

const AGENT = "sc-hermes";
const WS = "ws://127.0.0.2:9/api/ws";

// A row as the gateway reports one: an EPHEMERAL `id` and a DURABLE `session_key`.
function row(id, sessionKey, lastActive = "2026-08-17T09:00:00Z") {
  return { id, session_key: sessionKey, last_active: lastActive };
}

function listOf(rows) {
  return { result: { sessions: rows } };
}

// Records everything the CLI did, and answers the two frames it sends in order.
// SEALED AT MODULE SCOPE, because `gatewayUrl: ""` cannot express "no gateway" on its own. The module reads
// `deps.gatewayUrl || process.env.AIFY_HERMES_GATEWAY_URL || process.env.HERMES_TUI_GATEWAY_URL`, so an empty
// dep falls THROUGH to the ambient value — and a live hermes wrapper exports one. Two tests here assert the
// no-gateway branch; both passed on this machine and failed in a reviewer's live environment, which is the
// third instance of this shape in as many review rounds. Every test in this file supplies its own gatewayUrl,
// so deleting the ambient pair costs nothing and removes the fall-through.
const GATEWAY_CARRIERS = ["AIFY_HERMES_GATEWAY_URL", "HERMES_TUI_GATEWAY_URL"];
for (const name of GATEWAY_CARRIERS) delete process.env[name];
assert.deepEqual(GATEWAY_CARRIERS.filter((n) => process.env[n] !== undefined), [],
  "the gateway env seal did not take — the no-gateway tests below would read a live gateway");

function harness({ active = [], db = null, dbThrows = false, openThrows = false, ...rest } = {}) {
  const calls = { markerWrites: [], markerClears: [], activeFiles: [], out: [], err: [], closed: 0 };
  const deps = {
    out: (s) => calls.out.push(s),
    err: (s) => calls.err.push(s),
    gatewayUrl: rest.gatewayUrl === undefined ? WS : rest.gatewayUrl,
    readMarker: () => rest.marker || "",
    writeMarker: (id, value) => calls.markerWrites.push({ id, value }),
    clearMarker: (id, dir) => calls.markerClears.push({ id, dir }),
    writeActiveSessionFile: (file, sid) => calls.activeFiles.push({ file, sid }),
    tempDir: rest.tempDir || os.tmpdir(),
    activeSessionFile: rest.activeSessionFile === undefined ? "/tmp/active.json" : rest.activeSessionFile,
    explicitId: rest.explicitId || "",
    freshContext: rest.freshContext === true,
    openClient: openThrows
      ? async () => { throw new Error("gateway refused"); }
      : async () => ({
        request: async (frame) => {
          const method = frame?.method || "";
          if (method.includes("active_list")) return listOf(active);
          if (dbThrows) throw new Error("session.list unavailable");
          return listOf(db || []);
        },
        close: () => { calls.closed += 1; },
      }),
  };
  return { deps, calls };
}

// ── the placeholder guard ───────────────────────────────────────────────────────────────────────

test("an UNEXPANDED ${HERMES_SESSION_ID} is not treated as an explicit resume", async () => {
  // `--resume "${HERMES_SESSION_ID}"` with the variable unset hands the CLI the literal string. Seeding
  // it would write a poison id into both the marker and the active-session file, and the next launch
  // would try to resume a session by that name.
  const { deps, calls } = harness({ gatewayUrl: "", explicitId: "${HERMES_SESSION_ID}" });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.notEqual(result.source, "explicit-resume");
  assert.deepEqual(calls.markerWrites, []);
  assert.deepEqual(calls.activeFiles, []);
});

test("an AGENT ID is required", async () => {
  // The marker and the active file are both keyed by it. Resolving for "" would read and write a
  // shared, unowned marker.
  const { deps } = harness();
  for (const bad of ["", "   ", null, undefined]) {
    await assert.rejects(() => runResolveSessionCli(bad, deps), /requires an agentId/);
  }
});

// ── explicit resume ─────────────────────────────────────────────────────────────────────────────

test("an explicit id with NO GATEWAY is authoritative and is seeded", async () => {
  // Nothing can validate it, and the operator asked for it by name. Both the marker and the
  // active-session file are seeded so the in-session bridge reads the same id the TUI resumed.
  const { deps, calls } = harness({ gatewayUrl: "", explicitId: "sess-explicit" });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "sess-explicit");
  assert.equal(result.source, "explicit-resume");
  assert.deepEqual(calls.markerWrites, [{ id: AGENT, value: "sess-explicit" }]);
  assert.deepEqual(calls.activeFiles, [{ file: "/tmp/active.json", sid: "sess-explicit" }]);
});

test("an explicit id WITH a gateway is VALIDATED, not seeded blindly", async () => {
  // The stranded-console bug: an id hermes has garbage-collected would otherwise be seeded and then
  // launched as `hermes --resume <dead-id>`, which errors and leaves the console with nothing.
  const { deps, calls } = harness({
    explicitId: "sess-dead", active: [], db: [], dbThrows: false,
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "");
  assert.notEqual(result.source, "explicit-resume");
  assert.deepEqual(calls.activeFiles, [], "a dead explicit id was seeded into the active file");
});

test("an explicit id that the DB CONFIRMS resolves to its durable key", async () => {
  const { deps } = harness({
    explicitId: "eph-1",
    active: [],
    db: [row("eph-1", "durable-key-1")],
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "durable-key-1");
  assert.equal(result.source, "marker(db-resumable)");
});

// ── the DB beats the live list ──────────────────────────────────────────────────────────────────

test("a marker in the DB resolves even when NOTHING is live", async () => {
  // The fresh-session-every-restart bug, stated directly: after a gateway restart `active_list` is
  // empty, and the marker must still resume.
  const { deps, calls } = harness({
    marker: "eph-1", active: [], db: [row("eph-1", "durable-key-1")],
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "durable-key-1");
  assert.equal(result.source, "marker(db-resumable)");
  assert.deepEqual(calls.markerClears, [], "a DB-resumable marker was cleared");
});

test("a marker only in the LIVE list still resolves, and says so", async () => {
  const { deps } = harness({
    marker: "eph-1", active: [row("eph-1", "durable-key-1")], db: [],
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "durable-key-1");
  assert.equal(result.source, "marker(live)");
});

test("the DURABLE key is persisted even when the marker held the EPHEMERAL id", async () => {
  // The 4007 loop: `--resume` and `session.resume` need the durable key. Persisting the ephemeral id
  // makes the next attach fail with "session not found".
  const { deps, calls } = harness({
    marker: "eph-1", active: [], db: [row("eph-1", "durable-key-1")],
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.markerWrites, [{ id: AGENT, value: "durable-key-1" }]);
  assert.deepEqual(calls.activeFiles, [{ file: "/tmp/active.json", sid: "durable-key-1" }]);
});

test("with NO marker the most-recent LIVE session is taken", async () => {
  // Never an arbitrary historical row from the DB — only what is live now, which on this agent's own
  // gateway is the TUI the operator is looking at.
  const { deps } = harness({
    marker: "",
    active: [row("eph-old", "key-old", "2026-08-17T08:00:00Z"),
      row("eph-new", "key-new", "2026-08-17T10:00:00Z")],
    db: [row("eph-ancient", "key-ancient", "2020-01-01T00:00:00Z")],
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "key-new");
  assert.equal(result.source, "active_list(most-recent)");
});

// ── the conditional clear ───────────────────────────────────────────────────────────────────────

test("a marker PROVEN gone from the DB is cleared", async () => {
  // The dead-marker clear: `session.list` succeeded and did not contain it, so the id is dead and must
  // stop recurring on the next send-driven spawn.
  const { deps, calls } = harness({ marker: "eph-gone", active: [], db: [] });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "");
  assert.deepEqual(calls.markerClears, [{ id: AGENT, dir: os.tmpdir() }]);
});

test("a marker is NOT cleared when the DB could not be consulted", async () => {
  // The lost-history bug in its subtlest form. `session.list` was unavailable, so the marker cannot be
  // shown dead — clearing it would abandon a session that is still resumable.
  const { deps, calls } = harness({ marker: "eph-unknown", active: [], dbThrows: true });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "");
  assert.deepEqual(calls.markerClears, [], "the marker was cleared without proof it was dead");
});

test("a marker is NOT cleared when the whole QUERY failed", async () => {
  // Different failure, same rule: the marker is kept as the best-known answer AND left on disk.
  //
  // The `source !== "marker(query-failed)"` term in that guard turns out to be REDUNDANT, and a mutation
  // removing it survives: a failed query sets `resolved = marker`, so a non-empty marker never reaches
  // the clear at all, and an empty one is already blocked by `&& marker` — and `dbConsulted` is false on
  // that path anyway. Recorded rather than asserted, because no input distinguishes the two forms. The
  // term is worth keeping: it states the intent at the place a reader will look.
  const { deps, calls } = harness({ marker: "eph-kept", openThrows: true });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "eph-kept");
  assert.equal(result.source, "marker(query-failed)");
  assert.deepEqual(calls.markerClears, []);
});

test("NO gateway at all falls back to the marker without clearing it", async () => {
  const { deps, calls } = harness({ gatewayUrl: "", marker: "eph-offline" });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "eph-offline");
  assert.equal(result.source, "marker(no-gateway)");
  assert.deepEqual(calls.markerClears, []);
});

// ── writes, streams and cleanup ─────────────────────────────────────────────────────────────────

test("the marker write is SKIPPED when the file already holds the resolved id", async () => {
  // The write is best-effort and cheap, but a rewrite on every launch is a needless touch of a file
  // another process reads.
  const { deps, calls } = harness({
    marker: "durable-key-1", active: [], db: [row("eph-1", "durable-key-1")],
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.markerWrites, []);
  assert.deepEqual(calls.activeFiles, [{ file: "/tmp/active.json", sid: "durable-key-1" }],
    "the active file must still be written even when the marker matches");
});

test("the write-skip compares against the FILE, not the candidate", async () => {
  // The scenario the source comment describes and my first version could not reach: an EXPLICIT
  // `--resume <id>` becomes the candidate, resolves to itself, and DIFFERS from the saved marker. If
  // the comparison used the candidate the two would be equal and the write would be skipped — leaving
  // the marker on disk pointing at the old session while the TUI resumed the new one.
  const { deps, calls } = harness({
    explicitId: "durable-explicit",
    marker: "durable-stale",
    active: [],
    db: [row("eph-x", "durable-explicit"), row("eph-y", "durable-stale")],
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.markerWrites, [{ id: AGENT, value: "durable-explicit" }],
    "the resolved id was not persisted over the stale marker");
});

test("no ACTIVE-SESSION FILE configured means none is written", async () => {
  // The variable is what the wrapper exports; without it there is nothing to seed and no path to guess.
  const { deps, calls } = harness({
    marker: "eph-1", active: [], db: [row("eph-1", "durable-key-1")], activeSessionFile: "",
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.activeFiles, []);
});

test("the resolved id goes to STDOUT and every diagnostic to STDERR", async () => {
  // The wrapper captures stdout into a shell variable. A diagnostic there would be resumed as if it
  // were a session id.
  const { deps, calls } = harness({
    marker: "eph-1", active: [], db: [row("eph-1", "durable-key-1")],
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.out, ["durable-key-1\n"]);
  assert.ok(calls.err.length > 0, "nothing was reported to stderr");
  assert.ok(!calls.out.join("").includes("["), calls.out.join(""));
});

test("NOTHING resolved still prints an empty LINE, which the wrapper reads as 'start fresh'", async () => {
  const { deps, calls } = harness({ marker: "", active: [], db: [] });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.out, ["\n"]);
});

test("the gateway client is CLOSED on both paths", async () => {
  // It is a websocket opened per invocation of a short-lived CLI. Leaving it open holds a gateway
  // connection for the life of the wrapper.
  const resolved = harness({ marker: "eph-1", active: [], db: [row("eph-1", "durable-key-1")] });
  await runResolveSessionCli(AGENT, resolved.deps);
  assert.equal(resolved.calls.closed, 1);

  const empty = harness({ marker: "", active: [], db: [] });
  await runResolveSessionCli(AGENT, empty.deps);
  assert.equal(empty.calls.closed, 1);
});

// ── the default writer ──────────────────────────────────────────────────────────────────────────

test("the active-session file is written as JSON the Python side reads, mode 0600", async () => {
  // Written with the DEFAULT writer this time, not a spy: the file's shape is a contract with
  // `service/runtimes/hermes.py`, which reads `session_id` out of it, and its mode matters because it
  // names a live session inside a shared temp directory.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-active-"));
  const file = path.join(dir, "nested", "active-session.json");
  const { deps } = harness({
    marker: "eph-1",
    active: [],
    db: [row("eph-1", "durable-key-1")],
    activeSessionFile: file,
  });
  delete deps.writeActiveSessionFile;   // use the module's own default
  try {
    await runResolveSessionCli(AGENT, deps);
    assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf-8")), { session_id: "durable-key-1" });
    // The mode is only observable on POSIX — Windows does not carry these bits, so a mutation widening
    // 0o600 to 0o644 survives this suite ON THIS PLATFORM. The assertion runs wherever it can, and the
    // gap is stated rather than left to look like coverage.
    if (process.platform !== "win32") {
      assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("the default writer CREATES the parent directory", async () => {
  // The wrapper points it at a path under a temp dir that may not exist yet on a fresh boot. A missing
  // parent would throw inside a best-effort try and silently leave the file unwritten.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-active-"));
  const file = path.join(dir, "a", "b", "c.json");
  const { deps } = harness({
    marker: "eph-1", active: [], db: [row("eph-1", "durable-key-1")], activeSessionFile: file,
  });
  delete deps.writeActiveSessionFile;
  try {
    await runResolveSessionCli(AGENT, deps);
    assert.ok(fs.existsSync(file));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ── fresh context: the one instruction that must beat every resume path ─────────────────────────
//
// MEASURED 2026-08-31, and it is why `comms_restart freshContext=true` had never once worked for a
// hermes agent. `service/api_core/session_restart.py` sets `resume_policy="fresh_context"` and clears
// `agents.session_handle`; `spawn-loop.mjs` carries it; ONLY codex reads it. Hermes resumed anyway,
// and comms-senior-dev sat on a 5 JUNE conversation until it hit 1,122,638 tokens against a 900k
// window and could no longer answer anything.
//
// THREE INDEPENDENT PATHS LEAD BACK TO THE OLD SESSION, which is why clearing the marker is not a
// fix on its own:
//   1. the marker file itself;
//   2. `startResumeMarkerSync`, which rewrites that file from the gateway's live session -- the
//      marker's mtime was minutes old while holding a June id;
//   3. branch (b) here, which with NO marker falls back to `active_list(most-recent)` -- the same
//      live session, resurrected by a different route.
// So the instruction has to be honoured BEFORE any of them runs.

const FRESH_CARRIER = "AIFY_HERMES_FRESH_CONTEXT";
delete process.env[FRESH_CARRIER];
assert.equal(process.env[FRESH_CARRIER], undefined,
  "the fresh-context env seal did not take — the default-OFF test below would be reading the ambient value");

test("FRESH CONTEXT refuses to resume, even when the marker names a DB-resumable session", async () => {
  const { deps, calls } = harness({
    marker: "20260605_181038_6cd2ef",
    db: [row("eph-1", "20260605_181038_6cd2ef")],
    active: [row("eph-1", "20260605_181038_6cd2ef")],
    freshContext: true,
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "", "a fresh-context launch resolved a session to resume");
  assert.equal(result.source, "fresh-context");
  assert.deepEqual(calls.markerWrites, [], "a fresh-context launch persisted a resume id");
});

test("FRESH CONTEXT clears the marker, using the BARE-dir convention clearSessionMarker takes", async () => {
  // `clearSessionMarker(id, dir)` — not `{ tempDir }`, which is the shape the read/write helpers use.
  // Getting this wrong clears nothing and fails silently.
  const { deps, calls } = harness({
    marker: "20260605_181038_6cd2ef",
    db: [row("eph-1", "20260605_181038_6cd2ef")],
    tempDir: "/tmp/seal",
    freshContext: true,
  });
  await runResolveSessionCli(AGENT, deps);
  assert.equal(calls.markerClears.length, 1, "the stale marker was not cleared");
  assert.deepEqual(calls.markerClears[0], { id: AGENT, dir: "/tmp/seal" });
});

test("FRESH CONTEXT does not fall back to the most-recent LIVE session", async () => {
  // Path 3. With no marker at all the normal resolution resurrects `active_list(most-recent)`, which
  // on a running gateway is the very conversation the reset was asked to escape.
  const { deps, calls } = harness({
    marker: "",
    active: [row("eph-9", "20260605_181038_6cd2ef", "2026-08-31T12:00:00Z")],
    db: [row("eph-9", "20260605_181038_6cd2ef")],
    freshContext: true,
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "", "the most-recent live session was resurrected anyway");
});

test("FRESH CONTEXT never opens the gateway at all, so nothing can steer it back", async () => {
  // The early return is the point: any code that runs before the decision is another chance to
  // resolve a session. Proven by making the opener fail the test if it is reached.
  let opened = 0;
  const { deps } = harness({ marker: "20260605_181038_6cd2ef", freshContext: true });
  deps.openClient = async () => { opened += 1; throw new Error("must not be reached"); };
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(opened, 0, "a fresh-context launch consulted the gateway");
  assert.equal(result.resolved, "");
});

test("FRESH CONTEXT is OFF by default, so an ordinary launch still resumes its history", async () => {
  // ANTI-VACUITY. Losing the operator's thread on every restart is the bug this file's header names
  // first; a fix that started fresh unconditionally would reintroduce it.
  const { deps } = harness({
    marker: "20260605_181038_6cd2ef",
    db: [row("eph-1", "20260605_181038_6cd2ef")],
  });
  const result = await runResolveSessionCli(AGENT, deps);
  assert.equal(result.resolved, "20260605_181038_6cd2ef", "an ordinary launch stopped resuming");
  assert.match(result.source, /^marker/);
});

test("the env carrier turns it on, because the spawn reaches the wrapper through the environment", async () => {
  const { deps } = harness({ marker: "20260605_181038_6cd2ef", db: [row("e", "20260605_181038_6cd2ef")] });
  delete deps.freshContext;
  process.env[FRESH_CARRIER] = "1";
  try {
    const result = await runResolveSessionCli(AGENT, deps);
    assert.equal(result.resolved, "", "the environment carrier was ignored");
    assert.equal(result.source, "fresh-context");
  } finally {
    delete process.env[FRESH_CARRIER];
  }
});

test("FRESH CONTEXT clears the ACTIVE-SESSION FILE too, which discoverSessionId reads FIRST", async () => {
  // Path 4, and the one easiest to miss: the in-session bridge resolves its session id from the
  // per-agent active file as its PRIMARY source, ahead of the marker. Clearing the marker while
  // leaving that file behind hands the fresh session the old id by a different door.
  const { deps, calls } = harness({
    marker: "20260605_181038_6cd2ef",
    db: [row("eph-1", "20260605_181038_6cd2ef")],
    activeSessionFile: "/tmp/active.json",
    freshContext: true,
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.activeFiles, [{ file: "/tmp/active.json", sid: "" }],
                   "the stale active-session file was left pointing at the old conversation");
});

test("FRESH CONTEXT with no active file configured writes none", async () => {
  // Anti-vacuity for the test above: an unconditional write would create a file for an agent that
  // has none, and `discoverSessionId` would then start reading it.
  const { deps, calls } = harness({
    marker: "20260605_181038_6cd2ef",
    activeSessionFile: "",
    freshContext: true,
  });
  await runResolveSessionCli(AGENT, deps);
  assert.deepEqual(calls.activeFiles, []);
});
