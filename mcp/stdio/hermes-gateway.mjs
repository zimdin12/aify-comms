// The hermes gateway as the delivery loop sees it: re-ensuring a dead one, talking to it, reporting it dead,
// and the loop's own teardown.
//
// 0.9.10: STARTING A GATEWAY LEFT COMMS. The launcher starts it, from the agent's environment, on the port aify-env
// gives (aify-wrapper's `aify-hermes-gateway`), and the loop asks that same command when its gateway dies
// (hermes-gateway-source.mjs). So nothing here spawns, kills or picks a port for a gateway, and the loop's teardown
// releases only what the loop holds.
//
// DEPLOYMENT: this file runs on the HOST, not in the container. A change here is inert until `install.sh`
// is re-run and the client wrappers relaunch — a running wrapper keeps executing the copy it loaded at
// boot. `aify-comms doctor` reports that as `bridge-installed` and `bridge-current`.

import { MACHINE_ID, RUNTIME } from "./hermes-env.mjs";

const GATEWAY_PROBE_TIMEOUT_MS = Math.max(
  500,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_TIMEOUT_MS || 5000),
);
const RPC_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_RPC_TIMEOUT_MS || 60000));
export const MAX_REENSURE_WITHOUT_RECOVERY = 3;


/** PURE. `text` with every `token=` value replaced: the one rule each gateway text quoted out of here follows. */
function redactTokens(text) {
  return String(text || "").replace(/token=[^&\s]*/g, "token=<redacted>");
}


export function nextReEnsureBudget(current, { reEnsured = false, recovered = false, max = MAX_REENSURE_WITHOUT_RECOVERY } = {}) {
  if (recovered) return max;
  if (reEnsured) return Math.max(0, (Number(current) || 0) - 1);
  return Number(current) || 0;
}


export async function maybeReEnsureGatewayHost({
  isAlive,
  ensureHost,
  isStopping,
  log = () => {},
} = {}) {
  if (typeof isStopping === "function" && isStopping()) {
    return { reEnsured: false, reason: "stopping" };
  }
  if (typeof isAlive !== "function" || typeof ensureHost !== "function") {
    return { reEnsured: false, reason: "not-configured" };
  }
  let alive = false;
  try {
    const r = await isAlive();
    alive = r === true || (r && r.alive === true);
  } catch {
    // An unreachable/throwing probe counts as DEAD → proceed to re-ensure.
    alive = false;
  }
  if (alive) return { reEnsured: false, reason: "already-live" };
  try {
    const host = await ensureHost();
    try {
      log(
        "[hermes-managed-host] gateway host was found dead on the periodic cycle; " +
          "re-ensured it (blast-radius recovery — likely an operator `hermes update`/`--stop`).",
      );
    } catch {
      /* logging must never break recovery */
    }
    return { reEnsured: true, host };
  } catch (err) {
    return { reEnsured: false, reason: "ensure-failed", error: err };
  }
}


export async function openGatewayWsClient(wsUrl, { WebSocketImpl, timeoutMs = RPC_TIMEOUT_MS } = {}) {
  const WS = WebSocketImpl || (await import("ws")).default;
  const socket = new WS(wsUrl);
  const pending = new Map();
  let nextId = 100;

  // CONNECT TIMEOUT (2026-06-02 hotfix): a gateway that accepts the socket but
  // never completes the WS upgrade would otherwise hang this await FOREVER (the
  // open/error promise never settles), silently wedging the whole delivery loop
  // — it never claims, never writes its ready marker, and the agent looks dead.
  // Reject after `timeoutMs` so the caller treats it like a dead gateway (retry
  // / self-correct) instead of hanging.
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      try { socket.terminate?.() ?? socket.close?.(); } catch { /* ignore */ }
      reject(new Error(`hermes gateway WS connect timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    socket.once("open", () => { clearTimeout(timer); resolve(); });
    socket.once("error", (err) => { clearTimeout(timer); reject(err); });
  });

  socket.on("message", (raw) => {
    let msg;
    try {
      msg = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (msg.id !== undefined && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(msg.error);
      else p.resolve(msg.result ?? msg);
    }
    // Inbound events (deltas, tool frames, etc.) are owned by the TUI's
    // transport — this client ignores them; we only care about RPC replies.
  });
  socket.on("close", () => {
    for (const [, p] of pending) p.reject(new Error("hermes gateway WS closed"));
    pending.clear();
  });

  return {
    request(frame) {
      return new Promise((resolve, reject) => {
        if (socket.readyState !== 1 /* OPEN */) {
          reject(new Error("hermes gateway WS not open"));
          return;
        }
        const id = frame.id ?? nextId++;
        frame.id = id;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`hermes RPC ${frame.method} timed out`));
        }, timeoutMs);
        pending.set(id, {
          resolve: (v) => {
            clearTimeout(timer);
            resolve(v);
          },
          reject: (e) => {
            clearTimeout(timer);
            reject(e);
          },
        });
        socket.send(JSON.stringify(frame));
      });
    },
    close() {
      try {
        socket.close();
      } catch {
        /* ignore */
      }
    },
    _socket: socket,
  };
}


export function isGatewayConnectRefused(err) {
  if (!err) return false;
  const code = String(err.code || "").toUpperCase();
  if (
    code === "ECONNREFUSED" ||
    code === "EHOSTUNREACH" ||
    code === "ENETUNREACH" ||
    code === "ETIMEDOUT" ||
    code === "ENOTFOUND" ||
    code === "EAI_AGAIN"
  ) {
    return true;
  }
  const message = String(err.message || err || "");
  // WS open errors surface only the message on some impls. Exclude the known
  // post-connect / RPC messages so a mid-stream drop never trips the signal.
  if (/WS closed|WS not open|timed out|session busy/i.test(message)) return false;
  return /ECONNREFUSED|connection refused|connect ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|ENOTFOUND|getaddrinfo/i.test(
    message,
  );
}


/**
 * A gateway URL with its credentials removed, for text that leaves this process.
 *
 * A hermes gateway URL carries its auth token in the query string
 * (`ws://127.0.0.1:9147/api/ws?token=...`). Every message below is POSTed to the control plane -- as
 * a run's `error`, or as the `reason` that becomes `agents.status_note` -- and both are served by the
 * API and rendered on the dashboard. Measured on the live fleet 2026-08-25: SEVEN distinct gateway
 * tokens, 43 characters each, sitting in stored dispatch-run errors and readable by anything that can
 * read /dispatch/runs, which includes every agent.
 *
 * The tokens authenticate to a loopback gateway, so this is not remote exposure -- it is exposure
 * BETWEEN the agents sharing this host, which is the boundary the per-agent gateway exists to draw:
 * one agent's token is enough to attach to another's hermes session and drive it.
 *
 * Cut at the first `?` or `#` rather than parsed. A URL parser throws on the malformed input this is
 * most likely to be handed, and the answer to "I cannot parse this" must not be "print it anyway".
 */
export function redactGatewayUrl(gatewayUrl) {
  const raw = String(gatewayUrl || "").trim();
  if (!raw) return "(unknown)";
  const cut = raw.search(/[?#]/);
  return cut === -1 ? raw : raw.slice(0, cut);
}

/**
 * The liveness probe's version, which carries how many probes failed.
 *
 * Built rather than typed inline at the call site, which is where it was: that copy embedded the
 * token-bearing `host.wsUrl` AND told the operator the agent was "Self-correcting off 'available'",
 * which a managed agent does not do -- the server rests it cold-startable, still reading `available`,
 * so the next message can start a fresh session. Kept short on purpose: this becomes
 * `agents.status_note`, which the server truncates at 200 characters, and the remedy is the part
 * worth keeping.
 */
export function gatewayUnreachableAfterProbesMessage(gatewayUrl, consecutiveFailures) {
  const n = Number(consecutiveFailures) || 0;
  return (
    `Hermes gateway unreachable after ${n} liveness probes — restart this agent's hermes-aify ` +
    `session for a fresh gateway. Last seen at ${redactGatewayUrl(gatewayUrl)}.`
  );
}

/**
 * A gateway that answers but keeps turning this loop away (a 403 for a token it did not issue). It says
 * the agent cannot be delivered to, and nothing about whether a TUI is attached, which is unknown here.
 */
export function gatewayRejectedMessage(gatewayUrl, count, detail = "") {
  const url = redactGatewayUrl(gatewayUrl);
  // Within the 200-character status_note the server keeps, remedy first; the cause is cut, never the remedy.
  const why = redactTokens(detail).slice(0, 40);
  return (
    `Undeliverable: relaunch this agent's hermes-aify. Gateway ${url} rejected its delivery loop ` +
    `${Number(count) || 0} times in a row${why ? ` (${why})` : ""}; left running.`
  );
}

export function gatewayUnreachableMessage(gatewayUrl) {
  const url = redactGatewayUrl(gatewayUrl);
  return (
    `Hermes gateway unreachable at ${url} (connection refused). ` +
    `The gateway host likely died; restart this agent's hermes-aify session to get a fresh gateway.`
  );
}


export async function reportGatewayDead({
  httpCall,
  agentId,
  runtime = RUNTIME,
  machineId = MACHINE_ID,
  gatewayUrl = "",
  reason = "",
} = {}) {
  const id = String(agentId || "").trim();
  if (!httpCall || !id) return;
  const why = reason || gatewayUnreachableMessage(gatewayUrl);
  try {
    await httpCall("POST", `/agents/${encodeURIComponent(id)}/resident-lost`, {
      machineId,
      runtime,
      reason: why,
    });
  } catch (error) {
    console.error(
      `[hermes-managed-host] resident-lost self-correct for '${id}' failed (best-effort):`,
      error?.message || String(error),
    );
  }
}


export function gatewayIndexUrlFromWs(wsUrl) {
  const raw = String(wsUrl || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw);
    const proto = u.protocol === "wss:" ? "https:" : "http:";
    return `${proto}//${u.host}/`;
  } catch {
    const m = raw.match(/^wss?:\/\/([^/]+)/i);
    if (!m) return "";
    const proto = /^wss:/i.test(raw) ? "https:" : "http:";
    return `${proto}//${m[1]}/`;
  }
}


// `indexUrl` may be a function, read on every probe: the delivery loop's gateway can be replaced by a
// recovery on another port (0.9.10), and a URL captured at bring-up would keep probing the dead one.
export function makeGatewayReachabilityProbe({
  indexUrl,
  fetchImpl = (typeof fetch !== "undefined" ? fetch : undefined),
  timeoutMs = GATEWAY_PROBE_TIMEOUT_MS,
} = {}) {
  return async function probe() {
    const url = String((typeof indexUrl === "function" ? indexUrl() : indexUrl) || "").trim();
    if (!url || typeof fetchImpl !== "function") return { alive: false };
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller
      ? setTimeout(() => {
          try {
            controller.abort();
          } catch {
            /* ignore */
          }
        }, timeoutMs)
      : null;
    try {
      const res = await fetchImpl(url, {
        method: "GET",
        signal: controller ? controller.signal : undefined,
      });
      // A live dashboard host returns the index (200). Any HTTP response at all
      // means the port is bound + serving, so treat ok===false defensively but
      // a thrown connect (ECONNREFUSED) below is the real dead-gateway signal.
      return { alive: !!(res && res.ok !== false) };
    } catch {
      return { alive: false };
    } finally {
      if (timer) clearTimeout(timer);
    }
  };
}


export function shouldApplyGatewayTurnEnd(inFlight = {}) {
  return inFlight.dispatchTurnOpen !== true || inFlight.observedWorking === true;
}


/** On SIGTERM or SIGINT: run `teardown`, then exit. */
export function installShutdownTeardown({ teardown, proc = process } = {}) {
  const onSignal = async () => {
    if (typeof teardown === "function") await teardown();
    try {
      proc.exit(0);
    } catch {
      /* test fake / already exiting */
    }
  };
  proc.once("SIGTERM", onSignal);
  proc.once("SIGINT", onSignal);
}


// The loop's teardown, at most once per `state`: the shutdown hook and an explicit teardown can both fire. It runs
// `clearMarkers` (the claimer lease and the loop-ready marker) and touches no gateway: the gateway is the agent's,
// shared with its visible TUI, and ends with the agent's lease (killing one from here dropped the TUI's socket on
// 2026-06-02).
export function makeTeardown({ clearMarkers, state = { done: false } } = {}) {
  return async function teardown() {
    if (state.done) return;
    state.done = true;
    if (typeof clearMarkers === "function") {
      try {
        await clearMarkers();
      } catch {
        /* best-effort */
      }
    }
  };
}
