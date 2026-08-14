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
export const __RESIDENT_GATEWAY_TURN_POLL_MS = Math.max(
  250,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_POLL_MS || 3000),
);
export const __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE || 3),
);
export const DISPATCH_POLL_MS = Number(process.env.AIFY_DISPATCH_POLL_MS || 3000);
export const TERMINAL_CONTROL_POLL_MS = Math.max(
  200,
  Number(process.env.AIFY_TERMINAL_CONTROL_POLL_MS || 800),
);
