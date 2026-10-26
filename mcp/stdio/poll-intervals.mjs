// How often the bridge polls, and the floors that keep a misconfiguration from melting the service.
//
// Extracted from server.js in v0.5.4. Every one of these reads an env var at load, so they were only
// ever exercised by starting a bridge.
//
// THEY ARE NOT UNIFORM, and that is the thing worth having written down rather than discovered. The
// heartbeat falls back on a junk value; `DISPATCH_POLL_MS` carries no guard, so a hostile or fat-fingered
// value passes straight through — including NaN, which `setInterval` treats as ~0. The tests beside this
// pin that asymmetry, so the difference is a recorded choice rather than an oversight nobody noticed.

export const __HEARTBEAT_MS = Number(process.env.AIFY_SESSION_HEARTBEAT_MS || "60000") || 60000;
export const DISPATCH_POLL_MS = Number(process.env.AIFY_DISPATCH_POLL_MS || 3000);
