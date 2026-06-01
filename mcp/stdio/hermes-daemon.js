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
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { probeApiServer } from "./hermes-version.js";
import { agentEndpoint } from "./hermes-endpoint.js";
import { terminateProcessTree } from "./runtimes.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const execFile = promisify(nodeExecFile);

// --- per-agent daemon pid tracking -----------------------------------------
// Each hermes agent gets at most ONE `hermes gateway run` daemon. We persist the
// daemon's pid in a file alongside the per-agent port/key files (same tempDir +
// sanitizeAgentId convention from hermes-endpoint.js) so a later (re)spawn can
// kill the PRIOR daemon — even if its port has since changed/been abandoned —
// instead of leaking a stray hermes.exe. Mirrors the reuse-on-persist pattern of
// resolveGatewayPort/loadOrCreateKey.

// Sanitize an agentId into a safe filename fragment. Kept identical to
// hermes-endpoint.js's private sanitizeAgentId so the pid file sits next to the
// agent's port/key files.
function sanitizeAgentId(agentId) {
  return String(agentId || "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function daemonPidFile(agentId, tempDir, fsImpl) {
  return path.join(tempDir || os.tmpdir(), `aify-hermes-daemon-pid-${sanitizeAgentId(agentId)}`);
}

// Read the persisted daemon pid for an agent, or undefined. Never throws.
export function readDaemonPid(agentId, tempDir, { fs: fsImpl = fs } = {}) {
  if (!agentId) return undefined;
  try {
    const raw = String(fsImpl.readFileSync(daemonPidFile(agentId, tempDir), "utf8")).trim();
    const pid = parseInt(raw, 10);
    return Number.isInteger(pid) && pid > 0 ? pid : undefined;
  } catch {
    return undefined;
  }
}

// Persist the daemon pid for an agent. Best-effort; never throws.
export function writeDaemonPid(agentId, pid, tempDir, { fs: fsImpl = fs } = {}) {
  const n = Number(pid);
  if (!agentId || !Number.isInteger(n) || n <= 0) return false;
  try {
    fsImpl.writeFileSync(daemonPidFile(agentId, tempDir), String(n));
    return true;
  } catch {
    return false;
  }
}

// Remove the persisted daemon pid for an agent. Best-effort; never throws.
export function clearDaemonPid(agentId, tempDir, { fs: fsImpl = fs } = {}) {
  if (!agentId) return false;
  try {
    fsImpl.rmSync(daemonPidFile(agentId, tempDir), { force: true });
    return true;
  } catch {
    try {
      fsImpl.unlinkSync(daemonPidFile(agentId, tempDir));
      return true;
    } catch {
      return false;
    }
  }
}

// Is a pid alive right now? Best-effort, signal-0 probe. Never throws.
function defaultIsAlive(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    process.kill(n, 0);
    return true;
  } catch (err) {
    // EPERM = exists but not ours to signal → still alive.
    return err && err.code === "EPERM";
  }
}

// Default tree-killer keyed on a raw pid. Wraps terminateProcessTree (which
// takes a {pid} handle). Never throws.
function defaultKillTree(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    terminateProcessTree({ pid: n }, "SIGKILL");
    return true;
  } catch {
    return false;
  }
}

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
  // Injectable pid-tracking + killer so tests assert kill-prior fires without
  // touching real processes. Defaults are the real fs / tree-killer / alive-probe.
  killTree = defaultKillTree,
  isAlive = defaultIsAlive,
  readPid = readDaemonPid,
  writePid = writeDaemonPid,
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

  // 2. KILL-PRIOR before spawning a fresh daemon. The probe said NOT up, so the
  //    daemon we previously tracked (if any) is either dead or unhealthy — and
  //    its port may have been re-resolved (collision/death), so killByPort on the
  //    CURRENT port would miss it. Kill the prior pid's TREE to stop hermes.exe
  //    proliferation. Stale-pid-safe: only kill a pid that is still alive, and
  //    never the (already-confirmed-down) current daemon. Best-effort; never
  //    blocks the spawn.
  if (agentId) {
    const priorPid = readPid(agentId, tempDir);
    if (priorPid && isAlive(priorPid)) {
      killTree(priorPid);
    }
  }

  // 3. Spawn the daemon DETACHED so it outlives this bridge process.
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
    // Windows: without this, a detached console app (hermes.exe) pops a visible
    // empty cmd window. Hide it — the daemon is a background service.
    windowsHide: true,
  });
  if (child && typeof child.unref === "function") child.unref();

  // Persist the NEW daemon's pid so the next (re)spawn can kill-prior it.
  if (agentId && child && Number.isInteger(Number(child.pid))) {
    writePid(agentId, child.pid, tempDir);
  }

  // 4. Poll for health until the daemon answers or we time out.
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
  // Injectable pid-tracking + killer (defaults to the real fs / tree-killer /
  // alive-probe) so tests assert the tracked-pid kill without real processes.
  killTree = defaultKillTree,
  isAlive = defaultIsAlive,
  readPid = readDaemonPid,
  clearPid = clearDaemonPid,
} = {}) {
  let stopped = false;
  let pid;
  try {
    let derived = endpoint;
    if (!derived && agentId) {
      derived = agentEndpoint(agentId, tempDir ? { tempDir } : undefined);
    }
    const effPort = port ?? derived?.port;

    // 1. Port-based kill: take down whatever is LISTENING on the current port.
    if (effPort) {
      const res = await killByPort(effPort);
      if (res && res.killed) {
        stopped = true;
        pid = res.pid;
      }
    }

    // 2. Tracked-pid kill: covers a daemon whose port has already changed/been
    //    abandoned (so killByPort on the current port would miss the stray). Kill
    //    the tracked pid's TREE if it is still alive, then clear the pid file.
    //    Stale-pid-safe: only signal a pid that is alive.
    if (agentId) {
      const trackedPid = readPid(agentId, tempDir);
      if (trackedPid && isAlive(trackedPid)) {
        killTree(trackedPid);
        stopped = true;
        if (pid === undefined) pid = trackedPid;
      }
      clearPid(agentId, tempDir);
    }
  } catch {
    // Best-effort teardown must never throw — a failed reap is logged by callers.
  }
  return stopped ? { stopped: true, pid } : { stopped: false };
}
