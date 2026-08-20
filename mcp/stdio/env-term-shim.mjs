// A `term` that is not a pty.
//
// TerminalProcessManager reaches for `state.term` to write, resize and kill, and the console keepalive
// probes it. A process living in aify-env has no local pty, which is why the v0.6 Phase 8 seam refuses
// rather than half-delegating: a delegated start without this would produce agents that can be watched
// and not typed at, differing from local ones in ways nobody could attribute.
//
// THE CALLS ARE SYNCHRONOUS AND THE TRANSPORT IS NOT. Every call site does `terminal.term.write(...)`
// with no await, because a pty's write returns nothing. So this dispatches and returns immediately --
// and the promise nobody is holding must not swallow its own failure. Typing into a delegated console
// and having nothing happen, with no error anywhere, is the exact shape this guards against: the
// report goes to `onError` instead.

/**
 * @param {{client: object, id: string, pid: number, onError?: (op: string, error: string) => void}} spec
 *   `client` is an EnvClient; only write, resize and stop are used.
 * @returns {{pid: number, write: Function, resize: Function, kill: Function}}
 */
export function createEnvTerm({ client, id, pid, onError }) {
  const report = (op, error) => {
    // A missing handler loses the report, which is bad. An unhandled rejection takes the bridge down,
    // which is worse, so the absence is tolerated and the throw is not.
    if (typeof onError !== "function") return;
    try {
      onError(op, String(error));
    } catch {
      /* a broken reporter must not become a second failure */
    }
  };

  /** Fire the call, keep nothing, and make sure a failure still reaches somebody. */
  const dispatch = (op, run) => {
    Promise.resolve()
      .then(run)
      .then((result) => {
        if (result && result.ok === false) report(op, result.error ?? "refused");
      })
      .catch((error) => report(op, error?.message ?? error));
  };

  return {
    pid,
    write(data) {
      dispatch("write", () => client.write(id, data));
    },
    resize(cols, rows) {
      dispatch("resize", () => client.resize(id, cols, rows));
    },
    kill() {
      // The environment owns the process, so stopping it there IS the kill. There is no local signal
      // to send and nothing here to fall back to.
      dispatch("kill", () => client.stop(id));
    },
  };
}
