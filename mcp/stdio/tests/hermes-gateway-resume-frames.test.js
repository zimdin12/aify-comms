#!/usr/bin/env node
// The four `hermes-gateway-protocol.js` exports its own test file does not name: the two frames that
// RECOVER a session, the not-found error that triggers that recovery, and the session picker.
//
// `hermes-gateway-protocol.test.js` covers the frames used on the happy path — submit, steer,
// interrupt, notice, the list frames — and `isSessionBusyError`. The recovery half was left out, and
// recovery is where the documented bugs are.
//
// THE ONE TO GET RIGHT IS `buildSessionResumeFrame`. Hermes has TWO ids per session: the DURABLE
// `session_key` (persisted in hermes' SessionDB, what resume requires) and the EPHEMERAL `sid` (the
// in-memory dict key, regenerated on every gateway attach). This builder takes the DURABLE key and
// sends it in a parameter named `session_id` — a genuine trap, since every neighbouring frame puts
// the EPHEMERAL id in that same field name. Persisting the ephemeral one as the agent→session
// binding is what produced gateway 4007 "session not found" on the next launch (2026-06-04).
//
// `isSessionNotFoundError` must stay distinct from `isSessionBusyError` because the two recoveries
// are opposites: not-found means refresh the session list and retry against whatever is live, busy
// means steer into the running turn. Confusing them steers into a session that no longer exists.

import assert from "node:assert/strict";

import {
  buildSessionCreateFrame,
  buildSessionResumeFrame,
  isSessionBusyError,
  isSessionNotFoundError,
  pickFreshestSessionFromList,
} from "../hermes-gateway-protocol.js";

// ── buildSessionResumeFrame ──────────────────────────────────────────────────────────────────
{
  const frame = buildSessionResumeFrame({ id: 7, sessionKey: "2026-06-04T10:00:00Z-abc", cols: 120 });
  assert.equal(frame.jsonrpc, "2.0");
  assert.equal(frame.id, 7);
  assert.equal(frame.method, "session.resume");
  assert.equal(
    frame.params.session_id,
    "2026-06-04T10:00:00Z-abc",
    "the DURABLE session_key travels in the param named session_id — this is the trap",
  );
  assert.equal(frame.params.cols, 120);

  // cols has a default and coerces, because a bad width must not make the frame unparseable.
  assert.equal(buildSessionResumeFrame({ id: 1, sessionKey: "k" }).params.cols, 80);
  assert.equal(buildSessionResumeFrame({ id: 1, sessionKey: "k", cols: 0 }).params.cols, 80,
    "0 is falsy, so the default applies rather than a zero-width session");
  assert.equal(buildSessionResumeFrame({ id: 1, sessionKey: "k", cols: "160" }).params.cols, 160);
  assert.equal(buildSessionResumeFrame({ id: 1, sessionKey: "k", cols: "wide" }).params.cols, 80,
    "an unparseable width falls back rather than sending NaN");

  // A missing key produces an empty string, not "undefined" — the gateway would treat the literal
  // string "undefined" as a session key and fail with a confusing not-found.
  assert.equal(buildSessionResumeFrame({ id: 1 }).params.session_id, "");
  assert.equal(buildSessionResumeFrame({ id: 1, sessionKey: null }).params.session_id, "");
}

// ── buildSessionCreateFrame ──────────────────────────────────────────────────────────────────
{
  const frame = buildSessionCreateFrame({ id: 9, cwd: "C:/work/repo", cols: 100, title: "aify-sc-coder" });
  assert.equal(frame.method, "session.create");
  assert.equal(frame.id, 9);
  assert.deepEqual(frame.params, { cwd: "C:/work/repo", cols: 100, title: "aify-sc-coder" });

  // Every field is optional: create is the fallback when resume fails, so it must be buildable with
  // nothing but an id rather than throwing at the moment recovery is needed.
  assert.deepEqual(buildSessionCreateFrame({ id: 1 }).params, { cwd: "", cols: 80, title: "" });
  assert.deepEqual(
    buildSessionCreateFrame({ id: 1, cwd: null, title: undefined, cols: NaN }).params,
    { cwd: "", cols: 80, title: "" },
    "null/undefined/NaN all degrade to the documented defaults",
  );
}

// ── isSessionNotFoundError, and its distinction from busy ────────────────────────────────────
{
  assert.equal(isSessionNotFoundError({ code: 4010 }), true);
  assert.equal(isSessionNotFoundError({ code: "4010" }), true, "the code is compared numerically");

  // The textual signatures are the safety net for older gateway builds that used a different code.
  for (const message of ["session not found", "No such session", "UNKNOWN SESSION: abc"]) {
    assert.equal(isSessionNotFoundError({ code: 1, message }), true, message);
  }

  assert.equal(isSessionNotFoundError(null), false);
  assert.equal(isSessionNotFoundError(undefined), false);
  assert.equal(isSessionNotFoundError({}), false);
  assert.equal(isSessionNotFoundError({ code: 4009 }), false, "busy is not not-found");
  assert.equal(isSessionNotFoundError({ message: "session busy" }), false);

  // THE DISTINCTION. The recoveries are opposites — not-found refreshes the list and retries against
  // whatever is live, busy steers into the running turn. No error may satisfy both.
  const busy = { code: 4009, message: "session busy" };
  const missing = { code: 4010, message: "session not found" };
  assert.equal(isSessionBusyError(busy) && !isSessionNotFoundError(busy), true);
  assert.equal(isSessionNotFoundError(missing) && !isSessionBusyError(missing), true);
}

// ── pickFreshestSessionFromList ──────────────────────────────────────────────────────────────
{
  const at = (iso) => ({ createdAt: iso });

  assert.equal(pickFreshestSessionFromList(null), null);
  assert.equal(pickFreshestSessionFromList({}), null);
  assert.equal(pickFreshestSessionFromList([]), null);
  assert.equal(pickFreshestSessionFromList({ sessions: [] }), null);

  // Three envelope shapes the gateway has returned.
  assert.equal(pickFreshestSessionFromList([{ id: "a", ...at("2026-06-01T00:00:00Z") }]), "a");
  assert.equal(pickFreshestSessionFromList({ sessions: [{ id: "b" }] }), "b");
  assert.equal(pickFreshestSessionFromList({ items: [{ id: "c" }] }), "c");

  // Freshest wins regardless of order in the list.
  assert.equal(
    pickFreshestSessionFromList([
      { id: "old", ...at("2026-06-01T00:00:00Z") },
      { id: "new", ...at("2026-06-09T00:00:00Z") },
    ]),
    "new",
  );
  assert.equal(
    pickFreshestSessionFromList([
      { id: "new", ...at("2026-06-09T00:00:00Z") },
      { id: "old", ...at("2026-06-01T00:00:00Z") },
    ]),
    "new",
    "the answer must not depend on list order",
  );

  // All four timestamp spellings the gateway has used.
  for (const field of ["createdAt", "created_at", "startedAt", "started_at"]) {
    const picked = pickFreshestSessionFromList([
      { id: "old", [field]: "2026-06-01T00:00:00Z" },
      { id: "new", [field]: "2026-06-09T00:00:00Z" },
    ]);
    assert.equal(picked, "new", `${field} must order the sessions`);
  }

  // Three id spellings.
  for (const field of ["id", "session_id", "sessionId"]) {
    assert.equal(pickFreshestSessionFromList([{ [field]: "x" }]), "x", field);
  }

  // A row with no id is skipped rather than returned as "".
  assert.equal(
    pickFreshestSessionFromList([{ id: "", createdAt: "2026-06-09T00:00:00Z" }, { id: "real" }]),
    "real",
  );

  // AN UNPARSEABLE TIMESTAMP IS 0, NOT NaN. NaN comparisons are false in both directions, so a
  // single bad stamp would make the result depend on iteration order.
  assert.equal(
    pickFreshestSessionFromList([
      { id: "bad", createdAt: "not a date" },
      { id: "good", createdAt: "2026-06-09T00:00:00Z" },
    ]),
    "good",
  );

  // WITH NO TIMESTAMPS ANYWHERE the FIRST row wins, because the comparison is strictly greater-than
  // and every stamp ties at 0. The module comment says "falls back to the first entry's id", and
  // this is that claim pinned — it is order-dependent by design, not by accident.
  assert.equal(pickFreshestSessionFromList([{ id: "first" }, { id: "second" }]), "first");
}

console.log("hermes-gateway-resume-frames.test.js: all assertions passed");
