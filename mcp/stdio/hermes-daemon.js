#!/usr/bin/env node
// ensureDaemon — guarantee a long-lived per-agent `hermes gateway run` daemon
// is up with the api_server platform enabled, idempotently.
//
// ASYMMETRY(hermes): each hermes agent gets its OWN api_server daemon (own port
// + own key, derived deterministically from agentId via hermes-endpoint.js) so
// the aify-comms MCP tools loaded into that daemon carry the agent's
// AIFY_AGENT_ID and comms_send attributes the reply to the right agent. A
// single shared daemon is one process = one identity and cannot. This is
// hermes's equivalent of claude's "one process per agent."
//
// Managed hermes delivery POSTs to that agent's api_server platform (HTTP/SSE)
// running in-process inside its `hermes gateway run` daemon. This helper is the
// ensure-up step: probe first, and only spawn (DETACHED) if the daemon isn't
// already answering — so calling it from every per-agent sidecar launch is safe.
//
// spawn + probe are injected so tests never launch a real process or touch a
// real socket. Contract:
// docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import { spawn as nodeSpawn, execFile as nodeExecFile } from "node:child_process";
import { promisify } from "node:util";
import { probeApiServer } from "./hermes-version.js";
import { agentEndpoint } from "./hermes-endpoint.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const execFile = promisify(nodeExecFile);

// Ensure one api_server-enabled hermes gateway daemon is up for an agent.
//   - Resolve the per-agent endpoint: explicit `endpoint` wins; else derive via
//     agentEndpoint(agentId). Explicit baseUrl/key/port/host still override
//     (back-compat with callers that pass them directly).
//   - First probe that endpoint; if available → { started:false, version }.
//   - Else spawn `hermes gateway run --replace` detached with API_SERVER_* env,
//     unref it, and poll probe until healthy or healthTimeoutMs elapses.
//   - On success → { started:true, version, pid, endpoint }. On timeout → throw.
export async function ensureDaemon({
  agentId,
  endpoint,
  tempDir,
  baseUrl,
  key,
  port,
  host = "127.0.0.1",
  hermesCmd = "hermes",
  spawn = nodeSpawn,
  probe = probeApiServer,
  healthTimeoutMs = 15000,
  pollMs = 300,
} = {}) {
  // Resolve the effective endpoint. Precedence: explicit fields > endpoint
  // object > agentEndpoint(agentId) > legacy shared default.
  let derived = endpoint;
  if (!derived && agentId) {
    derived = agentEndpoint(agentId, tempDir ? { tempDir } : undefined);
  }
  const effHost = host ?? derived?.host ?? "127.0.0.1";
  const effPort = port ?? derived?.port ?? 8642;
  const effKey = key ?? derived?.key;
  const effBaseUrl = baseUrl ?? derived?.baseUrl ?? `http://${effHost}:${effPort}`;

  // 1. Idempotent fast-path: already up → no spawn.
  const initial = await probe({ baseUrl: effBaseUrl, key: effKey });
  if (initial && initial.available) {
    return {
      started: false,
      version: initial.version,
      endpoint: { host: effHost, port: effPort, baseUrl: effBaseUrl, key: effKey },
    };
  }

  // 2. Spawn the daemon DETACHED so it outlives this bridge process.
  const child = spawn(hermesCmd, ["gateway", "run", "--replace"], {
    env: {
      ...process.env,
      API_SERVER_ENABLED: "1",
      API_SERVER_KEY: effKey,
      API_SERVER_PORT: String(effPort),
      API_SERVER_HOST: effHost,
    },
    detached: true,
    stdio: "ignore",
  });
  if (child && typeof child.unref === "function") child.unref();

  // 3. Poll for health until the daemon answers or we time out.
  const deadline = Date.now() + healthTimeoutMs;
  for (;;) {
    const res = await probe({ baseUrl: effBaseUrl, key: effKey });
    if (res && res.available) {
      return {
        started: true,
        version: res.version,
        pid: child ? child.pid : undefined,
        endpoint: { host: effHost, port: effPort, baseUrl: effBaseUrl, key: effKey },
      };
    }
    if (Date.now() >= deadline) break;
    await sleep(pollMs);
  }

  throw new Error(
    `[hermes] hermes gateway daemon did not become healthy within ${healthTimeoutMs}ms — ` +
      "check `hermes gateway run` / API_SERVER_* env (API_SERVER_ENABLED, " +
      `API_SERVER_KEY, API_SERVER_PORT=${effPort}, API_SERVER_HOST=${effHost}).`,
  );
}

// Default port→PID→kill. Find the process LISTENING on `port` and kill it.
// Best-effort and platform-aware: Windows uses PowerShell's Get-NetTCPConnection
// (→ OwningProcess → Stop-Process); POSIX uses lsof (→ PID → kill). NEVER throws
// — on any failure or no-match it resolves { killed:false }. Injectable so tests
// never touch a real process.
async function defaultKillByPort(port) {
  if (!port) return { killed: false };
  try {
    if (process.platform === "win32") {
      // Resolve the owning PID of the listener on this port, then Stop-Process it.
      // Emit the PID so the caller can report it. -State Listen narrows to the
      // bound daemon (not transient client sockets).
      const ps =
        `$ErrorActionPreference='SilentlyContinue';` +
        `$c = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen;` +
        `if (-not $c) { exit 3 };` +
        `$p = $c | Select-Object -First 1 -ExpandProperty OwningProcess;` +
        `if (-not $p) { exit 3 };` +
        `Stop-Process -Id $p -Force;` +
        `Write-Output $p`;
      const { stdout } = await execFile("powershell.exe", [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        ps,
      ]);
      const pid = parseInt(String(stdout).trim(), 10);
      return { killed: Number.isFinite(pid), pid: Number.isFinite(pid) ? pid : undefined };
    }
    // POSIX: lsof -ti tcp:<port> -sTCP:LISTEN → PID(s), then kill each.
    const { stdout } = await execFile("lsof", [
      "-ti",
      `tcp:${Number(port)}`,
      "-sTCP:LISTEN",
    ]);
    const pids = String(stdout)
      .split(/\s+/)
      .map((s) => parseInt(s, 10))
      .filter((n) => Number.isFinite(n) && n > 0);
    if (pids.length === 0) return { killed: false };
    for (const pid of pids) {
      try {
        process.kill(pid, "SIGTERM");
      } catch {
        /* already gone */
      }
    }
    return { killed: true, pid: pids[0] };
  } catch {
    // lsof/powershell missing, no match, or non-zero exit → treat as not-found.
    return { killed: false };
  }
}

// Tear down the per-agent `hermes gateway run` daemon for an agent by killing
// whatever process is LISTENING on its api_server port. Symmetric counterpart to
// ensureDaemon: the sidecar that ensured the daemon tears it down on exit.
//   - Resolve the port: explicit endpoint.port/port wins; else agentEndpoint(agentId).
//   - killByPort(port) is injectable (defaults to defaultKillByPort).
//   - Idempotent: no daemon on that port → { stopped:false }.
//   - NEVER throws.
// Returns { stopped:bool, pid? }.
export async function stopDaemon({
  agentId,
  endpoint,
  tempDir,
  port,
  probe = probeApiServer, // accepted for symmetry/future use; not required here
  killByPort = defaultKillByPort,
} = {}) {
  try {
    let derived = endpoint;
    if (!derived && agentId) {
      derived = agentEndpoint(agentId, tempDir ? { tempDir } : undefined);
    }
    const effPort = port ?? derived?.port;
    if (!effPort) return { stopped: false };
    const res = await killByPort(effPort);
    if (res && res.killed) {
      return { stopped: true, pid: res.pid };
    }
    return { stopped: false };
  } catch {
    // Best-effort teardown must never throw — a failed reap is logged by callers.
    return { stopped: false };
  }
}
