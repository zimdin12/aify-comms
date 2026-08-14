// The claude-code runtime's pure decisions: permissions, the unlock command, and config resolution.
//
// SECOND PAYMENT ON THE UNTESTED BACKLOG. claude-code is the operator's primary runtime and none of this
// module's nine exports had a test. Two of them decide things that matter more than their size suggests:
//
//   * `managedClaudePermissionArgs` decides whether a run gets `--dangerously-skip-permissions`. A
//     MANAGED run gets it by default, which is the auto-approval behaviour already flagged as an operator
//     concern — so the escape hatches that turn it OFF are the part that must not regress silently.
//   * `buildManagedClaudeUnlockPowerShell` builds a PowerShell command from a session id and a list of
//     pids. Both come from disk markers. Its quoting and its integer filter are the only things standing
//     between a malformed marker and an injected command.
//
// Everything here is pure — no spawn, no network. The two exports that touch the filesystem
// (`claudeSessionTranscriptExists`, `staleClaudeAifyWrapperReason`) are exercised against a scratch dir.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  buildManagedClaudeUnlockPowerShell,
  claudeSessionTranscriptPath,
  isClaudeSessionInUseError,
  managedClaudeEffort,
  managedClaudeMaxTurns,
  managedClaudeModel,
  managedClaudePermissionArgs,
  staleClaudeAifyWrapperReason,
} from "../runtimes-claude.js";

const SCRATCH = mkdtempSync(path.join(os.tmpdir(), "aify-claude-"));
test.after(() => { try { rmSync(SCRATCH, { recursive: true, force: true }); } catch { /* best effort */ } });

// --- permissions -----------------------------------------------------------

const BYPASS = ["--dangerously-skip-permissions"];

test("a MANAGED run bypasses permissions by default — this is the auto-approval behaviour", () => {
  // Pinned as fact, not endorsed. `executionMode !== "resident"` is enough on its own, so every managed
  // run auto-approves unless something below turns it off. Anyone changing this should have to change a
  // test that says what it does.
  assert.deepEqual(managedClaudePermissionArgs({}, "managed"), BYPASS);
  assert.deepEqual(managedClaudePermissionArgs({}), BYPASS, "managed is also the default mode argument");
});

test("a RESIDENT run does NOT bypass unless it asks to", () => {
  // The operator's own live session. Silently disabling its prompts would remove the human from the loop.
  assert.deepEqual(managedClaudePermissionArgs({}, "resident"), []);
});

test("THE ESCAPE HATCHES: three ways to refuse the bypass, and they beat the managed default", () => {
  // Each of these must win over `executionMode !== "resident"`, or a managed agent cannot be run with
  // prompts on at all.
  assert.deepEqual(managedClaudePermissionArgs({ skipPermissions: false }, "managed"), []);
  assert.deepEqual(managedClaudePermissionArgs({ approvalPolicy: "ask" }, "managed"), []);
  assert.deepEqual(managedClaudePermissionArgs({ permissionMode: "default" }, "managed"), []);
});

test("the refusal is an EXPLICIT false — absent or truthy does not refuse", () => {
  // `skipPermissions === false`. A missing field must not read as "prompt me", or the managed default is
  // reversed by omission.
  for (const value of [undefined, null, 0, ""]) {
    assert.deepEqual(managedClaudePermissionArgs({ skipPermissions: value }, "managed"), BYPASS,
      `skipPermissions=${JSON.stringify(value)} must not count as a refusal`);
  }
});

test("'never' and 'full-auto' force the bypass even for a RESIDENT session", () => {
  for (const policy of ["never", "full-auto"]) {
    assert.deepEqual(managedClaudePermissionArgs({ approvalPolicy: policy }, "resident"), BYPASS, policy);
  }
});

test("policy matching is case- and whitespace-insensitive", () => {
  // These come from a config file written by hand. "Ask" failing to match "ask" would silently re-enable
  // auto-approval on a session the operator meant to gate.
  for (const policy of ["ASK", " Ask ", "Default", "  DEFAULT"]) {
    assert.deepEqual(managedClaudePermissionArgs({ approvalPolicy: policy }, "managed"), [],
      `${JSON.stringify(policy)} must be recognised as a refusal`);
  }
});

test("approvalPolicy takes precedence over permissionMode when both are set", () => {
  // `config.approvalPolicy || config.permissionMode`. Worth pinning because the two names are synonyms
  // from different config generations.
  assert.deepEqual(managedClaudePermissionArgs({ approvalPolicy: "ask", permissionMode: "never" }, "managed"), []);
});

// --- the unlock command ----------------------------------------------------

test("a session id is single-quoted and its quotes are DOUBLED — the injection guard", () => {
  // PowerShell escapes a single quote by doubling it. Without this, a marker containing `'` closes the
  // string and everything after it is executed.
  const ps = buildManagedClaudeUnlockPowerShell("abc'; Remove-Item C:\\ -Recurse; #");
  assert.match(ps, /\$sid = 'abc''; Remove-Item C:\\ -Recurse; #';/,
    "the quote must be doubled, leaving the payload inert inside the string");
  assert.ok(!/\$sid = 'abc'; Remove/.test(ps), "the string must not terminate early");
});

test("only positive integer pids reach the command", () => {
  // The pids come from marker files on disk. Anything else must be dropped rather than interpolated into
  // a `taskkill` argument.
  const ps = buildManagedClaudeUnlockPowerShell("sid", [123, "456", -1, 0, 1.5, NaN, null, "x; rm -rf /", undefined]);
  const literal = /\$markerPids = @\(([^)]*)\);/.exec(ps);
  assert.ok(literal, "the marker-pid literal must be present");
  assert.equal(literal[1], "123,456", "numeric strings are coerced; everything else is dropped");
  assert.ok(!ps.includes("rm -rf"), "no non-numeric payload may survive into the command");
});

test("no pids yields an empty PowerShell array, not an empty string", () => {
  // `@()` is a real empty array; `@` alone or a bare blank would be a syntax error and the unlock would
  // silently never run under SilentlyContinue.
  for (const value of [[], undefined, null, "not an array"]) {
    const ps = buildManagedClaudeUnlockPowerShell("sid", value);
    assert.match(ps, /\$markerPids = @\(\);/, `${JSON.stringify(value)} must produce @()`);
  }
});

test("the command refuses to kill its own process", () => {
  // `$targetPid -eq $ownPid`. Without it the unlock can terminate the shell running it, and the remaining
  // steps never happen.
  const ps = buildManagedClaudeUnlockPowerShell("sid", [42]);
  assert.match(ps, /\$ownPid = \$PID;/);
  assert.match(ps, /-eq \$ownPid\) \{ return \}/);
});

test("the kill helper does NOT use the read-only $pid automatic variable", () => {
  // A recorded bug: naming the parameter `$pid` throws "cannot overwrite variable pid", and under
  // SilentlyContinue the whole unlock becomes a silent no-op. The fix was `$targetPid`.
  const ps = buildManagedClaudeUnlockPowerShell("sid", [42]);
  assert.match(ps, /function Stop-AifyTree\(\$targetPid, \$reason\)/);
  assert.ok(!/function Stop-AifyTree\(\$pid[,)]/.test(ps), "$pid as a parameter re-breaks the unlock");
});

// --- config resolution -----------------------------------------------------

test("the model comes from the agent first, then config, then empty", () => {
  assert.equal(managedClaudeModel({ model: "opus" }, { model: "sonnet" }), "opus", "the agent wins");
  assert.equal(managedClaudeModel({}, { model: "sonnet" }), "sonnet");
  assert.equal(managedClaudeModel({}, {}), "", "no model is an empty string, not undefined");
  assert.equal(managedClaudeModel({ model: "  opus  " }, {}), "opus", "trimmed");
});

test("effort defaults to high", () => {
  assert.equal(managedClaudeEffort({}), "high");
  assert.equal(managedClaudeEffort({ effort: "low" }), "low");
});

test("maxTurns falls back to 50 for every unusable value, and is floored to an integer", () => {
  // A run that hits maxTurns stops mid-work, so a 0 or NaN slipping through would end every managed run
  // immediately.
  for (const value of [undefined, null, 0, -5, NaN, Infinity, "abc", {}]) {
    assert.equal(managedClaudeMaxTurns({ maxTurns: value }), 50, `maxTurns=${JSON.stringify(value)}`);
  }
  assert.equal(managedClaudeMaxTurns({ maxTurns: 12 }), 12);
  assert.equal(managedClaudeMaxTurns({ maxTurns: "7" }), 7, "numeric strings are accepted");
  assert.equal(managedClaudeMaxTurns({ maxTurns: 9.9 }), 9, "floored, not rounded");
});

// --- predicates ------------------------------------------------------------

test("the session-in-use error is recognised with and without an id", () => {
  // This drives a retry with a fresh session, so a missed match means the run fails instead of recovering.
  assert.equal(isClaudeSessionInUseError("Error: session id abc-123 is already in use"), true);
  assert.equal(isClaudeSessionInUseError("session ID IS ALREADY IN USE"), true, "case-insensitive");
  assert.equal(isClaudeSessionInUseError("session id is already in use"), true, "id is optional");
  assert.equal(isClaudeSessionInUseError("some other failure"), false);
  assert.equal(isClaudeSessionInUseError(undefined), false, "no text is not a match");
});

test("a transcript path is built under the user's claude projects directory", () => {
  const p = claudeSessionTranscriptPath("sess-1", "/work/proj");
  assert.match(p, /sess-1\.jsonl$/, "the session id names the transcript");
  assert.match(p, /projects/, "…under the projects directory");
});

test("the stale-wrapper check reports the flag it finds, and stays quiet otherwise", () => {
  // It reads a wrapper off disk and looks for a Claude flag that install.sh no longer writes. A false
  // positive here tells the operator to rerun install.sh for no reason.
  const stale = path.join(SCRATCH, "claude-aify-stale.cmd");
  writeFileSync(stale, "@echo off\nclaude --channels server:aify-comms-channel %*\n");
  assert.match(staleClaudeAifyWrapperReason(stale), /stale Claude --channels flag/);
  assert.match(staleClaudeAifyWrapperReason(stale), /rerun install\.sh/, "…and says what to do");

  const good = path.join(SCRATCH, "claude-aify-good.cmd");
  writeFileSync(good, "@echo off\nclaude --dangerously-load-development-channels %*\n");
  assert.equal(staleClaudeAifyWrapperReason(good), "", "a current wrapper is not reported");
});

test("a missing or empty wrapper path is not an error", () => {
  // Called on every launch, including when nothing is resolved yet.
  for (const value of ["", undefined, null, path.join(SCRATCH, "does-not-exist.cmd")]) {
    assert.equal(staleClaudeAifyWrapperReason(value), "", `${JSON.stringify(value)} must be quiet`);
  }
});

test("a directory in the wrapper position is skipped rather than read", () => {
  // `stat.isFile()`. Reading a directory throws, and the catch would swallow it — but the guard is what
  // makes that not depend on the catch.
  const dir = path.join(SCRATCH, "a-directory.cmd");
  mkdirSync(dir, { recursive: true });
  assert.equal(staleClaudeAifyWrapperReason(dir), "");
});
