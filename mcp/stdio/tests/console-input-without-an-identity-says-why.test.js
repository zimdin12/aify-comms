// An id-less caller is told what to do, not handed a field it cannot supply.
//
// REPORTED as "the comms_console_input schema gap -- the tool is uncallable through MCP because its
// schema omits what the server requires". The schema is not the defect and adding the field would be
// a security regression; the MESSAGE was the defect.
//
// THE SHAPE. `POST /agents/{id}/console/input` requires `from` and 400s without it, then 403s if it
// is not a REGISTERED agent. Both are right: writing keystrokes into another agent's live console is
// the privileged half of this pair, which is why `comms_console_tail` is an ungated GET and this is
// not. But `from` is stamped by the BRIDGE from AIFY_AGENT_ID and is deliberately not a tool
// parameter -- exposing it would let any caller name any agent as the requester.
//
// So a session with no id -- and an unregistered plain session is legitimately id-less, which this
// repo's own doctor already says -- got back "console input requires a `from` caller (the requesting
// agent id)": an error naming a field the caller has no way to provide, through a schema that does
// not offer it. Uncallable, with no way to tell why from the error.
import assert from "node:assert";

// REMOTE MODE, POINTED NOWHERE. `IS_REMOTE` is resolved at import time and the remote guard runs
// BEFORE the identity guard -- so without this the handler returns "only available in remote
// server mode" and this file never reaches what it is testing.
//
// 127.0.0.2:1 is the repo's hostile-env address: set, and answering nothing. Never the live
// service. Nothing here reaches the network anyway -- `httpCall` is injected -- but the address
// is what makes that a property of the test rather than a hope.
process.env.AIFY_SERVER_URL = "http://127.0.0.2:1";
process.env.CLAUDE_MCP_SERVER_URL = "";

// AND THE IDENTITY IS SEALED, because the handler falls back to AIFY_AGENT_ID and this suite
// runs INSIDE an agent, where it is set. The first version passed `from: ""` and watched the
// fallback pick up the ambient value -- the guard was right and the test was reading the
// machine it happened to run on. Both aliases are cleared: launch-identity reads either.
process.env.AIFY_AGENT_ID = "";
process.env.AIFY_COMMS_AGENT_ID = "";

const { commsConsoleInputHandler } = await import("../console-tools.mjs");
const { AIFY_AGENT_ID } = await import("../launch-identity.mjs");

// ASSERT THE SEAL. A seal that silently failed would make every refusal below pass for the
// wrong reason on one machine and fail on another.
assert.strictEqual(AIFY_AGENT_ID, "", "the ambient agent id leaked into this test process");

const text = (result) => result.content[0].text;

// A call that must never happen: if the handler reaches HTTP without an identity, the server would
// answer with the unactionable error this fix exists to replace.
function refusingHttpCall() {
  return () => {
    throw new Error("the handler called the server without a caller identity");
  };
}

{
  // WITHOUT an identity: refused locally, with a message that names the fix.
  //
  // `from` is passed explicitly as empty because the registration wrapper injects AIFY_AGENT_ID,
  // which in a test process is whatever the environment happens to hold. Passing it makes the case
  // the subject of the test rather than a property of the machine it runs on.
  const result = await commsConsoleInputHandler(
    { agentId: "sc-coder", text: "hello", from: "" },
    { httpCall: refusingHttpCall() },
  );
  assert.strictEqual(result.isError, true, "an id-less call was not refused");
  const message = text(result);
  assert.ok(/registered agent identity/i.test(message), message);
  assert.ok(/comms_register/.test(message), "the refusal does not say how to fix it: " + message);
  assert.ok(
    /comms_console_tail/.test(message),
    "the refusal does not say that READING still works, which is the useful next step: " + message,
  );
  // The old failure named a field the caller cannot set. Naming it again would send the reader back
  // to a schema that will never have it.
  assert.ok(
    !/`from`/.test(message),
    "the refusal still names the `from` field, which is not a tool parameter: " + message,
  );
}

{
  // WHITESPACE IS NOT AN IDENTITY. A wrapper that exports AIFY_AGENT_ID="" or " " is the same
  // no-identity case, and the server would 403 it as an unregistered caller.
  for (const blank of ["", "   ", "\t"]) {
    const result = await commsConsoleInputHandler(
      { agentId: "sc-coder", text: "x", from: blank },
      { httpCall: refusingHttpCall() },
    );
    // THE MESSAGE, not just `isError`. The handler wraps a throwing httpCall into an isError
    // result too, so asserting the flag alone cannot tell a local refusal from a call that went
    // out and failed -- a mutation dropping `.trim()` survived exactly that gap.
    assert.strictEqual(result.isError, true, `a blank identity ${JSON.stringify(blank)} was accepted`);
    assert.ok(
      /registered agent identity/i.test(text(result)),
      `a blank identity ${JSON.stringify(blank)} reached the server: ${text(result)}`,
    );
  }
}

{
  // WITH an identity: the handler proceeds, and sends the caller it resolved.
  //
  // ANTI-VACUITY. A guard that refused everything would satisfy every assertion above and make the
  // tool genuinely uncallable, which is the thing that was reported.
  let sent = null;
  const result = await commsConsoleInputHandler(
    { agentId: "sc-coder", text: "hello", enter: true, from: "comms-tech-lead" },
    {
      httpCall: async (method, path, body) => {
        sent = { method, path, body };
        return { ok: true, terminalId: "term-1", controlId: "ctl-1" };
      },
    },
  );
  assert.strictEqual(result.isError, undefined, text(result));
  assert.strictEqual(sent.method, "POST");
  assert.ok(sent.path.includes("/console/input"), sent.path);
  assert.strictEqual(sent.body.from, "comms-tech-lead", "the resolved caller was not sent");
  assert.strictEqual(sent.body.text, "hello");
  assert.strictEqual(sent.body.enter, true);
}

{
  // The identity check must not swallow the OTHER guard. Remote-mode is checked first and has its
  // own message; an id-less call in local mode should still say that, not this.
  const result = await commsConsoleInputHandler(
    { agentId: "sc-coder", text: "x", from: "comms-tech-lead" },
    { httpCall: async () => ({ ok: false, message: "no console" }) },
  );
  assert.strictEqual(result.isError, true);
  assert.ok(/no console/.test(text(result)), text(result));
}

console.log("console-input-without-an-identity-says-why.test.js: all assertions passed");
