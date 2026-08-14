// Resolving which hermes binary to launch.
//
// EIGHTH BACKLOG PAYMENT, and the last module on that list that can be import-tested — the two remaining
// entries have zero exports and are scripts.
//
// THE PROBE PATHS EXIST BECAUSE OF A REAL FAILURE, recorded in the module's own header: the upstream
// installer drops hermes into a per-user venv that lands only on the USER PATH, which a child process
// inherits at spawn time. A bridge launched from a shell that predates the install never sees it, so the
// resolver probes absolute locations rather than telling the operator to restart their shell. That
// fallback is the part worth guarding: if it silently stopped probing, hermes would appear "not
// installed" on a machine where it plainly is.
//
// PLATFORM. `defaultHermesCommand` has two branches and only the current platform's can execute here.
// This suite covers the one it runs on and says which that is, rather than pretending to cover both — the
// probe-path helper is exercised for both shapes indirectly through the env it reads.

import assert from "node:assert/strict";
import test from "node:test";
import { chmodSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { defaultHermesCommand } from "../runtimes-hermes.js";

const IS_WIN = process.platform === "win32";
const ROOT = mkdtempSync(path.join(os.tmpdir(), "aify-hermes-"));
test.after(() => { try { rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ } });

// PATH IS SEALED TOO, and that is what makes the probe branch reachable. On win32 the resolver returns
// early if `hasExecutable("hermes")` is true, which it is on any machine with hermes installed — so
// without emptying PATH the probe test below asserts nothing. Found by instrumenting a copy of the
// module after the standalone probe kept returning the real binary: this environment also has
// AIFY_HERMES_COMMAND set, so `configured` won before the branch was even entered.
const SEALED = ["AIFY_HERMES_COMMAND", "HERMES_COMMAND", "USERPROFILE", "HOME", "PATH", "Path"];

/** Run with a sealed env, restoring every variable afterwards. */
function withEnv(vars, run) {
  const saved = new Map(SEALED.map((k) => [k, process.env[k]]));
  try {
    for (const k of SEALED) delete process.env[k];
    Object.assign(process.env, vars);
    return run();
  } finally {
    for (const [k, v] of saved) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

let seq = 0;
function scratchHome() {
  const dir = path.join(ROOT, `home-${seq += 1}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** Create the installer-shaped hermes binary under a fake home, and return that home. */
function withInstalledHermes() {
  const home = scratchHome();
  const rel = IS_WIN
    ? ["AppData", "Local", "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe"]
    : [".local", "bin", "hermes"];
  const full = path.join(home, ...rel);
  mkdirSync(path.dirname(full), { recursive: true });
  writeFileSync(full, IS_WIN ? "MZ" : "#!/bin/sh\nexit 0\n");
  if (!IS_WIN) chmodSync(full, 0o755);
  return { home, full };
}

test("the seal restores every variable it touches", () => {
  // Asserted first: these are PATH-adjacent variables, and leaking one would change how later tests — and
  // anything else in this process — resolve executables.
  const before = SEALED.map((k) => process.env[k]);
  withEnv({ AIFY_HERMES_COMMAND: "leaked" }, defaultHermesCommand);
  assert.deepEqual(SEALED.map((k) => process.env[k]), before);
});

test("an explicitly configured command WINS over everything, and takes no args", () => {
  // The operator's own override. It must beat both the PATH lookup and the probe paths, or a deliberate
  // choice of a specific hermes build is silently ignored.
  const { home } = withInstalledHermes();
  const env = IS_WIN ? { USERPROFILE: home } : { HOME: home };
  const result = withEnv({ ...env, AIFY_HERMES_COMMAND: "/custom/hermes" }, defaultHermesCommand);
  assert.equal(result.command, "/custom/hermes");
  assert.deepEqual(result.args, [], "the resolver contributes no arguments of its own");
});

test("AIFY_HERMES_COMMAND takes precedence over the legacy HERMES_COMMAND", () => {
  // Two names for one setting. Reading them in the wrong order makes the aify-specific variable useless
  // on any machine that also has the generic one set.
  const result = withEnv(
    { AIFY_HERMES_COMMAND: "/aify/hermes", HERMES_COMMAND: "/legacy/hermes" },
    defaultHermesCommand,
  );
  assert.equal(result.command, "/aify/hermes");
});

test("the legacy variable is still honoured on its own", () => {
  assert.equal(withEnv({ HERMES_COMMAND: "/legacy/hermes" }, defaultHermesCommand).command, "/legacy/hermes");
});

test("a whitespace-only override is ignored rather than launched", () => {
  // `String(...).trim()`. An empty variable is a common shell accident, and launching "" would fail with
  // an unhelpful ENOENT instead of falling through to a working resolution.
  const result = withEnv({ AIFY_HERMES_COMMAND: "   " }, defaultHermesCommand);
  assert.notEqual(result.command.trim(), "", "must fall through, not resolve to blank");
});

test("with nothing configured and nothing installed, it still returns a usable shape", () => {
  // The caller spawns whatever comes back. Returning undefined or a bare string would break that contract
  // even in the not-installed case — the launch should fail at spawn with a clear ENOENT, not here.
  const result = withEnv(IS_WIN ? { USERPROFILE: scratchHome() } : { HOME: scratchHome() }, defaultHermesCommand);
  assert.equal(typeof result.command, "string");
  assert.ok(result.command.length > 0);
  assert.ok(Array.isArray(result.args));
});

test("THE PROBE FINDS AN INSTALLER-PLACED BINARY THAT IS NOT ON PATH", () => {
  // The failure the probe exists for: hermes installed into a per-user venv that the current process's
  // PATH never picked up. If this stopped working, hermes would look "not installed" on a machine where it
  // is, and the fix an operator would reach for is a shell restart — exactly the dance the probe avoids.
  //
  // PATH is emptied by the seal so the lookup genuinely fails and the probe is genuinely exercised. My
  // first version of this test guarded the assertion behind `if (result.command !== "hermes")`, which on
  // this machine was always false: it asserted nothing at all.
  const { home, full } = withInstalledHermes();
  const result = withEnv(IS_WIN ? { USERPROFILE: home } : { HOME: home }, defaultHermesCommand);
  assert.equal(result.command, full, "the probe must find the planted binary");
  assert.deepEqual(result.args, []);
});

test("a configured command still wins even when a probe path would match", () => {
  // Ordering: the operator's explicit choice is checked before the probe, so an installer-placed binary
  // cannot override a deliberate override.
  const { home } = withInstalledHermes();
  const env = IS_WIN ? { USERPROFILE: home } : { HOME: home };
  assert.equal(withEnv({ ...env, AIFY_HERMES_COMMAND: "/custom/hermes" }, defaultHermesCommand).command,
    "/custom/hermes");
});

test("no home directory means no probe paths, and no crash", () => {
  // USERPROFILE/HOME can be unset in a service context. `hermesProbePaths` returns [] and the resolver
  // still has to produce something spawnable.
  const result = withEnv({}, defaultHermesCommand);
  assert.equal(typeof result.command, "string");
  assert.ok(result.command.length > 0);
});

test("a directory sitting where the binary should be is not returned as the command", () => {
  // `fs.statSync(candidate).isFile()`. Spawning a directory fails with EACCES/EISDIR, which reads as a
  // permissions problem rather than a bad install.
  const home = scratchHome();
  const rel = IS_WIN
    ? ["AppData", "Local", "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe"]
    : [".local", "bin", "hermes"];
  mkdirSync(path.join(home, ...rel), { recursive: true });
  const result = withEnv(IS_WIN ? { USERPROFILE: home } : { HOME: home }, defaultHermesCommand);
  assert.notEqual(result.command, path.join(home, ...rel), "a directory must never be the resolved command");
});
