// The install-time OpenAI quota verdict, and why it is worded the way it is.
//
// `usage-preflight.js` was one of the two modules `every-module-is-imported-by-a-test.test.js` recorded
// as untestable: a bare script whose top level did the work, so importing it ran a LIVE quota check
// against the operator's own credentials. That gate's note said the answer was "an exported entry point
// or an end-to-end harness — a change to the module rather than to this list". This is the test for
// that change, and the change is what makes the test possible: `runUsagePreflight` takes its check and
// its logger, and the script tail runs only when the file is the process entry point.
//
// WHAT IT IS FOR. The ChatGPT usage pool fails SILENTLY by design — no token, fall back to a codex
// rollout, render a stale number — so the dashboard's quota panel can be dead for weeks and look fine.
// It was. This is the one place that says so during an install, which makes the WORDING the behaviour:
// a verdict an operator cannot act on is the same as no verdict.
//
// THE MOST IMPORTANT ASSERTION IS THE LAST LINE OF THE FAILURE CASE. A WARNING printed mid-install
// reads as "the install is broken"; the closing sentence is the only thing that says it is not.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  preflightErrorVerdict,
  runUsagePreflight,
  usagePreflightLines,
} from "../usage-preflight.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const OK = { ok: true, code: "ok", message: "ChatGPT usage reachable (Pro, 12% used)." };
const BAD = {
  ok: false,
  code: "no-token",
  message: "No ChatGPT auth token found.",
  detail: "Looked in ~/.codex/auth.json.",
};

function collect(verdict, options) {
  return usagePreflightLines(verdict, options).join("\n");
}

// ── importing it must not perform a quota check ──────────────────────────────────────────────────

test("IMPORTING the module prints nothing and performs no check", async () => {
  // The property the whole split exists for, and it has to be observed from a CHILD: this test file
  // already imported the module at its top, so nothing in-process can witness the import's effects.
  // Before the split, importing ran the quota check against the developer's real credentials and
  // printed the verdict — my first version of this test asserted a counter nothing incremented, which
  // is true of every implementation including the broken one.
  const child = spawnSync(process.execPath,
    ["-e", "import('../usage-preflight.js').then(m => { if (!m.runUsagePreflight) process.exit(3); })"],
    { cwd: HERE, encoding: "utf-8", timeout: 20_000 });
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stdout, "", `importing printed: ${JSON.stringify(child.stdout)}`);
  assert.ok(!/\[usage\]/.test(child.stdout + child.stderr), "a verdict was rendered on import");
});

test("the module still declares itself an executable script", () => {
  // `install.sh` invokes it as `node usage-preflight.js`, so the entry-point tail must stay. A split
  // that exported the functions and dropped the tail would leave the install step silently printing
  // nothing at all.
  const source = readFileSync(path.join(HERE, "..", "usage-preflight.js"), "utf-8");
  assert.match(source, /^#!\/usr\/bin\/env node/);
  assert.match(source, /if \(isEntryPoint\(\)\)/);
});

// ── the operator-facing rendering ────────────────────────────────────────────────────────────────

test("a GOOD verdict is one quiet line", () => {
  // It runs inside a long install log. Success has to be scannable and must not push anything else
  // off the screen.
  assert.deepEqual(usagePreflightLines(OK), ["  [usage] OK — ChatGPT usage reachable (Pro, 12% used)."]);
});

test("a BAD verdict says WARNING and names the cause", () => {
  const text = collect(BAD);
  assert.match(text, /WARNING/);
  assert.match(text, /No ChatGPT auth token found\./);
  assert.match(text, /Looked in ~\/\.codex\/auth\.json\./);
});

test("a BAD verdict says the install is otherwise FINE", () => {
  // The sentence that stops a warning reading as a failed install. Everything else about the fleet
  // works without this pool; only one dashboard panel is affected.
  assert.match(collect(BAD), /Everything else works; only the OpenAI quota panel is affected\./);
});

test("a bad verdict with NO detail omits the detail line rather than printing an empty one", () => {
  const lines = usagePreflightLines({ ok: false, message: "Something is off." });
  assert.ok(!lines.some((line) => line.trim() === "[usage]"), lines.join("|"));
  assert.match(lines.join("\n"), /Something is off\./);
});

test("the warning is padded with blank lines and the OK line is not", () => {
  // Deliberate asymmetry: the warning has to break out of a wall of install output, and success must
  // not cost three lines to say nothing happened.
  const warning = usagePreflightLines(BAD);
  assert.equal(warning[0], "");
  assert.equal(warning[warning.length - 1], "");
  assert.equal(usagePreflightLines(OK).length, 1);
});

test("--json prints ONE machine-readable line and no prose", () => {
  // An installing AGENT parses this. Prose mixed into the JSON line would make it unparseable, and
  // a second line would make it ambiguous.
  const lines = usagePreflightLines(BAD, { json: true });
  assert.equal(lines.length, 1);
  assert.deepEqual(JSON.parse(lines[0]), BAD);
});

test("--json carries the same four fields for a GOOD verdict", () => {
  const parsed = JSON.parse(usagePreflightLines(OK, { json: true })[0]);
  assert.equal(parsed.ok, true);
  assert.equal(parsed.message, OK.message);
});

test("a MISSING verdict renders as a warning rather than throwing", () => {
  // It is called with whatever the check returned. `undefined` from a check that resolved with
  // nothing must still produce output — an install step that prints nothing looks like it was skipped.
  for (const verdict of [undefined, null, {}]) {
    const text = collect(verdict);
    assert.match(text, /WARNING/, String(verdict));
  }
});

// ── the error verdict ───────────────────────────────────────────────────────────────────────────

test("a THROWN check becomes a verdict, not an exception", () => {
  // Always exits 0 is the contract: a quota check that crashes must not fail an install.
  const verdict = preflightErrorVerdict(new Error("socket hang up"));
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "error");
  assert.match(verdict.detail, /socket hang up/);
});

test("a non-Error rejection still produces a readable detail", () => {
  // `String(err)` on a plain object gives "[object Object]"; the message field is preferred when there
  // is one, and either way the detail is a string a human can read.
  assert.match(preflightErrorVerdict("just a string").detail, /just a string/);
  assert.match(preflightErrorVerdict({ message: "from an object" }).detail, /from an object/);
});

// ── the entry point ─────────────────────────────────────────────────────────────────────────────

test("runUsagePreflight logs the verdict its check returned", async () => {
  const logged = [];
  const verdict = await runUsagePreflight({
    argv: [], check: async () => OK, log: (line) => logged.push(line),
  });
  assert.equal(verdict, OK);
  assert.deepEqual(logged, ["  [usage] OK — ChatGPT usage reachable (Pro, 12% used)."]);
});

test("it passes --json through from the argv it was given", async () => {
  const logged = [];
  await runUsagePreflight({
    argv: ["node", "usage-preflight.js", "--json"], check: async () => OK,
    log: (line) => logged.push(line),
  });
  assert.equal(logged.length, 1);
  assert.equal(JSON.parse(logged[0]).ok, true);
});

test("a check that REJECTS still logs a warning and resolves", async () => {
  // The whole path an install depends on: no throw out of here, and something on stdout either way.
  const logged = [];
  const verdict = await runUsagePreflight({
    argv: [], check: async () => { throw new Error("no network"); },
    log: (line) => logged.push(line),
  });
  assert.equal(verdict.ok, false);
  assert.match(logged.join("\n"), /WARNING/);
  assert.match(logged.join("\n"), /no network/);
});

test("a check that throws SYNCHRONOUSLY is caught too", async () => {
  // `checkOpenAiUsageAccess` is async today, but the entry point must not depend on that — a
  // synchronous throw before the first await would otherwise escape and fail the install step.
  const logged = [];
  const verdict = await runUsagePreflight({
    argv: [], check: () => { throw new Error("sync boom"); },
    log: (line) => logged.push(line),
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /sync boom/);
});
