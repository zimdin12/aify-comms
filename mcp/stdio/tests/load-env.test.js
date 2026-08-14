// Loading env vars out of settings.local.json.
//
// SEVENTH BACKLOG PAYMENT. Small, and worth care because of what it touches: settings.local.json is where
// operators keep credentials, and this function WRITES INTO process.env. Two properties carry the risk —
// an explicitly-set variable must never be overwritten, and the project file must win over the user file.
// Getting either backwards would silently change which credentials a bridge runs with.
//
// SEALING, and more of it than usual. The function reads `os.homedir()` and `CLAUDE_PROJECT_DIR` at CALL
// time, so both are pointed at scratch directories; and because its whole job is mutating `process.env`,
// every variable it could touch is captured and restored around each test. Test variable names are
// prefixed so a leak cannot collide with anything real, and the restore is asserted rather than assumed.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { loadSettingsEnv } from "../load-env.js";

const ROOT = mkdtempSync(path.join(os.tmpdir(), "aify-load-env-"));
test.after(() => { try { rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ } });

const V1 = "AIFY_TEST_LOADENV_ONE";
const V2 = "AIFY_TEST_LOADENV_TWO";
const SEALED = ["HOME", "USERPROFILE", "CLAUDE_PROJECT_DIR", V1, V2];

let seq = 0;

/** Write a settings.local.json under `dir/.claude/` and return dir. */
function settings(dir, env) {
  mkdirSync(path.join(dir, ".claude"), { recursive: true });
  writeFileSync(path.join(dir, ".claude", "settings.local.json"),
    typeof env === "string" ? env : JSON.stringify({ env }));
  return dir;
}

function scratch(name) {
  const dir = path.join(ROOT, `${name}-${seq += 1}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * Run `loadSettingsEnv()` with a sealed home, project dir and variable set, then restore everything.
 * Returns the values of the test variables as the function left them.
 */
function withSealedEnv({ home, project, preset = {} }, run = loadSettingsEnv) {
  const saved = new Map(SEALED.map((k) => [k, process.env[k]]));
  try {
    for (const k of SEALED) delete process.env[k];
    if (home) { process.env.HOME = home; process.env.USERPROFILE = home; }
    if (project) process.env.CLAUDE_PROJECT_DIR = project;
    Object.assign(process.env, preset);
    run();
    return { [V1]: process.env[V1], [V2]: process.env[V2] };
  } finally {
    for (const [k, v] of saved) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

test("the seal restores every variable it touches", () => {
  // Asserted first, because every test below depends on it. A leak here would silently change the
  // environment of the rest of the suite.
  const before = SEALED.map((k) => process.env[k]);
  withSealedEnv({ home: settings(scratch("home"), { [V1]: "leaked" }) });
  assert.deepEqual(SEALED.map((k) => process.env[k]), before, "process.env must be exactly as it was");
  assert.equal(process.env[V1], undefined, "and the test variable must not survive");
});

test("a user-level settings file fills in its variables", () => {
  const out = withSealedEnv({ home: settings(scratch("home"), { [V1]: "from-user" }) });
  assert.equal(out[V1], "from-user");
});

test("THE PROJECT FILE OVERRIDES THE USER FILE", () => {
  // The documented precedence. Reversed, a repo-local override would be silently ignored and the bridge
  // would run with the operator's global value instead.
  const out = withSealedEnv({
    home: settings(scratch("home"), { [V1]: "from-user", [V2]: "user-only" }),
    project: settings(scratch("proj"), { [V1]: "from-project" }),
  });
  assert.equal(out[V1], "from-project", "project wins where both define it");
  assert.equal(out[V2], "user-only", "…and the user file still contributes what the project omits");
});

test("AN ALREADY-SET VARIABLE IS NEVER OVERWRITTEN", () => {
  // The other half of the risk. An operator who exports a credential in their shell has chosen it
  // deliberately; a settings file must not quietly replace it.
  const out = withSealedEnv({
    home: settings(scratch("home"), { [V1]: "from-file" }),
    preset: { [V1]: "from-shell" },
  });
  assert.equal(out[V1], "from-shell");
});

test("an EMPTY value in the file is skipped rather than setting an empty variable", () => {
  // `if (!process.env[key] && value)`. An empty string is usually a mistake in the file, and setting it
  // makes the variable *defined but blank*, which reads as configured to everything downstream.
  const out = withSealedEnv({ home: settings(scratch("home"), { [V1]: "", [V2]: "kept" }) });
  assert.equal(out[V1], undefined, "an empty value must not define the variable");
  assert.equal(out[V2], "kept");
});

test("MALFORMED JSON is ignored, and the other file still loads", () => {
  // `readJsonSafe` returns null. A half-written settings file must not stop the bridge starting — and
  // must not prevent the valid file from being read.
  const out = withSealedEnv({
    home: settings(scratch("home"), '{"env": {"broken"'),
    project: settings(scratch("proj"), { [V1]: "still-loaded" }),
  });
  assert.equal(out[V1], "still-loaded");
});

test("missing files, missing env key, and wrong shapes are all no-ops", () => {
  // Every one of these is an ordinary state: a fresh machine, a settings file with only permissions in it,
  // or a hand-edited file with the wrong type.
  assert.doesNotThrow(() => withSealedEnv({ home: scratch("empty-home"), project: scratch("empty-proj") }));
  assert.doesNotThrow(() => withSealedEnv({ home: settings(scratch("home"), undefined) }));
  for (const body of ['{"permissions": {}}', '{"env": null}', '{"env": "not an object"}', "[]", "null"]) {
    assert.doesNotThrow(() => withSealedEnv({ home: settings(scratch("home"), body) }), body);
  }
});

test("CLAUDE_PROJECT_DIR chooses the project file; without it the cwd is used", () => {
  // The bridge is launched from the agent's workspace, so the fallback is what makes a repo-local
  // settings file work at all when the variable is not set.
  const proj = settings(scratch("proj"), { [V1]: "via-env-var" });
  assert.equal(withSealedEnv({ home: scratch("home"), project: proj })[V1], "via-env-var");

  const cwdProj = settings(scratch("cwd-proj"), { [V1]: "via-cwd" });
  const originalCwd = process.cwd();
  try {
    process.chdir(cwdProj);
    assert.equal(withSealedEnv({ home: scratch("home") })[V1], "via-cwd");
  } finally {
    process.chdir(originalCwd);
  }
});

test("it returns nothing and communicates only through process.env", () => {
  // Worth pinning: a caller that expected a value back would silently get undefined and configure nothing.
  const home = settings(scratch("home"), { [V1]: "x" });
  let result;
  withSealedEnv({ home }, () => { result = loadSettingsEnv(); });
  assert.equal(result, undefined);
});
