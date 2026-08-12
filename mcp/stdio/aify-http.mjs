// How to reach the aify service: where it is, what key opens it, and a call that gives up rather than hanging.
//
// A NEUTRAL owner, created in v0.5.4 because `makeAifyHttpCall` has two callers on opposite sides of the
// hermes decomposition — `runDeliveryLoop` and `startResumeMarkerSync` — and it is neither a delivery-loop
// concept nor a session one. The reviewer's steer on the name was explicit and worth recording: NOT
// `hermes-api.mjs`, because nothing here wraps a Hermes API. This is the aify service's HTTP client, and a
// module named for the wrong service is a wrong answer that survives review.
//
// EVERY REQUEST HAS A DEADLINE. The AbortController is the point of this factory existing at all: a bridge
// that hangs on a request to a service that is down stops delivering work and reports nothing, which is
// indistinguishable from an idle agent. `HTTP_TIMEOUT_MS` follows the factory because the factory is its only
// reader.
//
// `coerceLoopbackToIPv4` rewrites `localhost` to `127.0.0.1` in the base URL, and it is not cosmetic: on a
// host where `localhost` resolves to `::1` first, a service listening only on IPv4 is unreachable through a
// name that looks correct in every log line.
//
// The factory takes `baseUrl` and `apiKey` as ARGUMENTS while also exporting the env-derived values its
// callers pass. That looks redundant and is deliberate: the arguments are what make it testable without
// environment, and the exported constants are what stop each caller re-deriving the same two values from
// `process.env` and drifting.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run and the wrappers relaunch.

function coerceLoopbackToIPv4(url) {
  return String(url || "").replace(/^(https?:\/\/)localhost(?=[:\/]|$)/i, "$1127.0.0.1");
}

export const AIFY_SERVER_URL = coerceLoopbackToIPv4(
  process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "",
).replace(/\/+$/, "");
export const AIFY_API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));


export function makeAifyHttpCall(baseUrl, apiKey) {
  return async function httpCall(method, endpoint, body = null) {
    if (!baseUrl) return null;
    const url = `${baseUrl}/api/v1${endpoint}`;
    const options = { method, headers: {} };
    if (apiKey) options.headers["X-API-Key"] = apiKey;
    if (body) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        const error = new Error(`HTTP ${res.status}: ${text}`);
        error.status = res.status;
        throw error;
      }
      return res.json().catch(() => ({}));
    } finally {
      clearTimeout(timeout);
    }
  };
}
