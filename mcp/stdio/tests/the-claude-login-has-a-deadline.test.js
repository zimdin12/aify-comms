// The one credential 21 agents share, and nothing was watching it.
//
// WHY THIS EXISTS, measured 2026-09-03 16:36 UTC. The task list recorded "claude login expires
// ~2026-09-05". `~/.claude/.credentials.json` said the access token expired at 23:09 THAT NIGHT and
// the refresh token at 11:57 the next morning -- roughly two days optimistic, in the direction where
// being wrong means a fleet that stops overnight with the operator asleep. `usage-collector.js` had
// been reading that same file for the quota pool all along, so the gap was never access to the data:
// nobody asked it the one question with a deadline attached.

import { test } from "node:test";
import assert from "node:assert/strict";

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  CLAUDE_LOGIN_EXPIRED,
  CLAUDE_LOGIN_EXPIRING,
  CLAUDE_LOGIN_OK,
  CLAUDE_LOGIN_UNKNOWN,
  CLAUDE_REFRESH_WARN_HOURS,
  checkClaudeLogin,
  claudeCredentialsPath,
  claudeLoginStateAt,
} from "../claude-auth-check.mjs";

const NOW = Date.UTC(2026, 8, 3, 16, 36);
const hours = (n) => NOW + n * 3_600_000;
const creds = (over = {}) => ({
  claudeAiOauth: {
    accessToken: "sk-ant-SECRET-DO-NOT-PRINT",
    refreshToken: "refresh-SECRET-DO-NOT-PRINT",
    expiresAt: hours(6.5),
    refreshTokenExpiresAt: hours(19.3),
    ...over,
  },
});

test("A HEALTHY LOGIN READS OK", () => {
  // POSITIVE CONTROL. Most assertions below are about NOT being ok, and a classifier that never
  // returned ok would satisfy them all while making the row permanently red.
  const v = claudeLoginStateAt(creds({ refreshTokenExpiresAt: hours(240) }), NOW);
  assert.equal(v.state, CLAUDE_LOGIN_OK);
  assert.match(v.detail, /good for 240h/);
});

test("THE LIVE SHAPE THAT PROMPTED THIS: 19.3h of refresh left is EXPIRING", () => {
  const v = claudeLoginStateAt(creds(), NOW);
  assert.equal(v.state, CLAUDE_LOGIN_EXPIRING);
  assert.match(v.detail, /19\.3h/);
  assert.match(v.detail, /every claude-code agent stops at once/,
    "the consequence is the whole reason to act, so it has to be in the row");
});

test("AN EXPIRED ACCESS TOKEN BESIDE A HEALTHY REFRESH IS STILL OK", () => {
  // THE CRIES-WOLF TRAP, and the reason the access token cannot decide the verdict. It is
  // short-lived and refreshed silently on next use, so this is the ORDINARY state between two
  // calls. A check that went red for it would go red most hours, get switched off, and then the
  // real deadline would pass unwatched too.
  const v = claudeLoginStateAt(creds({ expiresAt: hours(-2), refreshTokenExpiresAt: hours(240) }), NOW);
  assert.equal(v.state, CLAUDE_LOGIN_OK);
  assert.match(v.detail, /lapsed and refreshes on next use, which is normal/);
});

test("an expired REFRESH token is terminal and says what to do", () => {
  const v = claudeLoginStateAt(creds({ refreshTokenExpiresAt: hours(-3) }), NOW);
  assert.equal(v.state, CLAUDE_LOGIN_EXPIRED);
  assert.match(v.detail, /EXPIRED \(3\.0h ago\)/);
  assert.match(v.detail, /log in/i);
});

test("the boundary is the refresh window, not the access one", () => {
  const justInside = claudeLoginStateAt(creds({ refreshTokenExpiresAt: hours(CLAUDE_REFRESH_WARN_HOURS - 0.1) }), NOW);
  const justOutside = claudeLoginStateAt(creds({ refreshTokenExpiresAt: hours(CLAUDE_REFRESH_WARN_HOURS + 0.1) }), NOW);
  assert.equal(justInside.state, CLAUDE_LOGIN_EXPIRING);
  assert.equal(justOutside.state, CLAUDE_LOGIN_OK);
});

test("IT NEVER PUTS A TOKEN IN ITS OUTPUT", () => {
  // This row is meant to be pasted into a report. A health check that handles a credential is a
  // credential in one more place, so it reads two timestamps and nothing else.
  for (const over of [{}, { refreshTokenExpiresAt: hours(-1) }, { refreshTokenExpiresAt: hours(500) }]) {
    const v = claudeLoginStateAt(creds(over), NOW);
    assert.ok(!/SECRET-DO-NOT-PRINT/.test(v.detail), `a token reached the detail: ${v.detail}`);
  }
});

test("A HOST WITH NO CLAUDE LOGIN IS NOT BROKEN", () => {
  // A machine running only codex or hermes agents has no claude grant. Failing there teaches an
  // operator that this row is noise, and then it is noise on the day it matters.
  assert.equal(claudeLoginStateAt({}, NOW).state, CLAUDE_LOGIN_UNKNOWN);
  assert.equal(claudeLoginStateAt(null, NOW).state, CLAUDE_LOGIN_UNKNOWN);
  assert.equal(claudeLoginStateAt({ claudeAiOauth: "nonsense" }, NOW).state, CLAUDE_LOGIN_UNKNOWN);
});

test("AN UNREADABLE STAMP IS UNKNOWN, NEVER EXPIRED", () => {
  // Reporting "expired" for a value that could not be parsed would send an operator to
  // re-authenticate a login that may be perfectly healthy -- inventing a verdict from no evidence,
  // which is the shape `a2f9e42` exists to refuse.
  for (const bad of [undefined, null, "", "soon", NaN, 0, -1]) {
    assert.equal(claudeLoginStateAt(creds({ refreshTokenExpiresAt: bad }), NOW).state, CLAUDE_LOGIN_UNKNOWN,
      `refreshTokenExpiresAt=${JSON.stringify(bad)} was not treated as unknown`);
  }
});

test("the CHECK skips rather than fails when the file is absent", () => {
  const rows = [];
  checkClaudeLogin({
    add: (id, ok, code, detail) => rows.push({ id, ok, code, detail }),
    skip: (id, detail) => rows.push({ id, skipped: true, detail }),
    readFile: () => { throw Object.assign(new Error("ENOENT"), { code: "ENOENT" }); },
    now: () => NOW,
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].skipped, true, "a host with no claude login was reported as a failure");
  assert.match(rows[0].detail, /no claude credentials/);
});

test("THE CHECK REPORTS THE VERDICT ITS CLASSIFIER PRODUCED, and offers the fix", () => {
  // The call site, not just the predicate: a classifier proven correct and a check that reports
  // something else is the disconnected-call-site defect this project keeps finding.
  const rows = [];
  checkClaudeLogin({
    add: (id, ok, code, detail, fix) => rows.push({ id, ok, code, detail, fix }),
    skip: () => assert.fail("a readable, expiring login was skipped"),
    readFile: () => JSON.stringify(creds()),
    now: () => NOW,
  });
  assert.equal(rows[0].id, "claude-login");
  assert.equal(rows[0].code, CLAUDE_LOGIN_EXPIRING);
  assert.equal(rows[0].ok, false);
  assert.match(rows[0].fix, /Run `claude`/);
});

test("and it reports ok WITHOUT a fix when there is nothing to do", () => {
  const rows = [];
  checkClaudeLogin({
    add: (id, ok, code, detail, fix) => rows.push({ ok, fix }),
    skip: () => assert.fail("a healthy login was skipped"),
    readFile: () => JSON.stringify(creds({ refreshTokenExpiresAt: hours(500) })),
    now: () => NOW,
  });
  assert.equal(rows[0].ok, true);
  assert.equal(rows[0].fix, "", "a passing row offered a remedy for nothing");
});

test("BOTH READERS OF THAT FILE AGREE ON WHERE IT IS", () => {
  // `usage-collector.js` has read `~/.claude/.credentials.json` all along, for the quota pool, with
  // the path spelled inline. This module names it once and exports it. Two spellings of one location
  // agree until somebody edits one, and the symptom would be silent: a check that always skips
  // because it looks in the wrong place, on a host where the login is about to lapse.
  //
  // Compared as SEGMENTS rather than as a whole path, because the two build it differently (one
  // takes a `home` argument for testability) and a string compare would fail on that alone.
  const ours = claudeCredentialsPath("/home/op");
  assert.equal(ours, join("/home/op", ".claude", ".credentials.json"));

  const theirs = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "usage-collector.js"), "utf8");
  assert.match(theirs, /"\.claude"\s*,\s*"\.credentials\.json"/,
    "usage-collector.js no longer reads ~/.claude/.credentials.json the same way; one of the two moved");
});
