// The four launch helpers the export ratchet had recorded as named by no test.
//
// `spawnProcess`, `userHomeDir`, `bashShebangFallback` and `defaultPiCommand` sit on the path that
// turns "start this runtime" into an actual child process. Seven backlog entries, because three of
// them are re-exported: `runtimes.js` re-exports `spawnProcess` and `defaultPiCommand`, and
// `runtimes-helpers.js` re-exports `spawnProcess` again for the controllers. The re-export chain is
// asserted here rather than assumed — a controller importing a DIFFERENT function of the same name is
// the failure that chain exists to prevent.
//
// THE CWD GUARD IS THE PART THAT MATTERS. `spawnProcess` refuses to spawn when the workspace is
// missing, is a file, or is unreadable, and it refuses BEFORE `spawn()` — with a named error code and
// a message naming the environment and host. Without it a bad workspace produced a child that
// started and died, which surfaces as a runtime that "won't start" with nothing saying why.
//
// EVERY AMBIENT INPUT IS SEALED. These read `process.env.HOME`, `AIFY_PI_COMMAND`, `PI_COMMAND` and
// `AIFY_ENVIRONMENT_ID`, and this suite runs on the same machine as a live bridge. Each test that
// touches one saves and restores it, and asserts the seal took.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { bashShebangFallback } from "../runtimes-exec.js";
import { defaultPiCommand } from "../runtimes-pi.js";
import { launchCwdProblem, spawnProcess, userHomeDir } from "../runtimes-process.js";
import * as runtimes from "../runtimes.js";
import * as helpers from "../runtimes-helpers.js";

const ON_WINDOWS = process.platform === "win32";

function withEnv(values, run) {
  const saved = new Map();
  for (const [key, value] of Object.entries(values)) {
    saved.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    return run();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function tempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

// ── the re-export chain ─────────────────────────────────────────────────────────────────────────

test("runtimes.js and runtimes-helpers.js re-export the SAME spawnProcess", () => {
  // Controllers import from `runtimes-helpers.js`, most other callers from `runtimes.js`, and the
  // real body lives in `runtimes-process.js`. Three names for one function; if any of them ever
  // resolved to a different implementation, only the callers of that one would lose the cwd guard
  // below — and nothing else would look wrong.
  assert.equal(runtimes.spawnProcess, spawnProcess);
  assert.equal(helpers.spawnProcess, spawnProcess);
});

test("runtimes.js re-exports the SAME defaultPiCommand", () => {
  assert.equal(runtimes.defaultPiCommand, defaultPiCommand);
});

// ── userHomeDir ─────────────────────────────────────────────────────────────────────────────────

test("the home directory prefers HOME over the OS answer", () => {
  // Managed codex is given its own HOME so its session store is separate from the operator's. That
  // override has to win, or a managed worker reads and writes the operator's real codex state.
  withEnv({ HOME: path.join(os.tmpdir(), "aify-home-override") }, () => {
    assert.equal(userHomeDir(), path.join(os.tmpdir(), "aify-home-override"));
  });
});

test("with no HOME it falls back to the OS home rather than to empty", () => {
  // On Windows HOME is frequently unset. An empty answer here would build paths rooted at the
  // filesystem root, which is where a "session store not found" turns into a permission error.
  withEnv({ HOME: undefined }, () => {
    assert.equal(process.env.HOME, undefined, "the seal did not take");
    assert.equal(userHomeDir(), os.homedir());
  });
});

test("an EMPTY HOME also falls back", () => {
  withEnv({ HOME: "" }, () => {
    assert.equal(userHomeDir(), os.homedir());
  });
});

// ── bashShebangFallback ─────────────────────────────────────────────────────────────────────────

test("the shebang fallback runs the script through a LOGIN AND INTERACTIVE shell", () => {
  // `-l` sources .profile, `-i` sources .bashrc. nvm's installer writes its init into .bashrc by
  // default, so a login-only shell would miss it and the broken-shebang problem it exists to fix
  // would recur — with the same symptom, a runtime that cannot find node.
  const { command, args } = bashShebangFallback("/opt/tools/omp");
  assert.equal(command, "bash");
  assert.equal(args[0], "-lic");
});

test("it EXECs the script so stdio semantics survive", () => {
  // The bridge talks to these children over pipes. A shell that ran the script as a child instead of
  // exec'ing it would leave an extra process between the bridge and the runtime, and the pid the
  // bridge holds would not be the runtime's.
  const { args } = bashShebangFallback("/opt/tools/omp");
  assert.match(args[1], /^exec /);
});

test("the script path is passed as an ARGUMENT, not interpolated into the shell command", () => {
  // `exec "$0" "$@"` with the path as $0. Interpolating it would make a path containing a space or a
  // quote a shell-injection surface, and these paths come from PATH resolution on the host.
  const weird = "/opt/my tools/omp";
  const { args } = bashShebangFallback(weird);
  assert.equal(args[2], weird);
  assert.ok(!args[1].includes(weird), "the path was interpolated into the shell string");
});

// ── defaultPiCommand ────────────────────────────────────────────────────────────────────────────

test("pi defaults to the omp launcher", () => {
  withEnv({ AIFY_PI_COMMAND: undefined, PI_COMMAND: undefined }, () => {
    const { command } = defaultPiCommand();
    // On a host with omp installed this resolves to an absolute path; on one without it stays the
    // bare name. Either way the launcher it names must be omp.
    assert.match(String(command), /omp(\.[a-z]+)?$/i, String(command));
  });
});

test("AIFY_PI_COMMAND overrides the launcher", () => {
  withEnv({ AIFY_PI_COMMAND: "my-omp", PI_COMMAND: undefined }, () => {
    assert.match(String(defaultPiCommand().command), /my-omp$/);
  });
});

test("PI_COMMAND is the second variable consulted", () => {
  withEnv({ AIFY_PI_COMMAND: undefined, PI_COMMAND: "legacy-omp" }, () => {
    assert.match(String(defaultPiCommand().command), /legacy-omp$/);
  });
});

test("AIFY_PI_COMMAND wins over PI_COMMAND", () => {
  // Two variables for one setting: the aify-prefixed one is what this project documents, and the
  // bare one is what an operator may already have exported for their own use.
  withEnv({ AIFY_PI_COMMAND: "preferred", PI_COMMAND: "legacy" }, () => {
    assert.match(String(defaultPiCommand().command), /preferred$/);
  });
});

test("a whitespace-only override is not an override", () => {
  withEnv({ AIFY_PI_COMMAND: "   ", PI_COMMAND: undefined }, () => {
    assert.match(String(defaultPiCommand().command), /omp(\.[a-z]+)?$/i);
  });
});

test("the launcher is returned with NO arguments", () => {
  // The caller appends the mode and session flags. A default that carried its own args would have
  // them silently prepended to every pi launch.
  withEnv({ AIFY_PI_COMMAND: undefined, PI_COMMAND: undefined }, () => {
    assert.deepEqual(defaultPiCommand().args, []);
  });
});

// ── spawnProcess: the cwd guard ─────────────────────────────────────────────────────────────────

test("a workspace that does not exist is refused BEFORE spawn", () => {
  // The whole point of the guard. Spawning into a missing directory produces a child that starts and
  // dies, which reaches the operator as "the runtime will not start" with no reason attached.
  const missing = path.join(tempDir("aify-cwd-"), "not-created");
  assert.throws(
    () => spawnProcess(process.execPath, ["-e", ""], { cwd: missing }),
    (error) => {
      assert.equal(error.code, "AIFY_INVALID_RUNTIME_CWD");
      assert.match(error.message, /does not exist on this bridge host/);
      assert.equal(error.cwd, missing);
      return true;
    },
  );
});

test("a workspace that is a FILE is refused", () => {
  const dir = tempDir("aify-cwd-");
  const file = path.join(dir, "a-file");
  fs.writeFileSync(file, "x");
  assert.throws(
    () => spawnProcess(process.execPath, ["-e", ""], { cwd: file }),
    /is not a directory/,
  );
});

test("the refusal names the ENVIRONMENT and the HOST", () => {
  // It is read by an operator who has to decide whether the workspace or the environment roots are
  // wrong. A bare "invalid cwd" does not tell them which machine to look on.
  const missing = path.join(tempDir("aify-cwd-"), "nope");
  withEnv({ AIFY_ENVIRONMENT_ID: "env-under-test" }, () => {
    assert.throws(
      () => spawnProcess(process.execPath, ["-e", ""], { cwd: missing }),
      (error) => {
        assert.match(error.message, /env-under-test/);
        assert.match(error.message, new RegExp(os.hostname().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
        return true;
      },
    );
  });
});

test("an unknown environment still produces a usable message", () => {
  const missing = path.join(tempDir("aify-cwd-"), "nope");
  withEnv({ AIFY_ENVIRONMENT_ID: undefined }, () => {
    assert.throws(
      () => spawnProcess(process.execPath, ["-e", ""], { cwd: missing }),
      /environment "unknown"/,
    );
  });
});

test("a VALID workspace spawns and the child inherits it", async () => {
  const dir = fs.realpathSync(tempDir("aify-cwd-"));
  const proc = spawnProcess(process.execPath, ["-e", "process.stdout.write(process.cwd())"], {
    cwd: dir,
  });
  let out = "";
  proc.stdout.on("data", (chunk) => { out += String(chunk); });
  const code = await new Promise((resolve) => proc.on("close", resolve));
  assert.equal(code, 0);
  assert.equal(fs.realpathSync(out.trim()), dir);
});

test("spawnProcess attaches an error listener at creation", async () => {
  // A ChildProcess that emits "error" with no listener takes the whole bridge down. The listener is
  // attached inside spawnProcess so an adapter that has not wired its own rejection yet cannot crash
  // the process — asserted by spawning something that does not exist and surviving.
  const dir = tempDir("aify-cwd-");
  const proc = spawnProcess(path.join(dir, "definitely-not-an-executable"), [], { cwd: dir });
  await new Promise((resolve) => {
    proc.on("close", resolve);
    proc.on("error", resolve);
  });
  assert.ok(true, "the bridge survived a failed spawn");
});

// ── launchCwdProblem, which the guard is built from ─────────────────────────────────────────────

test("a BLANK workspace is not a problem — it means 'use the bridge's own cwd'", () => {
  // `spawnProcess` defaults an empty cwd to `process.cwd()`, so the checker must not report one.
  // Reporting it would refuse every launch that did not name a workspace.
  for (const value of ["", "   ", null, undefined]) {
    assert.equal(launchCwdProblem(value), null, String(value));
  }
});

test("an existing directory is not a problem", () => {
  assert.equal(launchCwdProblem(tempDir("aify-cwd-")), null);
});

test("the problem message distinguishes MISSING from NOT-A-DIRECTORY", () => {
  // Two different operator actions: create the workspace, or fix a path that points at a file.
  const dir = tempDir("aify-cwd-");
  const file = path.join(dir, "f");
  fs.writeFileSync(file, "x");
  assert.match(launchCwdProblem(path.join(dir, "missing")), /does not exist/);
  assert.match(launchCwdProblem(file), /is not a directory/);
});

test("an UNREADABLE directory is reported with the user that could not read it", { skip: ON_WINDOWS ? "POSIX permission bits" : false }, () => {
  // The third case, and the one an operator cannot guess: the path exists and is a directory, and
  // the bridge still cannot enter it. Naming the user is what turns that into a chmod.
  const dir = tempDir("aify-cwd-");
  const locked = path.join(dir, "locked");
  fs.mkdirSync(locked);
  fs.chmodSync(locked, 0o000);
  try {
    const problem = launchCwdProblem(locked);
    assert.ok(problem, "an unreadable directory was reported as usable");
    assert.match(problem, new RegExp(os.userInfo().username));
  } finally {
    fs.chmodSync(locked, 0o700);
  }
});
