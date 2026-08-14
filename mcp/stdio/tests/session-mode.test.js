// Which of the two session modes an arbitrary input becomes.
//
// A bridge-managed agent is either RESIDENT — launched and owned by a human — or MANAGED, spawned by the
// environment bridge and eligible to be stopped, restarted or reaped. Sixteen call sites in `server.js`
// turn lifecycle decisions on the answer, and none of it was reachable from a test: server.js is the bin
// entry point and nothing imports it.

import assert from "node:assert/strict";
import test from "node:test";
import { isUsedInBridge } from "./bridge-sources.mjs";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { normalizeSessionMode } from "../session-mode.mjs";

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
