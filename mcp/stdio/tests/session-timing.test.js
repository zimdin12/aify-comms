// The Deferred and the timeout reading the runtime session modules share, called.
//
// These used to be four copies of `createDeferred` and three of each timeout reader, pinned equal by
// comparing their source. One copy is proved by exercising it instead.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createDeferred, positiveMsFrom } from "../session-timing.mjs";

const MODULE = pathToFileURL(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "session-timing.mjs")).href;

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

test("a rejected Deferred with NO awaiter is not an unhandled rejection, even under strict mode", () => {
  // Observed in a CHILD run with --unhandled-rejections=strict, where an unhandled rejection kills
  // the process: the modules that use this reject Deferreds on ordinary paths (a session that fails
  // to start, a turn that is cancelled), and one copy once lacked the guard.
  const out = execFileSync(process.execPath, [
    "--unhandled-rejections=strict", "--input-type=module", "-e",
    `import { createDeferred } from ${JSON.stringify(MODULE)};
     createDeferred().reject(new Error("nobody is listening"));
     setTimeout(() => process.stdout.write("survived"), 40);`,
  ], { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
  assert.equal(out, "survived");
});

test("each Deferred is independent", () => {
  // Anti-vacuity: a `createDeferred` returning one shared promise would satisfy every case above.
  const a = createDeferred();
  const b = createDeferred();
  assert.notEqual(a.promise, b.promise);
  assert.notEqual(a.resolve, b.resolve);
});

test("positiveMsFrom prefers the configured value, then the environment, then the default", () => {
  assert.equal(positiveMsFrom(9000, "5000", 1), 9000, "an operator's per-agent value wins");
  assert.equal(positiveMsFrom(undefined, "5000", 1), 5000);
  assert.equal(positiveMsFrom(undefined, undefined, 1), 1);
});

test("positiveMsFrom skips every non-positive and non-finite value", () => {
  // Each of these would otherwise reap a live child at once, or never.
  for (const bad of [0, -1, "", "abc", NaN, Infinity, -Infinity, null, "0", "-5"]) {
    assert.equal(positiveMsFrom(bad, bad, 7), 7, `${JSON.stringify(bad)} must fall through to the default`);
  }
});
