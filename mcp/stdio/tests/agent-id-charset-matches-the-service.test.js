#!/usr/bin/env node
// The service accepts an agent id the bridge's process parsers could not read back.
//
// `service/api_core/validation.py` is the admission gate:
//
//     SAFE_NAME_RE = ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$
//     "Invalid agent ID: must be 1-128 alphanumeric chars, dots, hyphens, underscores."
//
// Three bridge parsers pulled an agent id out of a command line with `[A-Za-z0-9_-]+` — the same
// class MINUS THE DOT. `team.coder` registers fine (verified against the running app: 200), and each
// parser then read it back as `team`.
//
// WHAT THE TRUNCATION COST, per site:
//
//   terminal-control.js   the extracted id is compared to the wanted one, and a mismatch means
//                         "positively a different agent — recycled pid, skip". A truncated id never
//                         equals the full one, so the guard reported a DIFFERENT AGENT about the
//                         agent's own process and Stop was permanently refused. Functional break.
//
//   proc-probes.js        `cmdlineResidentAgent` feeds `owned.delete(residentAgent)` in
//                         reap-managed-survivors, enforcing "a live resident wrapper owns this
//                         triad, never reap any artifact for that agent". Deleting a truncated name
//                         deletes nothing, so gateway and daemon artifacts stayed reapable while the
//                         operator's session was live — the guarantee that file's header records an
//                         incident for ("taskkill /f-ed the operator's own session. NEVER AGAIN").
//
//   proc-probes.js        `cmdlineDeliveryLoopAgent` names the agent behind a delivery loop; a
//                         truncated name is looked up in `owned` and misses.
//
// This is a CROSS-LANGUAGE CONTRACT, so the test states it as one: the charset the Python gate
// admits is duplicated here as a literal, and the cases below are exactly the shapes that gate
// accepts and rejects. If the service's rule changes, this file should fail.

import assert from "node:assert/strict";

import { cmdlineDeliveryLoopAgent, cmdlineResidentAgent } from "../proc-probes.js";

// Mirrors service/api_core/validation.py::SAFE_NAME_RE. Duplicated deliberately — a test that
// imported the rule from the code it checks would agree with any change, including a wrong one.
const SERVICE_ACCEPTS = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;

//: Ids the SERVICE accepts. Every one must survive a round trip through the bridge's parsers.
const ACCEPTED = [
  "lc-coder",        // the everyday shape
  "lc_coder",
  "team.coder",      // THE ONE THAT BROKE: dots are admitted and were not parsed
  "a.b.c",
  "agent1",
  "x",               // single character
  "A9._-x",
];
//: Ids the service REJECTS, kept here so the fixture cannot drift into asserting the wrong contract.
const REJECTED = ["lc coder", "lc:coder", "lc/coder", "team%coder", "team+coder", ".leading", ""];

// ── the fixture agrees with the service's rule ───────────────────────────────────────────────
{
  for (const id of ACCEPTED) {
    assert.ok(SERVICE_ACCEPTS.test(id), `fixture drift: the service would REJECT ${id}`);
  }
  for (const id of REJECTED) {
    assert.ok(!SERVICE_ACCEPTS.test(id), `fixture drift: the service would ACCEPT ${id}`);
  }
}

// ── every accepted id survives the round trip ────────────────────────────────────────────────
{
  for (const id of ACCEPTED) {
    assert.equal(
      cmdlineResidentAgent(`C:\\node.exe claude-aify --aify-agent ${id} --resume abc`), id,
      `--aify-agent <${id}> came back changed. A truncated id is compared, deleted from an owned `
        + `set, or looked up — and each of those fails silently.`,
    );
    assert.equal(
      cmdlineResidentAgent(`node claude-aify --aify-agent=${id}`), id,
      `the = form of --aify-agent lost part of ${id}`,
    );
    assert.equal(
      cmdlineDeliveryLoopAgent(`node hermes-managed-host.js run ${id}`), id,
      `the delivery-loop parser lost part of ${id}`,
    );
  }
}

// ── the parsers still refuse what they should ────────────────────────────────────────────────
{
  assert.equal(cmdlineResidentAgent(""), null);
  assert.equal(cmdlineResidentAgent("node claude-aify --resume abc"), null, "no marker, no agent");
  assert.equal(cmdlineDeliveryLoopAgent("node hermes-managed-host.js ensure-host x"), null,
    "only the `run` subcommand is a long-lived delivery loop");
  assert.equal(
    cmdlineResidentAgent("node hermes-managed-host.js run lc-coder --aify-agent lc-coder"), null,
    "a managed delivery loop is not a resident session, whichever markers it carries",
  );
}

// ── a leading dot is not an id, and must not be captured ─────────────────────────────────────
{
  // The service's rule requires the FIRST character to be alphanumeric. The parsers mirror that, so
  // a stray `--aify-agent .hidden` yields the part after the dot rather than inventing an id that
  // the service would have refused.
  assert.notEqual(cmdlineResidentAgent("node x --aify-agent .hidden"), ".hidden");
}

// ── anti-vacuity ─────────────────────────────────────────────────────────────────────────────
{
  // Every round-trip assertion passes if the parser simply echoed its input, and every refusal
  // assertion if it always returned null. Both shapes must be present.
  assert.equal(cmdlineResidentAgent("node x --aify-agent lc-coder more args"), "lc-coder",
    "the parser must stop at the id, not swallow the rest of the command line");
  assert.ok(ACCEPTED.some((id) => id.includes(".")), "the dot case is the point of this file");
}

console.log("agent-id-charset-matches-the-service.test.js: all assertions passed");
