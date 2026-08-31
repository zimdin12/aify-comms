// `gateway-orphans` — hermes gateway hosts left running with no worker behind them.
//
// THE INCIDENT THIS IS BUILT FROM, 2026-08-31. The operator killed aify-env with managed agents live.
// The delivery loops died with it; the per-agent GATEWAY HOSTS did not, because the survivor sweep
// runs at bridge boot and on graceful shutdown and an abrupt kill is neither. An hour later
// `hermes update` refused to run, listing 45 live processes holding native `.pyd` files locked.
//
// `managed-orphans` would have said nothing: the loops were the half that died correctly.
//
// THE COMMAND LINES BELOW ARE REAL, copied from that capture.

import assert from "node:assert/strict";
import test from "node:test";

import { cmdlineDeliveryLoopAgent, cmdlineHermesGatewayPort } from "../proc-probes.js";
import {
  checkGatewayOrphans,
  gatewayOrphanVerdict,
  gatewayOwners,
  gatewaysInRange,
} from "../gateway-orphan-check.mjs";

const BASE = 8642;
const SPAN = 1000;

// Real, from the operator's process table that night.
const GATEWAY_CMD = "C:\\Users\\Administrator\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe dashboard --host 127.0.0.1 --port 8823";
const LOOP_CMD = "C:\\nvm4w\\nodejs\\node.exe C:\\Users\\Administrator\\.aify-comms\\mcp\\stdio/hermes-managed-host.js run comms-senior-dev";

// ── cmdlineHermesGatewayPort ────────────────────────────────────────────────────────────────────

test("it reads the port off a REAL gateway command line", () => {
  assert.equal(cmdlineHermesGatewayPort(GATEWAY_CMD), 8823);
});

test("a delivery LOOP is not a gateway, though both name hermes", () => {
  // The two are siblings in the same triad and the whole check turns on telling them apart.
  assert.equal(cmdlineHermesGatewayPort(LOOP_CMD), null);
});

test("`--port=N` is read as well as `--port N`", () => {
  assert.equal(cmdlineHermesGatewayPort("hermes dashboard --port=9001"), 9001);
});

test("a hermes process with no dashboard subcommand is not a gateway", () => {
  assert.equal(cmdlineHermesGatewayPort("hermes --tui --resume 20260605_181038_6cd2ef --port 9001"), null);
});

test("nonsense and out-of-range ports are refused rather than guessed at", () => {
  for (const bad of ["", null, undefined, "hermes dashboard", "hermes dashboard --port 99999"]) {
    assert.equal(cmdlineHermesGatewayPort(bad), null);
  }
});

// ── gatewaysInRange ─────────────────────────────────────────────────────────────────────────────

const rows = [
  { pid: 56540, commandLine: GATEWAY_CMD },                              // 8823 — ours
  { pid: 11111, commandLine: "hermes dashboard --port 3000" },           // outside the range
  { pid: 22222, commandLine: LOOP_CMD },                                 // a loop, not a gateway
  { pid: 33333, commandLine: "hermes dashboard --port 9342" },           // 9342 — ours, unclaimed
];

test("only ports in aify-comms' own range are ours to talk about", () => {
  // 8642-9641. A hermes dashboard somebody started by hand on 3000 is not this tool's business, and
  // reporting it would be this check telling the operator to kill their own terminal.
  const found = gatewaysInRange(rows, { toPort: cmdlineHermesGatewayPort, base: BASE, span: SPAN });
  assert.deepEqual(found.map((g) => g.port).sort(), [8823, 9342]);
});

// ── gatewayOwners ───────────────────────────────────────────────────────────────────────────────

test("the port markers are what bind a gateway to an agent", () => {
  // A gateway's command line carries a port and no agent id; `aify-hermes-port-<agent>` is the only
  // link, which is why establishing ownership that night took a marker read rather than a glance.
  const owners = gatewayOwners({ "graph-senior-dev": "8826", "comms-senior-dev": 9344 });
  assert.equal(owners.get(8826), "graph-senior-dev");
  assert.equal(owners.get(9344), "comms-senior-dev");
});

test("an unparseable marker is skipped, not mapped to NaN", () => {
  const owners = gatewayOwners({ a: "", b: "not-a-port", c: null });
  assert.equal(owners.size, 0);
});

// ── gatewayOrphanVerdict ────────────────────────────────────────────────────────────────────────

const managed = { "graph-senior-dev": { sessionMode: "managed" }, "sc-tester": { sessionMode: "managed" } };

test("a gateway whose agent has a live delivery loop is NOT an orphan", () => {
  const verdict = gatewayOrphanVerdict({
    gateways: [{ pid: 1, port: 8826 }],
    owners: gatewayOwners({ "graph-senior-dev": 8826 }),
    loopAgentIds: ["graph-senior-dev"],
    agents: managed,
  });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "ok");
});

test("THE INCIDENT: loops gone, gateway still running -> orphaned", () => {
  const verdict = gatewayOrphanVerdict({
    gateways: [{ pid: 56540, port: 8826 }],
    owners: gatewayOwners({ "graph-senior-dev": 8826 }),
    loopAgentIds: [],
    agents: managed,
  });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "orphaned");
  assert.match(verdict.detail, /graph-senior-dev pid 56540 port 8826/);
});

test("a RESIDENT session's gateway is never an orphan, however few loops are running", () => {
  // The false positive that would make this check useless: a resident hermes runs its own gateway and
  // has no managed delivery loop BY DESIGN. Reporting it would flag the operator's own terminal on
  // every run, and an alarm that fires on healthy state trains people to skim past it.
  const verdict = gatewayOrphanVerdict({
    gateways: [{ pid: 1, port: 8826 }],
    owners: gatewayOwners({ "operator-session": 8826 }),
    loopAgentIds: [],
    agents: { "operator-session": { sessionMode: "resident" } },
  });
  assert.equal(verdict.ok, true);
});

test("a gateway NO marker claims is reported, not dropped", () => {
  // Port 9342 that night belonged to no agent at all. A process nobody can account for is more
  // suspicious than an identified orphan, so silence would be the wrong answer.
  const verdict = gatewayOrphanVerdict({
    gateways: [{ pid: 33333, port: 9342 }],
    owners: gatewayOwners({}),
    loopAgentIds: [],
    agents: managed,
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /\(unclaimed\) pid 33333 port 9342/);
});

test("no gateways at all is an honest pass", () => {
  const verdict = gatewayOrphanVerdict({ gateways: [], owners: new Map(), loopAgentIds: [], agents: {} });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "none");
});

test("the fix explains WHY nothing collects them, and refuses to reap", () => {
  const verdict = gatewayOrphanVerdict({
    gateways: [{ pid: 1, port: 8826 }],
    owners: gatewayOwners({ "graph-senior-dev": 8826 }),
    loopAgentIds: [],
    agents: managed,
  });
  assert.match(verdict.fix, /BOOT/, "it does not say why nothing collects them");
  assert.match(verdict.fix, /\.pyd/, "it does not name the consequence the operator actually hit");
  assert.match(verdict.fix, /Reported rather than reaped/, "it does not say the decision is the operator's");
});

test("each missing input makes the answer UNKNOWN, never clean", () => {
  // No evidence is not a pass. `env-bridge` and `bridge-current` both shipped green-by-default and
  // both were wrong the same way.
  const full = { gateways: [], owners: new Map(), loopAgentIds: [], agents: {} };
  for (const key of ["gateways", "owners", "loopAgentIds", "agents"]) {
    const verdict = gatewayOrphanVerdict({ ...full, [key]: null });
    assert.equal(verdict.ok, false, `${key} missing was reported as clean`);
    assert.equal(verdict.code, "unknown-all");
  }
});

// ── the CHECK ───────────────────────────────────────────────────────────────────────────────────

function harness({ procRows, markers = {}, agents = managed } = {}) {
  const calls = { added: [] };
  return {
    calls,
    deps: {
      get: async () => (agents ? { agents } : null),
      add: (...args) => { calls.added.push(args); return args; },
      listProcesses: () => procRows,
      toPort: cmdlineHermesGatewayPort,
      loopAgent: cmdlineDeliveryLoopAgent,
      readPortMarkers: () => markers,
      base: BASE,
      span: SPAN,
    },
  };
}

/** The runner's own process must appear, or the table did not read the host. */
const selfRow = { pid: process.pid, commandLine: "node the-test-runner" };

test("the check reports the incident end to end", () => {
  const { deps, calls } = harness({
    procRows: [selfRow, { pid: 56540, commandLine: GATEWAY_CMD }],
    markers: { "graph-senior-dev": 8823 },
  });
  return checkGatewayOrphans(deps).then(() => {
    const [id, ok, code, detail] = calls.added[0];
    assert.equal(id, "gateway-orphans");
    assert.equal(ok, false);
    assert.equal(code, "orphaned");
    assert.match(detail, /graph-senior-dev/);
  });
});

test("a live loop beside the gateway clears it, through the CHECK", () => {
  // Anti-vacuity for the test above: proves the loop enumeration is actually consulted rather than
  // the check being hard-wired to complain.
  const { deps, calls } = harness({
    procRows: [selfRow, { pid: 56540, commandLine: GATEWAY_CMD }, { pid: 2, commandLine: LOOP_CMD.replace("comms-senior-dev", "graph-senior-dev") }],
    markers: { "graph-senior-dev": 8823 },
  });
  return checkGatewayOrphans(deps).then(() => {
    assert.equal(calls.added[0][1], true);
  });
});

test("a process table that does not contain THIS process is treated as unread", () => {
  // The conflation that hid a broken enumerator for a whole release: [] means both "no processes"
  // and "the read failed", and only one of those is an answer.
  const { deps, calls } = harness({ procRows: [], markers: {} });
  return checkGatewayOrphans(deps).then(() => {
    assert.equal(calls.added[0][1], false);
    assert.equal(calls.added[0][2], "unknown-all");
  });
});

test("a throwing enumerator does not take the doctor down with it", () => {
  const { deps, calls } = harness({ procRows: [selfRow] });
  deps.listProcesses = () => { throw new Error("cannot enumerate"); };
  return checkGatewayOrphans(deps).then(() => {
    assert.equal(calls.added[0][2], "unknown-all");
  });
});

test("a service that does not answer is unknown too", () => {
  const { deps, calls } = harness({ procRows: [selfRow], agents: null });
  return checkGatewayOrphans(deps).then(() => {
    assert.equal(calls.added[0][2], "unknown-all");
  });
});
