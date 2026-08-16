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

import { spawn as nodeSpawn, execFile as nodeExecFile, spawnSync as nodeSpawnSync } from "node:child_process";
import { PS_UTF8_PRELUDE } from "./win32-text.js";
import { promisify } from "node:util";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { probeApiServer } from "./hermes-version.js";
import { agentEndpoint, clearGatewayMarkers as defaultClearGatewayMarkers } from "./hermes-endpoint.js";
import { terminateProcessTree } from "./runtimes.js";
// The filename sanitiser has ONE owner (`hermes-endpoint.js`); this module carried a
// byte-identical copy until v0.5.4. Three copies of a function that turns an agent id into a
// PATH is three chances for the same agent to get two different files.
import { sanitizeAgentId } from "./hermes-endpoint.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const execFile = promisify(nodeExecFile);

// --- cmdline cross-check (anti-overkill under OS pid/port reuse) ------------
// SAFETY: a stale `aify-hermes-port-<agent>` / `aify-hermes-daemon-pid-<agent>`
// marker can outlive the daemon. The OS may then free that port/pid and hand it
// to an UNRELATED operator process (a dev server, an editor, anything). Killing
// it by the stale marker alone would take down the operator's own process. Before
// any port- or tracked-pid kill we therefore confirm the target's image/cmdline
// actually belongs to a hermes daemon. Mirrors reap-managed-claude.js's
// parentBelongsToAgent / defaultGetCmdline cmdline verification.

// Get the command line of a pid. Injectable. Returns "" when unknown.
//   - win32: PowerShell Get-CimInstance Win32_Process (CommandLine).
//   - posix: `ps -o args= -p <pid>`.
export function defaultGetCmdline(pid, spawnSync = nodeSpawnSync) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return "";
  try {
    if (process.platform === "win32") {
      // PS_UTF8_PRELUDE: cmdline/path fields feed parentBelongsToAgent path
      // matching; OEM-encoded output mangles non-ASCII profile paths.
      const ps =
        PS_UTF8_PRELUDE +
        `$p = Get-CimInstance Win32_Process -Filter "ProcessId=${n}" -ErrorAction SilentlyContinue;` +
        `if ($p) { "$($p.CommandLine)\`t$($p.ExecutablePath)\`t$($p.Name)" }`;
      const res = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps], {
        encoding: "utf8", windowsHide: true, timeout: 5000,
      });
      return String(res.stdout || "").trim();
    }
    const res = spawnSync("ps", ["-o", "args=", "-p", String(n)], { encoding: "utf8", timeout: 5000 });
    return String(res.stdout || "").trim();
  } catch {
    return "";
  }
}

// Does this image/path/cmdline string belong to a hermes daemon? Case-insensitive
// substring match on "hermes" — the listener/tracked-pid must be a hermes process,
// never an unrelated operator process that recycled the same port/pid. Returns
// false for empty/unknown input → fail-safe (no confirmation ⇒ no kill).
export function looksLikeHermesProcess(cmdline) {
  return /hermes/i.test(String(cmdline || ""));
}

// --- per-agent daemon pid tracking -----------------------------------------
// Each hermes agent gets at most ONE `hermes gateway run` daemon. We persist the
// daemon's pid in a file alongside the per-agent port/key files (same tempDir +
// sanitizeAgentId convention from hermes-endpoint.js) so a later (re)spawn can
// kill the PRIOR daemon — even if its port has since changed/been abandoned —
// instead of leaking a stray hermes.exe. Mirrors the reuse-on-persist pattern of
// resolveGatewayPort/loadOrCreateKey.

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
  getCmdline = defaultGetCmdline,
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
    // PID-REUSE SAFETY (bughunt 2026-07-03): a crashed daemon leaves a stale pid
    // marker; the OS can recycle that PID to an UNRELATED operator process, and
    // killTree would then SIGKILL that innocent tree. Verify the pid is actually a
    // hermes process before killing — same guard every other kill site in this file
    // uses (stopDaemon, reapDaemonsForAgent). Missing here was the only unguarded kill.
    if (priorPid && isAlive(priorPid) && looksLikeHermesProcess(getCmdline(priorPid))) {
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
  // A detached spawn emits 'error' ASYNCHRONOUSLY (e.g. ENOENT when hermes is
  // missing/mis-resolved — which happened live 2026-07-03). With no listener Node
  // re-throws it as an uncaught exception OUTSIDE the health-poll try/catch, killing
  // the whole managed-host process. Swallow it here; the health poll below already
  // handles "did not come up" by timing out and returning started:false.
  if (child && typeof child.on === "function") {
    child.on("error", (err) => {
      try { console.error(`[hermes-daemon] spawn error for agent=${agentId || "?"}: ${err?.message || err}`); } catch {}
    });
  }
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

// Resolve the PID(s) LISTENING on `port`. Platform-aware: Windows uses
// PowerShell's Get-NetTCPConnection (→ OwningProcess); POSIX uses lsof. Returns
// an array of pids (possibly empty). Never throws → [] on any failure. Injectable
// so killByPort's listener resolution is testable without binding a real socket.
export async function defaultResolveListenerPids(port) {
  if (!port) return [];
  try {
    if (process.platform === "win32") {
      const ps =
        `$ErrorActionPreference='SilentlyContinue';` +
        `$c = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen;` +
        `if (-not $c) { exit 3 };` +
        `$c | Select-Object -ExpandProperty OwningProcess`;
      const { stdout } = await execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps]);
      return String(stdout).split(/\s+/).map((s) => parseInt(s, 10)).filter((n) => Number.isFinite(n) && n > 0);
    }
    const { stdout } = await execFile("lsof", ["-ti", `tcp:${Number(port)}`, "-sTCP:LISTEN"]);
    return String(stdout).split(/\s+/).map((s) => parseInt(s, 10)).filter((n) => Number.isFinite(n) && n > 0);
  } catch {
    return [];
  }
}

// IS THIS A PID WE MAY SIGNAL? Its own predicate, so the rule can fail a test instead of only
// failing in production — the `doctor-predicates.js` pattern.
//
// It was inline in `defaultKillOnePid` and could not be proved ON THIS HOST: with the check removed,
// Windows `Stop-Process -Id 0` simply errors and the helper still returns false, so every mutation
// weakening the guard survived. The rule it encodes only bites on POSIX, where `process.kill(0,
// SIGTERM)` signals THE ENTIRE PROCESS GROUP — the wrapper, the bridge and every sibling the
// operator's shell started — and a negative pid signals a process group by number. A guard whose
// whole purpose is a platform this machine is not can still be tested as a predicate.
//
// STRINGS ARE REFUSED even when they parse: a pid arriving as text came from a marker file or a
// command's output, and the caller has not established it is still the process it thinks it is.
export function isKillablePid(value) {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

// Kill one pid (no tree). Platform-aware: Windows Stop-Process -Force; POSIX
// SIGTERM. Never throws → false on failure. Injectable.
export async function defaultKillOnePid(pid) {
  if (!isKillablePid(pid)) return false;
  const n = pid;
  try {
    if (process.platform === "win32") {
      await execFile("powershell.exe", [
        "-NoProfile", "-NonInteractive", "-Command",
        `$ErrorActionPreference='SilentlyContinue'; Stop-Process -Id ${n} -Force`,
      ]);
      return true;
    }
    process.kill(n, "SIGTERM");
    return true;
  } catch {
    return false;
  }
}

// Default port→PID→kill. Find the process LISTENING on `port`, VERIFY it is a
// hermes daemon, and only then kill it. NEVER throws — on any failure or no-match
// it resolves { killed:false }. Resolution / cmdline lookup / kill are all
// injectable so tests never touch a real process or socket.
//
// SAFETY (anti-overkill): a stale `aify-hermes-port-<agent>` marker can name a
// port the OS has since freed and handed to an UNRELATED operator process. Before
// killing, we resolve the listener's cmdline/image and require it to look like
// hermes (looksLikeHermesProcess). If it does not, we SKIP and resolve
// { killed:false, skipped:true } so we never kill the operator's own dev server.
export async function defaultKillByPort(
  port,
  {
    getCmdline = defaultGetCmdline,
    resolveListenerPids = defaultResolveListenerPids,
    killOnePid = defaultKillOnePid,
  } = {},
) {
  if (!port) return { killed: false };
  try {
    const pids = await resolveListenerPids(port);
    if (!Array.isArray(pids) || pids.length === 0) return { killed: false };
    let killedAny;
    let skippedAny = false;
    for (const pid of pids) {
      // VERIFY: the listener must be a hermes process before we kill it.
      if (!looksLikeHermesProcess(getCmdline(pid))) {
        skippedAny = true;
        try { console.error(`[hermes] killByPort: listener on port ${Number(port)} (pid ${pid}) is not hermes — SKIP (stale port marker, port reused by unrelated process)`); } catch { /* ignore */ }
        continue;
      }
      if (await killOnePid(pid) && killedAny === undefined) killedAny = pid;
    }
    if (killedAny !== undefined) return { killed: true, pid: killedAny };
    return { killed: false, skipped: skippedAny || undefined };
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
  // Injectable cmdline lookup so the tracked-pid cross-check is testable without
  // touching real processes.
  getCmdline = defaultGetCmdline,
  // Terminal teardown clears the agent's port/key markers (Task 4.1). stopDaemon
  // is an explicit/terminal stop, so dropping the markers here is safe (NOT a
  // transient retry). Injectable for tests.
  clearGatewayMarkers = defaultClearGatewayMarkers,
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
    //    the tracked pid's TREE if it is still alive AND its cmdline confirms it is
    //    hermes, then clear the pid file. Stale-pid-safe two ways: (a) only signal
    //    a pid that is alive; (b) anti-overkill — under OS pid reuse a stale
    //    daemon-pid marker can name an UNRELATED operator process, so verify the
    //    pid's cmdline looks like hermes before killTree. If it does not match, we
    //    SKIP the kill, log it, and still clear the stale marker.
    if (agentId) {
      const trackedPid = readPid(agentId, tempDir);
      if (trackedPid && isAlive(trackedPid)) {
        if (looksLikeHermesProcess(getCmdline(trackedPid))) {
          killTree(trackedPid);
          stopped = true;
          if (pid === undefined) pid = trackedPid;
        } else {
          try { console.error(`[hermes] stopDaemon: tracked pid ${trackedPid} for agent ${agentId} is not hermes — SKIP (stale daemon-pid marker, pid reused by unrelated process)`); } catch { /* ignore */ }
        }
      }
      clearPid(agentId, tempDir);
      // Terminal stop → also drop the agent's port/key gateway markers so a
      // restart is a clean slate (Task 4.1). Best-effort; never throws.
      try {
        clearGatewayMarkers(agentId, tempDir);
      } catch {
        /* best-effort */
      }
    }
  } catch {
    // Best-effort teardown must never throw — a failed reap is logged by callers.
  }
  return stopped ? { stopped: true, pid } : { stopped: false };
}
