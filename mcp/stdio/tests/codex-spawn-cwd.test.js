#!/usr/bin/env node
// The cwd a Codex thread is CREATED with, and the cwd its requests are SENT with.
//
// `codex-cwd-transform.test.js` next door proves the routing decision — that
// `resolveCodexRequestCwdFor` skips the launcher transform when `appServerUrl` is set — but it
// injects a STAND-IN transform (it says so: "For the test we stub it"). So the real transform,
// `codexWorkingPath`, and the real wiring, `resolveCodexRequestCwd`, were never called by a test.
// Neither was `codexSpawnCwd`.
//
// Both matter, and their failures arrive late:
//
//   * a request cwd in the wrong shape is rejected by Rust's AbsolutePathBuf ("deserialized without
//     a base path") and every resident dispatch on Windows fails;
//   * a spawn cwd in the wrong shape aborts the launch with AIFY_INVALID_RUNTIME_CWD.
//
// `codexSpawnCwd` also carries a fix with no test behind it. Bughunt 2026-07-03: the old ternary
// always composed HOMEDRIVE+HOMEPATH and ignored USERPROFILE even when set, so a roaming or
// mapped-drive profile yielded an inaccessible `H:\...` and killed the launch. Nothing pinned the
// fix, so the regression would have been silent.
//
// TWO THINGS THIS FILE IS CAREFUL ABOUT:
//
// 1. ENV IS SEALED. `codexSpawnCwd` reads USERPROFILE / HOMEDRIVE / HOMEPATH. Run naively on a
//    developer's Windows box it reads THEIR profile and asserts nothing repeatable — this is the
//    ambient-input trap that has bitten this repo before. Every case here sets the variables it
//    depends on, and the seal is asserted, not assumed.
//
// 2. THE WSL BRANCH CANNOT RUN OFF WINDOWS, AND SAYS SO. `isWslCodexLauncher` returns false unless
//    `process.platform === "win32"`, so on Linux the WSL transform and the whole env-precedence
//    path are unreachable. Those assertions are gated, and the file PRINTS that they did not run.
//    A gate that silently no-ops on the other platform is a green tick for evidence nobody
//    gathered.

import assert from "node:assert/strict";

import { codexSpawnCwd, resolveCodexRequestCwd } from "../runtimes-codex.js";

const WIN_CWD = "C:\\Docker\\sample-project";
const PLAIN = { command: "codex" };          // never a WSL launcher, on any platform
const WSL = { command: "wsl.exe" };          // a WSL launcher ONLY on win32
const ON_WINDOWS = process.platform === "win32";

/** Run `fn` with exactly these env vars set and everything restored afterwards. */
function withEnv(vars, fn) {
  const saved = new Map();
  for (const key of ["USERPROFILE", "HOMEDRIVE", "HOMEPATH"]) {
    saved.set(key, process.env[key]);
    delete process.env[key];
  }
  try {
    for (const [key, value] of Object.entries(vars)) process.env[key] = value;
    // The seal is ASSERTED: if a var this test does not set is still present, the case below would
    // be reading the machine rather than the fixture.
    for (const key of ["USERPROFILE", "HOMEDRIVE", "HOMEPATH"]) {
      if (!(key in vars)) {
        assert.equal(process.env[key], undefined, `${key} leaked into a sealed case`);
      }
    }
    return fn();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

// ── request cwd: the AbsolutePathBuf shape ───────────────────────────────────────────────────
// Portable: a non-WSL launcher takes the same branch on every platform.
{
  assert.equal(
    resolveCodexRequestCwd({ hostCwd: WIN_CWD, launcher: PLAIN, appServerUrl: "" }),
    "C:/Docker/sample-project",
    "backslashes must be normalised to forward slashes — Codex's Rust deserializer rejects "
      + "backslash paths with 'AbsolutePathBuf deserialized without a base path'",
  );
  assert.equal(
    resolveCodexRequestCwd({ hostCwd: "/home/dev/proj", launcher: PLAIN, appServerUrl: "" }),
    "/home/dev/proj",
    "a posix path must pass through unchanged",
  );
  assert.equal(
    resolveCodexRequestCwd({ hostCwd: "", launcher: PLAIN, appServerUrl: "" }), "",
    "an empty cwd must stay empty rather than becoming a bare separator",
  );
}

// ── spawn cwd: passthrough when the launcher is not WSL ──────────────────────────────────────
{
  assert.equal(
    codexSpawnCwd(PLAIN, WIN_CWD), WIN_CWD,
    "a non-WSL launcher spawns in the host cwd verbatim — backslashes included, because this is a "
      + "Windows process argument and not a JSON-RPC payload",
  );
  assert.equal(codexSpawnCwd(PLAIN, "/home/dev/proj"), "/home/dev/proj");
}

// ── the WSL branch, on Windows only ──────────────────────────────────────────────────────────
let wslChecks = 0;
if (ON_WINDOWS) {
  assert.equal(
    resolveCodexRequestCwd({ hostCwd: WIN_CWD, launcher: WSL, appServerUrl: "" }),
    "/mnt/c/Docker/sample-project",
    "spawning our own WSL-hosted Codex means the path must be translated for the WSL filesystem",
  );
  wslChecks++;

  assert.equal(
    resolveCodexRequestCwd({ hostCwd: WIN_CWD, launcher: WSL, appServerUrl: "ws://127.0.0.1:55555" }),
    "C:/Docker/sample-project",
    "an EXISTING app-server is native to the host, so the WSL transform must be skipped — this is "
      + "the guard codex-cwd-transform.test.js proves, re-checked here through the real transform",
  );
  wslChecks++;

  // The 2026-07-03 precedence fix, under a sealed env.
  assert.equal(
    withEnv({ USERPROFILE: "C:\\Users\\real", HOMEDRIVE: "H:", HOMEPATH: "\\mapped" },
      () => codexSpawnCwd(WSL, WIN_CWD)),
    "C:\\Users\\real",
    "USERPROFILE must win. The old ternary always composed HOMEDRIVE+HOMEPATH, so a roaming or "
      + "mapped-drive profile produced an inaccessible H:\\ and aborted the launch with "
      + "AIFY_INVALID_RUNTIME_CWD",
  );
  wslChecks++;

  assert.equal(
    withEnv({ HOMEDRIVE: "H:", HOMEPATH: "\\mapped" }, () => codexSpawnCwd(WSL, WIN_CWD)),
    "H:\\mapped",
    "with no USERPROFILE the composed home is the fallback, not a hardcoded drive",
  );
  wslChecks++;

  assert.equal(
    withEnv({}, () => codexSpawnCwd(WSL, WIN_CWD)), "C:\\",
    "with no profile variables at all the launch still needs a valid cwd",
  );
  wslChecks++;

  assert.equal(
    withEnv({ HOMEDRIVE: "H:" }, () => codexSpawnCwd(WSL, WIN_CWD)), "C:\\",
    "HOMEDRIVE without HOMEPATH is a half-set profile and must not compose to 'H:undefined'",
  );
  wslChecks++;

  assert.equal(wslChecks, 6, "a WSL assertion was skipped without being noticed");
} else {
  console.log(
    "codex-spawn-cwd.test.js: NOT VERIFIED HERE — isWslCodexLauncher() is win32-only, so the WSL "
      + "transform and the USERPROFILE/HOMEDRIVE precedence fix cannot be exercised on "
      + `${process.platform}. The portable assertions above did run.`,
  );
}

// ── the seal is real ─────────────────────────────────────────────────────────────────────────
{
  // withEnv must leave the process exactly as it found it, or later tests in the same runner
  // inherit a fabricated profile.
  const before = ["USERPROFILE", "HOMEDRIVE", "HOMEPATH"].map((k) => process.env[k]);
  withEnv({ USERPROFILE: "C:\\Users\\temp" }, () => codexSpawnCwd(PLAIN, WIN_CWD));
  const after = ["USERPROFILE", "HOMEDRIVE", "HOMEPATH"].map((k) => process.env[k]);
  assert.deepEqual(after, before, "withEnv leaked: the real environment was not restored");
}

console.log(
  `codex-spawn-cwd.test.js: all assertions passed (${wslChecks} WSL-specific, `
    + `${ON_WINDOWS ? "win32" : process.platform})`,
);
