#!/usr/bin/env node
// Tests that CALL `terminal-text.js` — the doctor-predicates.js standard, applied to the terminal
// text handling extracted from `terminal-runtime.js` in v0.5.4.
//
// FIVE OF THE EIGHT HAD NO TEST AT ALL. `classifyTerminalRuntimeOutput` and
// `terminalCommandWithoutResume` were exported and covered by three assertions between them;
// `appendTail`, `compactTerminalText`, `hermesResumeStillPending`, `hermesResumeStallHealMs` and
// `terminalEnvWithoutResume` were module-private and therefore unreachable from a test at all. They
// are the parts that decide whether a dying terminal gets diagnosed or retried, which is not a place
// to have been running on inspection.
//
// WHAT EACH GROUP IS FOR:
//   * the tail/compaction pair keeps a stuck dialog visible through a flood of spinner repaint;
//   * the resume strippers make a retry not resume the same dead session;
//   * the classifier turns a terminal's own dying words into a verdict the bridge can act on.

import assert from "node:assert/strict";

import {
  appendTail,
  classifyTerminalRuntimeOutput,
  compactTerminalText,
  hermesResumeStallHealMs,
  hermesResumeStillPending,
  terminalCommandWithoutResume,
  terminalEnvWithoutResume,
} from "../terminal-text.js";

// ── appendTail: the eviction window ──────────────────────────────────────────────────────────
// The 2026-07-14 incident this exists for: claude repaints an OSC window title continuously while
// any background work is alive, so a dialog the agent is STUCK on is pushed out of an 8KB tail
// within seconds. 15.9KB of pure repaint noise had accumulated after the compaction dialog.
{
  assert.equal(appendTail("", "hello"), "hello", "an empty tail takes the chunk");
  assert.equal(appendTail("ab", "cd"), "abcd", "chunks append in order");

  const osc = "]0;some window title";
  assert.equal(appendTail("", `before${osc}after`), "beforeafter",
    "OSC title sequences are dropped — they carry no screen text, only noise");

  assert.equal(appendTail("abcdef", "gh", 4), "efgh", "the window keeps the NEWEST bytes");
  assert.equal(appendTail("x".repeat(100), "", 10).length, 10, "an over-long tail is trimmed even with no chunk");

  assert.equal(appendTail(undefined, undefined), "", "degenerate input is empty, never 'undefined'");
  assert.equal(appendTail(null, null), "", "and null the same");
}

// ── compactTerminalText: what a human would read ─────────────────────────────────────────────
{
  assert.equal(compactTerminalText("[31mred[0m text"), "red text", "CSI colour codes go");
  assert.equal(compactTerminalText("a]0;titleb"), "a b", "OSC sequences go");
  assert.equal(compactTerminalText("  lots\n\n of\t\twhitespace  "), "lots of whitespace",
    "runs of whitespace collapse, and the result is trimmed");
  assert.equal(compactTerminalText(""), "");
  assert.equal(compactTerminalText(undefined), "", "never the string 'undefined'");
}

// ── hermesResumeStillPending: ordering, not presence ─────────────────────────────────────────
// The distinction that makes this correct: "resuming" followed by "ready" is a FINISHED resume, and
// only the LAST occurrence of each counts — a console that has resumed twice must not read as stuck
// because an older "resuming" appears before the newest "ready".
{
  assert.equal(hermesResumeStillPending("Resuming session abc"), true, "resuming with no ready is pending");
  assert.equal(hermesResumeStillPending("Resuming session abc\nReady."), false, "ready after resuming is done");
  assert.equal(hermesResumeStillPending("Ready.\nResuming session abc"), true,
    "a ready that PRECEDES the resume does not close it");
  assert.equal(hermesResumeStillPending("Resuming one\nReady\nResuming two"), true,
    "the newest resume is the one that matters");
  assert.equal(hermesResumeStillPending("Resuming one\nResuming two\nReady"), false);
  assert.equal(hermesResumeStillPending("nothing relevant here"), false, "no resume at all is not pending");
  assert.equal(hermesResumeStillPending(""), false);
  assert.equal(hermesResumeStillPending("[32mResuming[0m"), true, "colour codes do not hide it");
}

// ── hermesResumeStallHealMs: an override with a floor ────────────────────────────────────────
{
  const original = process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS;
  try {
    delete process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS;
    assert.equal(hermesResumeStallHealMs(), 30000, "the default when unset");

    process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS = "500";
    assert.equal(hermesResumeStallHealMs(), 500, "a sane override is honoured");

    process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS = "1";
    assert.equal(hermesResumeStallHealMs(), 25, "below the floor is raised to it, not accepted");

    for (const bogus of ["0", "-5", "abc", ""]) {
      process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS = bogus;
      assert.equal(hermesResumeStallHealMs(), 30000, `${JSON.stringify(bogus)} falls back to the default`);
    }
  } finally {
    if (original === undefined) delete process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS;
    else process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS = original;
  }
}

// ── classifyTerminalRuntimeOutput: a verdict, or nothing ─────────────────────────────────────
{
  assert.equal(classifyTerminalRuntimeOutput("pi", "No API key found for amazon-bedrock. Use /login.").kind, "auth");
  assert.equal(classifyTerminalRuntimeOutput("pi", "Unauthorized").kind, "auth");
  assert.equal(classifyTerminalRuntimeOutput("pi", "HTTP 401").kind, "auth");
  assert.equal(classifyTerminalRuntimeOutput("pi", "authentication failed").kind, "auth");
  assert.equal(classifyTerminalRuntimeOutput("pi", "AUTHENTICATION REQUIRED").kind, "auth",
    "the match is case-insensitive — a runtime shouting is still the same failure");

  const missing = classifyTerminalRuntimeOutput("pi", 'Session "dead-session" not found');
  assert.equal(missing.kind, "missing_session");
  assert.equal(missing.sessionHandle, "dead-session", "the handle is extracted so the caller can clear it");

  assert.equal(classifyTerminalRuntimeOutput("hermes", 'Session "h-1" does not exist').kind, "missing_session",
    "hermes gets the missing-session branch too");
  assert.equal(classifyTerminalRuntimeOutput("hermes", "No API key"), null,
    "but NOT the auth branch — that is pi-only, and it returns NULL rather than an unclassified "
    + "object, which is the difference between 'no verdict' and 'a verdict with no kind'");

  assert.equal(classifyTerminalRuntimeOutput("pi", ""), null, "empty output says nothing");
  assert.equal(classifyTerminalRuntimeOutput("pi", "starting up, all normal"), null,
    "ordinary output is null, not a guess");
  assert.equal(classifyTerminalRuntimeOutput("claude", "No API key found"), null,
    "a runtime with no rules gets no verdict");

  assert.equal(classifyTerminalRuntimeOutput("pi", "[31mUnauthorized[0m").kind, "auth",
    "the classifier compacts first, so colour codes cannot hide a failure");
}

// ── the resume strippers: a PAIR ─────────────────────────────────────────────────────────────
// Both halves have to drop the handle or a retry resumes the same dead session. The command side is
// delegated to `runtimes.js`; what is asserted here is that this wrapper does not lose it.
{
  assert.equal(
    terminalCommandWithoutResume("pi", "pi-aify --aify-agent worker --resume dead-session"),
    "pi-aify --aify-agent worker",
  );

  const env = terminalEnvWithoutResume("pi", {
    AIFY_SESSION_HANDLE: "dead-session",
    PATH: "/usr/bin",
  });
  assert.equal(env.AIFY_SESSION_HANDLE, undefined, "the generic handle is always dropped");
  assert.equal(env.PATH, "/usr/bin", "unrelated variables survive");

  const source = { AIFY_SESSION_HANDLE: "x" };
  terminalEnvWithoutResume("pi", source);
  assert.equal(source.AIFY_SESSION_HANDLE, "x", "the caller's env is not mutated");

  assert.deepEqual(terminalEnvWithoutResume("pi", undefined), {}, "no env is an empty env, not a throw");
}

console.log("terminal-text.test.js: all assertions passed");
