// Pi's output parsers and its failure classifier.
//
// FOURTH BACKLOG PAYMENT. Five pure exports, none of them tested. The classifier is the reason this one
// was worth doing next.
//
// `detectPiRuntimeFailure` decides WHETHER TO SELF-HEAL, and the branch order is the behaviour. It heals
// by recreating the session, which fixes a missing or misplaced session and fixes nothing else — so a
// missing API key must NOT reach the heal path, or the bridge retries forever against a credential
// problem no retry can solve. The precedence is fatal > auth > missing-session > project-mismatch, and
// each earlier branch RETURNS, so a message matching two categories takes the first. Every test below
// that swaps a pair is checking exactly that.

import assert from "node:assert/strict";
import test from "node:test";

import {
  detectPiRuntimeFailure,
  extractPiAssistantText,
  extractPiSessionState,
  normalizePiModelOverride,
} from "../runtimes-pi.js";

// --- the failure classifier ------------------------------------------------

const clean = { shouldHeal: false, authFailure: false, fatalRuntime: false, missingSession: false, healReason: null };

test("nothing to classify yields a clean verdict rather than a heal", () => {
  // The default must be "do nothing". A classifier that healed on an unrecognised message would recreate
  // sessions for every unfamiliar warning pi prints.
  for (const value of [undefined, null, "", "   "]) {
    assert.deepEqual(detectPiRuntimeFailure(value), { ...clean, message: "" },
      `${JSON.stringify(value)} must classify as nothing`);
  }
});

test("an object with an EMPTY message stringifies to [object Object] — recorded, not corrected", () => {
  // `String(value?.message || value || "")`: an empty `.message` is falsy, so the fallback takes the
  // OBJECT and stringifies it. The verdict is still clean, so nothing acts on it, but the message field
  // carries "[object Object]" into whatever logs it rather than an empty string.
  //
  // Pinned as observed behaviour. I expected "" here and was wrong; asserting what I assumed would have
  // made this test fail for the right reason and be "fixed" by loosening it. An Error with a blank
  // message is rare enough that changing the expression is not obviously worth the churn — but the next
  // person reading a "[object Object]" in a pi log should find this test rather than a puzzle.
  const v = detectPiRuntimeFailure({ message: "" });
  assert.equal(v.message, "[object Object]");
  assert.equal(v.shouldHeal, false, "the verdict is still clean — nothing acts on it");
  assert.equal(v.fatalRuntime, false);
  assert.equal(v.authFailure, false);
});

test("an unrecognised error is NOT healed", () => {
  const v = detectPiRuntimeFailure("something unexpected happened");
  assert.equal(v.shouldHeal, false);
  assert.equal(v.healReason, null);
});

test("a missing session heals, and names the reason", () => {
  // The recoverable case: recreating the session genuinely fixes it.
  for (const message of [
    'session "abc-123" not found',
    "session abc-123 does not exist",
    "session 'abc' missing",
    "No such session",
  ]) {
    const v = detectPiRuntimeFailure(message);
    assert.equal(v.shouldHeal, true, message);
    assert.equal(v.missingSession, true, message);
    assert.equal(v.healReason, "missing_session", message);
  }
});

test("a session in another project heals under its OWN reason", () => {
  // Distinct from missing_session even though both set missingSession — the reason is what the caller
  // logs, and collapsing them would hide a workspace misconfiguration behind a routine recreate.
  const v = detectPiRuntimeFailure('session "abc" is in another project');
  assert.equal(v.shouldHeal, true);
  assert.equal(v.healReason, "project_mismatch");
});

test("AN AUTH FAILURE NEVER HEALS — retrying a credential problem loops forever", () => {
  // The safety property. Healing recreates the session; it does not create an API key.
  for (const message of [
    "no api key found",
    "API key not found",
    "api key missing",
    "api key required",
    "not authenticated",
    "authentication failed",
    "Authentication required",
    "unauthorized",
    "unauthorised",
    "request failed with status 401",
  ]) {
    const v = detectPiRuntimeFailure(message);
    assert.equal(v.authFailure, true, message);
    assert.equal(v.shouldHeal, false, `${message} must not trigger a heal`);
  }
});

test("bedrock needs BOTH a provider hint and a credential word", () => {
  // `(bedrock) && (login|auth|credential|api key)`. Bedrock appearing alone — in a model name, say —
  // must not be read as an auth failure and suppress a legitimate heal.
  assert.equal(detectPiRuntimeFailure("amazon-bedrock credential error").authFailure, true);
  assert.equal(detectPiRuntimeFailure("using amazon-bedrock claude model").authFailure, false,
    "a provider name on its own is not an auth failure");
});

test("a FATAL runtime error never heals either, and outranks auth", () => {
  // Out of memory or a broken pipe means the process is gone; a new session would hit the same wall.
  for (const message of [
    "FATAL ERROR: something",
    "JavaScript heap out of memory",
    "Allocation failed - JavaScript heap out of memory",
    "write EPIPE",
  ]) {
    const v = detectPiRuntimeFailure(message);
    assert.equal(v.fatalRuntime, true, message);
    assert.equal(v.shouldHeal, false, message);
  }
});

test("PRECEDENCE: fatal beats auth beats missing-session", () => {
  // Each branch returns, so a message matching two categories takes the earlier one. Re-ordering them
  // would turn an out-of-memory crash into an endless session-recreate loop, which is the failure this
  // ordering exists to prevent.
  const fatalAndSession = detectPiRuntimeFailure('FATAL ERROR: session "abc" not found');
  assert.equal(fatalAndSession.fatalRuntime, true);
  assert.equal(fatalAndSession.shouldHeal, false, "fatal must win over the heal");

  const authAndSession = detectPiRuntimeFailure('no api key; session "abc" not found');
  assert.equal(authAndSession.authFailure, true);
  assert.equal(authAndSession.shouldHeal, false, "auth must win over the heal");

  const fatalAndAuth = detectPiRuntimeFailure("FATAL ERROR: not authenticated");
  assert.equal(fatalAndAuth.fatalRuntime, true);
  assert.equal(fatalAndAuth.authFailure, false, "fatal returns before auth is even evaluated");
});

test("an Error object is read through .message, and whitespace is collapsed", () => {
  // Callers pass both raw strings and Errors. The collapsed message goes into a log line, so an embedded
  // newline would break the format.
  const v = detectPiRuntimeFailure(new Error("no    api\n  key"));
  assert.equal(v.message, "no api key");
  assert.equal(v.authFailure, true, "…and the collapsing happens BEFORE matching");
});

// --- assistant text --------------------------------------------------------

test("assistant text is collected from string content and from text parts", () => {
  assert.equal(extractPiAssistantText({ role: "assistant", content: "hello" }), "hello");
  assert.equal(
    extractPiAssistantText([{ role: "assistant", content: [{ type: "text", text: "a" }, { type: "text", text: "b" }] }]),
    "a\nb",
  );
});

test("only ASSISTANT messages are collected", () => {
  // A user or system turn leaking into the assistant text would be shown back to the operator as the
  // agent's own answer.
  const messages = [
    { role: "user", content: "the question" },
    { role: "assistant", content: "the answer" },
    { role: "system", content: "instructions" },
  ];
  assert.equal(extractPiAssistantText(messages), "the answer");
});

test("non-text parts are skipped, not stringified", () => {
  // Tool calls and images appear in the same array. `[object Object]` in the reply is the failure mode.
  const message = {
    role: "assistant",
    content: [
      { type: "tool_use", name: "bash", input: { cmd: "ls" } },
      { type: "text", text: "done" },
      { type: "image", source: {} },
    ],
  };
  assert.equal(extractPiAssistantText(message), "done");
});

test("malformed shapes yield an empty string rather than throwing", () => {
  // This parses output from another process; a throw here would fail the turn.
  for (const value of [undefined, null, {}, [], "a string", 42, { role: "assistant" },
    { role: "assistant", content: [null, 7, { type: "text" }] }]) {
    assert.equal(extractPiAssistantText(value), "", `${JSON.stringify(value)} must be empty`);
  }
});

test("the role check is case-insensitive", () => {
  assert.equal(extractPiAssistantText({ role: "Assistant", content: "hi" }), "hi");
});

// --- session state ---------------------------------------------------------

test("session id and file are found at any of the accepted nesting levels", () => {
  // Pi has emitted all three shapes across versions. Reading only one would silently lose the session id
  // and force a fresh session on every turn.
  assert.deepEqual(extractPiSessionState({ data: { sessionId: "a", sessionFile: "/f" } }), { sessionId: "a", sessionFile: "/f" });
  assert.deepEqual(extractPiSessionState({ sessionId: "b", sessionPath: "/g" }), { sessionId: "b", sessionFile: "/g" });
  assert.deepEqual(extractPiSessionState({ data: { session: { id: "c", path: "/h" } } }), { sessionId: "c", sessionFile: "/h" });
});

test("the CASED variants are accepted too", () => {
  assert.equal(extractPiSessionState({ data: { sessionID: "x" } }).sessionId, "x");
});

test("data wins over the top level when both are present", () => {
  assert.equal(extractPiSessionState({ data: { sessionId: "inner" }, sessionId: "outer" }).sessionId, "inner");
});

test("an absent session yields empty strings, never undefined", () => {
  // The caller writes these into a marker file; "undefined" as a session id would be persisted verbatim.
  for (const value of [undefined, null, {}, "text", 42]) {
    assert.deepEqual(extractPiSessionState(value), { sessionId: "", sessionFile: "" }, JSON.stringify(value));
  }
});

// --- model override --------------------------------------------------------

test("placeholder model names normalise to empty so pi picks its own default", () => {
  // Passing "default" through as a literal model name makes pi fail to resolve it.
  for (const value of ["default", "unknown", "auto", "DEFAULT", "  Auto  "]) {
    assert.equal(normalizePiModelOverride(value), "", `${JSON.stringify(value)} is a placeholder`);
  }
});

test("a real model name is kept and trimmed", () => {
  assert.equal(normalizePiModelOverride("  anthropic/claude-sonnet-5  "), "anthropic/claude-sonnet-5");
  assert.equal(normalizePiModelOverride(undefined), "");
});
