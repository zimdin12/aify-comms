// The gate's stand-in agent (plan s4-hard-switch page 2): a defined managed agent with a scripted launcher. aify-env
// starts it as `claude-aify` (gate-stand-in.sh), exactly as it starts a real worker, with the agent id, its own lifetime
// and its own instance in the environment. It then does what a Claude worker's bridge does, and nothing more:
//   - registers, and beats a channel-sidecar heartbeat, so comms can deliver to it;
//   - claims runs through `/dispatch/claim` and steer or interrupt controls through `/dispatch/controls/claim`;
//   - reports `turn-start` and `turn-end` to aify-env's real `POST /agents/:id/turn-event` for its current lifetime.
// It never posts comms' legacy turn routes: the gate sets a legacy answer itself where a case needs one.
//
// THE HARNESS DRIVES IT through one file, /tmp/standin/control.json: `turn` ("busy" or "idle") and `runStatus` (the
// status a claimed run is written with: "completed", "running" or "delivered"). It answers through one append-only
// log, /tmp/standin/<agent>.log, one JSON object per line, and writes its lifetime to /tmp/standin/<agent>.lifetime.json.
// Bounded protocol evidence only: real Claude and Hermes queueing is not exercised here (page 2).

import fs from "node:fs";
import path from "node:path";

const DIR = "/tmp/standin";
const AGENT = String(process.env.AIFY_AGENT_ID || "").trim();
const LIFETIME = String(process.env.AIFY_LIFETIME || "");
const INSTANCE = String(process.env.AIFY_ENV_INSTANCE || "");
const ENV_URL = String(process.env.AIFY_ENV_URL || "");
const COMMS = String(process.env.SANDBOX_COMMS_URL || "http://comms:8800/api/v1");
const KEY = String(process.env.SANDBOX_COMMS_KEY || "");
const BRIDGE = `stand-in-${LIFETIME}`;
const CONTROL = path.join(DIR, "control.json");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

fs.mkdirSync(DIR, { recursive: true });
const LOG = path.join(DIR, `${AGENT || "unnamed"}.log`);
/** One line per observation, with the wall time and the lifetime it happened in. Never the key. */
function log(event, fields = {}) {
  fs.appendFileSync(LOG, `${JSON.stringify({ t: Date.now(), lifetime: LIFETIME, event, ...fields })}\n`);
}

async function comms(method, route, body) {
  const response = await fetch(`${COMMS}${route}`, {
    method, headers: { "x-api-key": KEY, "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30_000),
  });
  const text = await response.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* recorded as text */ }
  return { status: response.status, json, text: json ? "" : text.slice(0, 300) };
}

/** What the harness wants now; a missing or unreadable file means idle and completed. */
function control() {
  try {
    const value = JSON.parse(fs.readFileSync(CONTROL, "utf8"));
    return { turn: value.turn === "busy" ? "busy" : "idle", runStatus: value.runStatus || "completed" };
  } catch { return { turn: "idle", runStatus: "completed" }; }
}

/** True only when aify-env applied the event; anything else is retried, as a paused aify-env answers nothing. */
async function turnEvent(kind) {
  try {
    const response = await fetch(new URL(`/agents/${encodeURIComponent(AGENT)}/turn-event`, ENV_URL), {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ instance: INSTANCE, lifetime: LIFETIME, kind, firedAtUs: Date.now() * 1000 }),
      signal: AbortSignal.timeout(5_000),
    });
    const answer = await response.json().catch(() => null);
    log("turn-event", { kind, status: response.status, answer });
    return response.status === 200 && answer?.applied === true;
  } catch (error) {
    log("turn-event", { kind, error: String(error?.message || error) });
    return false;
  }
}

async function turnLoop() {
  let reported = "idle";
  for (;;) {
    const wanted = control().turn;
    if (wanted !== reported && await turnEvent(wanted === "busy" ? "turn-start" : "turn-end")) reported = wanted;
    await sleep(wanted !== reported ? 1000 : 200);
  }
}

async function heartbeatLoop() {
  for (;;) {
    const answer = await comms("POST", `/agents/${encodeURIComponent(AGENT)}/heartbeat`, {
      bridgeId: BRIDGE, bridgeKind: "channel-sidecar", liveness: true, bridgeBuild: "gate-stand-in",
    }).catch((error) => ({ status: 0, text: String(error?.message || error) }));
    if (answer.status !== 200) log("heartbeat", { status: answer.status, text: answer.text });
    await sleep(10_000);
  }
}

async function claimLoop(machineId) {
  for (;;) {
    const answer = await comms("POST", "/dispatch/claim", {
      agentId: AGENT, machineId, bridgeId: BRIDGE, bridgeKind: "channel-sidecar",
      executionModes: ["channel", "resident"], waitMs: 2000,
    }).catch((error) => ({ status: 0, json: null, text: String(error?.message || error) }));
    const run = answer.json?.run;
    if (answer.status !== 200 || answer.json?.stopped || answer.json?.release) {
      log("claim", { status: answer.status, stopped: !!answer.json?.stopped, release: !!answer.json?.release,
        text: answer.text });
      await sleep(1000);
      continue;
    }
    // Logged, so a withheld run is judged against a claimer that kept asking, not one that had stopped.
    if (!run) { log("polled"); continue; }
    const runStatus = control().runStatus;
    log("delivered", { runId: run.id, messageId: run.messageId || run.sourceMessageId || "", subject: run.subject || "",
      executionMode: run.executionMode || "", writtenAs: runStatus });
    const receipt = await comms("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
      status: runStatus, summary: "", runtime: "claude-code", appendEvent: "Delivered to the gate stand-in",
      eventType: runStatus === "running" ? "started" : "delivered",
    }).catch((error) => ({ status: 0, text: String(error?.message || error) }));
    if (receipt.status !== 200) log("receipt", { runId: run.id, status: receipt.status, text: receipt.text });
  }
}

async function controlsLoop(machineId) {
  for (;;) {
    const answer = await comms("POST", "/dispatch/controls/claim", { agentId: AGENT, machineId })
      .catch(() => ({ status: 0, json: null }));
    for (const item of answer.json?.controls || []) {
      log("control", { controlId: item.id, action: item.action || "", runId: item.runId || "" });
      await comms("PATCH", `/dispatch/controls/${encodeURIComponent(item.id)}`, {
        status: "completed", response: "Control received by the gate stand-in",
      }).catch(() => null);
    }
    await sleep(1000);
  }
}

async function main() {
  if (!AGENT || !LIFETIME || !INSTANCE || !ENV_URL || !KEY) {
    log("refused", { reason: "missing agent, lifetime, instance, env url or key" });
    process.exit(64);
  }
  fs.writeFileSync(path.join(DIR, `${AGENT}.lifetime.json`),
    JSON.stringify({ lifetime: LIFETIME, instance: INSTANCE, pid: process.pid, startedAt: Date.now() }));
  const info = await comms("GET", `/agents/${encodeURIComponent(AGENT)}`);
  const machineId = String(info.json?.agent?.machineId || "");
  const registered = await comms("POST", "/agents", {
    agentId: AGENT, role: process.env.AIFY_AGENT_ROLE || "coder", name: AGENT, cwd: process.cwd(),
    runtime: "claude-code", machineId, bridgeId: BRIDGE, launchMode: "detached", sessionMode: "managed",
    capabilities: ["managed-run", "resume", "interrupt", "steer", "spawn"], runtimeConfig: { channelEnabled: true },
    terminalId: String(process.env.AIFY_TERMINAL_ID || ""), managedWrapperChild: true, restoreDeleted: true,
    autoRegister: true, bridgeStartedAt: new Date().toISOString(), bridgeBuild: "gate-stand-in",
  });
  log("registered", { status: registered.status, machineId, text: registered.text });
  await Promise.all([heartbeatLoop(), turnLoop(), claimLoop(machineId), controlsLoop(machineId)]);
}

main().catch((error) => { log("crashed", { error: String(error?.stack || error) }); process.exit(70); });
