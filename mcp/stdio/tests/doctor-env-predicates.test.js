// aify-doctor's env-bridge predicates — the check with the worst track record in this repo.
//
// v0.2 item B2, done in v0.1. It shipped the SAME false green twice, and both times the reason it
// survived was structural: doctor.js runs its checks at import and calls process.exit(), so no test
// could reach the predicates. They now live in doctor-predicates.js.
//
// The payload in `test_all_offline_payload_reports_zero_connected` is the shape that actually
// produced the "2 connected" lie with zero bridges alive.

import assert from "node:assert/strict";

import {
  ENV_FUTURE_SKEW_MS,
  ENV_STALE_AFTER_MS,
  describeEnv,
  envIsOnline,
  envIsOnlineAt,
  envLastSeenMs,
  envStateIsUnknown,
  bridgeInstallVerdict,
} from "../doctor-predicates.js";

const NOW = Date.parse("2026-07-26T12:00:00Z");
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// --- the false green that shipped twice -------------------------------------------------------

test("an all-offline payload yields zero connected environments", () => {
  // The `756f3a5` regression: this list has TWO registered rows and doctor reported "2 connected".
  const list = [
    { id: "env-stale-24h", status: "online", lastSeen: iso(24 * 60 * 60 * 1000) },
    { id: "env-dead-7w", status: "online", lastSeen: iso(49 * 24 * 60 * 60 * 1000) },
  ];
  assert.equal(list.filter((e) => envIsOnlineAt(e, NOW)).length, 0);
});

test("a degraded environment is NOT connected — the spawn picker requires online", () => {
  // R3b: doctor must not be greener than the thing it claims to prove.
  assert.equal(envIsOnlineAt({ id: "e", status: "degraded", lastSeen: iso(1000) }, NOW), false);
});

test("a FUTURE lastSeen does not pass the staleness bound", () => {
  // R3a: `now - seen` goes negative and trivially satisfies `<= ENV_STALE_AFTER_MS`.
  for (const ms of [60 * 1000, 24 * 60 * 60 * 1000, 365 * 24 * 60 * 60 * 1000]) {
    assert.equal(
      envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(-ms) }, NOW),
      false,
      `a lastSeen ${ms}ms in the future must not read as connected`,
    );
  }
});

test("an undatable row is not connected", () => {
  for (const lastSeen of ["", null, undefined, "not-a-date", "   "]) {
    assert.equal(
      envIsOnlineAt({ id: "e", status: "online", lastSeen }, NOW),
      false,
      `lastSeen ${JSON.stringify(lastSeen)} cannot prove liveness`,
    );
  }
});

// --- the boundary ------------------------------------------------------------------------------

test("a fresh online environment is connected", () => {
  assert.equal(envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(5000) }, NOW), true);
});

test("age 0 and exactly the bound are connected; one ms past it is not", () => {
  assert.equal(envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(0) }, NOW), true);
  assert.equal(envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(ENV_STALE_AFTER_MS) }, NOW), true);
  assert.equal(envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(ENV_STALE_AFTER_MS + 1) }, NOW), false);
});

test("status is matched case- and whitespace-insensitively", () => {
  assert.equal(envIsOnlineAt({ id: "e", status: "  ONLINE ", lastSeen: iso(1000) }, NOW), true);
});

test("a stamp a few seconds ahead is CLOCK SKEW, not a bogus future stamp", () => {
  // FALSE RED, live 2026-08-03. doctor reported "No environment bridge is ONLINE — dashboard-managed
  // spawns cannot run" while printing that very row as `[online, last seen ...]`, and the bridge was
  // heartbeating every few seconds throughout.
  //
  // The service writes `last_seen` from inside the CONTAINER; doctor evaluates it on the HOST. On
  // this machine the container clock measured 4.1s AHEAD of the host, so any heartbeat newer than
  // ~4s carried a timestamp in doctor's future, `age >= 0` rejected it, and a live bridge scored
  // EXACTLY the same as a 24h-dead one. Whether doctor passed depended on where in the heartbeat
  // cycle it happened to run.
  //
  // R3a's guard is still right — a bogus far-future stamp must not green a dead bridge — but it had
  // zero tolerance for the ordinary container-vs-host drift this deployment always has. So the
  // rejection now starts beyond a bounded skew allowance instead of at zero. The 60s/24h/365d cases
  // in the R3a test above must keep failing, which is why the allowance sits well below 60s.
  for (const ms of [1000, 4100, ENV_FUTURE_SKEW_MS]) {
    assert.equal(
      envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(-ms) }, NOW),
      true,
      `a stamp ${ms}ms ahead is ordinary clock skew and must still read as connected`,
    );
  }
  assert.equal(
    envIsOnlineAt({ id: "e", status: "online", lastSeen: iso(-(ENV_FUTURE_SKEW_MS + 1)) }, NOW),
    false,
    "one ms past the skew allowance is a bogus stamp again",
  );
  assert.ok(ENV_FUTURE_SKEW_MS < 60 * 1000, "must stay under the 60s the R3a test pins as bogus");
});

test("a naive timestamp is read as UTC, not local time", () => {
  // The server writes `%Y-%m-%dT%H:%M:%SZ`, but a row without the Z must not be parsed in the
  // host's zone — that would shift the age by the UTC offset and flip liveness either way.
  const naive = new Date(NOW - 1000).toISOString().replace("Z", "").replace(/\.\d+$/, "");
  assert.equal(Number.isNaN(envLastSeenMs({ lastSeen: naive })), false);
  assert.equal(envIsOnlineAt({ id: "e", status: "online", lastSeen: naive }, NOW), true);
});

test("a missing or malformed env object does not throw", () => {
  for (const env of [undefined, null, {}, { status: 5 }]) {
    assert.equal(envIsOnlineAt(env, NOW), false);
  }
});

// --- the arity trap --------------------------------------------------------------------------

test("envIsOnline is safe as a filter callback — filter passes (element, index, array)", () => {
  // REGRESSION, caught live before it shipped. An earlier cut of this module had a single
  // `envIsOnline(env, now = Date.now())`, and doctor calls `list.filter(envIsOnline)`. Filter
  // supplies the INDEX as the second argument, so `now` became 0 for the first element: the age
  // went hugely negative, the `age >= 0` guard rejected it, and a bridge that had beaten 68 seconds
  // earlier reported as not connected. The clock is injectable ONLY via envIsOnlineAt for this
  // reason; envIsOnline must stay arity-1.
  assert.equal(envIsOnline.length, 1, "envIsOnline must take exactly one argument");
  const fresh = [{ id: "env-fresh", status: "online", lastSeen: new Date().toISOString() }];
  assert.deepEqual(
    fresh.filter(envIsOnline).map((e) => e.id),
    ["env-fresh"],
    "a fresh online bridge must survive `filter(envIsOnline)` regardless of its index",
  );
  // And at a non-zero index, which is where an index-as-clock bug hides from a 1-element fixture.
  const padded = [
    { id: "env-dead", status: "offline", lastSeen: new Date().toISOString() },
    { id: "env-fresh", status: "online", lastSeen: new Date().toISOString() },
  ];
  assert.deepEqual(padded.filter(envIsOnline).map((e) => e.id), ["env-fresh"]);
});

// --- the remaining predicates ------------------------------------------------------------------

test("known server-side statuses are not flagged unknown", () => {
  // These are the five the server can serve: three from registration (api_v2.py:10105) plus the
  // two server-side ones. A NEW status appearing here should trip the unknown warning, not be
  // silently treated as offline.
  for (const status of ["online", "degraded", "offline", "forgotten", "disabled"]) {
    assert.equal(envStateIsUnknown({ status }), false, `${status} is a known status`);
  }
});

test("an unrecognised status is flagged unknown", () => {
  for (const status of ["connected", "ready", "active", "", undefined]) {
    assert.equal(envStateIsUnknown({ status }), true, `${status} must be flagged, not assumed dead`);
  }
});

test("describeEnv names the row and its age so the failure is actionable", () => {
  assert.equal(
    describeEnv({ id: "env-1", status: "Online", lastSeen: "2026-07-25T10:00:00Z" }),
    "env-1 [online, last seen 2026-07-25T10:00:00Z]",
  );
  assert.equal(describeEnv({}), "(unnamed) [unknown]");
});

// ── N13: `bridge-installed` must key on BRIDGE changes, not on repo HEAD ──────────────
// It used to fail whenever the marker sha != HEAD, so a docs-only or service-only commit reported
// "the bridge is stale, re-run install.sh" — false, and repeated often enough to train the operator
// to skim past the one check that catches a genuinely silent failure.

test("bridge-installed: marker == HEAD is clean", () => {
  const v = bridgeInstallVerdict({ installedSha: "abc1234def", headSha: "abc1234def", headShort: "abc1234" });
  assert.equal(v.ok, true);
  assert.equal(v.code, "ok");
  assert.match(v.detail, /== repo HEAD/);
});

test("bridge-installed: behind by commits that DO touch mcp/stdio is stale", () => {
  const v = bridgeInstallVerdict({
    installedSha: "old1111", headSha: "new2222", headShort: "new2222",
    bridgeCommits: 2, totalCommits: 9,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale");
  assert.match(v.detail, /2 commit\(s\) since then changed mcp\/stdio/);
  assert.match(v.fix, /install\.sh/);
  // The relaunch half matters as much as the copy half: install.sh updates the files on disk, but a
  // RUNNING wrapper keeps the code it loaded at boot.
  assert.match(v.fix, /relaunch/i);
});

test("N13 REGRESSION: behind by commits that do NOT touch mcp/stdio is CLEAN, not stale", () => {
  const v = bridgeInstallVerdict({
    installedSha: "old1111", headSha: "new2222", headShort: "new2222",
    bridgeCommits: 0, totalCommits: 8,
  });
  assert.equal(v.ok, true, "a docs-only or service-only commit must not report the bridge as stale");
  assert.equal(v.code, "ok");
  assert.match(v.detail, /none touching mcp\/stdio/);
  assert.equal(v.fix, "", "there is nothing for the operator to do, so offer no fix");
});

test("bridge-installed: the count is reported so 'clean' is auditable, not just asserted", () => {
  const v = bridgeInstallVerdict({
    installedSha: "old1111", headSha: "new2222", headShort: "new2222",
    bridgeCommits: 0, totalCommits: 8,
  });
  assert.match(v.detail, /8 commit\(s\) ahead/);
});

test("bridge-installed: no marker sha, and no checkout, are distinct outcomes", () => {
  const noMarker = bridgeInstallVerdict({ installedSha: "", headSha: "x" });
  assert.equal(noMarker.ok, false);
  assert.equal(noMarker.code, "unknown-version");

  const noRepo = bridgeInstallVerdict({ installedSha: "abc1234", headSha: "" });
  assert.equal(noRepo.ok, true, "no checkout to compare against is not a failure");
  assert.match(noRepo.detail, /no checkout/);
});

test("bridge-installed: degenerate counts do not flip the verdict", () => {
  // Enumerating the degenerate numeric inputs, not just the happy path — a missing/NaN count from a
  // failed `git` call must not read as "bridge changed".
  for (const bridgeCommits of [undefined, null, 0, "", "0", NaN]) {
    const v = bridgeInstallVerdict({
      installedSha: "old1111", headSha: "new2222", headShort: "new2222", bridgeCommits, totalCommits: 3,
    });
    assert.equal(v.ok, true, `bridgeCommits=${String(bridgeCommits)} must not report stale`);
  }
  for (const bridgeCommits of [1, "1", 7]) {
    const v = bridgeInstallVerdict({
      installedSha: "old1111", headSha: "new2222", headShort: "new2222", bridgeCommits, totalCommits: 9,
    });
    assert.equal(v.ok, false, `bridgeCommits=${String(bridgeCommits)} must report stale`);
  }
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`  ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL ${name}`);
    console.log(`       ${error.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} doctor env-predicate tests passed`);
if (failed) process.exit(1);
