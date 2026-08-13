// The four runtime session modules each declare their own `createDeferred`, and one of them was wrong.
//
// A Deferred whose promise is rejected with NO awaiter attached becomes an unhandled rejection. In Node that
// is a warning by default and a process kill under `--unhandled-rejections=strict`, and these modules reject
// their Deferreds on ordinary paths — a session that fails to start, a turn that is cancelled. Three of the
// four attach a no-op `.catch` to absorb that case; `pi-session.js` did not, so a pi session failing to
// start could take the bridge's stderr — or the bridge — with it in a way the other three could not.
//
// THE GUARD IS NOT A SWALLOW. `promise.catch(() => {})` adds a handler to the promise; every real awaiter
// still attaches its own and still sees the rejection. That is asserted below rather than assumed, because
// "we swallow errors here" would be a much worse bug than the one being fixed.
//
// The four copies are NOT unified into one module. They are four runtimes' session managers and may
// legitimately diverge — `idleTimeoutFor` next door genuinely does, reading a different config key and env
// var per runtime, and that is correct rather than duplication. What must not diverge is this: whether a
// rejected Deferred can crash the process. So this is an agreement test, per the promotion rule, not a
// refactor.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// pi's copies moved to `pi-session-timeouts.mjs` in v0.5.4 — a pi-ONLY module, so they are still one
// per runtime and this agreement still has four participants. THE COST OF A LOCATION PIN: all five
// cases here went red on that move even though nothing about the agreement changed, because they
// assert where the code LIVES in order to compare copies that genuinely may differ. Kept, because
// comparing four implementations has no behavioural equivalent — but re-aimed by hand.
const MODULES = [
  "pi-session-timeouts.mjs",
  "codex-session.js",
  "hermes-session.js",
  "hermes-managed-gateway-session.js",
];

// Pull one module's `createDeferred` out by brace matching and evaluate it in isolation, so the assertion
// is about the real source rather than a re-typed copy of it.
function deferredSourceOf(file) {
  const lines = fs.readFileSync(path.join(STDIO, file), "utf-8").split("\n");
  const i = lines.findIndex((l) => /^(export )?function createDeferred\s*\(/.test(l));
  assert.notEqual(i, -1, `${file} must declare createDeferred`);
  let depth = 0; let started = false;
  for (let j = i; j < lines.length; j += 1) {
    for (const ch of lines[j]) {
      if (ch === "{") { depth += 1; started = true; } else if (ch === "}") depth -= 1;
    }
    if (started && depth === 0) return lines.slice(i, j + 1).join("\n").replace(/^export /, "");
  }
  throw new Error(`unterminated createDeferred in ${file}`);
}

// Run one module's implementation in a CHILD, reject its Deferred with nothing awaiting, and report whether
// the process saw an unhandled rejection. It has to be a child: an unhandled rejection is a process-level
// event, so observing it in-process would leak between cases.
function unhandledRejectionsFor(file) {
  const src = deferredSourceOf(file);
  const script = `
    ${src}
    const seen = [];
    process.on("unhandledRejection", (e) => { seen.push(String(e && e.message || e)); });
    const d = createDeferred();
    d.reject(new Error("no awaiter"));
    setTimeout(() => { process.stdout.write(JSON.stringify(seen)); }, 40);
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script],
    { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] }));
}

test("NO session module's Deferred can produce an unhandled rejection", () => {
  // The agreement. `pi-session.js` failed this until v0.5.4 — it was the only one of the four without the
  // guard, and the probe that found it is the same shape as this test.
  for (const file of MODULES) {
    assert.deepEqual(unhandledRejectionsFor(file), [],
      `${file}'s createDeferred leaves a rejection unhandled — a failing session can take the process down`);
  }
});

test("the guard does not swallow: a REAL awaiter still sees the rejection", () => {
  // The property that makes the fix safe. If adding `.catch(() => {})` hid errors from real callers it
  // would be a worse defect than the one it fixes.
  for (const file of MODULES) {
    const src = deferredSourceOf(file);
    const out = execFileSync(process.execPath, ["--input-type=module", "-e", `
      ${src}
      const d = createDeferred();
      let saw = "";
      d.promise.catch((e) => { saw = e.message; });
      d.reject(new Error("real awaiter"));
      setTimeout(() => { process.stdout.write(saw); }, 40);
    `], { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
    assert.equal(out, "real awaiter", `${file} hides rejections from real awaiters`);
  }
});

test("resolve still works in every copy", () => {
  // Anti-vacuity: a `createDeferred` that never settled would pass both tests above.
  for (const file of MODULES) {
    const src = deferredSourceOf(file);
    const out = execFileSync(process.execPath, ["--input-type=module", "-e", `
      ${src}
      const d = createDeferred();
      d.promise.then((v) => { process.stdout.write(String(v)); });
      d.resolve("ok");
    `], { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
    assert.equal(out, "ok", `${file}'s Deferred does not resolve`);
  }
});

test("every copy still carries the guard in its SOURCE", () => {
  // The behavioural tests above would also pass if someone replaced the body with something that never
  // rejects. This pins the mechanism the comments in three of the four files describe.
  for (const file of MODULES) {
    assert.match(deferredSourceOf(file), /\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)/,
      `${file} lost its no-op catch`);
  }
});

test("the timeout helpers beside it are NOT duplicates and must stay separate", () => {
  // Recorded so a future reader does not "unify" them. `idleTimeoutFor` and `startupTimeoutFor` exist in
  // three of these files with the same shape and DIFFERENT content: each reads its own runtime's config key
  // and env var. That is per-runtime policy, not duplication, and merging them would silently give one
  // runtime another's timeout.
  const keys = {
    "pi-session-timeouts.mjs": ["piIdleTimeoutMs", "AIFY_PI_IDLE_TIMEOUT_MS"],
    "codex-session.js": ["codexIdleTimeoutMs", "AIFY_CODEX_IDLE_TIMEOUT_MS"],
    "hermes-session.js": ["hermesIdleTimeoutMs", "AIFY_HERMES_IDLE_TIMEOUT_MS"],
  };
  for (const [file, [cfgKey, envKey]] of Object.entries(keys)) {
    const src = fs.readFileSync(path.join(STDIO, file), "utf-8");
    assert.ok(src.includes(cfgKey), `${file} must read its own config key ${cfgKey}`);
    assert.ok(src.includes(envKey), `${file} must read its own env var ${envKey}`);
  }
});
