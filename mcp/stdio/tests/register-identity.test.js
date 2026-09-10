// Registration identity diagnostics describe this MCP bridge, not unobserved parent hooks.
import assert from "node:assert/strict";
import { test } from "node:test";
import { residentIdentityWarning } from "../register-identity.js";

test("absent identity warns about bridge configuration, not proven runtime failure", () => {
  const w = residentIdentityWarning({ registeredAgentId: "a", envAgentId: "", runtime: "claude-code" });
  assert.match(w, /MCP bridge.*no usable.*AIFY_AGENT_ID/);
  assert.doesNotMatch(w, /cannot report turns|no session handle is captured|status will latch|only.*relaunch/i);
  assert.match(w, /[Vv]erify/, "ask for evidence before choosing a repair");
  assert.match(w, /Registration itself worked/);
});

test("concrete mismatch warns without asserting where all turns go", () => {
  const w = residentIdentityWarning({ registeredAgentId: "new-agent", envAgentId: "old-agent", sessionMode: "resident" });
  assert.match(w, /MCP bridge/);
  assert.match(w, /"old-agent".*not "new-agent"/);
  assert.doesNotMatch(w, /will receive none|status will latch|signals from this terminal are reported/);
});

test("matching resident identity is not warned", () => {
  assert.equal(residentIdentityWarning({ registeredAgentId: "a", envAgentId: " a ", sessionMode: "resident" }), "");
});

test("managed sessions exclude absent, placeholder and mismatched identity warnings", () => {
  for (const envAgentId of ["", "${AIFY_AGENT_ID}", "other-agent"]) {
    assert.equal(residentIdentityWarning({ registeredAgentId: "a", envAgentId, sessionMode: "managed", runtime: "hermes" }), "");
  }
});

test("conditional launch guidance uses the runtime wrapper", () => {
  for (const [runtime, wrapper] of [["claude-code", "claude-aify"], ["codex", "codex-aify"], ["hermes", "hermes-aify"]]) {
    const w = residentIdentityWarning({ registeredAgentId: "a", runtime });
    assert.match(w, /If a new launch is needed/);
    assert.ok(w.includes(`${wrapper} --aify-agent a`));
  }
});

test("blanks and unresolved templates are unavailable, never registration targets", () => {
  for (const envAgentId of [undefined, "   ", "${AIFY_AGENT_ID}", "  ${AIFY_COMMS_AGENT_ID}  "]) {
    const w = residentIdentityWarning({ registeredAgentId: "a", envAgentId, runtime: "hermes" });
    assert.match(w, /no usable.*AIFY_AGENT_ID/);
    assert.ok(!w.includes("${"), "never recommend registering a template literal");
  }
});

test("degenerate registration inputs do not warn", () => {
  assert.equal(residentIdentityWarning(), "");
  assert.equal(residentIdentityWarning({}), "");
  assert.equal(residentIdentityWarning({ registeredAgentId: "", envAgentId: "" }), "");
});
