// The command an operator pastes to bring an environment's bridge back.
//
// This string is the fix `aify-comms doctor` points at when it reports "no environment bridge is ONLINE —
// dashboard-managed spawns cannot run". It is copied by a human under time pressure into a shell on a host
// this code has never seen, so the two ways it fails are: the wrong `cd` idiom for that OS, and a workspace
// root with a space in it silently splitting into two arguments.

import assert from "node:assert/strict";
import { test } from "node:test";

import { environmentStartCommand } from "./environment-start-command.mjs";

const lines = (env) => environmentStartCommand(env).split("\n");

test("WINDOWS gets `cd /d`, which is the only form that changes drive as well as directory", () => {
  // `cd C:\x` from a D: prompt changes nothing and prints nothing. The operator would paste it, see no
  // error, and start a bridge in the wrong place.
  const [cd, run] = lines({ os: "windows", cwdRoots: ["C:/Docker"] });
  assert.equal(cd, "cd /d C:/Docker");
  assert.equal(run, "aify-comms");
});

test("mac and linux get their own idioms, and an unknown OS falls back to the WSL path", () => {
  assert.equal(lines({ os: "macos", cwdRoots: [] })[0], 'cd "$HOME"',
    "with no roots a mac lands in the home directory");
  assert.equal(lines({ os: "linux", cwdRoots: [] })[0], "cd /mnt/c/Docker",
    "linux with no roots uses the WSL mount, which is where this project lives");
  assert.equal(lines({})[0], "cd /mnt/c/Docker", "an environment with no os at all still yields a command");
});

test("`kind` is accepted when `os` is absent — the record has both spellings", () => {
  assert.equal(lines({ kind: "windows", cwdRoots: ["C:/x"] })[0], "cd /d C:/x");
  assert.equal(lines({ kind: "macos" })[0], 'cd "$HOME"');
});

test("A ROOT WITH A SPACE IS QUOTED — the failure that produces no error at all", () => {
  // `cd C:/Program Files/x` is two arguments. The shell reports nothing useful and the bridge starts
  // somewhere else, which then registers roots nobody asked for.
  const [cd] = lines({ os: "windows", cwdRoots: ["C:/Program Files/x"] });
  assert.equal(cd, 'cd /d "C:/Program Files/x"');
  const [posix] = lines({ os: "linux", cwdRoots: ["/srv/my projects"] });
  assert.equal(posix, 'cd "/srv/my projects"');
  // Quotes and backticks are hostile in a different way — a backtick would execute.
  assert.match(lines({ os: "linux", cwdRoots: ['/srv/`whoami`'] })[0], /"/,
    "a backtick-bearing root must be quoted, not pasted raw");
  // …and a plain root is NOT quoted, so the common case stays readable.
  assert.equal(lines({ os: "linux", cwdRoots: ["/srv/app"] })[0], "cd /srv/app");
});

test("the FIRST root is where it lands; the rest become arguments", () => {
  // The environment advertises an ordered list and the first is the default workspace. Passing them all to
  // `cd` would be wrong; dropping them would lose the roots the operator configured.
  const [cd, run] = lines({ os: "linux", cwdRoots: ["/a", "/b", "/c"] });
  assert.equal(cd, "cd /a");
  assert.equal(run, "aify-comms /b /c");
});

test("extra roots are quoted too", () => {
  const [, run] = lines({ os: "linux", cwdRoots: ["/a", "/two words"] });
  assert.equal(run, 'aify-comms "/two words"');
});

test("it reads roots through the shared field reader, so every spelling works", () => {
  // `environmentRoots` accepts cwdRoots / cwd_roots / roots / workspaceRoots. If this bypassed it, an
  // environment recorded by an older route would produce a command with no roots at all.
  for (const key of ["cwdRoots", "cwd_roots", "roots", "workspaceRoots"]) {
    assert.equal(lines({ os: "linux", [key]: ["/srv/x"] })[0], "cd /srv/x",
      `roots spelled ${key} must reach the command`);
  }
});

test("a junk or empty record still produces a runnable two-line command", () => {
  // It is rendered into the dashboard unconditionally. A throw here would blank the environment card, and
  // an empty string would leave the operator with nothing to copy while doctor tells them to copy it.
  for (const env of [undefined, null, {}, { cwdRoots: null }, { cwdRoots: ["", "  "] }, { os: 42 }]) {
    const out = environmentStartCommand(env ?? {});
    const parts = out.split("\n");
    assert.equal(parts.length, 2, `${JSON.stringify(env)} must still yield cd + command`);
    assert.match(parts[0], /^cd /, "…starting with a cd");
    assert.match(parts[1], /^aify-comms/, "…and running the launcher");
    assert.doesNotMatch(out, /undefined|null|NaN|\[object Object\]/, `leaked a placeholder: ${out}`);
  }
});

test("it names `aify-comms`, not a bare invocation of something else", () => {
  // The launcher name is the contract with the operator's PATH. `aify-doctor` is the older name for the
  // verifier and must never appear here — this line STARTS a bridge.
  const out = environmentStartCommand({ os: "linux", cwdRoots: ["/x"] });
  assert.match(out, /(?:^|\n)aify-comms\b/m);
  assert.doesNotMatch(out, /aify-doctor|--check/, "this starts a bridge; it is not the verifier");
});
