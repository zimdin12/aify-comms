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

// THE SIBLING FIELD, which was compared RAW at both of its bridge call sites — each of them sitting
// one line away from a `normalizeSessionMode(...)` call on the same object.
//
//   dispatch-loop.mjs            (liveAgent.launchMode || "") === "none"
//   managed-environment-sync.mjs (managedInfo.launchMode || "managed") === "none"
//
// `none` is the STOP marker: the service writes it as part of stopping an agent
// (`SET status = 'stopped', launch_mode = 'none'`), so it means "the operator stopped this; do not
// start it". Compared case-sensitively, a stored `"None"` reads as not-stopped — the first site then
// leaves a stopped resident host running, and the second syncs an agent the operator disabled.
//
// `"None"` is the obvious accident rather than a hostile input: `str(None)` in Python produces it,
// and `comms_register` accepts `launchMode` as a free-form string. The service now normalises on the
// way in (`_normalize_launch_mode`); this is the same rule on the side that reads back what older
// rows already hold.
//
// CASE ONLY — no vocabulary check. Unlike session mode there are three known values
// (`detached`, `managed`, `none`) and no owning set, so folding case fixes the defect without
// inventing a ruling about unknown modes.
export function normalizeLaunchMode(mode) {
  return String(mode || "detached").trim().toLowerCase() || "detached";
}
