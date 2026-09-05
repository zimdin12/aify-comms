#!/usr/bin/env node
// Seven exports of LIVE modules whose only naming test rode along in the v0.6.2 residue deletion.
//
// The deletion removed 62 files belonging to the retired environment-bridge tier. Several of those
// test files also happened to be the only ones naming exports of modules that SURVIVE, and
// `every-export-is-named-by-a-test` went red on exactly that — which is the gate doing its job: a
// deletion that quietly reduces coverage of code that stays is the expensive kind, because the tree
// still looks tested afterwards.
//
// The standard that gate sets is a test that CALLS the export, not one that mentions it. Each of
// these is called with the inputs that decide something.

import assert from "node:assert/strict";
import test from "node:test";

import { AIFY_COMMS_RECEIPT_TEXT, claudeAifyReceiptLine } from "../aify-console-markers.js";
import { forgetResolvedExecutables, inspectShebang } from "../runtimes-exec.js";
import {
  RUNTIME_SESSION_ENV_VARS,
  hostIsWsl,
  runtimeCommandWithoutResume,
  sessionEnvVarsForRuntime,
} from "../runtimes.js";

test("the receipt line IS the marker, so a reader cannot drift from the writer", () => {
  // Three live modules match on this text — `claude-channel-content.js`, `codex-session.js` and the
  // codex legacy controller. A line built by hand in any of them is a receipt nothing recognises.
  assert.equal(claudeAifyReceiptLine(), AIFY_COMMS_RECEIPT_TEXT);
  assert.equal(AIFY_COMMS_RECEIPT_TEXT, "aify-comms message received");
});

test("RUNTIME_SESSION_ENV_VARS names each runtime's session carriers, and is FROZEN", () => {
  // These are the variable names a wrapper must pass through for a session to survive a relaunch.
  // Frozen because a caller mutating the shared table would change what every other reader sees.
  assert.deepEqual(RUNTIME_SESSION_ENV_VARS["claude-code"], ["CLAUDE_SESSION_ID"]);
  assert.deepEqual(RUNTIME_SESSION_ENV_VARS.codex, ["CODEX_THREAD_ID"]);
  assert.ok(Object.isFrozen(RUNTIME_SESSION_ENV_VARS), "a shared table that can be mutated is not shared");
});

test("hostIsWsl reads the kernel rather than guessing from the platform", () => {
  // Injected reader, so this runs the real decision on every host. WSL matters because a Linux
  // release string is how a Windows host running a Linux userland gives itself away, and paths
  // resolve differently on each side.
  assert.equal(hostIsWsl({ platform: "win32", readFile: () => "irrelevant" }), false,
    "only linux can be WSL");
  assert.equal(hostIsWsl({ platform: "linux", readFile: () => "5.15.0-microsoft-standard-WSL2" }), true);
  assert.equal(hostIsWsl({ platform: "linux", readFile: () => "6.1.0-generic" }), false);
  // A kernel that cannot be read is NOT WSL — a guess either way would change how paths resolve.
  assert.equal(hostIsWsl({ platform: "linux", readFile: () => { throw new Error("ENOENT"); } }), false);
});

test("inspectShebang answers for a file that is not there, rather than throwing", () => {
  // It is asked about launchers that may have been moved or never installed, and it runs inside a
  // capability probe: a throw there would make a whole runtime read as unavailable for the wrong
  // reason. On win32 it declines to answer at all, because shebangs mean nothing there.
  const answer = inspectShebang("C:/this/path/does/not/exist/launcher.sh");
  assert.equal(answer, null);
});

test("forgetResolvedExecutables empties the cache it owns", () => {
  // The resolver memoises where each runtime's binary is. This is what a test — or an operator who
  // has just installed a runtime — calls so the next lookup asks the filesystem again. Calling it
  // twice must be safe: the second call has nothing to clear.
  forgetResolvedExecutables();
  forgetResolvedExecutables();
  assert.ok(true, "clearing an already-empty cache threw");
});

test("a resume command can be UNDONE, which is what lets a dead session be restarted fresh", () => {
  // The other half of the resume round-trip. `resumeCommand(id)` builds it; this takes it back out,
  // so a session that cannot be resumed any more is restarted without one rather than being retried
  // for ever against an id the runtime has forgotten.
  // It takes and returns a COMMAND STRING — checked against the source rather than assumed; my
  // first version of this test asserted an array and failed for its own reason, not the code's.
  const without = runtimeCommandWithoutResume("claude-code", "claude --resume abc123 --model opus");
  assert.ok(!without.includes("abc123"), `the session id survived removal: ${without}`);
  assert.ok(without.includes("claude"), "it removed more than the resume");
  assert.ok(without.includes("--model opus"), "it took an unrelated flag with it");
  // A runtime with no resume shape hands the command back untouched rather than mangling it.
  assert.equal(runtimeCommandWithoutResume("no-such-runtime", "x --resume y"), "x --resume y");
});

test("sessionEnvVarsForRuntime answers for a runtime it has never heard of", () => {
  // Called while building a launch environment, so an unknown runtime must yield an empty list
  // rather than throwing — a new runtime should not be able to stop an existing one launching.
  assert.deepEqual(sessionEnvVarsForRuntime("claude-code"), ["CLAUDE_SESSION_ID"]);
  assert.deepEqual(sessionEnvVarsForRuntime("no-such-runtime"), []);
  assert.deepEqual(sessionEnvVarsForRuntime(undefined), []);
});
