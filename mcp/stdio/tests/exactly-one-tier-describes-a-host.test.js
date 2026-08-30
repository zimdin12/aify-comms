#!/usr/bin/env node
// One advertiser per host, decided from a fact the other tier reports rather than a flag on each side.
//
// WHAT GOES WRONG WITH TWO. `runtimes`, `terminalRuntimes`, `terminal` and `pty` are last-writer-wins
// on the service, and the two tiers compute them differently: this bridge lists five runtimes with
// capability flags out of its own table, aify-env lists the wrappers actually installed on the host.
// Both writing makes the row change on every beat, which reads like failing hardware rather than like
// two components disagreeing.
//
// WHAT GOES WRONG WITH NONE. The row goes stale, or -- before the preservation fix -- was erased. So
// the rule cannot be "whoever feels like it": standing down requires a POSITIVE answer from the tier
// taking the job, and every other outcome leaves this bridge doing it.
//
// WHY OMITTING IS SAFE NOW AND WAS NOT BEFORE. `service/api_core/environment_registration.py`
// preserves a field its caller did not mention. Until that landed, a heartbeat omitting `runtimes`
// erased them, and this whole design would have emptied the row instead of leaving it to aify-env.

import assert from "node:assert/strict";
import test from "node:test";

import { buildEnvironmentPayload } from "../environment-advertisement.mjs";
import { environmentHeartbeatPayload } from "../environment-identity.mjs";

/** The four fields aify-env takes over. Named once so a fifth cannot be added on one side only. */
const HOST_FACTS = ["runtimes", "terminal", "pty", "terminalRuntimes"];

/** What this bridge keeps regardless: its own identity, and the two fields aify-env never sends. */
const ALWAYS_OURS = ["id", "machineId", "os", "kind", "bridgeId", "bridgeVersion", "label", "cwdRoots"];

test("standing down omits exactly the fields aify-env took over", () => {
  const payload = environmentHeartbeatPayload({ hostDescribedByEnvironment: true });
  for (const field of HOST_FACTS) {
    assert.ok(!(field in payload), `${field} was still sent while aify-env was describing the host`);
  }
});

test("and keeps everything aify-env does not send", () => {
  // `label` and `cwdRoots` are the point of this one. aify-env deliberately sends neither -- one is
  // the operator's chosen name, the other is the service's policy -- so if this bridge dropped them
  // too, nothing would ever set them again.
  const payload = environmentHeartbeatPayload({ hostDescribedByEnvironment: true });
  for (const field of ALWAYS_OURS) {
    assert.ok(field in payload, `${field} vanished when the bridge stood down; nobody else sends it`);
  }
});

test("the default is to SEND — standing down needs a positive answer", () => {
  const payload = environmentHeartbeatPayload();
  for (const field of HOST_FACTS) {
    assert.ok(field in payload, `${field} was dropped without aify-env claiming it`);
  }
});

test("every not-a-yes leaves this bridge describing the host", () => {
  // A daemon too old to report the field, one that said no, one whose body could not be read. All of
  // them arrive here as something other than `true`, and all of them must keep the bridge advertising:
  // the failure of standing down for nobody is a host with no runtimes at all.
  for (const answer of [undefined, null, false, 0, "", "true", 1, {}]) {
    const payload = buildEnvironmentPayload({
      terminalManager: null, envHealthy: null, envProcesses: null,
      localTerminal: false, envAdvertising: answer,
    });
    assert.ok("runtimes" in payload,
      `envAdvertising=${JSON.stringify(answer)} was treated as a claim on this host`);
  }
});

test("a literal true, and only that, hands the host over", () => {
  const payload = buildEnvironmentPayload({
    terminalManager: null, envHealthy: true, envProcesses: [],
    localTerminal: true, envAdvertising: true,
  });
  for (const field of HOST_FACTS) {
    assert.ok(!(field in payload), `${field} survived the handover`);
  }
  assert.equal(typeof payload.bridgeId, "string");
  assert.notEqual(payload.bridgeId, "", "the bridge stopped identifying itself as well");
});

test("the bridge's own identity is never part of the handover", () => {
  // Supersession is arbitrated on `bridgeId` plus `bridgeStartedAt`, and aify-env has neither -- it is
  // not a bridge. Dropping these would disarm the arbitration between a stale bridge and a fresh one,
  // which is the failure `02045701` was written for.
  const payload = environmentHeartbeatPayload({ hostDescribedByEnvironment: true });
  assert.notEqual(String(payload.bridgeId || ""), "");
  assert.notEqual(String(payload.bridgeVersion || ""), "");
  assert.equal(typeof payload.metadata?.bridgeStartedAt === "string"
    || payload.metadata?.bridgeStartedAt === undefined, true);
});
