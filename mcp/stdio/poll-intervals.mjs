// How often the bridge polls, and the floors that keep a misconfiguration from melting the service.
//
// Extracted from server.js in v0.5.4. Every one of these reads an env var at load, so they were only
// ever exercised by starting a bridge — and what they encode is a safety property: `Math.max(floor, …)`
// is what stops `AIFY_TERMINAL_CONTROL_POLL_MS=1` turning a poll loop into a denial of service against
// the operator's own service.
//
// THEY ARE NOT UNIFORM, and that is the thing worth having written down rather than discovered. Three
// carry a floor; `DISPATCH_POLL_MS` carries none, so a hostile or fat-fingered value passes straight
// through — including NaN, which `setInterval` treats as ~0. The tests beside this pin each clamp AND
// that asymmetry, so the difference is a recorded choice rather than an oversight nobody noticed.

export const __HEARTBEAT_MS = Number(process.env.AIFY_SESSION_HEARTBEAT_MS || "60000") || 60000;
// RESIDENT-HERMES turn-state detector (status-accuracy Task 1, 2026-06-07).
// Armed alongside the gateway-liveness probe above — same precondition
// (gateway-backed hermes) — so a RESIDENT hermes ends its turn on sustained
// gateway idle instead of latching `working` until the 1800s backstop. Reads
// the gateway's OWN session status (session.active_list → the agent's session)
// and posts /turn-start on the gateway "working" edge / /turn-end on sustained
// idle, re-stamping turn-busy every 45s while working (< the server's 120s stale
// window) so a long autonomous turn never goes stale → `online`. ANTI-FEEDBACK:
// gateway-truth-driven, never the server's derived status; only SETs on a
// gateway working read, only CLEARs on sustained idle. Worst case (gateway read
// fails): the reader returns "" → a transient no-op → today's 1800s backstop
// still applies. Gated by shouldArmResidentHermesTurnDetector so a non-hermes /
// no-gateway resident is a no-op.
export const __RESIDENT_GATEWAY_TURN_POLL_MS = Math.max(
  250,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_POLL_MS || 3000),
);
export const __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE || 3),
);
export const DISPATCH_POLL_MS = Number(process.env.AIFY_DISPATCH_POLL_MS || 3000);
// Terminal-control loop polls separately and much tighter: console input is
// latency-sensitive (operator typing), and the terminal_controls query is
// small + indexed, so a sub-second cadence is perf-safe. Dispatch/spawn
// polling stays at the heavier DISPATCH_POLL_MS.
export const TERMINAL_CONTROL_POLL_MS = Math.max(
  200,
  Number(process.env.AIFY_TERMINAL_CONTROL_POLL_MS || 800),
);
