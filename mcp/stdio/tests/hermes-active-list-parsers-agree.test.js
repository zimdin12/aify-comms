#!/usr/bin/env node
// The gateway's active_list response is parsed by TWO functions, in two modules, and the copies are
// the same code.
//
//   hermes-gateway-protocol.js :: activeListRows   (private)   -> feeds pickSessionStatusForKey,
//                                                                 i.e. the agent's STATUS
//   hermes-active-session.mjs  :: activeListRowsLocal (export) -> feeds the active-session /
//                                                                 marker path, i.e. DELIVERY and
//                                                                 resume
//
// Their bodies are byte-identical (11 lines, the same four-way shape ladder). The neighbouring pair
// `rowRealId` / `rowRealIdLocal` is identical too, modulo the parameter name — and the `Local`
// suffix on both says whoever wrote the second copy knew the first existed.
//
// WHY A DRIFT HERE WOULD BE EXPENSIVE. The four accepted shapes exist because the gateway's response
// envelope has changed before; `rowResumeKey` next door carries a whole note about "older gateway
// shapes" and a 4007 "session not found" incident. If a NEW envelope is taught to one parser and not
// the other, the two do not fail — they disagree: status is read from a payload the delivery path
// sees as empty, or the reverse. An agent that looks idle while its session is live, or live while
// the status engine sees nothing, is the exact class this project has spent the most time chasing.
//
// AN AGREEMENT TEST, NOT A MERGE. Which module should OWN this is a reviewer's call — the same
// ruling already made for `createDeferred`, the turn-busy reporting family,
// `DelegatedManagedController` and `parseProcLines`. Keeping the two from drifting in the meantime
// is not a reviewer's call.
//
// MEASURED when written: the two agree on all seven payload shapes below, including the three that
// must yield nothing.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { declarationSpan } from "../../../service/new_dashboard/extraction-proof.mjs";
import { activeListRowsLocal } from "../hermes-active-session.mjs";
import { pickSessionStatusForKey } from "../hermes-gateway-protocol.js";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const sourceOf = (relative) =>
  readFileSync(path.join(STDIO, relative), "utf-8").replace(/\r\n/g, "\n");

function bodyOf(relative, name) {
  const src = sourceOf(relative);
  const span = declarationSpan(src, name);
  assert.ok(span, `${name} not found in ${relative} — if it was renamed, repoint this test`);
  return src.split("\n").slice(span.start, span.end + 1).join("\n");
}

// ── the two copies are the same code ─────────────────────────────────────────────────────────
{
  const shared = bodyOf("hermes-gateway-protocol.js", "activeListRows");
  const local = bodyOf("hermes-active-session.mjs", "activeListRowsLocal");
  const normalise = (body, name) =>
    body.replace(/^export\s+/, "").replace(new RegExp(`\\b${name}\\b`), "NAME");
  assert.equal(
    normalise(local, "activeListRowsLocal"), normalise(shared, "activeListRows"),
    "the copies have drifted at the SOURCE level. That is not automatically wrong — but it means "
      + "one of the two now accepts a gateway envelope the other does not, so status and delivery "
      + "will read the same response differently. Settle both, or record why they differ.",
  );
}
{
  // The neighbouring pair, identical apart from the parameter identifier (`r` vs `row`). Normalised
  // explicitly rather than waved at, so a real change cannot hide behind "it's just naming".
  const shared = bodyOf("hermes-gateway-protocol.js", "rowRealId");
  const local = bodyOf("hermes-active-session.mjs", "rowRealIdLocal");
  const normalise = (body, name, param) =>
    body
      .replace(/^export\s+/, "")
      .replace(new RegExp(`\\b${name}\\b`), "NAME")
      .replace(new RegExp(`\\b${param}\\b`, "g"), "ROW");
  assert.equal(
    normalise(local, "rowRealIdLocal", "row"), normalise(shared, "rowRealId", "r"),
    "the ephemeral-session-id extractors have drifted. Both answer 'which in-memory sid do I "
      + "target for prompt.submit/session.steer' — two answers means delivery aims at one session "
      + "while the status path describes another.",
  );
}

// ── and they behave the same on every envelope ───────────────────────────────────────────────
// Behavioural, not just textual: identical source today does not stop someone editing one copy
// tomorrow, and this half fails on the meaning rather than the characters.
const ROW = { session_key: "k1", id: "sid-1", status: "working" };

const ACCEPTED = {
  "a bare array of rows": [ROW],
  "result.sessions": { result: { sessions: [ROW] } },
  "sessions at the top level": { sessions: [ROW] },
  "result as the array itself": { result: [ROW] },
};
const REJECTED = {
  "an empty object": {},
  "null": null,
  "result.sessions holding a non-array": { result: { sessions: "nope" } },
};

for (const [label, payload] of Object.entries(ACCEPTED)) {
  const rows = activeListRowsLocal(payload);
  assert.equal(rows.length, 1, `${label}: the delivery-side parser found no rows`);
  assert.equal(
    pickSessionStatusForKey(payload, "k1"), "working",
    `${label}: the status-side parser did not see the row the delivery side did — the two parsers `
      + `have diverged on which envelopes they accept`,
  );
}

for (const [label, payload] of Object.entries(REJECTED)) {
  assert.deepEqual(activeListRowsLocal(payload), [], `${label}: delivery side invented rows`);
  assert.equal(
    pickSessionStatusForKey(payload, "k1"), "",
    `${label}: status side invented a status from an envelope carrying no rows`,
  );
}

// ── anti-vacuity ─────────────────────────────────────────────────────────────────────────────
{
  // Every ACCEPTED assertion would also pass if both parsers accepted literally anything, and every
  // REJECTED one if both returned nothing always. The pair above only means something because the
  // same input set produces BOTH outcomes.
  assert.ok(Object.keys(ACCEPTED).length >= 4 && Object.keys(REJECTED).length >= 3);
  assert.notDeepEqual(
    activeListRowsLocal([ROW]), activeListRowsLocal({}),
    "the parser is answering the same thing for every input; this file proves nothing",
  );
}

console.log("hermes-active-list-parsers-agree.test.js: all assertions passed");
