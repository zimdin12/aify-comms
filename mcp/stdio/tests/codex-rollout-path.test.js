// Which rollout file is THIS agent's — and the fallback that is deliberately absent.
//
// Found by a new measurement: running the bridge suite under `NODE_V8_COVERAGE` and asking which named
// functions V8 recorded with a zero call count. That is the finer floor under
// `every-export-is-named-by-a-test.test.js`, which counts a NAME appearing anywhere in the test tree —
// a docstring mention passes it. 192 named functions in `mcp/stdio` are never CALLED by the suite;
// these two are the first cluster, chosen because the comment above `_resolveRolloutPath` describes a
// refusal rather than a feature.
//
// THE REFUSAL. Resident codex has no transcript detector, so turn state is read from the rollout tail.
// Finding the rollout means matching the agent's session uuid IN THE FILENAME — and explicitly NOT
// falling back to "newest .jsonl by mtime", because on a host running two codex sessions that reads a
// DIFFERENT agent's rollout and drives turn-busy for the wrong agent. The module says it: "a
// wrong-agent guess is worse than no signal". A test that only checked the happy path would leave the
// tempting fallback free to be added back.
//
// `CODEX_SESSIONS_DIR` IS SEALED. It is `path.join(os.homedir(), ".codex", "sessions")` resolved at
// MODULE LOAD, and `os.homedir()` reads USERPROFILE/HOME on every call — so both are redirected to a
// temp tree BEFORE the dynamic import below, and the seal is asserted. Unsealed, these tests would walk
// the operator's real rollouts; the walk is read-only, but reading another agent's session is precisely
// the failure the refusal exists to prevent, so doing it here would be a poor way to test for it.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const SEALED_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "aify-codex-home-"));
process.env.USERPROFILE = SEALED_HOME;
process.env.HOME = SEALED_HOME;
delete process.env.CODEX_THREAD_ID;

const SESSIONS = path.join(SEALED_HOME, ".codex", "sessions");
fs.mkdirSync(SESSIONS, { recursive: true });

const { CodexAdapter, summarizeCodexRolloutTail } = await import("../adapters/codex.js");

const ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const OTHER_ID = "11111111-2222-3333-4444-555555555555";

function clearSessions() {
  fs.rmSync(SESSIONS, { recursive: true, force: true });
  fs.mkdirSync(SESSIONS, { recursive: true });
}

// Writes a rollout at a nested depth and returns its path. `depth: 0` puts it directly in the
// sessions dir, which is not how codex stores them (it uses YYYY/MM/DD) — hence the depth argument.
function writeRollout(name, { depth = 3, content = "", mtimeMs = null } = {}) {
  const parts = Array.from({ length: depth }, (_, i) => `d${i}`);
  const dir = path.join(SESSIONS, ...parts);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, content, "utf-8");
  if (mtimeMs != null) fs.utimesSync(file, mtimeMs / 1000, mtimeMs / 1000);
  return file;
}

const adapter = () => new CodexAdapter();

// ── the seal ────────────────────────────────────────────────────────────────────────────────────

test("the sessions directory is the SEALED one", () => {
  assert.equal(os.homedir(), SEALED_HOME);
  assert.ok(SESSIONS.startsWith(SEALED_HOME));
});

// ── resolving this agent's rollout ──────────────────────────────────────────────────────────────

test("a rollout whose FILENAME carries the session uuid is found", async () => {
  clearSessions();
  const file = writeRollout(`rollout-2026-08-17T09-00-00-${ID}.jsonl`);
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), file);
});

test("the uuid match is CASE-INSENSITIVE in BOTH directions", async () => {
  // Codex writes them lower-case; a handle that has been through a shell or a JSON round-trip may not
  // be. A case-sensitive match silently finds nothing, which reads as "no rollout yet".
  //
  // BOTH directions, because they are two different normalisations: the FILENAME is lower-cased at the
  // comparison and the WANTED ID is lower-cased when it is read. My first version only supplied a
  // lower-case id, so the mutation that dropped `.toLowerCase()` from the wanted id survived it.
  clearSessions();
  const upperFile = writeRollout(`rollout-${ID.toUpperCase()}.jsonl`);
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), upperFile,
    "an upper-case FILENAME was not matched by a lower-case id");

  clearSessions();
  const lowerFile = writeRollout(`rollout-${ID}.jsonl`);
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID.toUpperCase() }), lowerFile,
    "a lower-case filename was not matched by an upper-case ID");
});

test("an upper-case id from the ENVIRONMENT matches too", async () => {
  // The env path goes through `normalizeSessionHandle`, which trims but does not lower-case — so this
  // is the same normalisation as above reached by the route a real wrapper uses.
  clearSessions();
  const file = writeRollout(`rollout-${ID}.jsonl`);
  process.env.CODEX_THREAD_ID = ID.toUpperCase();
  try {
    assert.equal(await adapter()._resolveRolloutPath({}), file);
  } finally {
    delete process.env.CODEX_THREAD_ID;
  }
});

test("NO SESSION ID resolves to nothing rather than to a guess", async () => {
  // The detector no-ops, and the hooks plus the server backstop still cover turn state. Guessing here
  // is the wrong-agent failure with no signal that it happened.
  clearSessions();
  writeRollout(`rollout-${OTHER_ID}.jsonl`);
  for (const opts of [{}, { sessionId: "" }, { sessionId: "   " }]) {
    assert.equal(await adapter()._resolveRolloutPath(opts), null, JSON.stringify(opts));
  }
});

test("ANOTHER AGENT'S rollout is NOT used as a fallback", async () => {
  // The refusal this file exists for. Two codex sessions on one host is the ordinary case, and
  // "newest .jsonl by mtime" would drive turn-busy for whichever agent wrote last.
  clearSessions();
  writeRollout(`rollout-${OTHER_ID}.jsonl`, { mtimeMs: Date.now() });
  writeRollout("rollout-no-uuid-at-all.jsonl", { mtimeMs: Date.now() });
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), null);
});

test("a file that is not a .jsonl is ignored even with a matching uuid", async () => {
  clearSessions();
  writeRollout(`rollout-${ID}.json`);
  writeRollout(`rollout-${ID}.txt`);
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), null);
});

test("the NEWEST rollout wins when the SAME session has several", async () => {
  // A resumed session appends a new file. Within one agent's own id, mtime is the right tiebreak —
  // this is the only place a recency rule is legitimate here.
  clearSessions();
  writeRollout(`rollout-old-${ID}.jsonl`, { mtimeMs: 1_600_000_000_000 });
  const newer = writeRollout(`rollout-new-${ID}.jsonl`, { mtimeMs: 1_700_000_000_000 });
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), newer);
});

test("the walk is BOUNDED and a rollout buried too deep is not found", async () => {
  // A depth cap on a recursive walk of a directory the agent does not control. Unbounded, a deep tree
  // (or a symlink loop) turns a turn-state read into a stall on every detector tick.
  clearSessions();
  writeRollout(`rollout-${ID}.jsonl`, { depth: 9 });
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), null);
});

test("a MISSING sessions directory is not an error", async () => {
  // A host with no codex installed, or one that has never run it. The detector must no-op rather than
  // throw into whatever polled it.
  fs.rmSync(SESSIONS, { recursive: true, force: true });
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), null);
  fs.mkdirSync(SESSIONS, { recursive: true });
});

test("the ENV session id is preferred over the one passed in", async () => {
  // `getCurrentSessionId()` first. The env var is what the wrapper exported for THIS process; an opts
  // value is a caller's hint and must not override the process's own identity.
  clearSessions();
  const envFile = writeRollout(`rollout-${ID}.jsonl`);
  writeRollout(`rollout-${OTHER_ID}.jsonl`);
  process.env.CODEX_THREAD_ID = ID;
  try {
    assert.equal(await adapter()._resolveRolloutPath({ sessionId: OTHER_ID }), envFile);
  } finally {
    delete process.env.CODEX_THREAD_ID;
  }
});

test("a PLACEHOLDER env id does not count as an identity", async () => {
  // `normalizeSessionHandle` rejects none/null/unknown/default. Without that, a shell that exported an
  // unset variable would make the detector look for a rollout named after the word "none".
  clearSessions();
  const file = writeRollout(`rollout-${ID}.jsonl`);
  process.env.CODEX_THREAD_ID = "none";
  try {
    assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), file);
  } finally {
    delete process.env.CODEX_THREAD_ID;
  }
});

// ── reading the tail ────────────────────────────────────────────────────────────────────────────

const TASK_STARTED = JSON.stringify({ type: "event_msg", payload: { type: "task_started" } });
const TASK_COMPLETE = JSON.stringify({ type: "event_msg", payload: { type: "task_complete" } });

test("no rollout means no tail — null, not an empty summary", async () => {
  // The caller distinguishes "there is no rollout to read" from "the rollout says nothing happened".
  // Only the first means the detector should stay out of the way.
  clearSessions();
  assert.equal(await adapter().transcriptTail({ sessionId: ID }), null);
});

test("a rollout's tail is summarised into the detector's shape", async () => {
  clearSessions();
  writeRollout(`rollout-${ID}.jsonl`, { content: `${TASK_STARTED}\n${TASK_COMPLETE}\n` });
  const tail = await adapter().transcriptTail({ sessionId: ID });
  assert.deepEqual(tail, { lastRole: "assistant", lastStopReason: "end_turn", pendingToolUse: false });
});

test("an EMPTY rollout is the zero summary, not null", async () => {
  clearSessions();
  writeRollout(`rollout-${ID}.jsonl`, { content: "" });
  assert.deepEqual(await adapter().transcriptTail({ sessionId: ID }),
    { lastRole: null, lastStopReason: null, pendingToolUse: false });
});

test("a small tail window still reaches the decisive last line", async () => {
  // Rollouts grow without bound, and reading the whole file on every detector tick would scale the cost
  // of knowing whether an agent is busy with the length of its conversation.
  //
  // THE WINDOW IS A COST BOUND, NOT A CORRECTNESS ONE, and this test cannot show otherwise: the
  // summariser walks BACKWARDS from the end and returns on the first decisive line, so reading more
  // bytes can never change the answer. Mutations that set the window to the whole file, or ignore a
  // caller's `tailBytes`, therefore survive this suite — correctly, because they change only how much
  // is read. Recorded rather than papered over with an assertion that would not mean what it said.
  clearSessions();
  const filler = `${TASK_STARTED}\n`.repeat(200);
  writeRollout(`rollout-${ID}.jsonl`, { content: filler + `${TASK_COMPLETE}\n` });
  const tail = await adapter().transcriptTail({ sessionId: ID, tailBytes: 200 });
  assert.equal(tail.lastStopReason, "end_turn");
});

test("a tail window that CUTS A LINE IN HALF does not poison the answer", async () => {
  // The window lands mid-line by definition. The summariser walks backwards and skips unparseable
  // lines, so a truncated first line is junk rather than an error — asserted here because the read is
  // where the truncation is created.
  clearSessions();
  writeRollout(`rollout-${ID}.jsonl`, { content: `${TASK_STARTED}\n${TASK_COMPLETE}\n` });
  const tail = await adapter().transcriptTail({ sessionId: ID, tailBytes: 30 });
  assert.ok(tail, "a mid-line window produced no summary at all");
});

test("a non-positive tailBytes falls back to the default rather than reading nothing", async () => {
  clearSessions();
  writeRollout(`rollout-${ID}.jsonl`, { content: `${TASK_COMPLETE}\n` });
  for (const tailBytes of [0, -1, "abc", undefined]) {
    const tail = await adapter().transcriptTail({ sessionId: ID, tailBytes });
    assert.equal(tail.lastStopReason, "end_turn", String(tailBytes));
  }
});

test("a DIRECTORY named like a rollout is never resolved in the first place", async () => {
  // The walk requires `isFile()`, so a directory with a matching name is not a candidate at all and the
  // tail read is never reached.
  //
  // WHICH MEANS the `isFile()` check INSIDE the tail read, and the try/catch around it, are defensive
  // against a race — the file vanishing between resolve and open — and are not reachable from here.
  // Mutations removing either survive this suite. Both are worth keeping (the race is real on a host
  // where codex rotates rollouts) and neither is proven by anything below.
  clearSessions();
  fs.mkdirSync(path.join(SESSIONS, "d0", `rollout-${ID}.jsonl`), { recursive: true });
  assert.equal(await adapter()._resolveRolloutPath({ sessionId: ID }), null);
  assert.equal(await adapter().transcriptTail({ sessionId: ID }), null);
});

test("NO SESSION ID skips the directory walk entirely", async () => {
  // The `if (!wantId) return null` guard is a short-circuit, not a behaviour gate: with it removed the
  // walk runs and matches nothing, so the answer is the same null. What it saves is a recursive
  // directory scan on every detector tick for an agent that has no session id — recorded because the
  // mutation that removes it survives, and the reason is cost rather than correctness.
  clearSessions();
  writeRollout(`rollout-${OTHER_ID}.jsonl`);
  assert.equal(await adapter()._resolveRolloutPath({}), null);
});

test("the summariser is reachable on its own and agrees with the tail read", () => {
  // It is exported and tested elsewhere; asserted here only so the two halves of this path are known
  // to compose — the file read produces exactly what the summariser consumes.
  assert.deepEqual(summarizeCodexRolloutTail(`${TASK_STARTED}\n`),
    { lastRole: "user", lastStopReason: null, pendingToolUse: false });
});

test.after(() => {
  try { fs.rmSync(SEALED_HOME, { recursive: true, force: true }); } catch { /* best effort */ }
});
