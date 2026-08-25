// What the operator is told when a console attaches, including the part that was being dropped.
//
// THE INFORMATION EXISTED AND DIED ONE LINE SHORT. aify-env answers `terminal: true|false` on a
// spawn, and says why it bothers: "A PTY gives a real terminal, which is what a web console needs to
// render a TUI at all. Piped stdio gives the output and none of the terminal. So the handle SAYS
// which one it got: a caller that silently receives pipes when it expected a terminal gets output
// that looks slightly wrong and no warning." `startDelegated` reads that field and returns it as
// `pty`; `startPipeProcess` returns `pty: false` for the same reason on this host. Then the terminal
// control loop -- the only caller -- read `started.pid` and wrote
// `[terminal attached pid=N]`, dropping the flag on the line the operator actually reads.
//
// A PTY is missing for one reason on either side: node-pty did not load. It is a native module, so a
// Node upgrade is enough to do it, which is what makes this worth saying out loud rather than
// treating as a configuration a host either has or does not.
//
// The operator's standing requirement is that a managed agent shows a real TUI in the web console.
// This does not restore one -- nothing here can -- it makes the degradation legible instead of
// leaving "the console looks slightly wrong" as the only symptom.
//
// PURE, and here rather than inline, for the reason the `*-predicates.js` modules exist: the
// terminal-control loop cannot be exercised without starting a terminal, so anything left inline
// there can only fail in production.

/** The `pty` flag as a definite answer, or null when the caller did not say. */
export function ptyState(started) {
  if (!started || typeof started !== "object") return null;
  if (started.pty === true) return true;
  if (started.pty === false) return false;
  return null;
}

/**
 * The output line written when a console attaches.
 *
 * `pty: false` earns the second line. UNKNOWN does not: an older bridge or a path that never set the
 * flag has told us nothing, and warning on silence would put "this console will not render a TUI" in
 * front of an operator whose console renders one perfectly. That is the opposite of the usual
 * fail-closed rule and it is deliberate -- the cost here is a false alarm on a healthy console, not a
 * missed failure, because the console itself shows whether a TUI renders.
 */
export function attachNotice(started) {
  const pid = started && started.pid != null ? String(started.pid) : "";
  const attached = `[terminal attached pid=${pid}]\n`;
  if (ptyState(started) !== false) return attached;
  return attached
    + "[no pty on this host: node-pty did not load, so this console is piped output "
    + "and will not render a TUI]\n";
}
