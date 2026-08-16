// Which of the two session modes an arbitrary input becomes.
//
// A bridge-managed agent is either RESIDENT — launched and owned by a human — or MANAGED, spawned by the
// environment bridge and eligible to be stopped, restarted or reaped. Sixteen call sites in `server.js`
// turn lifecycle decisions on the answer, and none of it was reachable from a test: server.js is the bin
// entry point and nothing imports it.

import assert from "node:assert/strict";
import test from "node:test";
import { bridgeSources, isUsedInBridge } from "./bridge-sources.mjs";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { normalizeLaunchMode, normalizeSessionMode } from "../session-mode.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("only an exact 'managed' produces managed — everything else is resident", () => {
  // THE SAFETY DIRECTION, and the reason this four-line function is worth a test file. Resident is the
  // mode the bridge does NOT stop, restart or reap, because a human owns that session. So an input it
  // cannot read must land on resident: a bug upstream then costs a missed automation rather than a
  // killed session someone was working in. A "normalise with a default" that defaulted the other way
  // would turn every unreadable value into something reapable.
  assert.equal(normalizeSessionMode("managed"), "managed");
  assert.equal(normalizeSessionMode("resident"), "resident");

  for (const input of [
    undefined, null, "", "   ", "MANAGED_", "manage", "managd", "managed-worker", "unmanaged",
    "supervised", "0", 0, false, true, [], {}, "resident ", "RESIDENT",
  ]) {
    assert.equal(
      normalizeSessionMode(input), "resident",
      `${JSON.stringify(input)} must fail toward resident`,
    );
  }
  // `true` is in that list without an exception, which is worth stating: my first version excused it,
  // assuming a boolean might slip through. `String(true)` is "true", which is not "managed", so it lands
  // on resident like everything else. An exception carved for a case that does not need one hides
  // whatever the real behaviour is.
});

test("case and surrounding whitespace do not change the answer", () => {
  // Modes arrive from registration payloads and API responses written by several different runtimes, so
  // "Managed" and " managed\n" are inputs that actually occur.
  for (const input of ["MANAGED", "Managed", " managed", "managed ", "\tmanaged\n", " MaNaGeD "]) {
    assert.equal(normalizeSessionMode(input), "managed", `${JSON.stringify(input)} is still managed`);
  }
  for (const input of ["RESIDENT", " Resident ", "\tresident"]) {
    assert.equal(normalizeSessionMode(input), "resident");
  }
});

test("the result is always one of exactly two strings", () => {
  // Callers branch on this value directly. A third possible return — say passing an unknown mode
  // through — would make every `=== "managed"` check silently fall to the else branch for a mode the
  // system does know about, which is a worse failure than rejecting it.
  const outputs = new Set(
    ["managed", "resident", "", null, undefined, "other", 42].map(normalizeSessionMode),
  );
  assert.deepEqual([...outputs].sort(), ["managed", "resident"]);
});

test("server.js no longer declares it, and the BRIDGE still calls it", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.doesNotMatch(src, /^(?:export\s+)?function\s+normalizeSessionMode\b/m, "must be imported");
  // BRIDGE-WIDE. The last caller in server.js moved to `managed-environment-sync.mjs` in v0.5.4;
  // the intent was always "the bridge still calls it", and naming server.js is what broke it on a
  // pure relocation. The no-redeclaration check above stays pinned to server.js on purpose.
  assert.equal(isUsedInBridge("normalizeSessionMode"), true,
    "the bridge must still normalise session modes somewhere");
});

test("the leaf imports nothing", () => {
  const src = readFileSync(path.join(STDIO, "session-mode.mjs"), "utf-8");
  assert.ok(!/^import\s/m.test(src), "deciding between two strings needs no dependencies");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
});

// ── normalizeLaunchMode — the sibling field that was compared RAW ────────────────────────────────
//
// `launch_mode` sits beside `session_mode` on every agent row and answers a different question:
// `none` is the STOP marker the service writes when an agent is stopped
// (`SET status = 'stopped', launch_mode = 'none'`), meaning "the operator stopped this; do not start
// it". Two bridge sites compared it case-sensitively, each ONE LINE from a `normalizeSessionMode(...)`
// call on the same object — the sibling was normalised and this one was not.


test("a stop marker is recognised however it is spelled", () => {
  // `"None"` is the obvious accident, not a hostile input: `str(None)` in Python produces exactly
  // that, and `comms_register` takes `launchMode` as a free-form string. Unrecognised, the first
  // call site leaves a STOPPED resident host running and the second syncs an agent the operator
  // disabled — in both cases the stop is silently not honoured.
  for (const spelling of ["none", "None", "NONE", "  none  ", "nOnE"]) {
    assert.equal(normalizeLaunchMode(spelling), "none", `${JSON.stringify(spelling)} is a stop marker`);
  }
});

test("the other known modes survive, and absence means detached", () => {
  assert.equal(normalizeLaunchMode("detached"), "detached");
  assert.equal(normalizeLaunchMode("Detached"), "detached");
  assert.equal(normalizeLaunchMode("managed"), "managed");
  assert.equal(normalizeLaunchMode("MANAGED"), "managed");
  for (const absent of [null, undefined, "", "   "]) {
    assert.equal(normalizeLaunchMode(absent), "detached", "absent means the default launch mode");
  }
});

test("an unknown mode is folded, NOT replaced — this is not a vocabulary check", () => {
  // Deliberately unlike `normalizeSessionMode`, which collapses everything to one of two values.
  // There is no owning set for launch modes and inventing one would be a ruling; folding case fixes
  // the defect without deciding what an unrecognised mode means.
  assert.equal(normalizeLaunchMode("Codex-Live"), "codex-live");
  assert.equal(normalizeLaunchMode("future-mode"), "future-mode");
});

test("BOTH raw comparisons are gone from the bridge", () => {
  // The defect was two literal comparisons, so the fix is only complete when neither remains. A
  // bridge-wide scan rather than two named files: the invariant is "nothing compares launchMode
  // raw", and naming files is what broke a sibling test on a pure relocation.
  // COMMENTS STRIPPED FIRST. Without that this failed on `session-mode.mjs` itself, whose comment
  // quotes both old comparisons verbatim to record what was wrong — a scan that reads prose reports
  // the documentation of a fix as the fix being absent.
  const code = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^.*?\/\/.*$/gm, (l) => l.split("//")[0]);
  for (const [file, src] of bridgeSources()) {
    assert.doesNotMatch(
      code(src), /launchMode\s*\|\|\s*["'][^"']*["']\s*\)\s*===/,
      `${file} still compares a raw launchMode — normalizeLaunchMode() exists for this`,
    );
  }
  assert.equal(isUsedInBridge("normalizeLaunchMode"), true, "…and the normaliser is actually used");
});
