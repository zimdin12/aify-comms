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

// FIRST: this file writes hermes markers, which must not land in the real %TEMP% when it is run on its own.
import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { anchorOffsetMs, startTimes } from "aify-wrapper/lib/process-identity.mjs";

import { runHermesDaemonCli } from "../hermes-daemon-cli.js";
import { stopDaemon } from "../hermes-daemon.js";
import { agentPort, claimedByOtherAgents, writeSessionIdMarker } from "../hermes-endpoint.js";
import { ancestry, hermesHome, ownedGatewayPorts, persistedGatewayPort, planPriorReap, readSessionLeases, reapPriorHermes, sessionNamedByAnotherAgent } from "../hermes-prior-reap.mjs";
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

test("THE INCIDENT: the gateway on the PERSISTED port, and a lease holder inside an old gateway's tree, are stopped once each", () => {
  const rows = [
    { pid: 59544, ppid: 4, commandLine: gatewayCmd(9272) },
    { pid: 59545, ppid: 59544, commandLine: `python -m hermes dashboard --port 9272` },
    // A previous generation's gateway on a port no marker names any more, whose TUI child still holds the session.
    { pid: 61000, ppid: 4, commandLine: gatewayCmd(9400) },
    { pid: 61001, ppid: 61000, commandLine: "hermes --tui --resume 20260715_001441_960b8f" },
  ];
  const leases = [lease(59544, "20260715_001441_960b8f", T0), lease(61001, "20260715_001441_960b8f", T0 + 1000)];
  const stops = plan({ rows, leases, leaseStarts: new Map([[59544, T0 + 200], [61001, T0 + 1200]]) });
  assert.deepEqual(stops.map((s) => s.pid), [59544, 61001], JSON.stringify(stops));
  assert.match(stops[0].why, /port 9272/);
  assert.match(stops[1].why, /lease on session 20260715_001441_960b8f/);
});

test("the operator's own `hermes --resume` holding the agent's session is never stopped (external review, 2026-09-15)", () => {
  // Outside any gateway tree: a person resumed the agent's conversation in their own terminal. Every start of the
  // agent, a message cold-starting it included, killed it.
  const rows = [
    { pid: 70000, ppid: 4, commandLine: "hermes --resume 20260715_001441_960b8f" },
    { pid: 61000, ppid: 4, commandLine: gatewayCmd(9400) },
    { pid: 61001, ppid: 61000, commandLine: "hermes --tui --resume 20260715_001441_960b8f" },
  ];
  const leaseStarts = new Map([[70000, T0], [61001, T0]]);
  assert.deepEqual(plan({ rows, leases: [lease(70000, "20260715_001441_960b8f", T0)], leaseStarts }), []);
  // CONTROL: the same holder rule, the holder inside a gateway tree, is stopped.
  assert.deepEqual(plan({ rows, leases: [lease(61001, "20260715_001441_960b8f", T0)], leaseStarts }).map((s) => s.pid), [61001]);
});

test("hermes' own wall-clock record is matched against an anchored start time (external review, 2026-09-16)", () => {
  // hermes writes `process_start_time` from the wall clock; on Linux this repo reads start times through the
  // boot anchor, which drifts from that clock (121 s on this machine's WSL). Unconverted, a leftover the reap
  // exists to collect reads as a recycled pid and is left running.
  const offsetMs = 18_000;
  const rows = [
    { pid: 61000, ppid: 4, commandLine: gatewayCmd(9400) },
    { pid: 61001, ppid: 61000, commandLine: "hermes --tui --resume 20260715_001441_960b8f" },
  ];
  const leases = [lease(61001, "20260715_001441_960b8f", T0)];
  const leaseStarts = new Map([[61001, T0 + offsetMs]]);
  assert.deepEqual(plan({ rows, leases, leaseStarts, offsetMs }).map((s) => s.pid), [61001], "the leftover was left running");
  assert.deepEqual(plan({ rows, leases, leaseStarts }), [], "control: the same drift, unconverted, hides it");
  // CONTROL: a pid recycled well past the drift is still not ours.
  assert.deepEqual(plan({ rows, leases, leaseStarts: new Map([[61001, T0 + offsetMs + 60_000]]), offsetMs }), []);
});

test("nothing else is stopped: another agent's port, another session, a recycled pid, a non-hermes pid, the caller's own ancestry", () => {
  const rows = [
    { pid: 10, ppid: 4, commandLine: gatewayCmd(9000) },
    { pid: 11, ppid: 4, commandLine: "hermes --tui --resume other-session" },
    { pid: 12, ppid: 20, commandLine: "hermes --tui --resume 20260715_001441_960b8f" },
    { pid: 20, ppid: 4, commandLine: gatewayCmd(9500) },
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

test("reapPriorHermes ASKS for the clock offset and hands it to the plan", () => {
  // The rule above is only worth having if the real reap uses it: the offset is read from this host, not
  // assumed, so a leftover is collected on a Linux host whose anchored start times sit away from hermes' clock.
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-clock-"));
  fs.writeFileSync(path.join(tempDir, "aify-hermes-session-probe-c"), "20260715_001441_960b8f");
  const offsetMs = 18_000;
  const rows = [{ pid: 61000, ppid: 4, commandLine: gatewayCmd(9400) }, { pid: 61001, ppid: 61000, commandLine: "hermes --tui --resume 20260715_001441_960b8f" }];
  const killed = [];
  const common = {
    agentId: "probe-c", tempDir, waitMs: 0, self: 1, alive: () => false, listeners: () => [],
    listProcesses: () => rows,
    leases: () => [lease(61001, "20260715_001441_960b8f", T0)],
    sessionIdOf: () => "20260715_001441_960b8f",
    starts: () => new Map([[61001, T0 + offsetMs]]),
    kill: (pid) => killed.push(pid),
  };
  reapPriorHermes({ ...common, clockOffset: () => offsetMs });
  assert.deepEqual(killed, [61001], "the offset this host reports did not reach the plan");
  // CONTROL: with no drift reported, the same leftover reads as a recycled pid and is left alone.
  killed.length = 0;
  reapPriorHermes({ ...common, clockOffset: () => 0 });
  assert.deepEqual(killed, [], "control: the offset is not being ignored on both paths");
});

test("ancestry walks parents without looping on a cycle", () => {
  assert.deepEqual([...ancestry(5, [{ pid: 5, ppid: 4 }, { pid: 4, ppid: 3 }, { pid: 3, ppid: 5 }])], [5, 4, 3]);
});

test("reapPriorHermes keeps the port marker's answer honest: held while something still listens, and on any failure", () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-"));
  fs.writeFileSync(path.join(tempDir, "aify-hermes-port-probe-a"), "9272");
  let table = [{ pid: 700, ppid: 4, commandLine: gatewayCmd(9272) }];
  const common = { agentId: "probe-a", tempDir, leases: () => [], sessionIdOf: () => "", starts: () => new Map(), waitMs: 0, self: 1, listeners: () => [] };
  const killed = [];
  const gone = reapPriorHermes({ ...common, listProcesses: () => table, kill: (pid) => { killed.push(pid); table = []; }, alive: () => false });
  assert.deepEqual([killed, gone.portStillHeld], [[700], false]);
  table = [{ pid: 701, ppid: 4, commandLine: gatewayCmd(9272) }];
  const stuck = reapPriorHermes({ ...common, listProcesses: () => table, kill: () => {}, alive: () => true });
  assert.deepEqual([stuck.stopped.map((s) => s.pid), stuck.portStillHeld], [[701], true]);
  assert.deepEqual(reapPriorHermes({ ...common, listProcesses: () => { throw new Error("no table"); } }), { stopped: [], portStillHeld: true });

  // Through the real listing: a query that timed out is no table, not an empty host.
  const row = process.platform === "win32" ? "1\t2\tnode a.js\n" : "1 2 node a.js\n";
  const { listeners: _sealed, ...unsealed } = common;
  const listed = (res) => reapPriorHermes({ ...unsealed, kill: () => { throw new Error("must not kill"); }, spawnSync: () => res }).portStillHeld;
  assert.equal(listed({ status: 0, stdout: row }), false, "control: a good table with no gateway frees the port");
  assert.equal(listed({ status: null, error: Object.assign(new Error("timeout"), { code: "ETIMEDOUT" }), stdout: "" }), true, "a timed-out listing read as nothing listening");
});

test("THE INCIDENT 2026-09-15: an ELEVATED gateway, whose command line reads as empty, still holds the port", () => {
  // `hermes update` in an Administrator terminal relaunched mc-senior-dev's gateway on 9273. To this
  // process its command line was empty, so no plan could name it and no kill could reach it.
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-elevated-"));
  fs.writeFileSync(path.join(tempDir, "aify-hermes-port-probe-e"), "9273");
  const common = { agentId: "probe-e", tempDir, leases: () => [], sessionIdOf: () => "", starts: () => new Map(), waitMs: 0, self: 1, alive: () => false,
    kill: () => { throw new Error("nothing it can identify may be killed"); }, listProcesses: () => [{ pid: 65916, ppid: 66464, commandLine: "" }] };
  const said = [];
  const realError = console.error;
  console.error = (line) => said.push(String(line));
  try {
    const held = reapPriorHermes({ ...common, listeners: () => [{ port: 9273, pid: 65916 }, { port: 9009, pid: 104172 }] });
    assert.deepEqual(held, { stopped: [], portStillHeld: true }, "an unreadable listener on the agent's own port read as a free port");
    assert.ok(said.some((line) => /port 9273 is held by pid 65916/.test(line) && /Administrator/.test(line)), "the operator was not told what holds the port");
    // CONTROL: the socket gone, the same table frees the port.
    said.length = 0;
    assert.equal(reapPriorHermes({ ...common, listeners: () => [{ port: 9009, pid: 104172 }] }).portStillHeld, false);
    assert.deepEqual(said, [], "a listener on some other port was reported");
    assert.equal(reapPriorHermes({ ...common, listeners: () => { throw new Error("netstat failed"); } }).portStillHeld, true, "a socket listing that failed read as a free port");
  } finally {
    console.error = realError;
  }
});

test("ANOTHER AGENT'S gateway on a colliding port, and a session another agent also names, are never collected", () => {
  // The reap must stop only what is THIS agent's: hash ports collide, and one conversation can be named by
  // several agents' session markers. Each refusal below has a control that DOES reap, so the refusal is
  // the ownership rule and not a broken probe.
  const run = ({ markers, table, leaseList }) => {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-own-"));
    for (const [name, value] of Object.entries(markers)) fs.writeFileSync(path.join(tempDir, name), value);
    const killed = [];
    reapPriorHermes({
      agentId: "probe-x", tempDir, listProcesses: () => table, leases: () => leaseList,
      starts: () => new Map(leaseList.map((l) => [l.pid, l.process_start_time * 1000])),
      // Both times above are written on ONE clock, so the conversion between hermes' clock and this host's
      // anchored one has nothing to do here. Left real, this read the host's own `anchorOffsetMs()`: on a
      // Linux host whose clock had moved (574 s here) the control below collected nothing and the test
      // failed for a reason no change of this repo's could cause.
      clockOffset: () => 0,
      kill: (pid) => killed.push(pid), alive: () => false, waitMs: 0, self: 1, listeners: () => [],
    });
    return killed;
  };
  const hashed = agentPort("probe-x");
  const onHash = [{ pid: 500, ppid: 4, commandLine: gatewayCmd(hashed) }];
  assert.deepEqual(run({ markers: { "aify-hermes-port-other-agent": String(hashed) }, table: onHash, leaseList: [] }), [],
    "another agent's gateway on this agent's hash port was stopped");
  assert.deepEqual(run({ markers: {}, table: onHash, leaseList: [] }), [500], "control: unclaimed, the hash port is this agent's");
  assert.deepEqual(run({ markers: { "aify-hermes-port-probe-x": "9300" }, table: onHash, leaseList: [] }), [],
    "an agent with a persisted port does not own its hash port too");

  const holder = [{ pid: 590, ppid: 4, commandLine: gatewayCmd(9600) }, { pid: 600, ppid: 590, commandLine: "hermes --tui --resume shared-sess" }];
  const leaseList = [lease(600, "shared-sess", T0)];
  const sessions = { "aify-hermes-session-probe-x": "shared-sess" };
  assert.deepEqual(run({ markers: { ...sessions, "aify-hermes-session-other-agent": "shared-sess" }, table: holder, leaseList }), [],
    "a session another agent also names was collected, ending that agent's TUI");
  assert.deepEqual(run({ markers: { ...sessions, "aify-hermes-session-other-agent": "different" }, table: holder, leaseList }), [600],
    "control: named by this agent alone, the lease holder is collected");
  assert.equal(sessionNamedByAnotherAgent("probe-x", "s", { tempDir: path.join(os.tmpdir(), "no-such-dir-aify") }), true,
    "an unreadable marker directory must not read as nobody else");
  assert.deepEqual(ownedGatewayPorts("probe-x", { tempDir: fs.mkdtempSync(path.join(os.tmpdir(), "aify-own-")) }), [hashed]);
  const markers = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claims-"));
  fs.writeFileSync(path.join(markers, "aify-hermes-port-probe-x"), "9400");
  fs.writeFileSync(path.join(markers, "aify-hermes-port-other-agent"), "9401");
  fs.writeFileSync(path.join(markers, "aify-hermes-port-broken"), "80");
  assert.deepEqual([...claimedByOtherAgents(markers, "probe-x")], [9401], "the agent's own marker and an out-of-range one are not claims");
});

test("a port another agent's marker claims is never killed by port, and a shared marker port is never cleared while held", async () => {
  // stopDaemon's port kill: the derived port is the HASH port, which a neighbour's gateway can hold.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-stop-claimed-"));
  const hashed = agentPort("probe-z");
  const calls = [];
  const base = { agentId: "probe-z", tempDir: dir, killByPort: async (p) => { calls.push(p); return { killed: false }; },
    readPid: () => null, clearPid: () => {}, clearGatewayMarkers: () => {}, reapPrior: true, reap: () => ({ stopped: [], portStillHeld: false }) };
  await stopDaemon(base);
  assert.deepEqual(calls, [hashed], "control: unclaimed, this agent's own daemon port is killed by port");
  fs.writeFileSync(path.join(dir, "aify-hermes-port-neighbour"), String(hashed));
  await stopDaemon(base);
  assert.deepEqual(calls, [hashed], "a neighbour's gateway on this agent's hash port was killed by port");

  // Two markers naming one port (a raced first launch): the reap stops nothing there, and must not clear
  // this agent's marker while a gateway still listens on it.
  const shared = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reap-shared-"));
  fs.writeFileSync(path.join(shared, "aify-hermes-port-probe-y"), "9272");
  fs.writeFileSync(path.join(shared, "aify-hermes-port-other"), "9272");
  const common = { agentId: "probe-y", tempDir: shared, leases: () => [], sessionIdOf: () => "", starts: () => new Map(), waitMs: 0, self: 1, kill: () => { throw new Error("must not kill"); }, alive: () => false, listeners: () => [] };
  const held = reapPriorHermes({ ...common, listProcesses: () => [{ pid: 800, ppid: 4, commandLine: gatewayCmd(9272) }] });
  assert.deepEqual([held.stopped, held.portStillHeld], [[], true]);
  assert.equal(reapPriorHermes({ ...common, listProcesses: () => [] }).portStillHeld, false, "control: nothing listening, the marker may go");
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
});

test("kill-prior's `stop` reaps only for a launcher holding the agent lease", async () => {
  // The lease claim is what established that no live instance is running, or that this start replaced it.
  // Without one, a leftover-looking gateway may be a live instance's, and an automatic start must not end it.
  const quiet = { stdout: () => {}, stderr: () => {} };
  const asked = async (env) => {
    const calls = [];
    const code = await runHermesDaemonCli({ ...quiet, argv: ["node", CLI, "stop", "probe-l"], env, stop: async (a) => { calls.push(a); return { stopped: false }; } });
    assert.equal(code, 0);
    return calls;
  };
  assert.deepEqual(await asked({ AIFY_AGENT_LEASE: "4812" }), [{ agentId: "probe-l", reapPrior: true }], "a launcher holding the lease does not reap");
  for (const env of [{}, { AIFY_AGENT_LEASE: "" }, { AIFY_AGENT_LEASE: "0" }, { AIFY_AGENT_LEASE: "not-a-pid" }]) {
    assert.deepEqual(await asked(env), [{ agentId: "probe-l", reapPrior: false }], `reaped without a lease: ${JSON.stringify(env)}`);
  }
});

test("a reap owns no port when the other agents' claims cannot be read", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-owned-"));
  assert.deepEqual(ownedGatewayPorts("probe-o", { tempDir: dir }), [agentPort("probe-o")], "control: a readable, empty marker dir leaves the hash port owned");
  const notADir = path.join(dir, "file");
  fs.writeFileSync(notADir, "");
  assert.deepEqual(ownedGatewayPorts("probe-o", { tempDir: notADir }), [], "an unlistable marker dir read as nobody else's claim");
  assert.equal(claimedByOtherAgents(notADir, "probe-o", { strict: true }), null);
  assert.deepEqual([...claimedByOtherAgents(notADir, "probe-o")], [], "control: the lenient read still answers no claims for its other callers");
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
  // The holder runs inside an older gateway's tree, on a port no marker names: a REAL child of that gateway.
  const childPidFile = path.join(tempDir, "holder.pid");
  const oldGateway = spawn(process.execPath, ["-e",
    `const c=require("child_process").spawn(process.execPath,["-e","setInterval(()=>{},1e3)","hermes","--tui","--resume","sess-real"],{stdio:"ignore",windowsHide:true});require("fs").writeFileSync(${JSON.stringify(childPidFile)},String(c.pid));setInterval(()=>{},1e3)`,
    "hermes", "dashboard", "--port", String(persisted + 1)], { stdio: "ignore", detached: true, windowsHide: true });
  started.push(oldGateway.pid);
  const operator = sleeper("hermes", "--resume", "sess-real");
  const stranger = sleeper("hermes", "--tui", "--resume", "sess-real");
  await new Promise((r) => setTimeout(r, 1500));
  const holder = Number(fs.readFileSync(childPidFile, "utf8"));
  started.push(holder);
  const starts = startTimes([holder, stranger, operator]);
  assert.ok(starts.get(holder) && starts.get(stranger) && starts.get(operator), "control: this host reads start times");
  // ON HERMES' CLOCK, because this file below is hermes' own record and hermes writes the wall clock. On Linux
  // `startTimes` is on the boot anchor's clock, which drifts from it (574 s on the review host, 2026-09-16),
  // and the reap converts between the two -- so a fixture left anchored is a start time hermes would never
  // have written, and this proof would fail on a host whose clock had moved. 0 on Windows, where there is no anchor.
  const onHermesClock = (at) => at - anchorOffsetMs();
  fs.writeFileSync(path.join(tempDir, `aify-hermes-port-${agentId}`), String(persisted));
  assert.ok(writeSessionIdMarker(agentId, "sess-real", { tempDir }));
  fs.mkdirSync(path.join(home, "runtime"));
  fs.writeFileSync(path.join(home, "runtime", "active_sessions.json"), JSON.stringify({ entries: [
    lease(holder, "sess-real", onHermesClock(starts.get(holder))),
    lease(stranger, "sess-real", onHermesClock(starts.get(stranger)) - 3_600_000),
    lease(operator, "sess-real", onHermesClock(starts.get(operator))),
  ] }));

  const res = spawnSync(process.execPath, [CLI, "stop", agentId], {
    encoding: "utf8", timeout: 120_000, env: sealedChildEnv({ TEMP: tempDir, TMP: tempDir, HERMES_HOME: home, AIFY_AGENT_LEASE: String(process.pid) }),
  });
  assert.equal(res.status, 0, res.stderr);
  const deadline = Date.now() + 15_000;
  while ((alive(gateway) || alive(holder)) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));
  assert.equal(alive(gateway), false, `the gateway on the persisted port survived:\n${res.stderr}`);
  assert.equal(alive(holder), false, `the session's lease holder survived:\n${res.stderr}`);
  assert.equal(alive(stranger), true, "a pid whose recorded start differs from the OS's was killed");
  assert.equal(alive(operator), true, "a `hermes --resume` outside every gateway tree -- the operator's own -- was killed");
  assert.equal(fs.existsSync(path.join(tempDir, `aify-hermes-port-${agentId}`)), false, "the port marker outlived a port nothing holds");
  assert.equal(fs.existsSync(path.join(tempDir, `aify-hermes-session-${agentId}`)), true, "the session marker is the resume binding and must survive");
});
