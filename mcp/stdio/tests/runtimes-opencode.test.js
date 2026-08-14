// OpenCode's model splitting, permission config, and response parsing.
//
// FIFTH BACKLOG PAYMENT. Four pure exports, none tested.
//
// THESE TESTS DO NOT RUN OPENCODE. The standing instruction here is not to run opencode in this
// environment — its local model saturates the GPU — and nothing below launches it. Every export in this
// module is a pure function over plain objects; this file imports and calls them, and no process starts.
// Said explicitly because "opencode" in a test filename is otherwise a reasonable thing to be wary of.
//
// `opencodePermissionConfig` is the same decision `managedClaudePermissionArgs` makes for claude, in a
// different shape: a MANAGED run is granted bash/edit/webfetch by default. It is worth the same treatment
// — pinned as fact, with the escape hatches asserted to work, so the auto-grant cannot widen or the
// override silently stop applying.

import assert from "node:assert/strict";
import test from "node:test";

import {
  opencodePermissionConfig,
  requireOpenCodeData,
  splitProviderModel,
  summarizeOpenCodeParts,
} from "../runtimes-opencode.js";

// --- permissions -----------------------------------------------------------

const ALLOW_ALL = { bash: "allow", edit: "allow", webfetch: "allow" };
const ASK_ALL = { bash: "ask", edit: "ask", webfetch: "ask" };

test("a MANAGED run is granted bash, edit and webfetch by default", () => {
  // The auto-grant, pinned as fact rather than endorsed — the same shape as claude's managed bypass.
  assert.deepEqual(opencodePermissionConfig({}, "managed"), ALLOW_ALL);
  assert.deepEqual(opencodePermissionConfig({}), ALLOW_ALL, "managed is also the default mode argument");
});

test("a RESIDENT run gets NO permission config, leaving opencode's own defaults in place", () => {
  // `undefined`, not an empty object: the caller omits the field entirely, so opencode keeps whatever the
  // operator configured for their own session. Returning `{}` would override it with nothing.
  assert.equal(opencodePermissionConfig({}, "resident"), undefined);
});

test("an explicit permission object wins over everything, including the managed default", () => {
  // The full-control escape hatch. It must beat both the policy shortcuts and the managed auto-grant, or
  // a hand-written permission set is silently ignored on managed runs.
  const custom = { bash: "ask", edit: "deny" };
  assert.equal(opencodePermissionConfig({ permission: custom }, "managed"), custom, "the SAME object");
  assert.equal(opencodePermissionConfig({ permission: custom, approvalPolicy: "never" }, "managed"), custom,
    "…even when a policy is also set");
});

test("a non-object permission field is IGNORED rather than passed through", () => {
  // `typeof config.permission === "object"`. A string here would reach opencode as a malformed config;
  // falling back to the policy path is the safer read.
  for (const bad of ["allow", 42, true]) {
    assert.deepEqual(opencodePermissionConfig({ permission: bad }, "managed"), ALLOW_ALL,
      `permission=${JSON.stringify(bad)} must not be used as a config`);
  }
});

test("policy 'ask' downgrades a MANAGED run to prompting", () => {
  // The escape hatch that matters: without it a managed agent cannot be run with prompts on.
  assert.deepEqual(opencodePermissionConfig({ approvalPolicy: "ask" }, "managed"), ASK_ALL);
});

test("policy 'never' and 'auto' grant everything even for a RESIDENT session", () => {
  for (const policy of ["never", "auto"]) {
    assert.deepEqual(opencodePermissionConfig({ approvalPolicy: policy }, "resident"), ALLOW_ALL, policy);
  }
});

test("policy matching is case- and whitespace-insensitive", () => {
  // Hand-written config. "Ask" failing to match would silently re-enable the auto-grant.
  assert.deepEqual(opencodePermissionConfig({ approvalPolicy: " ASK " }, "managed"), ASK_ALL);
  assert.deepEqual(opencodePermissionConfig({ approvalPolicy: "Never" }, "resident"), ALLOW_ALL);
});

test("an unrecognised policy falls through to the mode default, not to a grant", () => {
  // A typo'd policy on a resident session must not be treated as "never".
  assert.equal(opencodePermissionConfig({ approvalPolicy: "sometimes" }, "resident"), undefined);
  assert.deepEqual(opencodePermissionConfig({ approvalPolicy: "sometimes" }, "managed"), ALLOW_ALL);
});

// --- model splitting -------------------------------------------------------

test("provider and model split on the FIRST slash, and the rest stays with the model", () => {
  // Model ids contain slashes — "openrouter/anthropic/claude" is provider "openrouter", model
  // "anthropic/claude". Splitting on the last one, or taking only two parts, corrupts the id.
  assert.deepEqual(splitProviderModel("anthropic/claude-sonnet-5"),
    { providerID: "anthropic", modelID: "claude-sonnet-5" });
  assert.deepEqual(splitProviderModel("openrouter/anthropic/claude-3.5"),
    { providerID: "openrouter", modelID: "anthropic/claude-3.5" });
});

test("anything without both halves is null, not a partial object", () => {
  // The caller branches on null. A `{providerID: "x", modelID: ""}` would be sent as a real model.
  for (const value of ["", "   ", "no-slash", "/leading", "trailing/", "/", undefined, null, 42]) {
    assert.equal(splitProviderModel(value), null, `${JSON.stringify(value)} must be null`);
  }
});

test("surrounding whitespace is trimmed from both halves", () => {
  assert.deepEqual(splitProviderModel("  anthropic/claude  "),
    { providerID: "anthropic", modelID: "claude" });
});

// --- response parsing ------------------------------------------------------

test("text parts are concatenated WITHOUT separators", () => {
  // `join("")`. These are streamed fragments of one message — joining with newlines would insert breaks
  // mid-sentence.
  assert.equal(summarizeOpenCodeParts([{ type: "text", text: "hel" }, { type: "text", text: "lo" }]), "hello");
});

test("non-text parts are skipped rather than stringified", () => {
  // Tool calls arrive in the same array; "[object Object]" in the reply is the failure mode.
  const parts = [
    { type: "tool", name: "bash" },
    { type: "text", text: "done" },
    { type: "reasoning", text: "hidden" },
  ];
  assert.equal(summarizeOpenCodeParts(parts), "done", "only type:'text' contributes");
});

test("malformed parts yield an empty string rather than throwing", () => {
  assert.equal(summarizeOpenCodeParts([]), "");
  assert.equal(summarizeOpenCodeParts(), "");
  assert.equal(summarizeOpenCodeParts([null, 7, "str", { type: "text" }]), "",
    "a text part with no text contributes nothing");
});

test("data is returned when present, even when it is falsy-looking", () => {
  assert.deepEqual(requireOpenCodeData({ data: { id: 1 } }), { id: 1 });
});

test("THE ERROR MESSAGE IS UNWRAPPED FROM THE MOST SPECIFIC PLACE FIRST", () => {
  // Three nesting levels, and the order matters: the innermost is the one that says what actually went
  // wrong. Preferring the outer message would report "request failed" for everything.
  assert.throws(
    () => requireOpenCodeData({ error: { data: { message: "model not found" }, message: "request failed" } }, "fallback"),
    /model not found/,
  );
  assert.throws(() => requireOpenCodeData({ error: { message: "request failed" } }, "fallback"), /request failed/);
  assert.throws(() => requireOpenCodeData({}, "fallback"), /fallback/);
  assert.throws(() => requireOpenCodeData(undefined, "fallback"), /fallback/);
});

test("a missing data field throws rather than returning undefined", () => {
  // The caller uses the result directly. Returning undefined would move the failure to a later line where
  // the cause is no longer visible.
  assert.throws(() => requireOpenCodeData({ data: null }, "no data"), /no data/);
});
