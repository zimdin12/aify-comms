#!/usr/bin/env node
// A gateway URL carries its auth token. These messages are stored by the control plane and served
// back over the API, so none of them may carry one.
//
// A hermes gateway URL is `ws://127.0.0.1:<port>/api/ws?token=<43 chars>`. Every builder here is
// POSTed to the server -- as a dispatch run's `error`, or as the `reason` that becomes
// `agents.status_note` -- and both are readable from /dispatch/runs and rendered on the dashboard.
//
// MEASURED ON THE LIVE FLEET, 2026-08-25, which is why this file exists rather than being a
// precaution: seven distinct gateway tokens, 43 characters each, were sitting in stored dispatch-run
// errors for four agents across two teams. The tokens authenticate to a LOOPBACK gateway, so this was
// never remote exposure -- it is exposure between the agents sharing the host, which is the boundary
// the per-agent gateway draws in the first place. One agent's token is enough to attach to another's
// hermes session and drive it.
//
// Written as a sweep over every builder rather than a check on the one that leaked, because the leak
// was not a mistake in any single message: it was that four call sites each interpolated the URL they
// had, and the URL they had was the one with the credential in it.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  noAttachedSessionTeardownMessage,
  noTuiAttachedMessage,
} from "../hermes-delivery-run.mjs";
import {
  gatewayUnreachableAfterProbesMessage,
  gatewayUnreachableMessage,
  redactGatewayUrl,
} from "../hermes-gateway.mjs";

// A real shape, token length included, taken from a failed run on the live fleet.
const TOKEN = "boxCCW04x3h49iuQa0wtMi37WWTd-BRjsffbcHRZYak";
const GATEWAY = `ws://127.0.0.1:9147/api/ws?token=${TOKEN}`;
const ADDRESS = "ws://127.0.0.1:9147/api/ws";

// Builders whose text becomes `agents.status_note`, via the `reason` POSTed to resident-lost.
const STATUS_NOTE_BUILDERS = [
  ["gatewayUnreachableMessage", () => gatewayUnreachableMessage(GATEWAY)],
  ["gatewayUnreachableAfterProbesMessage", () => gatewayUnreachableAfterProbesMessage(GATEWAY, 3)],
  ["noAttachedSessionTeardownMessage", () => noAttachedSessionTeardownMessage(GATEWAY, 10)],
];

// Builders whose text becomes a dispatch run's `error`, through markRunFailed. A different field with
// a different budget: the longest error stored on the live fleet measured 350 characters, so this one
// is not truncated at 200 and must not be held to that.
const RUN_ERROR_BUILDERS = [
  ["noTuiAttachedMessage", () => noTuiAttachedMessage(GATEWAY, 5)],
];

// Redaction applies to both: the credential must not travel, whichever field carries the text.
const BUILDERS = [...STATUS_NOTE_BUILDERS, ...RUN_ERROR_BUILDERS];

test("the fixture really does carry a credential", () => {
  // Positive control. Every assertion below is an ABSENCE, and absence proves nothing if the thing
  // was never present -- a fixture quietly rewritten without its token would turn this whole file
  // green while testing air.
  assert.ok(GATEWAY.includes("token="), "the fixture lost its token, so the sweep proves nothing");
  assert.equal(TOKEN.length, 43, "the fixture no longer looks like a real gateway token");
});

test("no builder emits the gateway credential", () => {
  for (const [name, build] of BUILDERS) {
    const message = build();
    assert.ok(!message.includes(TOKEN), `${name} leaked the gateway token`);
    assert.ok(!/token=/.test(message), `${name} carried a token parameter`);
  }
});

test("every builder still names the gateway, so the message stays actionable", () => {
  // Redaction that removed the address too would be a different defect: an operator cannot act on
  // "a gateway somewhere is unreachable".
  for (const [name, build] of BUILDERS) {
    assert.ok(build().includes(ADDRESS), `${name} no longer says which gateway`);
  }
});

test("every builder fits the status_note the server truncates at 200", () => {
  // A CROSS-COMPONENT FACT: service/api_core/resident_loss.py writes the reason into
  // agents.status_note as `str(req.reason)[:200]`. Each of these messages ends with its remedy, so
  // overflowing does not just lose detail -- it loses the only sentence that tells the operator what
  // to do. Caught exactly this way while writing the teardown message: 278 characters.
  // Scoped to the builders that land there: an earlier version of this test held the run-error builder
  // to the same 200 and was simply wrong about where its text goes.
  for (const [name, build] of STATUS_NOTE_BUILDERS) {
    const length = build().length;
    assert.ok(length <= 200, `${name} is ${length} chars; status_note truncates at 200`);
  }
});

test("redactGatewayUrl cuts at the first query or fragment", () => {
  assert.equal(redactGatewayUrl(GATEWAY), ADDRESS);
  assert.equal(redactGatewayUrl("ws://h:1/p#frag"), "ws://h:1/p");
  assert.equal(redactGatewayUrl("ws://h:1/p?a=1#frag"), "ws://h:1/p");
  assert.equal(redactGatewayUrl("ws://h:1/p"), "ws://h:1/p", "a clean URL passes through");
});

test("redactGatewayUrl says unknown rather than nothing when it has nothing", () => {
  // The builders print this straight into operator-facing text, so an empty answer would read as a
  // truncated sentence rather than as missing information.
  for (const empty of ["", "   ", null, undefined]) {
    assert.equal(redactGatewayUrl(empty), "(unknown)");
  }
});

test("redactGatewayUrl does not parse, so malformed input still gets redacted", () => {
  // `new URL()` throws on these, and the answer to "I cannot parse this" must never be "print it
  // anyway" -- that is precisely the input most likely to be carrying something odd.
  assert.equal(redactGatewayUrl("not a url?token=secret"), "not a url");
  assert.equal(redactGatewayUrl("?token=secret"), "");
});
