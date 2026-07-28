// Tests for register-identity.js — warning a resident that registered without a launch identity.
//
// The failure this catches is invisible by construction: registration SUCCEEDS, the agent looks
// fine, and then its status latches forever because the turn hooks (gated on AIFY_AGENT_ID) never
// fire and no session handle is ever captured. Reported 2026-07-28 as "llama-manager does not have
// cli command that i can copy" — registered, resident, empty handle.

import assert from "node:assert/strict";
import { test } from "node:test";
import { residentIdentityWarning } from "../register-identity.js";

test("a resident with NO AIFY_AGENT_ID is warned, with the cause and the cure", () => {
  const w = residentIdentityWarning({
    registeredAgentId: "llama-manager",
    envAgentId: "",
    sessionMode: "resident",
    runtime: "claude-code",
  });
  assert.ok(w, "must warn");
  assert.match(w, /no AIFY_AGENT_ID/, "name the missing variable");
  assert.match(w, /LAUNCHES/, "explain that it can only be set at launch — that is why it cannot be fixed in place");
  assert.match(w, /latch/, "state the real consequence, not just 'may not work'");
  assert.match(w, /claude-aify --aify-agent llama-manager/, "give the exact command to fix it");
  assert.match(w, /Registration itself worked/, "must not imply the registration failed");
});

test("a MISMATCHED identity is warned — the turns go to the other agent", () => {
  const w = residentIdentityWarning({
    registeredAgentId: "new-agent",
    envAgentId: "old-agent",
    sessionMode: "resident",
    runtime: "claude-code",
  });
  assert.match(w, /"old-agent"/);
  assert.match(w, /"new-agent"/);
  assert.match(w, /latch/);
});

test("a correctly-launched resident is NOT warned", () => {
  assert.equal(
    residentIdentityWarning({
      registeredAgentId: "sc-manager",
      envAgentId: "sc-manager",
      sessionMode: "resident",
      runtime: "claude-code",
    }),
    "",
  );
});

test("MANAGED sessions are never warned — their identity comes from the spawner", () => {
  assert.equal(
    residentIdentityWarning({
      registeredAgentId: "sc-coder",
      envAgentId: "",
      sessionMode: "managed",
      runtime: "hermes",
    }),
    "",
  );
});

test("the suggested wrapper matches the runtime", () => {
  const cases = [
    ["claude-code", "claude-aify"],
    ["codex", "codex-aify"],
    ["hermes", "hermes-aify"],
  ];
  for (const [runtime, wrapper] of cases) {
    const w = residentIdentityWarning({ registeredAgentId: "a", envAgentId: "", sessionMode: "resident", runtime });
    assert.match(w, new RegExp(`${wrapper} --aify-agent a`), runtime);
  }
});

test("whitespace-only env identity counts as missing, not as a mismatch", () => {
  const w = residentIdentityWarning({
    registeredAgentId: "a",
    envAgentId: "   ",
    sessionMode: "resident",
    runtime: "claude-code",
  });
  assert.match(w, /no AIFY_AGENT_ID/, "a blank value is absent, not a different agent");
});

test("degenerate inputs never throw and never produce a bogus warning", () => {
  assert.equal(residentIdentityWarning(), "");
  assert.equal(residentIdentityWarning({}), "");
  assert.equal(residentIdentityWarning({ registeredAgentId: "" , envAgentId: "" }), "");
  // An unspecified sessionMode defaults to the resident treatment (that is the registration default).
  assert.ok(residentIdentityWarning({ registeredAgentId: "a", envAgentId: "" }));
});
