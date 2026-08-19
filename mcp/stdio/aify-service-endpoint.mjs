// How to reach the aify service: where it is, what key opens it, and the call that fails over.
//
// v0.5.4 layer 0 of the server.js decomposition (docs/JS_SERVER_JS_PROOF_PACKET.md). server.js is the live
// MCP bridge and this is its hottest path, so it moved first, smallest, and alone.
//
// WHY THIS POPULATION AND NOT THE 144 LINES THE PACKET PROMISED. The packet scoped layer 0 as the failover
// latch plus its readers. Measuring the move exposed a constraint it had missed: `SERVER_URLS` is BUILT with
// `uniqueServerUrls` at module scope, and `httpCall` reads `SERVER_URLS`. Leaving the constant in server.js
// while moving the function would have made this leaf import upward from the bridge — a cycle. URL
// resolution therefore belongs here too, which is also the more honest subject: not "the latch", but how to
// reach the service at all.
//
// THE LATCH IS THE POINT. `ACTIVE_SERVER_URL` is a multi-URL failover latch with exactly ONE writer, inside
// `httpCall`, advanced only after a request succeeds. Module state in ESM is a per-process singleton, so
// moving it here preserves exactly one instance — that is what makes this a relocation rather than a
// redesign. It must stay one instance: two copies would let the bridge and its callers disagree about which
// server is live, silently.
//
// API_KEY IS ENV-DERIVED AND STAYS THAT WAY. It is read from the environment here and never logged, never
// captured in a fixture, never returned. Naming the binding is the whole of what this comment does.
//
// DEPLOYMENT: host code. Nothing here is live until `install.sh` is re-run (sequentially, never in parallel)
// AND every wrapper relaunches. Running bridges keep executing the copy they loaded at boot, so a green
// suite proves the repo, not the fleet. `aify-comms doctor`'s `bridge-current` will read red until the
// relaunch, and that red is accurate.

const API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";

// Windows + Docker Desktop: `localhost` resolves to IPv6 ::1 first, but
// Docker Desktop's IPv6 port forwarding is unreliable — HTTP requests
// time out silently. Force the IPv4 loopback. Benign on Linux/macOS.
//
// This comment arrived in v0.5.4 from `server.js`, where it had been left behind when the function it
// describes moved here. It had come to sit above `IS_REMOTE`, which is not what it is about — a reader
// would have taken it as an explanation of remote-mode detection. Comments do not follow code unless
// someone moves them.
function coerceLoopbackToIPv4(url) {
  return String(url || "").replace(
    /^(https?:\/\/)localhost(?=[:\/]|$)/i,
    "$1127.0.0.1",
  );
}

/**
 * The environment names this bridge reads to find its service, in precedence order.
 *
 * Exported because something outside has to say the same thing and must not say it from memory: the
 * service registry declares which env names carry a service's endpoint, and a runtime's per-server MCP
 * env block is KEY-SCOPED — proven on Claude Code 2.1.236, where a per-server AIFY_SERVER_URL beat an
 * inherited value while an inherited AIFY_COMMS_URL passed through untouched. So a name this bridge
 * reads but the registry does not declare would be INHERITED from whatever launched the runtime,
 * quietly and correctly-looking, until two services disagree about where they point.
 *
 * One list, read here and declared there. Not a regex over this file, and not a second hand-typed copy.
 */
export const ENDPOINT_ENV_NAMES = ["CLAUDE_MCP_SERVER_URL", "AIFY_SERVER_URL"];

const SERVER_URL = coerceLoopbackToIPv4(
  ENDPOINT_ENV_NAMES.map((name) => process.env[name]).find(Boolean) || "",
);

// Whether this bridge talks to a remote service over HTTP or drives the local filesystem store.
//
// Declared in `server.js` until v0.5.4, where it was one line — `!!SERVER_URL` — reading a value this
// module already owned, and read from 55 places. That made it look like a dependency of whatever tool
// group was being extracted at the time; it is not. It is a property OF the endpoint, and its owner is
// the module that resolves the endpoint.
const IS_REMOTE = !!SERVER_URL;

function defaultFallbackServerUrls(primary) {
  if (!/^https?:\/\/(localhost|127\.0\.0\.1)(?::|\/|$)/i.test(String(primary || ""))) return [];
  // Loopback only. Previously this also added host.docker.internal and a
  // hardcoded LAN IP (192.0.2.10), which silently failed a local bridge
  // over to a developer's shared server — a plain local install would register
  // its agents on a remote host. Fallbacks now stay on the loopback the
  // operator already chose. Set AIFY_SERVER_FALLBACK_URLS / CLAUDE_MCP_FALLBACK_URLS
  // to opt into any non-loopback fallback explicitly.
  return ["http://127.0.0.1:8800", "http://localhost:8800"];
}

function splitServerUrls(value) {
  return String(value || "")
    .split(/[,\s]+/)
    .map(item => coerceLoopbackToIPv4(item.trim().replace(/\/+$/, "")))
    .filter(Boolean);
}

function uniqueServerUrls(urls) {
  const seen = new Set();
  const result = [];
  for (const url of urls) {
    if (!url || seen.has(url)) continue;
    seen.add(url);
    result.push(url);
  }
  return result;
}

const SERVER_URLS = uniqueServerUrls([
  SERVER_URL,
  ...splitServerUrls(process.env.CLAUDE_MCP_FALLBACK_URLS || process.env.AIFY_SERVER_FALLBACK_URLS || ""),
  ...defaultFallbackServerUrls(SERVER_URL),
]);

let ACTIVE_SERVER_URL = SERVER_URLS[0] || "";

const HTTP_RETRY_ATTEMPTS = 3;

const HTTP_RETRY_BASE_MS = 250;

const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));

const RETRIABLE_POST_PATHS = new Set([
  "/agents",              // INSERT OR REPLACE — idempotent
  "/channels/join",       // channel join is idempotent (SKIP suffix match below)
]);

function isRetriableRequest(method, endpoint, body = null) {
  const m = String(method || "").toUpperCase();
  if (m === "GET" || m === "PATCH" || m === "DELETE") return true;
  if (m !== "POST") return false;
  const path = String(endpoint || "");
  if (RETRIABLE_POST_PATHS.has(path)) return true;
  // Per-agent heartbeat and per-channel join are idempotent but have
  // dynamic path segments, so match by suffix.
  if (/^\/agents\/[^/]+\/heartbeat$/.test(path)) return true;
  if (path === "/environments/heartbeat") return true;
  if (/^\/channels\/[^/]+\/join$/.test(path)) return true;
  // /messages/send is idempotent ONLY when the body carries a clientNonce (#240):
  // the server collapses a retry to the original message. A nonce-less send is NOT
  // retriable (it would double-send), so gate on the nonce actually being present.
  if (path === "/messages/send" && body && typeof body === "object" && String(body.clientNonce || "").trim()) return true;
  return false;
}

function isTransientHttpError(error) {
  if (!error) return false;
  const name = String(error.name || "");
  const code = String(error.code || "");
  const message = String(error.message || "");
  if (name === "AbortError" || name === "TimeoutError") return true;
  if (/ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|EPIPE|socket hang up|fetch failed|network/i.test(code + " " + message)) {
    return true;
  }
  return false;
}

async function httpCall(method, endpoint, body = null, opts = {}) {
  // opts.timeoutMs overrides the default per-attempt abort timeout. Long-poll claim
  // calls pass a value larger than the server's max hold so the bridge does NOT abort
  // (and trip its failure counter) while the server is legitimately holding the request.
  const callTimeoutMs = Math.max(1, Number(opts.timeoutMs) || HTTP_TIMEOUT_MS);
  const baseOptions = { method, headers: {} };
  if (API_KEY) baseOptions.headers["X-API-Key"] = API_KEY;
  if (body) {
    baseOptions.headers["Content-Type"] = "application/json";
    baseOptions.body = JSON.stringify(body);
  }
  const retriable = isRetriableRequest(method, endpoint, body);
  const maxAttempts = retriable ? HTTP_RETRY_ATTEMPTS : 1;
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const urls = uniqueServerUrls([ACTIVE_SERVER_URL, ...SERVER_URLS]);
    for (const baseUrl of urls) {
      const url = `${baseUrl}/api/v1${endpoint}`;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), callTimeoutMs);
      try {
        const options = { ...baseOptions, headers: { ...baseOptions.headers }, signal: controller.signal };
        const res = await fetch(url, options);
        if (!res.ok) {
          const text = await res.text();
          const err = new Error(`HTTP ${res.status}: ${text}`);
          err.status = res.status;
          err.serverUrl = baseUrl;
          // 5xx is retriable as a transient server blip, but only on safe
          // methods. 4xx is a real error — never retry.
          if (!(retriable && res.status >= 500 && res.status < 600 && attempt < maxAttempts)) {
            throw err;
          }
          lastError = err;
          continue;
        }
        ACTIVE_SERVER_URL = baseUrl;
        return res.json();
      } catch (error) {
        if (error?.name === "AbortError") {
          const timeoutError = new Error(`HTTP ${method} ${endpoint} timed out after ${callTimeoutMs}ms`);
          timeoutError.name = "TimeoutError";
          timeoutError.serverUrl = baseUrl;
          lastError = timeoutError;
        } else {
          error.serverUrl = error.serverUrl || baseUrl;
          lastError = error;
        }
        if (!isTransientHttpError(error) || !retriable) throw lastError;
      } finally {
        clearTimeout(timeout);
      }
    }
    if (attempt >= maxAttempts) throw lastError;
    await new Promise((r) => setTimeout(r, HTTP_RETRY_BASE_MS * 2 ** (attempt - 1)));
  }
  throw lastError || new Error("httpCall exhausted retries without error");
}


/**
 * The URL the last successful request used. READ-ONLY ACCESSOR, deliberately.
 *
 * `noteControlClaimFailure` and `noteSpawnClaimFailure` log which server a claim failed against. They are
 * claim/spawn bookkeeping, not HTTP ownership — `noteSpawnClaimFailure` also drives spawn-loop counters —
 * so the reviewer ruled they stay in server.js and read the latch through this rather than move here.
 *
 * That is a DECLARED SUBSTITUTION, not a byte-identical relocation: those two helpers now call
 * `activeServerUrl()` where they read `ACTIVE_SERVER_URL`. Same value, one owner, no second latch.
 */
export function activeServerUrl() {
  return ACTIVE_SERVER_URL;
}


export { API_KEY, IS_REMOTE, SERVER_URL, SERVER_URLS, coerceLoopbackToIPv4, uniqueServerUrls, httpCall,
         // splitServerUrls / defaultFallbackServerUrls joined the surface in v0.5.4, when three other
         // modules stopped declaring their own copies of these helpers.
         splitServerUrls, defaultFallbackServerUrls,
         isRetriableRequest, isTransientHttpError, HTTP_TIMEOUT_MS, HTTP_RETRY_ATTEMPTS,
         HTTP_RETRY_BASE_MS, RETRIABLE_POST_PATHS };

// How a failed call to the service is REPORTED, which is a property of the endpoint rather than of whoever
// made the call. A transient failure — the service restarting, a connection reset — is expected operation
// and says which URL it was talking to and that it will retry; anything else is a real error and is logged
// as one. It joins this module because all three things it consults (`isTransientHttpError`,
// `activeServerUrl`, `SERVER_URL`) are defined here, and because a caller that logged a transient failure as
// an error would make an ordinary restart look like a fault.
export function logTransientOrError(prefix, error) {
  if (isTransientHttpError(error)) {
    const target = error?.serverUrl || activeServerUrl() || SERVER_URL;
    console.error(`${prefix}: transient HTTP error against ${target}: ${error?.message || String(error)}; retrying`);
    return;
  }
  console.error(`${prefix}:`, error);
}
