// Whether an environment can CLAIM a spawn -- the one question `/spawn` actually asks.
//
// WHY THIS IS ITS OWN MODULE. It began inside `doctor-predicates.js`, which was right for the
// doctor and wrong for everyone else: the tool an AGENT consults before spawning is `comms_envs`,
// and an MCP tool group must not import the doctor to ask a question that is not the doctor's. The
// subject here is SPAWN CLAIMING, so it owns a file and both callers import it.
//
// MEASURED 2026-09-02, and this is the whole reason the file exists: `comms_envs` rendered
// `windows:StevenZ-L:default [online]` while `/spawn` returned 409 in the same minute, and an agent
// correctly trusting the tool reported the fleet ready and was refused six times. Two instruments,
// two different fields, and the one the agent reads was the wrong one. Now there is one.
//
// PURE, and imports nothing: it is judged entirely from a row and a clock, which is what lets both
// the doctor and the tool ask it without either dragging the other into its process.

//: CAN ANYTHING CLAIM A SPAWN HERE -- a DIFFERENT question from `envIsOnline`, and they were the
//: same one until this was written.
//:
//: `status` and `lastSeen` are refreshed by aify-env ADVERTISING the host. Since 2026-08-30 that is
//: what those fields mean, and the service split the question accordingly: `/spawn` asks
//: `environment_has_live_bridge()` (metadata.bridgeLastSeen plus a live `bridge_instances` row),
//: while `environment_effective_status` still ages on `last_seen`. The doctor never followed, so
//: `env-bridge` -- the check whose entire job is "can dashboard-managed spawns run" -- answered from
//: the field that does not determine it.
//:
//: MEASURED 2026-09-02, and it cost the operator a blocked fleet: the row read `status: online,
//: lastSeen 17:26:41Z` while `bridgeLastSeen` was `2026-09-01T15:38:12Z`. There had been no bridge
//: for a day. `comms_envs` and this doctor both said online; `/spawn` returned 409 in the same
//: minute and was the only one telling the truth. The two agreed by accident beforehand only because
//: aify-env's advertisements were failing with 401 -- fixing that credential broke the coincidence
//: and exposed the conflation.
export const SPAWN_CLAIMER_FRESH_SECONDS = 90;
//: How far ahead a stamp may sit and still count. NOT ZERO: a container clock 4.1s ahead of the host
//: once made this doctor call every environment dead.
export const BRIDGE_STAMP_SKEW_SECONDS = 120;

export const BRIDGE_STAMP_FRESH = "fresh";
export const BRIDGE_STAMP_STALE = "stale";
export const BRIDGE_STAMP_ABSENT = "absent";
export const BRIDGE_STAMP_INVALID = "invalid";

/**
 * Classify `metadata.bridgeLastSeen` into the SAME four answers the service uses.
 *
 * FOUR, NOT A BOOLEAN, because the collapse was got backwards once already: ABSENT read as "unknown,
 * and unknown means yes" kept every pre-stamp row authorised for ever, and INVALID read as absent
 * turned corrupt data into authorisation. Here ABSENT means the doctor CANNOT tell -- the service
 * resolves it against `bridge_instances`, a table no endpoint exposes -- so it must be reported as
 * unproven rather than answered either way.
 */
export function bridgeStampStateAt(env, now) {
  const stamp = String(env?.metadata?.bridgeLastSeen || "").trim();
  if (!stamp) return BRIDGE_STAMP_ABSENT;
  const at = Date.parse(stamp);
  if (Number.isNaN(at)) return BRIDGE_STAMP_INVALID;
  const age = now - at;
  if (age < -BRIDGE_STAMP_SKEW_SECONDS * 1000) return BRIDGE_STAMP_INVALID;
  return age <= SPAWN_CLAIMER_FRESH_SECONDS * 1000 ? BRIDGE_STAMP_FRESH : BRIDGE_STAMP_STALE;
}

/** True only when a bridge has spoken for this environment recently enough to claim a spawn. */
export function envCanClaimASpawnAt(env, now) {
  return bridgeStampStateAt(env, now) === BRIDGE_STAMP_FRESH;
}

export function envCanClaimASpawn(env) {
  return envCanClaimASpawnAt(env, Date.now());
}

/** How old the bridge stamp is, in the operator's terms, or "" when there is none to age. */
export function bridgeStampAgeAt(env, now) {
  const stamp = String(env?.metadata?.bridgeLastSeen || "").trim();
  const at = Date.parse(stamp);
  if (!stamp || Number.isNaN(at)) return "";
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 120) return `${seconds}s ago`;
  if (seconds < 7200) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}
