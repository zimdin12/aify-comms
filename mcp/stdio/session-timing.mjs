// The promise and timeout helpers the native runtime session modules share.
//
// codex-session.js, hermes-session.js, hermes-managed-gateway-session.js and pi-session.js each
// declared their own `createDeferred`, and pi's once lacked the no-op catch below. They also each
// spelled out the same "config key, else env var, else default" read for their timeouts. The
// POLICY stays per runtime -- each module names its own config key, env var and default -- and only
// the reading is shared, so one runtime cannot pick up another's timeout.

// A promise with its resolve/reject exposed.
//
// THE NO-OP CATCH IS LOAD-BEARING. These modules reject Deferreds on ordinary paths (a session that
// fails to start, a turn that is cancelled), and a rejection nobody awaits is an unhandled rejection,
// which is a process kill under `--unhandled-rejections=strict`. It is not a swallow: every real
// awaiter attaches its own handler and still sees the rejection.
export function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  promise.catch(() => {});
  return { promise, resolve, reject };
}

// The first positive, finite number of: a configured value, an environment value, the default.
export function positiveMsFrom(configValue, envValue, fallback) {
  const fromConfig = Number(configValue);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(envValue);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return fallback;
}
