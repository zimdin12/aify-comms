// The two session modes, and the fact that there are only two.
//
// A bridge-managed agent is either RESIDENT — a session a human launched and owns — or MANAGED, one the
// dashboard/environment bridge spawned and may stop, restart or replace. Almost every lifecycle decision
// in the bridge turns on which it is, and `normalizeSessionMode` is the single place an arbitrary input
// becomes one of the two.
//
// v0.5.4 layer 0 of the server.js decomposition. Sixteen call sites, no owner. Extracted as its own tiny
// owner on the reviewer's ruling rather than folded into `launch-identity.mjs`: this normalises a VALUE
// that arrives per-agent from a registration payload or an API response, which is not the same thing as
// what THIS process was launched as.
//
// IT FAILS TOWARD RESIDENT, AND THAT DIRECTION IS THE WHOLE DESIGN. Anything not exactly "managed"
// becomes "resident" — including an empty string, a typo, and an unrecognised future mode. Resident is
// the mode the bridge does NOT stop, restart or reap: an operator-owned session. So an unreadable input
// yields the conservative answer, and a bug upstream costs a missed automation rather than a killed
// session someone was working in. The tests below assert that direction explicitly, because "normalise
// with a default" reads like a formality and this default is a safety property.

export function normalizeSessionMode(mode) {
  const value = String(mode || "resident").trim().toLowerCase();
  return value === "managed" ? "managed" : "resident";
}
