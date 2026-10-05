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
// An omitted apiKey resolves through the shared destination-bound owner on each request. An explicit
// apiKey, including "", stays the caller's choice. Hermes must omit it rather than freeze a startup miss.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run and the wrappers relaunch.

import { keyForUrl } from "./aify-service-endpoint.mjs";

function coerceLoopbackToIPv4(url) {
  return String(url || "").replace(/^(https?:\/\/)localhost(?=[:\/]|$)/i, "$1127.0.0.1");
}

export const AIFY_SERVER_URL = coerceLoopbackToIPv4(
  process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "",
).replace(/\/+$/, "");
/**
 * The key this process authenticates with, RE-EXPORTED rather than resolved again.
 *
 * IT WAS RESOLVED HERE TOO, and that was the defect. `aify-http.mjs` read the environment while
 * `aify-service-endpoint.mjs` read it separately for `API_KEY` -- so fixing the credential-store
 * fallback here repaired the delivery loops and left every MCP tool still returning 401, because
 * `server.js` and the channel sidecars import `API_KEY` from THERE. Two spellings of one fact.
 * The resolution now lives at the source and this is the alias its old readers keep.
 */
export { API_KEY as AIFY_API_KEY } from "./aify-service-endpoint.mjs";
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));


export function makeAifyHttpCall(baseUrl, apiKey) {
  return async function httpCall(method, endpoint, body = null) {
    if (!baseUrl) return null;
    const url = `${baseUrl}/api/v1${endpoint}`;
    const options = { method, headers: {} };
    const key = apiKey === undefined ? keyForUrl(baseUrl) : apiKey;
    if (key) options.headers["X-API-Key"] = key;
    if (body) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
    try {
      // NEVER FOLLOWED. `fetch` follows redirects by default and re-sends the headers, so a 302
      // hands `X-API-Key` to whatever it points at. Review reproduced it here and in
      // `aify-service-endpoint.mjs` after I had fixed only the doctor. A 3xx fails `res.ok`.
      const res = await fetch(url, { ...options, redirect: "manual", signal: controller.signal });
      if (!res.ok) {
        if (res.status === 401 && apiKey === undefined) {
          keyForUrl(baseUrl, { refresh: true });
          console.error("[aify-comms] HTTP 401: credential re-resolved for the next call; request not replayed");
        }
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
