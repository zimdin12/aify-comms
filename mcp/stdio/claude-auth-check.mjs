// Whether the operator's claude login still has a way back, and how long is left.
//
// WHY THIS EXISTS. Every claude-code agent on this host authenticates with one OAuth grant in
// `~/.claude/.credentials.json`. When it runs out, 21 agents stop working at once -- and nothing
// watched it. `usage-collector.js` already reads that exact file for the quota pool, so the gap was
// never access to the data; it was that nobody asked it the one question with a deadline attached.
//
// MEASURED 2026-09-03 16:36 UTC, which is why this was written: the task list recorded "claude login
// expires ~2026-09-05" and the file said the access token expired at 23:09 THAT NIGHT and the
// refresh token at 11:57 the next morning. Roughly two days optimistic, in the direction where being
// wrong means a fleet that stops overnight with the operator asleep.
//
// THE ACCESS TOKEN EXPIRING IS ROUTINE AND MUST NOT ALARM. It is short-lived and refreshed silently
// while the refresh token is valid -- an hourly check that went red every hour would be switched off
// within a day, and then the real deadline would pass unwatched too. Only the REFRESH token's window
// requires a human, so only that one decides the verdict. The access token's age is reported as
// context, never as a verdict.
//
// IT NEVER READS THE TOKENS. Only the two expiry stamps beside them. A health check that handles a
// credential is a credential in one more place, and this one is called by a tool whose whole output
// is meant to be pasted into a report.

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/** Under this much refresh-token life, a human has to log in before it lapses. */
export const CLAUDE_REFRESH_WARN_HOURS = 24;

export const CLAUDE_LOGIN_OK = "ok";
export const CLAUDE_LOGIN_EXPIRING = "expiring";
export const CLAUDE_LOGIN_EXPIRED = "expired";
export const CLAUDE_LOGIN_UNKNOWN = "unknown";

/**
 * Classify a credentials payload, without touching what is in it beyond two timestamps.
 *
 * FOUR STATES, NOT A BOOLEAN, for the reason `spawn-claimer.mjs` gives for the same choice: an
 * absent file and an expired grant are different facts, and collapsing them makes one of the two
 * read as the other. UNKNOWN here means this host has no claude login recorded at all -- which on a
 * machine running codex or hermes agents is perfectly normal and must not read as broken.
 *
 * @param {unknown} creds parsed `~/.claude/.credentials.json`
 * @param {number} now epoch ms
 */
export function claudeLoginStateAt(creds, now) {
  const oauth = creds && typeof creds === "object" ? creds.claudeAiOauth : null;
  if (!oauth || typeof oauth !== "object") return { state: CLAUDE_LOGIN_UNKNOWN, detail: "" };

  const refreshAt = Number(oauth.refreshTokenExpiresAt);
  const accessAt = Number(oauth.expiresAt);
  // A STAMP THAT IS NOT A NUMBER IS NOT A DEADLINE. Reporting "expired" for an unparseable value
  // would send an operator to re-authenticate a login that may be perfectly healthy.
  if (!Number.isFinite(refreshAt) || refreshAt <= 0) return { state: CLAUDE_LOGIN_UNKNOWN, detail: "" };

  const refreshHours = (refreshAt - now) / 3_600_000;
  const accessHours = Number.isFinite(accessAt) ? (accessAt - now) / 3_600_000 : null;
  // CONTEXT, NOT A VERDICT: an expired access token beside a healthy refresh token is the ordinary
  // state between two calls, so it is reported and never counted.
  const access = accessHours === null
    ? ""
    : accessHours < 0
      ? " The short-lived access token has lapsed and refreshes on next use, which is normal."
      : ` The access token has ${accessHours.toFixed(1)}h left and renews itself.`;

  if (refreshHours <= 0) {
    return {
      state: CLAUDE_LOGIN_EXPIRED,
      detail: `the claude login has EXPIRED (${Math.abs(refreshHours).toFixed(1)}h ago). `
        + `Every claude-code agent on this host needs it: run \`claude\` once and log in.`,
    };
  }
  if (refreshHours < CLAUDE_REFRESH_WARN_HOURS) {
    return {
      state: CLAUDE_LOGIN_EXPIRING,
      detail: `the claude login lapses in ${refreshHours.toFixed(1)}h and cannot renew itself past `
        + `that. Log in before then, or every claude-code agent stops at once.${access}`,
    };
  }
  return {
    state: CLAUDE_LOGIN_OK,
    detail: `the claude login is good for ${Math.floor(refreshHours)}h.${access}`,
  };
}

/** Where claude keeps it. Named once; `usage-collector.js` reads the same path for the quota pool. */
export function claudeCredentialsPath(home = homedir()) {
  return join(home, ".claude", ".credentials.json");
}

/**
 * The `claude-login` check.
 *
 * UNKNOWN IS A SKIP, NOT A FAILURE. A host running only codex or hermes agents has no claude login
 * and is not broken; failing there would teach an operator that this row is noise. A host that DOES
 * run claude agents and has no login will fail on `usage-anthropic` and on the agents themselves.
 *
 * @param {{add: Function, skip: Function, readFile?: Function, now?: () => number, home?: string}} deps
 */
export function checkClaudeLogin({ add, skip, readFile = readFileSync, now = Date.now, home }) {
  const path = claudeCredentialsPath(home);
  let parsed;
  try {
    parsed = JSON.parse(String(readFile(path, "utf8")));
  } catch {
    return skip("claude-login", "no claude credentials on this host, so there is no login to age");
  }
  const verdict = claudeLoginStateAt(parsed, now());
  if (verdict.state === CLAUDE_LOGIN_UNKNOWN) {
    return skip("claude-login", "claude credentials carry no readable expiry, so nothing was measured");
  }
  const ok = verdict.state === CLAUDE_LOGIN_OK;
  return add("claude-login", ok, verdict.state, verdict.detail,
    ok ? "" : "Run `claude` in a terminal and complete the login.");
}
