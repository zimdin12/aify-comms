// Talking to aify-env, the host that owns processes and terminals.
//
// OFF BY DEFAULT, and that is the whole safety story for this phase. With no endpoint configured
// `isEnabled()` is false and every caller takes exactly the path it takes today — same spawn, same
// manager, same bytes. Turning it on is a deliberate act on an idle fleet, not a side effect of
// deploying this file.
//
// Why this exists: spawning currently lives inside aify-comms, so a second service can only run its
// own spawner (two PTY owners on one host, and this project has incidents from ONE colliding with
// itself) or depend on aify-comms. Moving the capability out is the fix; this is the client half of it.
//
// WHAT THIS CANNOT DO YET, said here because it decides when the flag may be flipped: aify-env has no
// output STREAM. Start, stop and list are request/response, and a managed agent's console needs its
// output continuously. Until the protocol grows a stream, delegation can carry a spawn but not a
// console — so the flag stays off regardless of how well the code below works.

const DEFAULT_TIMEOUT_MS = 5000;

/**
 * Is delegation configured?
 *
 * Keyed on the endpoint being SET rather than on a separate boolean, so there is one thing to get
 * right. A flag that is on with nowhere to talk to is a state nobody wants to debug.
 */
export function isEnabled(env = process.env) {
  return typeof env.AIFY_ENV_ENDPOINT === "string" && env.AIFY_ENV_ENDPOINT.trim() !== "";
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

  async list() {
    return this.#request("GET", "/processes", undefined, 200);
  }

  async health() {
    return this.#request("GET", "/health", undefined, 200);
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
