// Talking to aify-env, the host that owns processes and terminals.
//
// OFF BY DEFAULT, and that is the whole safety story for this phase. It takes an explicit
// `AIFY_COMMS_DELEGATE_SPAWNS=1` AND an `AIFY_ENV_ENDPOINT` to turn on; with either missing every
// caller takes exactly the path it takes today — same spawn, same manager, same bytes. Turning it on
// is a deliberate act on an idle fleet, not a side effect of deploying this file.
//
// Why this exists: spawning currently lives inside aify-comms, so a second service can only run its
// own spawner (two PTY owners on one host, and this project has incidents from ONE colliding with
// itself) or depend on aify-comms. Moving the capability out is the fix; this is the client half of it.
//
// WHAT THIS CANNOT DO YET, said here because it decides when the flag may be flipped: aify-env can now
// stream output, take input and resize — that gap is closed. What is still missing is a `term` shim in
// TerminalProcessManager (write/resize/kill plus the console keepalive), and the fact that aify-comms
// composes a shell command STRING while aify-env allowlists a launcher FILE. Until both are settled the
// seam refuses when the flag is on, rather than half-delegating. See docs/PHASE8_STATUS.md.

const DEFAULT_TIMEOUT_MS = 5000;

/** Only these mean yes. "0" and "false" are what somebody types when they mean off. */
const AFFIRMATIVE = new Set(["1", "true", "yes", "on"]);

/**
 * Is delegation turned on, and does it have somewhere to go?
 *
 * TWO QUESTIONS, TWO ANSWERS, and getting that wrong was the point of this function's first version.
 * It keyed on `AIFY_ENV_ENDPOINT` alone — "one thing to get right" — except that is the variable
 * aify-env's OWN doctor and TUI read to find the daemon. An operator who exported it to look at their
 * environment would have made every managed spawn in aify-comms refuse. Knowing where aify-env is says
 * nothing about wanting to send work there.
 *
 * So: an explicit opt-in, AND an endpoint. Opting in with nowhere to send it is not "on" — it is a
 * misconfiguration, and calling it on would produce a refusal whose message points at the wrong half.
 */
export function isEnabled(env = process.env) {
  const optedIn = AFFIRMATIVE.has(String(env.AIFY_COMMS_DELEGATE_SPAWNS ?? "").trim().toLowerCase());
  const endpoint = String(env.AIFY_ENV_ENDPOINT ?? "").trim();
  return optedIn && endpoint !== "";
}

export class EnvClient {
  #endpoint;
  #timeoutMs;
  #fetch;

  constructor({ endpoint, timeoutMs = DEFAULT_TIMEOUT_MS, fetchImpl } = {}) {
    this.#endpoint = String(endpoint ?? "").replace(/\/$/, "");
    this.#timeoutMs = timeoutMs;
    this.#fetch = fetchImpl ?? globalThis.fetch;
  }

  get endpoint() {
    return this.#endpoint;
  }

  /**
   * Ask the environment to start a launcher.
   *
   * Returns `{ok, handle}` or `{ok: false, error}`. It never throws: a caller deciding whether to fall
   * back to spawning locally must be able to read the answer, and an exception at this boundary would
   * make "the environment refused" and "the environment is not there" look the same to a catch block
   * that then does the wrong one.
   */
  async start({ service, launcher, args = [], cwd, env }) {
    return this.#request("POST", "/processes", { service, launcher, args, cwd, env }, 201);
  }

  async stop(id) {
    return this.#request("DELETE", `/processes/${encodeURIComponent(id)}`, undefined, 204);
  }

  /**
   * Type into a delegated process.
   *
   * Without this a delegated console is a viewer: watchable, not usable. The caller that wants to
   * delegate would have to keep a local pty for the writing, which defeats delegating at all.
   */
  async write(id, data) {
    return this.#request("POST", `/processes/${encodeURIComponent(id)}/input`, { data: String(data ?? "") }, 200);
  }

  /**
   * Resize a delegated process's terminal.
   *
   * REPORTS WHETHER IT APPLIED. A piped process has no terminal, and accepting the request silently
   * would let a console believe it had set a width while the agent kept wrapping at the default, with
   * nothing anywhere saying why.
   */
  async resize(id, cols, rows) {
    return this.#request("POST", `/processes/${encodeURIComponent(id)}/resize`, { cols, rows }, 200);
  }

  async list() {
    return this.#request("GET", "/processes", undefined, 200);
  }

  async health() {
    return this.#request("GET", "/health", undefined, 200);
  }

  /**
   * Watch a process's output.
   *
   * Returns an unsubscribe function, or NULL when there is nothing to watch — no such process, or no
   * environment answering. A caller has to be able to tell that from an open stream that is simply
   * quiet: one means look elsewhere, the other means wait, and a console that cannot distinguish them
   * shows empty either way and gives nobody a reason.
   *
   * Reads in a background loop rather than returning a promise the caller awaits, because the caller
   * is wiring a console and wants to carry on.
   */
  async subscribeOutput(id, listener) {
    if (!this.#endpoint) return null;

    let response;
    try {
      response = await this.#fetch(`${this.#endpoint}/processes/${encodeURIComponent(id)}/output`, {
        // No timeout: this connection is meant to stay open. A timeout here would sever a healthy
        // console on a quiet agent, which looks exactly like the agent having died.
        headers: { accept: "text/event-stream" },
      });
    } catch {
      return null;
    }
    if (response.status !== 200 || !response.body?.getReader) return null;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let stopped = false;
    let pending = "";

    (async () => {
      while (!stopped) {
        let chunk;
        try {
          chunk = await reader.read();
        } catch {
          return;
        }
        if (chunk.done) return;
        pending += decoder.decode(chunk.value, { stream: true });

        // Events are newline-delimited, which is exactly why each payload is JSON-encoded: a newline
        // inside the output would otherwise end the event early.
        const parts = pending.split(String.fromCharCode(10, 10));
        pending = parts.pop() ?? "";
        for (const frame of parts) {
          const line = frame.split(String.fromCharCode(10)).find((l) => l.startsWith("data: "));
          if (!line) continue;
          let text;
          try {
            text = JSON.parse(line.slice("data: ".length));
          } catch {
            // Malformed framing. Skipping beats delivering a fragment of protocol into a console.
            continue;
          }
          try {
            listener(text);
          } catch {
            // A broken consumer must not sever the stream for anyone else watching.
          }
        }
      }
    })();

    return () => {
      stopped = true;
      try {
        reader.cancel();
      } catch {
        // Already closed.
      }
    };
  }

  async #request(method, path, body, expected) {
    if (!this.#endpoint) return { ok: false, error: "no aify-env endpoint configured" };
    let response;
    try {
      response = await this.#fetch(`${this.#endpoint}${path}`, {
        method,
        headers: body === undefined ? undefined : { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(this.#timeoutMs),
      });
    } catch (error) {
      // Unreachable is not the same as refused, and the caller acts differently on each.
      return { ok: false, error: `aify-env unreachable: ${error.cause?.code ?? error.name ?? error.message}` };
    }

    if (response.status === 204) return { ok: true, handle: null };

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (response.status !== expected) {
      return {
        ok: false,
        error: payload?.error ?? `aify-env answered ${response.status}`,
        status: response.status,
      };
    }
    return { ok: true, handle: payload };
  }
}
