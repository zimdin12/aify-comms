// Pure predicates behind aify-doctor's `env-bridge` check.
//
// Extracted from doctor.js (v0.2 item B2, done in v0.1) for ONE reason: doctor.js is a top-level
// script that runs every check at import and ends in `process.exit()`, so it cannot be imported by
// a test. That structural fact is why the check with the worst track record in this repo had zero
// unit coverage — and it shipped the same false green twice:
//
//   1. `756f3a5` — the check counted REGISTERED rows and reported "2 connected" with zero bridges
//      alive (one stale 24h, one ~7 weeks).
//   2. review R3/R3a — a FUTURE or unparseable `lastSeen` slipped through the staleness bound, and
//      `degraded` was treated as usable for spawn when the spawn picker requires `online`.
//
// Behaviour here is identical to what doctor.js did before the move; the tests are the new part.
// No I/O, no clock reads except the `now` a caller passes in, so every branch is directly testable.

// ONLINE ONLY — matched to the SPAWN PICKER, which is the thing the env-bridge check claims to
// prove. `api_v2.py` (env selection for a cold start) does `if status.lower() != "online": continue`,
// so a `degraded` environment CANNOT host a managed spawn. An earlier version counted degraded as
// connected, which let doctor read green while no spawn could actually run — the same false-green
// class, one layer along (review R3b). Note the codebase has a THIRD, looser notion: the
// reachability test in api_v2 accepts {online, degraded} when deciding whether an agent is merely
// reachable. That is a different question and is deliberately left alone; "can host a new spawn" is
// the one this check is about.
export const ENV_CONNECTED_STATES = new Set(["online"]);
export const ENV_KNOWN_STATES = new Set(["online", "degraded", "offline", "forgotten", "disabled"]);
// Independent staleness bound. The server derives liveness from `last_seen`, and a bug there is
// exactly how a dead bridge got reported as live twice now (first the row-count check, then
// `degraded` never ageing out because the staleness test was gated on status == "online"). A
// verifier whose whole job is to fail loudly must not depend solely on the value under test — so
// doctor ALSO ages the row itself. Generous vs `environment_offline_seconds` (90s default): this is
// a backstop against a broken derivation, not a second opinion on normal jitter.
export const ENV_STALE_AFTER_MS = 10 * 60 * 1000;

export function envLastSeenMs(env) {
  const raw = String(env?.lastSeen || "").trim();
  if (!raw) return NaN;
  return Date.parse(raw.endsWith("Z") || raw.includes("+") ? raw : `${raw}Z`);
}

// Split in two ON PURPOSE — do not merge them back into one function with a defaulted `now`.
// That is what the first version did, and it broke the check immediately: doctor calls
// `list.filter(envIsOnline)`, and `Array.prototype.filter` invokes its callback with
// (element, INDEX, array). The index bound to `now`, so `now - seen` for element 0 was a hugely
// negative age, the `age >= 0` guard rejected it, and a bridge that had beaten 68 seconds ago read
// as not connected. A false RED this time, but the same root shape as the false greens above: a
// predicate whose arity is larger than its callers pass.
//
// So `envIsOnline` takes EXACTLY ONE argument and is safe to hand to filter/map/some; the clock
// is injectable only through `envIsOnlineAt`, which is what the tests drive.
export function envIsOnline(env) {
  return envIsOnlineAt(env, Date.now());
}

export function envIsOnlineAt(env, now) {
  if (!ENV_CONNECTED_STATES.has(String(env?.status || "").trim().toLowerCase())) return false;
  const seen = envLastSeenMs(env);
  // Unparseable or MISSING lastSeen → NOT connected. An earlier version trusted the served status
  // here "rather than invent a failure", but this check exists to fail loudly: a row we cannot date
  // is a row we cannot prove is alive, and every false green in this file so far came from treating
  // unprovable as fine. The detail line names the row so the cause is obvious.
  if (Number.isNaN(seen)) return false;
  // A FUTURE lastSeen must NOT pass (review R3, 2026-07-26): `now - seen` goes negative and
  // trivially satisfies the bound, so a clock-skewed or bogus stamp would green a dead bridge — the
  // very false green this check exists to catch. Require a non-negative age.
  const age = now - seen;
  return age >= 0 && age <= ENV_STALE_AFTER_MS;
}

export function envStateIsUnknown(env) {
  return !ENV_KNOWN_STATES.has(String(env?.status || "").trim().toLowerCase());
}

export function describeEnv(env) {
  const seen = String(env?.lastSeen || "").trim();
  const state = String(env?.status || "unknown").trim().toLowerCase() || "unknown";
  return `${env?.id || "(unnamed)"} [${state}${seen ? `, last seen ${seen}` : ""}]`;
}
