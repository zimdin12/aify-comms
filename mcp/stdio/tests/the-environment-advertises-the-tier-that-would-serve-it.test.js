// An environment advertises the capability of the tier that would actually provide it.
//
// THE OPERATOR'S QUESTION, 2026-08-28: "why do i see agents available if their env is down. shouldnt
// they be offline? like residents when they are not connected and not available (to write to)".
//
// The status engine was never the problem. `derive()` reads `if (mode == "managed" && !env_reachable)
// return "offline"`, and `_managed_env_reachable` gates on the environment row's effective status.
// What was wrong is what feeds it: `terminal` / `pty` / `terminalRuntimes` all came from
// `bridgeTerminalSupported()`, which answers exactly one question -- did node-pty load in THIS process.
//
// That was the right question until v0.6 Phase 8 flipped on 2026-08-25. Delegation makes aify-env
// REQUIRED; the bridge refuses to host a spawn rather than falling back. Since then the bridge's own
// node-pty has had nothing to do with whether a managed agent can start.
//
// MEASURED THE DAY THIS WAS WRITTEN, on the operator's host: aify-env down, environment row still
// `online` (the BRIDGE heartbeats it, and the bridge was fine), `terminal: true`, `pty: true`,
// `terminalRuntimes: [codex, claude-code, hermes, opencode, pi]` -- and 20 managed agents reading
// `available` with 4 processes in existence. The environment payload carried NO field naming aify-env
// or delegation at all; checked key by key.
//
// `available` is a promise, not a description. status_engine.py says so in its own words: it "PROMISES
// cold-start on the next send", and "saying `available` is a false promise that sends the operator
// hunting a delivery bug". Every one of those sends would have failed.
import assert from "node:assert/strict";
import { test } from "node:test";

import { envTerminalHealth, probeEnvTerminal, terminalCapability } from "../terminal-capability.mjs";

test("with delegation OFF the local pty still decides", () => {
  // The pre-Phase-8 answer, unchanged. A bridge that hosts its own terminals is entitled to answer
  // for them, and breaking that would be trading one wrong tier for another.
  assert.equal(terminalCapability({ delegationEnabled: false, localTerminal: true }).terminal, true);
  assert.equal(terminalCapability({ delegationEnabled: false, localTerminal: false }).terminal, false);
});

test("with delegation ON the local pty is not consulted", () => {
  // THE DEFECT, in one assertion. The bridge's node-pty loads fine on this host; it is simply not the
  // thing that would open the terminal any more.
  const verdict = terminalCapability({ delegationEnabled: true, envHealthy: false, localTerminal: true });
  assert.equal(verdict.terminal, false,
    "a loaded local node-pty still decided a delegated environment's capability");
  // AND THE REASON MUST STILL BE THE RIGHT ONE. A mutation gating this branch on `!localTerminal`
  // kept the verdict false -- it fell through to the "did not answer" branch -- and only the reason
  // changed. That is the same defect class as a doctor check describing something it did not
  // measure: aify-env DID answer, and telling an operator it went quiet sends them at the wrong tier.
  assert.match(verdict.reason, /answered but reports no terminal/,
    "aify-env answered NO and the reason claims it did not answer at all");
});

test("a delegated environment whose aify-env answers YES advertises a terminal", () => {
  assert.equal(
    terminalCapability({ delegationEnabled: true, envHealthy: true, localTerminal: false }).terminal,
    true,
    "aify-env said it can host terminals and the bridge refused to believe it",
  );
});

test("SILENCE IS NOT YES: an unanswered aify-env advertises no terminal", () => {
  // This repo's own rule -- "a check that could not gather evidence must NOT report ok" -- and the
  // direction matters. Advertising a terminal that cannot be opened sends work into a hole;
  // withholding one that could costs a queued send the next heartbeat releases.
  const verdict = terminalCapability({ delegationEnabled: true, envHealthy: null, localTerminal: true });
  assert.equal(verdict.terminal, false);
  assert.match(verdict.reason, /did not answer/);
});

test("every verdict says which tier answered", () => {
  // Including the yeses. A field only present on failure is one nobody builds a habit of reading, and
  // an operator looking at a row that says NO needs to know who said so.
  for (const input of [
    { delegationEnabled: false, localTerminal: true },
    { delegationEnabled: false, localTerminal: false },
    { delegationEnabled: true, envHealthy: true },
    { delegationEnabled: true, envHealthy: false },
    { delegationEnabled: true, envHealthy: null },
  ]) {
    const { reason } = terminalCapability(input);
    assert.ok(reason && reason.length > 10, `no reason for ${JSON.stringify(input)}`);
  }
});

test("no arguments does not advertise a terminal", () => {
  // A default that says yes is the false green this module exists to remove.
  assert.equal(terminalCapability().terminal, false);
});

// ---- reading aify-env's answer ------------------------------------------------------------------

test("the three states of aify-env's health stay three", () => {
  // Collapsing any two is how the original defect happened. A body with no `terminals.available` is
  // not a body saying no; it is an older environment that cannot answer.
  assert.equal(envTerminalHealth({ terminals: { available: true } }), true);
  assert.equal(envTerminalHealth({ terminals: { available: false } }), false);
  assert.equal(envTerminalHealth({ terminals: {} }), null);
  assert.equal(envTerminalHealth({}), null);
  assert.equal(envTerminalHealth(null), null);
  assert.equal(envTerminalHealth(undefined), null);
  assert.equal(envTerminalHealth("healthy"), null);
  assert.equal(envTerminalHealth({ terminals: { available: "yes" } }), null,
    "a non-boolean was read as an answer");
});

// ---- the CALL, not just the predicate -----------------------------------------------------------
//
// A predicate proven in isolation leaves the call to it unproven, and this repo has paid for that
// once already (doctor.js's service check: a verdict everybody tested, an early return nobody did).
// The first version of this reader lived inline in server.js and read `result.body` -- a field
// EnvClient does not have. It returns `{ ok, handle }`. Nothing could have caught it there.

const delegationWith = (health, { enabled = true } = {}) => ({
  isEnabled: () => enabled,
  client: { health: async () => health },
});

test("it reads the field EnvClient actually returns", () => {
  // THE BUG THIS TEST EXISTS FOR. `{ ok: true, handle: <body> }` is the shape; a reader expecting
  // `body` gets undefined, reports UNKNOWN for ever, and takes every managed agent dark.
  return probeEnvTerminal(delegationWith({ ok: true, handle: { terminals: { available: true } } }))
    .then((answer) => assert.equal(answer, true, "the probe read the wrong field off the client"));
});

test("a client REFUSAL is no answer, not a no", async () => {
  // `{ ok: false }` means the request did not land. Reporting that as "aify-env says it has no
  // terminal" would be inventing an answer, and the two lead to different operator action.
  assert.equal(await probeEnvTerminal(delegationWith({ ok: false, error: "aify-env unreachable" })), null);
});

test("aify-env answering NO is distinct from not answering", async () => {
  assert.equal(await probeEnvTerminal(delegationWith({ ok: true, handle: { terminals: { available: false } } })), false);
});

test("with delegation off nothing is asked", async () => {
  // A probe against an endpoint nobody serves spends a timeout every heartbeat.
  let asked = false;
  const delegation = { isEnabled: () => false, client: { health: async () => { asked = true; return {}; } } };
  assert.equal(await probeEnvTerminal(delegation), null);
  assert.equal(asked, false, "the probe called out with delegation off");
});

test("a client that throws is no answer rather than a crash", async () => {
  // This runs inside the heartbeat. A throw here would stop the environment reporting at all, which
  // is a worse failure than the one being fixed.
  const delegation = { isEnabled: () => true, client: { health: async () => { throw new Error("boom"); } } };
  assert.equal(await probeEnvTerminal(delegation), null);
});

test("a missing or malformed delegation is no answer", async () => {
  for (const delegation of [null, undefined, {}, { isEnabled: () => true }]) {
    assert.equal(await probeEnvTerminal(delegation), null, `threw or answered for ${JSON.stringify(delegation)}`);
  }
});
