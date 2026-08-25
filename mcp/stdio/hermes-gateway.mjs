// The hermes gateway host: starting it, proving it is alive, reporting it dead, and tearing it down.
//
// FIRST EXTRACTION from `hermes-managed-host.js` (3,017 lines) in v0.5.4. Fifteen functions, ~500 lines, one
// subject. Twelve were ALREADY exported and keep their public names — `server.js` imports four of them at
// bridge startup and `hermes-channel.js` one — so this is a pure relocation of an existing public surface
// rather than an invention of one. Three helpers (`waitForIndexToken`, `scrapeToken`, `sleep`) were private
// and stay private.
//
// WHY `installShutdownTeardown` IS HERE even though its name is not about gateways. It shares
// `_teardownState` with `teardownGatewayHost`, and that object is reached through a DEFAULT PARAMETER
// (`state = _teardownState`) rather than by name, so it never appears on the left of an assignment and a
// name-based scan for mutated module state does not see it at all. Extracting only the gateway half would
// have put one reader of a shared mutable object on each side of a module boundary — the same class of
// defect as two instances of a write queue. Both readers live here, so the state has one owner.
//
// The teardown flag matters because teardown is idempotent by contract: the shutdown hook and an explicit
// teardown can both fire, and `done` is what stops the second one from killing a gateway a later run has
// already started.
//
// DEPLOYMENT: this file runs on the HOST, not in the container. A change here is inert until `install.sh`
// is re-run and the client wrappers relaunch — a running wrapper keeps executing the copy it loaded at
// boot. `aify-comms doctor` reports that as `bridge-installed` and `bridge-current`.

import fs from "fs";
import os from "os";
import path from "path";

import { isTuiDepsBuildFailure, tuiDepsBuildFailureMessage } from "./hermes-gateway-liveness.js";
import { HERMES_CMD, MACHINE_ID, RUNTIME } from "./hermes-env.mjs";

// `sleep` is exported and that deserves a word, because a generic 3-line helper on a gateway module's
// public surface looks like carelessness. It has readers on BOTH sides — `waitForIndexToken` here, and four
// call sites left in the host — and there is no neutral owner to put it in: `mcp/stdio` currently has FIVE
// separate private `sleep` definitions (claude-channel, hermes-channel, hermes-daemon, server, and this
// file's original), none of them exported. Adding a sixth copy would break the one-owner rule this series
// runs on, and inventing a `hermes-shared.mjs` for it would be the junk drawer under a better name. So it
// has one owner and the host imports it, which is the correct direction even if the name reads oddly here.
// Unifying the five copies is a real task and it is not this slice.


const GATEWAY_PROBE_TIMEOUT_MS = Math.max(
  500,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_TIMEOUT_MS || 5000),
);
const READY_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_GATEWAY_READY_MS || 60000));
const RPC_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_RPC_TIMEOUT_MS || 60000));
export const MAX_REENSURE_WITHOUT_RECOVERY = 3;
const _teardownState = { done: false };


export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}


async function scrapeToken(indexUrl, fetchImpl) {
  const res = await fetchImpl(indexUrl, { method: "GET" });
  if (!res || res.ok === false) {
    const status = res?.status ?? "?";
    throw new Error(`dashboard index ${indexUrl} returned ${status}`);
  }
  const body = await res.text();
  const match = String(body).match(/__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/);
  if (!match) throw new Error(`__HERMES_SESSION_TOKEN__ not found in ${indexUrl}`);
  return match[1];
}


async function waitForIndexToken(indexUrl, fetchImpl, { deadlineMs, intervalMs, detectFailure } = {}) {
  const deadline = Date.now() + deadlineMs;
  let lastErr = null;
  for (;;) {
    try {
      return await scrapeToken(indexUrl, fetchImpl);
    } catch (err) {
      lastErr = err;
      // FAIL FAST on a known-fatal boot signature (task #237 item e). The
      // "Installing TUI dependencies" npm step runs OUTSIDE `--skip-build` and, on
      // hermes upstream drift, dies `npm error Missing script: "build"` — the
      // dashboard then NEVER binds, so without this check the launch limps to the
      // opaque ~60s readiness timeout. `detectFailure` (injected by ensureGatewayHost)
      // returns the CLEAR, distinct message the instant the signature is seen.
      if (typeof detectFailure === "function") {
        let sig = null;
        try {
          sig = await detectFailure();
        } catch {
          sig = null;
        }
        if (sig) throw new Error(sig);
      }
      if (Date.now() > deadline) {
        throw new Error(
          `hermes dashboard at ${indexUrl} did not become ready within ${deadlineMs}ms: ` +
            (lastErr?.message || String(lastErr)),
        );
      }
      await sleep(intervalMs);
    }
  }
}


export async function ensureGatewayHost({
  agentId,
  port,
  hermesCmd = HERMES_CMD,
  spawn,
  fetchImpl = (typeof fetch !== "undefined" ? fetch : undefined),
  probeFirst = true,
  readyTimeoutMs = READY_TIMEOUT_MS,
  readyIntervalMs = 250,
  // Readiness WS-verify (2026-06-04, hardening from the gateway-WS incident).
  verifyWs = String(process.env.AIFY_HERMES_VERIFY_WS || "1").trim() !== "0",
  openWsImpl = openGatewayWsClient,
  wsVerifyTimeoutMs = 5000,
} = {}) {
  if (!spawn) throw new Error("ensureGatewayHost requires an injected spawn");
  if (!fetchImpl) throw new Error("ensureGatewayHost requires a fetch implementation");
  const indexUrl = `http://127.0.0.1:${port}/`;
  const wsUrlFor = (token) => `ws://127.0.0.1:${port}/api/ws?token=${token}`;

  // Readiness is NOT just the index token. The `/api/ws` WebSocket the bridge +
  // visible TUI attach to is gated SEPARATELY by the dashboard embedded-chat
  // feature (HERMES_DASHBOARD_TUI / `--tui`). A host can serve the index while
  // `/api/ws` closes 4403 — the 2026-06-04 fleet incident, where the index-only
  // readiness check declared the gateway "ready" and the dead socket surfaced
  // only later as a headless orphan. Verify the socket actually OPENs before
  // declaring ready, so a dead WS fails FAST here with an actionable error
  // instead of limping. Injectable + timeout-bounded; the transient probe client
  // is closed immediately so it never lingers as an "attached session".
  const verifyWsOpen = async (token) => {
    if (!verifyWs) return;
    let probe = null;
    try {
      probe = await openWsImpl(wsUrlFor(token), { timeoutMs: wsVerifyTimeoutMs });
    } catch (err) {
      throw new Error(
        `hermes gateway /api/ws on port ${port} did not accept a WebSocket ` +
        `(embedded-chat/HERMES_DASHBOARD_TUI likely disabled — index served but ` +
        `/api/ws closed): ${err?.message || String(err)}`,
      );
    } finally {
      try { probe?.close?.(); } catch { /* ignore */ }
    }
  };

  // Idempotent probe: a host already serving the index → reuse it, no spawn.
  // Verify its /api/ws too — a stale pre-fix host serves the index but its socket
  // is dead, and silently reusing it reproduces the incident.
  if (probeFirst) {
    let reuseToken = null;
    try {
      reuseToken = await scrapeToken(indexUrl, fetchImpl);
    } catch {
      reuseToken = null; /* not up yet → spawn below */
    }
    if (reuseToken) {
      await verifyWsOpen(reuseToken);
      return { port, token: reuseToken, wsUrl: wsUrlFor(reuseToken), child: null, reused: true };
    }
  }

  // hermes 0.15.1 (2026.5.29) moved `--tui` to a TOP-LEVEL flag; the `dashboard`
  // subcommand now REJECTS it ("unrecognized arguments: --tui"), which killed the
  // gateway host at spawn → ensure-host's 60s readiness timeout → every managed
  // hermes dispatch reaped as "no live claimer". So the `--tui` CLI flag is dropped.
  //
  // BUT (2026-06-04, root-caused from the operator's "gateway websocket connection
  // failed" incident): `--tui` did MORE than the index — it enabled the dashboard's
  // EMBEDDED-CHAT feature, which gates the `/api/ws` JSON-RPC WebSocket the bridge +
  // visible TUI attach to (`web_server.py`: `if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
  // ws.close(code=4403)` on `/api/ws`). Plain `hermes dashboard` serves the index
  // TOKEN but its `/api/ws` CLOSES (empirically: code=1006/4403) — so the earlier
  // "plain dashboard serves /api/ws (verified)" claim was WRONG; it only verified the
  // index, not the socket. `_DASHBOARD_EMBEDDED_CHAT_ENABLED` is set by `--tui` OR the
  // `HERMES_DASHBOARD_TUI=1` env (hermes_cli/web_server.start_server). Since the flag
  // is rejected on the subcommand, we set the ENV instead (verified: `/api/ws` -> OPEN
  // with it, CLOSE without). This is the crash-safe equivalent of the old `--tui`.
  const args = [
    "dashboard",
    "--port",
    String(port),
    "--host",
    "127.0.0.1",
    "--no-open",
    "--skip-build",
  ];
  // Capture the gateway host's stderr (was `stdio:"ignore"`, which SILENTLY hid
  // spawn/arg errors — the 0.15.1 `--tui` rejection took a full manual repro to
  // surface). stdin/stdout stay ignored; stderr → a per-port log so the next
  // gateway failure is one `tail` away.
  let gwErrFd = "ignore";
  let gwErrPath = "";
  try {
    const logDir = path.join(os.homedir(), ".local", "state", "aify-comms");
    fs.mkdirSync(logDir, { recursive: true });
    gwErrPath = path.join(logDir, `hermes-gateway-host-${port}.log`);
    // TRUNCATE per spawn ("w", not "a") — #237 fix: detectBootFailure reads this log's
    // tail to fail-fast on a TUI-deps/npm-build failure. In append mode a PRIOR boot's
    // failure signature lingered in the tail, so after the operator fixed the build and
    // relaunched (as the error tells them to), the healthy boot's early readiness polls
    // re-read the stale signature and FALSE-ABORTED the fixed relaunch. Each spawn owns a
    // fresh log so detection only ever sees the CURRENT boot.
    gwErrFd = fs.openSync(gwErrPath, "w");
  } catch {
    gwErrFd = "ignore";
    gwErrPath = "";
  }
  const child = spawn(hermesCmd, args, {
    stdio: ["ignore", "ignore", gwErrFd],
    detached: true,
    windowsHide: true, // CRITICAL: no popup window on Windows (ConPTY-less child).
    // Managed agents run unattended — there is no operator at the wheel to answer
    // tool-approval prompts (execute_code, etc.). hermes freezes YOLO at import from
    // HERMES_YOLO_MODE (tools/approval.py), so the gateway HOST that actually runs
    // the dispatch turn must carry it — the wrapper's `--yolo` only reaches the
    // visible TUI *client*, which does NOT govern the gateway-hosted turn's approvals.
    // `hermes dashboard` REJECTS a `--yolo` flag (unrecognized arg, like the 0.15.1
    // `--tui` rejection), so the env var is the correct, crash-safe lever.
    //
    // HERMES_DASHBOARD_TUI=1 enables the dashboard EMBEDDED-CHAT feature that gates
    // the `/api/ws` WebSocket the bridge + visible TUI attach to (see the args comment
    // above). Without it `/api/ws` closes 4403 → "gateway websocket connection failed"
    // across all managed hermes agents → headless orphans. It is the crash-safe env
    // equivalent of the `--tui` flag the dashboard subcommand rejects.
    // INERT on current hermes: the upstream 0.15.1 patch `cae6b5486` hardcodes
    // `_DASHBOARD_EMBEDDED_CHAT_ENABLED = True` and removed the `--tui`/HERMES_DASHBOARD_TUI
    // gate, so on that build (and later) plain `hermes dashboard` already serves `/api/ws`
    // and this env is a harmless no-op. DO NOT REMOVE IT — it is retained as the crash-safe
    // lever for PINNED-OLDER hermes 0.15.x builds (pre-`cae6b5486`), where `/api/ws` still
    // closes 4403 without it. (See KNOWN_ISSUES.md and DECISIONS.md.)
    env: { ...process.env, HERMES_YOLO_MODE: "1", HERMES_DASHBOARD_TUI: "1" },
  });
  if (typeof gwErrFd === "number") {
    try { fs.closeSync(gwErrFd); } catch {}
  }
  // A detached spawn emits 'error' ASYNCHRONOUSLY (ENOENT when the hermes binary is
  // missing/mis-resolved — happened live 2026-07-03 when a hermes update left no
  // hermes.exe). With no listener Node re-throws it as an UNCAUGHT exception outside
  // the awaited waitForIndexToken below, bypassing the caller's try/catch and killing
  // the whole managed-host process (heartbeat + delivery loop + turn detector). Route
  // it into the same "did not come up" failure path the readiness timeout uses.
  if (child && typeof child.on === "function") {
    child.on("error", (err) => {
      try { console.error(`[hermes-managed-host] gateway spawn error on port ${port}: ${err?.message || err}`); } catch {}
    });
  }
  // Don't let the gateway host keep the helper alive on its own; we manage its
  // lifecycle explicitly via teardown.
  if (typeof child.unref === "function") child.unref();

  // TUI-deps / npm-build boot-failure FAST-FAIL (task #237 item e). The gateway
  // child's stderr can carry the fatal `npm error Missing script: "build"` from the
  // "Installing TUI dependencies" step (runs OUTSIDE `--skip-build`); when it does,
  // the dashboard never binds and — without this — the launch limps to the opaque ~60s
  // readiness timeout. We watch stderr TWO ways so both stdio shapes are covered:
  //   (1) a readable child.stderr stream (the opt-in pipe path + injected test fakes),
  //   (2) the per-port stderr LOG FILE (the LIVE path routes stderr to a file fd, so
  //       child.stderr is null there) — read its tail during the readiness poll.
  // Both are additive + best-effort and NEVER touch the happy-path launch. Reading the
  // log file only happens DURING the readiness wait (this CLI/loop process is still
  // alive), so it never risks the detached child's file fd.
  let gwStderrBuf = "";
  if (child && child.stderr && typeof child.stderr.on === "function") {
    try {
      child.stderr.on("data", (chunk) => {
        try {
          gwStderrBuf = (gwStderrBuf + String(chunk)).slice(-8192);
        } catch {
          /* ignore */
        }
      });
      child.stderr.on("error", () => {});
    } catch {
      /* ignore — stderr scanning is best-effort */
    }
  }
  const detectBootFailure = async () => {
    if (isTuiDepsBuildFailure(gwStderrBuf)) return tuiDepsBuildFailureMessage(port, gwStderrBuf);
    if (gwErrPath) {
      try {
        const tail = fs.readFileSync(gwErrPath, "utf8").slice(-8192);
        if (isTuiDepsBuildFailure(tail)) return tuiDepsBuildFailureMessage(port, tail);
      } catch {
        /* log unreadable yet → nothing to detect */
      }
    }
    return null;
  };

  const token = await waitForIndexToken(indexUrl, fetchImpl, {
    detectFailure: detectBootFailure,
    deadlineMs: readyTimeoutMs,
    intervalMs: readyIntervalMs,
  });
  // Index served — now confirm the /api/ws socket actually opens (see verifyWsOpen).
  await verifyWsOpen(token);
  return { port, token, wsUrl: wsUrlFor(token), child, reused: false };
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


export function makeGatewayReachabilityProbe({
  indexUrl,
  fetchImpl = (typeof fetch !== "undefined" ? fetch : undefined),
  timeoutMs = GATEWAY_PROBE_TIMEOUT_MS,
} = {}) {
  return async function probe() {
    const url = String(indexUrl || "").trim();
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


export async function teardownGatewayHost({ child, state = _teardownState } = {}) {
  if (state.done) return;
  state.done = true;
  try {
    if (child && typeof child.kill === "function") child.kill("SIGTERM");
  } catch (error) {
    console.error(
      "[hermes-managed-host] gateway-host teardown failed (best-effort):",
      error?.message || String(error),
    );
  }
}


export function installShutdownTeardown({
  getChild,
  teardown,
  proc = process,
  state = _teardownState,
} = {}) {
  const onSignal = async () => {
    if (typeof teardown === "function") {
      await teardown();
    } else {
      const child = typeof getChild === "function" ? getChild() : null;
      await teardownGatewayHost({ child, state });
    }
    try {
      proc.exit(0);
    } catch {
      /* test fake / already exiting */
    }
  };
  proc.once("SIGTERM", onSignal);
  proc.once("SIGINT", onSignal);
}


// v0.5.4: `makeTeardown` arrived from the host as an ordinary top-level relocation. It belongs here because
// this module already owns `teardownGatewayHost`, `installShutdownTeardown` and `_teardownState` — tearing a
// gateway down IS its subject, and the host was only its caller.
//
// It moves cleanly because it captures nothing: `gatewayChild`, `clearMarkers` and `state` are all
// parameters, it calls no other function, and it imports nothing. That is the difference between this and the
// seams inside `runDeliveryLoop`, seven of which close over the loop's mutable `let` bindings and therefore
// cannot be relocated at all.
//
// THE COMMENT INSIDE IT IS THE VALUABLE PART and is preserved verbatim: the loop must kill the gateway ONLY
// if it spawned the child itself. A REUSED gateway (gatewayChild === null) is the one the wrapper's
// `ensure-host` started for the VISIBLE TUI, which shares it — killing that dropped the TUI's WebSocket in
// production on 2026-06-02. A shared gateway's lifetime belongs to the TUI, not to a delivery loop, and it is
// reaped by kill-prior on relaunch instead.

export function makeTeardown({
  gatewayChild = null,
  clearMarkers,
  state = { done: false },
} = {}) {
  return async function teardown() {
    if (state.done) return;
    state.done = true;
    try {
      // Kill the gateway host ONLY if THIS loop itself spawned it (an owned
      // child handle). A REUSED gateway (gatewayChild===null) is the one the
      // wrapper's `ensure-host` started for the VISIBLE TUI — the TUI shares that
      // gateway, so the loop MUST NOT kill it. Port-killing a reused gateway here
      // dropped the TUI's WebSocket ("gateway websocket connection failed",
      // 2026-06-02). A reused/shared gateway is reaped by kill-prior on relaunch
      // and the env-bridge survivor sweep on restart — its lifetime ties to the
      // TUI/console, NOT this loop.
      if (gatewayChild && typeof gatewayChild.kill === "function") {
        gatewayChild.kill("SIGTERM");
      }
    } catch (error) {
      console.error(
        "[hermes-managed-host] gateway-host teardown failed (best-effort):",
        error?.message || String(error),
      );
    }
    if (typeof clearMarkers === "function") {
      try {
        await clearMarkers();
      } catch {
        /* best-effort */
      }
    }
  };
}
