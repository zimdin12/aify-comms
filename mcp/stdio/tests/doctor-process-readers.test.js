// The two readers behind aify-doctor's `agent-identity` check.
//
// Found by a V8-coverage census of the bridge suite: both had a ZERO call count. For the two readers
// that decide whether the doctor cries wolf or reports green, that is the same shape as the false
// greens which moved the env-bridge predicates out of `doctor.js` in the first place — and the same
// structural cause, since `doctor.js` runs every check at import and ends in `process.exit()`, so
// nothing declared there can be reached by a test. This slice moves them to `doctor-predicates.js`
// with an injectable `/proc` root and a reader, which is the pattern that file exists to be.
//
// WHAT THEY DECIDE. `agent-identity` catches an agent that REGISTERED but whose process carries no
// `AIFY_AGENT_ID` — invisible from the database, because it messages and heartbeats perfectly while
// its status latches forever. The check must separate that from a plain claude+comms session that
// never registered and is legitimately id-less, and `readBoundAgentId` IS that separation:
// `comms_register` writes a binding file keyed by the CLIENT pid, so a binding means "this session
// registered". Wrong in one direction the check cries wolf on every plain session; wrong in the other
// it reports green over exactly the agents it exists to find.
//
// A FAKE /proc TREE ON DISK, not a stubbed reader, for the paths — the readers build them by string
// concatenation and `path.join`, and a stub would let a wrong path pass. The real `readFileSync` walks
// the fake tree. `TMPDIR` is passed explicitly rather than set, so nothing here depends on the
// developer's own temp directory or leaves a binding file in it.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { readBoundAgentId, readProcEnv } from "../doctor-predicates.js";

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "aify-doctor-proc-"));
const PROC = path.join(ROOT, "proc");
const TMP = path.join(ROOT, "tmp");
fs.mkdirSync(PROC, { recursive: true });
fs.mkdirSync(TMP, { recursive: true });

function writeProc(pid, files) {
  const dir = path.join(PROC, String(pid));
  fs.mkdirSync(dir, { recursive: true });
  for (const [name, content] of Object.entries(files)) {
    fs.writeFileSync(path.join(dir, name), content, "utf-8");
  }
}

// `/proc/<pid>/stat` field 4 (1-indexed) is the ppid. Fields 1-2 are the pid and the comm, and comm
// is parenthesised BECAUSE it can contain spaces — the reader splits on space and takes index 3,
// which is correct only while comm has none.
function statLine(pid, ppid, comm = "node") {
  return `${pid} (${comm}) S ${ppid} ${pid} 0 0 -1 4194304 0 0`;
}

function environ(pairs) {
  return Object.entries(pairs).map(([k, v]) => `${k}=${v}`).join("\0") + "\0";
}

const opts = { procRoot: PROC, tmpDir: TMP };

// ── readProcEnv ─────────────────────────────────────────────────────────────────────────────────

test("the process environment is parsed into a plain object", () => {
  writeProc(101, { environ: environ({ AIFY_AGENT_ID: "sc-coder", PATH: "/usr/bin" }) });
  assert.deepEqual(readProcEnv(101, opts), { AIFY_AGENT_ID: "sc-coder", PATH: "/usr/bin" });
});

test("a value CONTAINING an equals sign keeps all of it", () => {
  // `indexOf("=")` and slice, not split — a token, a URL with a query string, or a base64 value with
  // padding would otherwise be truncated at its first `=`, and the check would compare a mangled id.
  writeProc(102, { environ: environ({ AIFY_SERVER_URL: "http://h/?a=1&b=2", K: "x==" }) });
  const env = readProcEnv(102, opts);
  assert.equal(env.AIFY_SERVER_URL, "http://h/?a=1&b=2");
  assert.equal(env.K, "x==");
});

test("an EMPTY value is kept as an empty string, not dropped", () => {
  // `AIFY_AGENT_ID=` is exactly what a wrapper writes when it expanded an unset variable, and it is a
  // DIFFERENT state from the variable being absent. The check reads truthiness, so both end up
  // "anonymous" — but the reader must not silently turn one into the other.
  writeProc(103, { environ: environ({ AIFY_AGENT_ID: "" }) });
  assert.deepEqual(readProcEnv(103, opts), { AIFY_AGENT_ID: "" });
});

test("a malformed entry with NO equals sign is skipped", () => {
  writeProc(104, { environ: `justakey\0AIFY_AGENT_ID=ok\0` });
  assert.deepEqual(readProcEnv(104, opts), { AIFY_AGENT_ID: "ok" });
});

test("an entry beginning with an equals sign is skipped", () => {
  // `i > 0`, not `i >= 0`: an empty variable NAME is not a variable, and admitting it would put a ""
  // key in the object.
  writeProc(105, { environ: `=novalue\0AIFY_AGENT_ID=ok\0` });
  assert.deepEqual(readProcEnv(105, opts), { AIFY_AGENT_ID: "ok" });
});

test("a process that is GONE reads as an empty environment, not an error", () => {
  // The check iterates every pid in /proc, and processes exit while it does. A throw here would abort
  // the whole check partway through and report on a subset of the fleet.
  assert.deepEqual(readProcEnv(999_999_998, opts), {});
});

test("an UNREADABLE environ reads as empty", () => {
  // /proc/<pid>/environ is readable only by the owner. A doctor run as a different user must skip
  // those processes rather than fail.
  writeProc(106, {});
  assert.deepEqual(readProcEnv(106, opts), {});
});

// ── readBoundAgentId ────────────────────────────────────────────────────────────────────────────

test("a binding written for the CLIENT pid is found through the bridge's ppid", () => {
  // The whole point: `comms_register` runs inside the claude process, which is the bridge's PARENT, so
  // the binding is keyed by the parent pid. Looking only at the bridge's own pid would find nothing and
  // report every registered agent as never having registered.
  writeProc(201, { stat: statLine(201, 200) });
  fs.writeFileSync(path.join(TMP, "aify-agent-200"), "sc-coder", "utf-8");
  assert.equal(readBoundAgentId(201, opts), "sc-coder");
});

test("a binding written for the BRIDGE's own pid is also found", () => {
  // The fallback, and it is not redundant: a bridge launched without a wrapper parent writes its own.
  writeProc(202, { stat: statLine(202, 1) });
  fs.writeFileSync(path.join(TMP, "aify-agent-202"), "sc-solo", "utf-8");
  assert.equal(readBoundAgentId(202, opts), "sc-solo");
});

test("the PARENT's binding wins over the bridge's own", () => {
  // Order matters: the parent is where `comms_register` actually ran. A stale self-binding from an
  // earlier launch must not shadow the identity of the session that is running now.
  writeProc(203, { stat: statLine(203, 300) });
  fs.writeFileSync(path.join(TMP, "aify-agent-300"), "from-parent", "utf-8");
  fs.writeFileSync(path.join(TMP, "aify-agent-203"), "from-self", "utf-8");
  assert.equal(readBoundAgentId(203, opts), "from-parent");
});

test("a JSON binding is read for its agentId", () => {
  // Two formats are in the wild — a bare id and a JSON object — and the reader tells them apart by the
  // leading brace. Treating JSON as a bare id would report an agent named `{"agentId":"…"}`.
  writeProc(204, { stat: statLine(204, 400) });
  fs.writeFileSync(path.join(TMP, "aify-agent-400"),
    JSON.stringify({ agentId: "sc-json", pid: 400 }), "utf-8");
  assert.equal(readBoundAgentId(204, opts), "sc-json");
});

test("a JSON binding with NO agentId is not a binding", () => {
  // It falls through to the next candidate rather than returning "" from a file that exists — which is
  // the difference between "this session registered under another pid" and "it did not register".
  writeProc(205, { stat: statLine(205, 500) });
  fs.writeFileSync(path.join(TMP, "aify-agent-500"), JSON.stringify({ pid: 500 }), "utf-8");
  fs.writeFileSync(path.join(TMP, "aify-agent-205"), "sc-fallback", "utf-8");
  assert.equal(readBoundAgentId(205, opts), "sc-fallback");
});

test("UNPARSEABLE JSON does not stop the search", () => {
  // A binding file written half-way. The reader must move on to the next pid rather than throw out of
  // a check that is iterating the whole process table.
  writeProc(206, { stat: statLine(206, 600) });
  fs.writeFileSync(path.join(TMP, "aify-agent-600"), '{"agentId":', "utf-8");
  fs.writeFileSync(path.join(TMP, "aify-agent-206"), "sc-after-junk", "utf-8");
  assert.equal(readBoundAgentId(206, opts), "sc-after-junk");
});

test("a BLANK binding file is not a binding", () => {
  writeProc(207, { stat: statLine(207, 700) });
  fs.writeFileSync(path.join(TMP, "aify-agent-700"), "   \n", "utf-8");
  assert.equal(readBoundAgentId(207, opts), "");
});

test("the id is TRIMMED", () => {
  // These files are written by shell redirection as often as by node.
  writeProc(208, { stat: statLine(208, 800) });
  fs.writeFileSync(path.join(TMP, "aify-agent-800"), "  sc-padded\n", "utf-8");
  assert.equal(readBoundAgentId(208, opts), "sc-padded");
});

test("NO binding anywhere is an empty string — a legitimately anonymous session", () => {
  // The answer that makes the check safe: a plain claude+comms session never registered, so it has no
  // binding and must not be reported as a broken agent.
  writeProc(209, { stat: statLine(209, 900) });
  assert.equal(readBoundAgentId(209, opts), "");
});

test("a process with NO stat file yields nothing rather than searching the bridge pid", () => {
  // It returns early, before the tmp lookup. A pid that vanished cannot be attributed to an agent, and
  // guessing from the bridge pid alone would attribute someone else's binding to it.
  fs.writeFileSync(path.join(TMP, "aify-agent-210"), "should-not-be-found", "utf-8");
  assert.equal(readBoundAgentId(210, opts), "");
});

test("a stat line whose ppid field is absent is survived", () => {
  writeProc(211, { stat: "211" });
  assert.equal(readBoundAgentId(211, opts), "");
});

test("a comm containing a SPACE breaks the ppid parse — recorded, not fixed", () => {
  // `/proc/<pid>/stat` parenthesises the process name precisely because it may contain spaces, and a
  // split on space then puts the ppid somewhere other than index 3. The bridge is always `node`, so
  // this is unreachable today; it is asserted so the assumption is written down where the parse is,
  // rather than discovered by whatever first renames a process.
  writeProc(212, { stat: statLine(212, 1000, "my node") });
  fs.writeFileSync(path.join(TMP, "aify-agent-1000"), "sc-spaced", "utf-8");
  assert.equal(readBoundAgentId(212, opts), "",
    "the ppid parse now survives a spaced comm — update this note, it is a fix not a regression");
});

test.after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
});
