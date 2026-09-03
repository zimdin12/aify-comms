// `aify-comms doctor` threw ReferenceError on its first line of real work, and every test passed.
//
// THE INCIDENT, 2026-08-16. A deploy re-installed the bridge, and the verifier that exists to prove
// the deploy took crashed instead:
//
//     ReferenceError: SERVICE_RUNTIME_PATHS is not defined
//         at checkService (.../doctor.js:113:10)
//
// `doctor.js` used the name in a SPREAD (`...SERVICE_RUNTIME_PATHS`) and did not import it. The
// v0.5.4 dead-import sweep had removed it: that detector excluded a name preceded by `.` so `obj.x`
// would not look like a use of an imported `x`, and a spread's own dots made the name read as
// unused. The detector was fixed later — it handles spreads correctly today, verified — but the
// deletion it had already made stayed, because NOTHING CAUGHT IT:
//
//   * `node --check` only PARSES. An undefined name is a runtime error, not a syntax error.
//   * `doctor-predicates.js` is thoroughly tested, but the predicates are pure. The file that
//     COMPOSES them and does the I/O had no test that executed a single line of it.
//   * JavaScript has no equivalent of `scripts/undefined_name_sweep.py`, which is what catches this
//     class on the Python side.
//
// So this runs the real thing, as a process, the way an operator does. It would have failed on the
// commit that introduced the defect, and it is deliberately the cheapest possible check of the one
// property every other doctor test assumes: that the program RUNS.
//
// IT ASSERTS SHAPE, NEVER VERDICTS. Whether the service is up, a bridge is online or a token is
// valid depends on the machine this runs on; those are what doctor is for, not what it must report
// here. A check that demanded green would fail on any developer's laptop and be deleted within a
// week. What it demands is: the process completes, emits parseable JSON, and reports every check id
// the file declares.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { doctorSourceText } from "./doctor-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DOCTOR = path.join(STDIO, "doctor.js");

//: Every id `doctor.js` can `add(...)`, read from the file rather than retyped — a check that stops
//: being reported is exactly the kind of silent narrowing this repo keeps finding.
function declaredCheckIds() {
  const source = doctorSourceText();
  return [...new Set([...source.matchAll(/add\(\s*"([a-z-]+)"/g)].map((m) => m[1]))].sort();
}

function runDoctor() {
  // `--json` so the contract is data rather than console formatting. A non-zero exit is normal —
  // doctor exits 1 under `--strict`, and without it still returns a report for failing checks — so
  // stdout is what matters, not the code. A CRASH produces no stdout at all, which is the case
  // this test exists for.
  let stdout = "";
  try {
    stdout = execFileSync(process.execPath, [DOCTOR, "--json"], {
      cwd: STDIO,
      encoding: "utf-8",
      timeout: 120000,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    stdout = error?.stdout || "";
    // A ReferenceError leaves stderr full and stdout empty; surface it rather than failing on a
    // confusing JSON parse error three lines down.
    if (!stdout.trim()) {
      assert.fail(
        `doctor.js produced NO output — it crashed before reporting anything.\n${error?.stderr || error}`,
      );
    }
  }
  return JSON.parse(stdout);
}

test("doctor RUNS — it produces a parseable report instead of crashing", () => {
  const report = runDoctor();
  assert.equal(typeof report, "object", "the report must be an object");
  assert.ok(Array.isArray(report.checks), "the report must carry a checks array");
  assert.ok(report.checks.length > 0, "a report with no checks proves nothing ran");
  assert.equal(typeof report.ok, "boolean", "the top-level verdict must be a boolean");
});

test("every check the file declares is actually reported", () => {
  // The property that would have caught the incident even if the crash had been swallowed: a check
  // whose body throws must not simply vanish from the report.
  const report = runDoctor();
  const reported = [...new Set(report.checks.map((c) => c.id))].sort();
  assert.deepEqual(
    reported, declaredCheckIds(),
    "the ids doctor reports differ from the ids it declares — a check is missing from the run",
  );
});

test("each check reports the fields a caller reads", () => {
  // `aify-comms doctor --json` is documented as `{ok, checks:[{id, ok, code, detail, fix}]}` and is
  // consumed by scripted/agent checks, so the field set is a contract, not a formatting detail.
  const report = runDoctor();
  for (const check of report.checks) {
    assert.equal(typeof check.id, "string", `id missing on ${JSON.stringify(check)}`);
    assert.ok(check.id, "a check reported an empty id");
    assert.ok(
      typeof check.ok === "boolean" || check.ok === null,
      `${check.id}: ok must be a boolean, or null for a skipped check`,
    );
    assert.equal(typeof check.code, "string", `${check.id}: code must be a string`);
    assert.equal(typeof check.detail, "string", `${check.id}: detail must be a string`);
  }
});

test("a failing check carries a fix, because the report is what an operator acts on", () => {
  // Not "there must be a failure" — that depends on the machine. Only: IF one failed, it says what
  // to do. A red check with no remedy is the shape this tool exists to replace.
  //
  // A SKIP IS NOT A FAILURE and is excluded by name. Since D10 a skipped check reports `ok: false`
  // so that a consumer keying on `.ok` cannot read it as a pass -- which means `ok === false` alone
  // no longer means "failed", here or anywhere else. There is nothing to fix about a check that
  // could not run: `bridge-running` on Windows is "process inspection is Linux-only", and demanding
  // a remedy for it would be demanding a remedy for the operating system.
  const report = runDoctor();
  for (const check of report.checks.filter((c) => c.ok === false && !c.skipped)) {
    assert.ok(
      String(check.fix || "").trim(),
      `${check.id} failed with no fix — "${check.detail}"`,
    );
  }
});

test("the id scan reads the file, so it cannot silently agree with an empty run", () => {
  // Anti-vacuity for the comparison above: if `declaredCheckIds()` ever returned [], the equality
  // test would pass on a doctor that reported nothing at all.
  //
  // The named ids must be ones THIS tool still owns. `wrappers` was in this list and moved to
  // aify-wrapper in v0.6, which turned a control into a false alarm -- the scan was working
  // perfectly and the test said it was broken.
  const ids = declaredCheckIds();
  assert.ok(ids.length >= 8, `only ${ids.length} check ids found across the doctor sources`);
  for (const expected of ["service", "bridge-installed", "env-bridge", "skills-installed"]) {
    assert.ok(ids.includes(expected), `${expected} is no longer declared by any doctor source`);
  }
});
