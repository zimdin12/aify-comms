// kill-prior collects what a previous generation of an agent's hermes left running (hermes-prior-reap.mjs).
//
// THE INCIDENT, 2026-09-14: three agents refused every message because the previous generation's gateway
// host still held hermes' session lease, on the port the agent had PERSISTED rather than the hash port
// `stopDaemon` looked at -- and `stopDaemon` then cleared the persisted marker, so the next launch moved
// to a new port and the leftover was never found again.
//
// The plan is proven on process tables; the last test runs the real `hermes-daemon-cli.js stop` against
// real processes whose command lines are shaped like a gateway host and a lease holder, sealed to a temp
// marker directory and a temp HERMES_HOME, which is the Windows path kill-prior actually takes.

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { startTimes } from "aify-wrapper/lib/process-identity.mjs";

import { stopDaemon } from "../hermes-daemon.js";
import { agentPort, writeSessionIdMarker } from "../hermes-endpoint.js";
import { ancestry, hermesHome, persistedGatewayPort, planPriorReap, readSessionLeases, reapPriorHermes } from "../hermes-prior-reap.mjs";
import { defaultListProcesses } from "../proc-probes.js";
import { sealedChildEnv } from "./_child-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = path.join(HERE, "..", "hermes-daemon-cli.js");
const T0 = Date.parse("2026-09-14T18:00:00Z");
const gatewayCmd = (port) => `C:\\hermes\\venv\\Scripts\\hermes.exe dashboard --host 127.0.0.1 --port ${port}`;
const lease = (pid, sessionId, startedMs) => ({ pid, session_id: sessionId, process_start_time: startedMs / 1000, surface: "tui" });

function plan(overrides = {}) {
  return planPriorReap({
    ports: [9272, 9341],
    rows: [],
    leases: [],
    sessionId: "20260715_001441_960b8f",
    leaseStarts: new Map(),
    protect: new Set([1, 2]),
    ...overrides,
  });
}

test("THE INCIDENT: the gateway on the PERSISTED port and the session's lease holder are stopped, once each", () => {
  const rows = [
    { pid: 59544, ppid: 4, commandLine: gatewayCmd(9272) },
    { pid: 59545, ppid: 59544, commandLine: `python -m hermes dashboard --port 9272` },
    { pid: 70000, ppid: 4, commandLine: "hermes --tui --resume 20260715_001441_960b8f" },
  ];
  const leases = [lease(59544, "20260715_001441_960b8f", T0), lease(70000, "20260715_001441_960b8f", T0 + 1000)];
  const stops = plan({ rows, leases, leaseStarts: new Map([[59544, T0 + 200], [70000, T0 + 1200]]) });
  assert.deepEqual(stops.map((s) => s.pid), [59544, 70000], JSON.stringify(stops));
  assert.match(stops[0].why, /port 9272/);
  assert.match(stops[1].why, /lease on session 20260715_001441_960b8f/);
});

test("nothing else is stopped: another agent's port, another session, a recycled pid, a non-hermes pid, the caller's own ancestry", () => {
  const rows = [
    { pid: 10, ppid: 4, commandLine: gatewayCmd(9000) },
    { pid: 11, ppid: 4, commandLine: "hermes --tui --resume other-session" },
    { pid: 12, ppid: 4, commandLine: "hermes --tui --resume 20260715_001441_960b8f" },
    { pid: 13, ppid: 4, commandLine: "node some-dev-server.js" },
    { pid: 2, ppid: 1, commandLine: gatewayCmd(9272) },
  ];
  const leases = [
    lease(11, "other-session", T0),
    lease(12, "20260715_001441_960b8f", T0 - 3_600_000),
    lease(13, "20260715_001441_960b8f", T0),
    lease(2, "20260715_001441_960b8f", T0),
  ];
  const leaseStarts = new Map([[11, T0], [12, T0], [13, T0], [2, T0]]);
  assert.deepEqual(plan({ rows, leases, leaseStarts }), []);
  // CONTROL: the same table with pid 12's recorded start corrected is stopped, so the refusals above are the rules.
  assert.deepEqual(plan({ rows, leases: [lease(12, "20260715_001441_960b8f", T0)], leaseStarts }).map((s) => s.pid), [12]);
  // And with no session marker, no lease is consulted at all.
  assert.deepEqual(plan({ rows, leases: [lease(12, "20260715_001441_960b8f", T0)], leaseStarts, sessionId: "" }), []);
});

test("ancestry walks parents without looping on a cycle", () => {
  assert.deepEqual([...ancestry(5, [{ pid: 5, ppid: 4 }, { pid: 4, ppid: 3 }, { pid: 3, ppid: 5 }])], [5, 4, 3]);
});

test("reapPriorHermes keeps the port marker's answer honest: held while something still listens, and on any failure", () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-"));
  fs.writeFileSync(path.join(tempDir, "aify-hermes-port-probe-a"), "9272");
  let table = [{ pid: 700, ppid: 4, commandLine: gatewayCmd(9272) }];
  const common = { agentId: "probe-a", tempDir, leases: () => [], sessionIdOf: () => "", starts: () => new Map(), waitMs: 0, self: 1 };
  const killed = [];
  const gone = reapPriorHermes({ ...common, listProcesses: () => table, kill: (pid) => { killed.push(pid); table = []; }, alive: () => false });
  assert.deepEqual([killed, gone.portStillHeld], [[700], false]);
  table = [{ pid: 701, ppid: 4, commandLine: gatewayCmd(9272) }];
  const stuck = reapPriorHermes({ ...common, listProcesses: () => table, kill: () => {}, alive: () => true });
  assert.deepEqual([stuck.stopped.map((s) => s.pid), stuck.portStillHeld], [[701], true]);
  assert.deepEqual(reapPriorHermes({ ...common, listProcesses: () => { throw new Error("no table"); } }), { stopped: [], portStillHeld: true });
});

test("stopDaemon clears the gateway markers only when the reap says the port is free, and only kill-prior reaps", async () => {
  const cleared = [];
  const base = { agentId: "probe-a", killByPort: async () => ({ killed: false }), readPid: () => null, clearPid: () => {}, clearGatewayMarkers: (id) => cleared.push(id) };
  const held = await stopDaemon({ ...base, reapPrior: true, reap: () => ({ stopped: [{ pid: 9 }], portStillHeld: true }) });
  assert.deepEqual([held, cleared], [{ stopped: true, pid: 9 }, []]);
  const free = await stopDaemon({ ...base, reapPrior: true, reap: () => ({ stopped: [], portStillHeld: false }) });
  assert.deepEqual([free, cleared], [{ stopped: false }, ["probe-a"]]);
  let reaped = 0;
  await stopDaemon({ ...base, reap: () => { reaped += 1; return { stopped: [], portStillHeld: false }; } });
  assert.equal(reaped, 0, "a sidecar's own stop reaped another generation");
  const cli = fs.readFileSync(CLI, "utf8");
  assert.match(cli, /stop\(\{ agentId, reapPrior: true \}\)/, "kill-prior's stop does not reap");
});

test("markers and leases are read from where hermes and this bridge write them", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-read-"));
  fs.writeFileSync(path.join(dir, "aify-hermes-port-probe-b"), " 9300\n");
  fs.writeFileSync(path.join(dir, "aify-hermes-port-probe-c"), "80");
  assert.equal(persistedGatewayPort("probe-b", { tempDir: dir }), 9300);
  assert.equal(persistedGatewayPort("probe-c", { tempDir: dir }), null, "an out-of-range marker is not a port");
  assert.equal(persistedGatewayPort("missing", { tempDir: dir }), null);
  fs.mkdirSync(path.join(dir, "runtime"));
  fs.writeFileSync(path.join(dir, "runtime", "active_sessions.json"), JSON.stringify({ entries: [lease(5, "s", T0)] }));
  assert.equal(readSessionLeases(dir)[0].pid, 5);
  assert.deepEqual(readSessionLeases(path.join(dir, "nope")), []);
  assert.equal(hermesHome({ env: { HERMES_HOME: "/h" } }), "/h");
  assert.equal(hermesHome({ env: { LOCALAPPDATA: "C:\\L" }, platform: "win32" }), path.join("C:\\L", "hermes"));
  assert.equal(hermesHome({ env: {}, platform: "linux", home: "/u" }), path.join("/u", ".hermes"));
});

test("REAL PROCESSES through the real `stop`: the leftover gateway and lease holder go, a recycled pid stays, the marker follows", async (t) => {
  const agentId = `reap-probe-${process.pid}`;
  const persisted = 9555;
  const hashed = agentPort(agentId);
  const onOurPorts = new RegExp(`--port[\\s=]+(${persisted}|${hashed})\\b`);
  const busy = defaultListProcesses().filter((row) => onOurPorts.test(row.commandLine || ""));
  if (busy.length) {
    t.skip(`a live process already names port ${persisted} or ${hashed}; this test will not share one`);
    return;
  }
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-real-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-home-"));
  const started = [];
  const sleeper = (...words) => {
    const proc = spawn(process.execPath, ["-e", "setInterval(()=>{},1e3)", ...words], { stdio: "ignore", detached: true, windowsHide: true });
    started.push(proc.pid);
    return proc.pid;
  };
  t.after(() => { for (const pid of started) { try { process.kill(pid); } catch {} } });
  const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };

  const gateway = sleeper("hermes", "dashboard", "--port", String(persisted));
  const holder = sleeper("hermes", "--tui", "--resume", "sess-real");
  const stranger = sleeper("hermes", "--tui", "--resume", "sess-real");
  await new Promise((r) => setTimeout(r, 1500));
  const starts = startTimes([holder, stranger]);
  assert.ok(starts.get(holder) && starts.get(stranger), "control: this host reads start times");
  fs.writeFileSync(path.join(tempDir, `aify-hermes-port-${agentId}`), String(persisted));
  assert.ok(writeSessionIdMarker(agentId, "sess-real", { tempDir }));
  fs.mkdirSync(path.join(home, "runtime"));
  fs.writeFileSync(path.join(home, "runtime", "active_sessions.json"), JSON.stringify({ entries: [
    lease(holder, "sess-real", starts.get(holder)),
    lease(stranger, "sess-real", starts.get(stranger) - 3_600_000),
  ] }));

  const res = spawnSync(process.execPath, [CLI, "stop", agentId], {
    encoding: "utf8", timeout: 120_000, env: sealedChildEnv({ TEMP: tempDir, TMP: tempDir, HERMES_HOME: home }),
  });
  assert.equal(res.status, 0, res.stderr);
  const deadline = Date.now() + 15_000;
  while ((alive(gateway) || alive(holder)) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));
  assert.equal(alive(gateway), false, `the gateway on the persisted port survived:\n${res.stderr}`);
  assert.equal(alive(holder), false, `the session's lease holder survived:\n${res.stderr}`);
  assert.equal(alive(stranger), true, "a pid whose recorded start differs from the OS's was killed");
  assert.equal(fs.existsSync(path.join(tempDir, `aify-hermes-port-${agentId}`)), false, "the port marker outlived a port nothing holds");
  assert.equal(fs.existsSync(path.join(tempDir, `aify-hermes-session-${agentId}`)), true, "the session marker is the resume binding and must survive");
});
