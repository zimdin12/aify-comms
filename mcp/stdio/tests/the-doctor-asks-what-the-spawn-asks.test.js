// `env-bridge` answered from the field that does not decide whether a spawn can run.
//
// THE INCIDENT, 2026-09-02. The environment row read `status: online, lastSeen 17:26:41Z` while its
// `metadata.bridgeLastSeen` was `2026-09-01T15:38:12Z`. This doctor reported "1 online", `comms_envs`
// agreed, and `/spawn` returned 409 in the same minute -- correctly, because there had been no bridge
// for a day. The operator's manager was told the fleet was ready, tried six spawns, and was refused
// six times.
//
// WHY THE TWO EVER AGREED. `status` and `lastSeen` are aged from `last_seen`, which only a bridge
// used to write. Since 2026-08-30 aify-env heartbeats that same row to DESCRIBE the host, so a fresh
// `last_seen` no longer implies anything can start a process there. The service split the question
// then -- `/spawn` asks `environment_has_live_bridge()`, which reads `bridgeLastSeen` -- and the
// doctor never followed. They kept agreeing only because aify-env's advertisements were failing with
// 401; fixing that credential broke the coincidence and exposed the conflation.
//
// SO THE RULE THIS FILE PINS: a check whose job is "can dashboard-managed spawns run" must ask what
// the spawn asks. Not something correlated with it.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  bridgeStampStateAt,
  bridgeStampAgeAt,
  envCanClaimASpawn,
  envCanClaimASpawnAt,
  SPAWN_CLAIMER_FRESH_SECONDS,
  BRIDGE_STAMP_SKEW_SECONDS,
  BRIDGE_STAMP_FRESH,
  BRIDGE_STAMP_STALE,
  BRIDGE_STAMP_ABSENT,
  BRIDGE_STAMP_INVALID,
} from "../spawn-claimer.mjs";
// The OTHER question, imported from where it still lives, so the contrast test below compares two
// modules rather than two names in one file -- which is the separation the fix actually made.
import { envIsOnlineAt } from "../doctor-predicates.js";

const NOW = Date.parse("2026-09-02T17:30:00Z");
const iso = (secondsAgo) => new Date(NOW - secondsAgo * 1000).toISOString();

/** An environment row shaped like the API's, with the two timestamps set independently. */
const row = ({ status = "online", lastSeen = iso(5), bridgeLastSeen = null } = {}) => ({
  id: "windows:host:default",
  status,
  lastSeen,
  metadata: bridgeLastSeen === null ? {} : { bridgeLastSeen },
});

test("THE INCIDENT: advertised online, no bridge for a day", () => {
  // The exact pair measured on the operator's machine.
  const env = row({ status: "online", lastSeen: "2026-09-02T17:26:41Z", bridgeLastSeen: "2026-09-01T15:38:12Z" });
  assert.equal(envIsOnlineAt(env, NOW), true, "CONTROL: the old predicate still reads this as online");
  assert.equal(envCanClaimASpawnAt(env, NOW), false,
    "the doctor would again tell an operator the fleet is ready while every spawn 409s");
});

test("a fresh bridge stamp can claim a spawn", () => {
  assert.equal(envCanClaimASpawnAt(row({ bridgeLastSeen: iso(10) }), NOW), true);
});

test("the boundary is the stated one, on both sides", () => {
  assert.equal(bridgeStampStateAt(row({ bridgeLastSeen: iso(SPAWN_CLAIMER_FRESH_SECONDS - 5) }), NOW), BRIDGE_STAMP_FRESH);
  assert.equal(bridgeStampStateAt(row({ bridgeLastSeen: iso(SPAWN_CLAIMER_FRESH_SECONDS + 5) }), NOW), BRIDGE_STAMP_STALE);
});

test("ABSENT is its own answer, not a yes and not a no", () => {
  // The collapse was got backwards once: ABSENT read as "unknown, and unknown means yes" kept every
  // row registered before the field existed authorised for ever. The doctor cannot resolve it --
  // the service consults `bridge_instances`, which no endpoint exposes -- so it must not claim
  // either way, and must not claim a spawn can run.
  assert.equal(bridgeStampStateAt(row({ bridgeLastSeen: null }), NOW), BRIDGE_STAMP_ABSENT);
  assert.equal(envCanClaimASpawnAt(row({ bridgeLastSeen: null }), NOW), false);
});

test("an unreadable stamp is corrupt data, never a heartbeat", () => {
  // INVALID read as ABSENT once turned a bad write into authorisation.
  assert.equal(bridgeStampStateAt(row({ bridgeLastSeen: "not-a-date" }), NOW), BRIDGE_STAMP_INVALID);
  assert.equal(envCanClaimASpawnAt(row({ bridgeLastSeen: "not-a-date" }), NOW), false);
});

test("ordinary clock skew is tolerated; a stamp far ahead is not", () => {
  // NOT ZERO TOLERANCE: a container clock 4.1s ahead of the host once made this doctor call every
  // environment dead. Beyond the tolerance a future stamp would read live for ever.
  assert.equal(envCanClaimASpawnAt(row({ bridgeLastSeen: iso(-5) }), NOW), true, "5s of skew must pass");
  assert.equal(
    bridgeStampStateAt(row({ bridgeLastSeen: iso(-(BRIDGE_STAMP_SKEW_SECONDS + 60)) }), NOW),
    BRIDGE_STAMP_INVALID,
  );
});

test("the age is reported in the operator's terms", () => {
  assert.equal(bridgeStampAgeAt(row({ bridgeLastSeen: iso(30) }), NOW), "30s ago");
  assert.equal(bridgeStampAgeAt(row({ bridgeLastSeen: iso(3600) }), NOW), "60m ago");
  assert.equal(bridgeStampAgeAt(row({ bridgeLastSeen: iso(26 * 3600) }), NOW), "26h ago");
  assert.equal(bridgeStampAgeAt(row({ bridgeLastSeen: null }), NOW), "", "nothing to age is not '0s ago'");
});

test("ONE NUMBER: the doctor's freshness window equals the service's", () => {
  // The constant is declared in both languages because the doctor is JS and the rule is Python. This
  // repo's answer to unavoidable duplication is an agreement test, and it earns its place here: the
  // service's own comment records that two numbers for this one question (90 against 120) made the
  // SAME bridge live at age 100s on one path and dead on the other.
  const source = readFileSync(
    fileURLToPath(new URL("../../../service/env_status.py", import.meta.url)),
    "utf8",
  );
  const match = source.match(/^SPAWN_CLAIMER_FRESH_SECONDS\s*=\s*(\d+)/m);
  assert.ok(match, "service/env_status.py no longer declares SPAWN_CLAIMER_FRESH_SECONDS; this gate is blind");
  assert.equal(
    SPAWN_CLAIMER_FRESH_SECONDS, Number(match[1]),
    "the doctor and the spawn path disagree about how fresh a bridge stamp must be, so the doctor "
    + "will report a spawn as runnable that the service refuses, or the reverse",
  );
});

test("CONTROL: the two predicates genuinely differ, so neither test above is vacuous", () => {
  // If `envCanClaimASpawnAt` merely re-implemented `envIsOnlineAt`, every assertion here would hold
  // while the defect remained. One row where they disagree is the whole point of the change.
  const advertised = row({ status: "online", lastSeen: iso(5), bridgeLastSeen: iso(86400) });
  assert.notEqual(envIsOnlineAt(advertised, NOW), envCanClaimASpawnAt(advertised, NOW));
});

test("envCanClaimASpawn reads the clock so callers do not each pass one", () => {
  // The wrapper every check actually calls. Tested separately because a wrapper that dropped the
  // argument, or inverted the answer, would leave all the `At` tests above green while every check
  // in the doctor read the opposite -- and `now` is the one thing they cannot inject.
  const live = { id: "x", status: "online", metadata: { bridgeLastSeen: new Date().toISOString() } };
  const dead = { id: "y", status: "online", metadata: { bridgeLastSeen: "2020-01-01T00:00:00Z" } };
  assert.equal(envCanClaimASpawn(live), true);
  assert.equal(envCanClaimASpawn(dead), false);
});
