// What the service is told when a terminal's process ends.
//
// THE INFORMATION EXISTED AND DIED ONE HOP SHORT. node-pty hands `{exitCode, signal}` to the exit
// wiring in `terminal-runtime.js`, which spreads both into the exit detail as `code` and `signal`.
// Every exit path does it: the PTY (`{code: exitCode, signal}`), the delegated aify-env process
// (`{code, signal: null}`), the piped child (`{code, signal}`) and a forced stop
// (`{signal: "SIGTERM"}`). Only a spawn failure carries no code, and it carries an `error` instead.
//
// `terminal-manager.mjs` then read `detail.error.message` and posted an output marker plus a status,
// dropping both numbers. So `terminal_sessions` recorded HOW a terminal ended nowhere at all. When
// sc-claude and sc-architect died mid-turn on 2026-08-26 the operator asked why, and every record
// said `status='stopped'` with an empty `error` and nothing else -- a terminal that dies took its
// reason with it.
//
// PURE, and here rather than inline, for the reason the `*-predicates.js` modules exist: the exit
// hook calls a module-scoped `httpCall`, so anything built inside it can only be checked by starting
// a terminal and killing it. The body is decided here and asserted directly.
//
// WHAT THIS CANNOT TELL YOU YET, traced 2026-08-26 and disclosed here because a reader would
// otherwise trust the column further than it deserves. A LOCAL pty reports a true code and signal. A
// DELEGATED one does not always: aify-env drops the signal where Node hands it over -- `runner.mjs:284`
// is `child.on("close", (code) => finish(code))` and that event is `(code, signal)` -- and then
// coerces the resulting null code to 0 at `runner.mjs:185`. Node gives a null code precisely WHEN a
// signal killed the process, so a signalled delegated terminal arrives here as a manufactured
// `exitCode: 0` with no signal left to contradict it.
//
// Nothing below can repair that: the distinction is destroyed two hops before this module sees it.
// Everything here is written for the case where it IS fixed -- code and signal stay separate fields, a
// non-numeric code is refused, and an absent field means "nobody said" rather than zero. The fix is a
// change to aify-env's exit frame, a wire contract on a live tier and therefore the operator's call;
// see docs/V0_7_WEAK_POINTS.md, decision 15.

/**
 * The POST body for a terminal's exit, minus the bridge id its caller adds.
 *
 * THREE THINGS THAT MUST NOT BE COLLAPSED:
 *
 * - A code of 0 is a CLEAN EXIT and the most common value there is. `if (code)` would discard
 *   exactly the case this exists to record, which is why the test is `typeof === "number"`.
 * - A signal-killed process reports a NULL code and a signal, so the two travel as separate fields
 *   rather than one. "killed by SIGKILL" and "exited 0" are different answers.
 * - A field that is ABSENT means nobody said. It is omitted rather than sent as null or "", so a
 *   service reading it can tell silence from a reported value, and an older service ignores what it
 *   does not know about.
 */
export function exitReport(detail = {}) {
  const error = detail?.error?.message || "";
  const body = {
    output: error ? `\n[terminal failed] ${error}\n` : `\n[terminal exited]\n`,
    status: error ? "failed" : "stopped",
  };
  if (typeof detail?.code === "number" && Number.isFinite(detail.code)) body.exitCode = detail.code;
  const signal = detail?.signal == null ? "" : String(detail.signal).trim();
  if (signal) body.exitSignal = signal;
  return body;
}
