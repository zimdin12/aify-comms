// The hook-side poster for a resident's turn and approval moments, run as the real process.
//
// Every runtime hook used to curl `/turn-start` and `/turn-end` with no `X-API-Key`. Once the service
// enforced a key those requests were answered 401 and discarded by `|| true`, so no resident hook event
// had landed at all -- silently, because a hook must never print. This drives the script the way a hook
// does: its own process, the env a hook actually carries (AIFY_AGENT_ID + AIFY_COMMS_URL), a JSON
// payload on stdin, and a stub service that records what arrived.
//
// The stub listens on 127.0.0.2, not 127.0.0.1: a loopback 127.0.0.1 primary makes the endpoint module
// add the operator's real 127.0.0.1:8800 as a fallback.

import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { sealedChildEnv } from "./_child-env.mjs";
import { ENDPOINT_ENV_NAMES } from "../aify-service-endpoint.mjs";
import { hookFiredAtUs, postAgentState } from "../agent-state-event.mjs";
import { defaultMachineId } from "../machine-id.mjs";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "..", "agent-state-event.mjs");

function stub({ status = 200, hang = false } = {}) {
  const requests = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      requests.push({ method: req.method, url: req.url, key: req.headers["x-api-key"], body });
      if (hang) return;
      res.statusCode = status;
      res.end("{}");
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.2", () => {
      resolve({ url: `http://127.0.0.2:${server.address().port}`, requests, close: () => server.closeAllConnections?.() || server.close() });
    });
  });
}

function run(args, env, { closeStdin = true } = {}) {
  return new Promise((resolve) => {
    const started = Date.now();
    const childEnv = sealedChildEnv({ AIFY_ENV_URL: undefined, AIFY_ENV_INSTANCE: undefined, AIFY_LIFETIME: undefined, ...env });
    const child = spawn(process.execPath, [SCRIPT, ...args], { env: childEnv, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (c) => { out += c; });
    child.stderr.on("data", (c) => { err += c; });
    child.stdin.on("error", () => {});
    child.stdin.write(JSON.stringify({ hook_event_name: "PermissionRequest", session_id: "s" }));
    if (closeStdin) child.stdin.end();
    child.on("exit", (code) => resolve({ code, out, err, ms: Date.now() - started }));
  });
}

const hookEnv = (url, extra = {}) => ({ AIFY_AGENT_ID: "hook-agent", AIFY_COMMS_URL: url, AIFY_API_KEY: "test-key", ...extra });

test("the module still reads AIFY_SERVER_URL, the name this script bridges AIFY_COMMS_URL into", () => {
  assert.ok(ENDPOINT_ENV_NAMES.includes("AIFY_SERVER_URL"), ENDPOINT_ENV_NAMES.join(","));
});

for (const [event, path, body] of [
  ["turn-start", "/api/v1/agents/hook-agent/turn-start", {}],
  ["turn-end", "/api/v1/agents/hook-agent/turn-end", {}],
  ["blocked", "/api/v1/agents/hook-agent/status-event", { kind: "blocked" }],
  ["unblocked", "/api/v1/agents/hook-agent/status-event", { kind: "unblocked" }],
]) {
  test(`${event} posts ${path} with the key, from a hook's environment`, async () => {
    const s = await stub();
    try {
      const r = await run([event], hookEnv(s.url, { AIFY_HOOK_FIRED_AT: "1790451762.694110" }));
      assert.equal(r.code, 0);
      assert.equal(s.requests.length, 1, JSON.stringify(s.requests));
      const [req] = s.requests;
      assert.equal(req.method, "POST");
      assert.equal(req.url, path);
      assert.equal(req.key, "test-key", "the request must authenticate");
      // No bridgeId, so the service takes a turn signal as the authoritative harness one. The stamp
      // orders the background hooks' events: the shell time the hook command passed, and the host.
      assert.deepEqual(JSON.parse(req.body), { ...body, firedAtUs: 1790451762694110, machineId: defaultMachineId() });
      assert.equal(r.out, "", "a hook's stdout is parsed by codex; it must stay empty");
      assert.equal(r.err, "");
    } finally {
      s.close();
    }
  });
}

test("the fired-at time is the shell's to the microsecond, in either decimal separator, else the process start", () => {
  assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: "1790451762.694110" }, 7), 1790451762694110);
  assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: "1790451762,694111" }, 7), 1790451762694111);
  assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: "1790451762.5" }, 7), 1790451762500000);
  for (const unusable of [undefined, "", "   ", "soon", "0", "-5", "1.2.3", "99999999999999999999"]) {
    assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: unusable }, 7), 7, String(unusable));
  }
});

test("an endpoint the module reads itself wins over AIFY_COMMS_URL", async () => {
  const own = await stub();
  const hook = await stub();
  try {
    await run(["turn-end"], hookEnv(hook.url, { AIFY_SERVER_URL: own.url }));
    assert.equal(own.requests.length, 1);
    assert.equal(hook.requests.length, 0);
  } finally {
    own.close();
    hook.close();
  }
});

test("postAgentState refuses an unknown event or a missing identity before it reaches for a service", async () => {
  const saved = process.env.AIFY_AGENT_ID;
  try {
    process.env.AIFY_AGENT_ID = "";
    assert.equal(await postAgentState("turn-start"), false);
    process.env.AIFY_AGENT_ID = "someone";
    assert.equal(await postAgentState("no-such-event"), false);
  } finally {
    if (saved === undefined) delete process.env.AIFY_AGENT_ID;
    else process.env.AIFY_AGENT_ID = saved;
  }
});

test("refused, unknown, or identity-less: silent and exit 0", async () => {
  const s = await stub({ status: 401 });
  try {
    for (const [args, env] of [
      [["turn-start"], hookEnv(s.url)],
      [["no-such-event"], hookEnv(s.url)],
      [[], hookEnv(s.url)],
      [["turn-start"], hookEnv(s.url, { AIFY_AGENT_ID: undefined })],
    ]) {
      const r = await run(args, env);
      assert.deepEqual([r.code, r.out, r.err], [0, "", ""], `${args} ${JSON.stringify(env)}`);
    }
    assert.equal(s.requests.length, 1, "only the valid event with an identity may reach the service");
  } finally {
    s.close();
  }
});

test("a service that never answers and a stdin nobody closes cannot hold the hook", async () => {
  const s = await stub({ hang: true });
  try {
    const r = await run(["blocked"], hookEnv(s.url), { closeStdin: false });
    assert.equal(r.code, 0);
    // The script gives up at 2s and exits by 2.5s; the margin is node start-up on a loaded machine.
    // Without the bound this never exits, because neither the service nor stdin ever finishes.
    assert.ok(r.ms < 4500, `took ${r.ms}ms`);
  } finally {
    s.close();
  }
});

test("a nanosecond time from GNU date (the hook's fallback under dash) keeps its microseconds", () => {
  assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: "1790451762.694110987" }, 7), 1790451762694110);
  assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: "1790451762.1234567890" }, 7), 7, "ten decimals is not a time");
  assert.equal(hookFiredAtUs({ AIFY_HOOK_FIRED_AT: "1790451762.%N" }, 7), 7, "a date without %N falls back");
});

// G5 crosses the real poster -> env receiver -> host, without starting a daemon.
import fs from "node:fs";
import os from "node:os";
import { pathToFileURL } from "node:url";
import { siblingCheckout } from "./_sibling-checkout.mjs";
import * as poster from "../agent-state-event.mjs";

const LIFETIME = "7f3c9e2a-1111-4111-8111-111111111111";
const privateHome = () => fs.mkdtempSync(join(os.tmpdir(), "g5-hook-"));
const privateEnv = (home, extra = {}) => ({
  HOME: home, USERPROFILE: home, APPDATA: join(home, "AppData", "Roaming"),
  LOCALAPPDATA: join(home, "AppData", "Local"), TEMP: home, TMP: home,
  AIFY_ENV_INSTANCE: "test", AIFY_LIFETIME: LIFETIME, ...extra,
});
function descriptor(home, url, changes = {}) {
  fs.mkdirSync(join(home, ".aify", "env"), { recursive: true });
  fs.writeFileSync(join(home, ".aify", "env", "test.json"), JSON.stringify({
    url, instance: "test", pid: process.pid, startedAt: "2026-10-01T00:00:00.000Z", ...changes,
  }));
}
async function envServer(receive = () => ({ status: 200, body: {} })) {
  const requests = [];
  const server = createServer(async (req, res) => {
    let text = "";
    for await (const chunk of req) text += chunk;
    const body = JSON.parse(text);
    requests.push({ url: req.url, headers: req.headers, body });
    const result = receive(body);
    res.writeHead(result.status, { "content-type": "application/json", ...(result.headers || {}) });
    res.end(JSON.stringify(result.body));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, requests,
    close: () => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }) };
}

test("G5 first bound hook opens the real env host, blocks, unblocks and ends for resident and managed", async () => {
  const sibling = siblingCheckout("aify-env", "lib/agent-turn-events.mjs");
  assert.ok(sibling.dir, `Set AIFY_ENV_REPO: ${sibling.looked}`);
  const { AgentStateHost } = await import(pathToFileURL(join(sibling.dir, "lib/agent-state-host.mjs")));
  const { AgentTurnEvents } = await import(pathToFileURL(join(sibling.dir, "lib/agent-turn-events.mjs")));
  for (const mode of ["resident", "managed"]) {
    const home = privateHome();
    const nowUs = Date.now() * 1000;
    const record = { agentId: "hook-agent", lifetime: LIFETIME, instance: "test", harness: "claude",
      pid: 123, launcher: "/owned/claude-aify", writtenAtUs: nowUs - 1000000 };
    if (mode === "resident") {
      fs.mkdirSync(join(home, ".aify", "residents"), { recursive: true });
      fs.writeFileSync(join(home, ".aify", "residents", `hook-agent.${LIFETIME}.json`), JSON.stringify(record));
    }
    const host = new AgentStateHost({ aifyHome: join(home, ".aify"), instance: "test", nowUs: () => nowUs,
      probe: () => new Map([[123, { alive: true, createdAtUs: nowUs - 2000000, commandLine: "/owned/claude-aify" }]]) });
    host.boot();
    if (mode === "managed") host.startManaged(record);
    const facts = { mode, definition: "valid", stoppedByOperator: false };
    assert.equal(host.current("hook-agent", facts).state, "unknown");
    const receiver = new AgentTurnEvents({ host, instance: "test" });
    const env = await envServer(body => receiver.receive("hook-agent", body));
    descriptor(home, env.url);
    try {
      let tick = nowUs - 1000;
      for (const [kind, state] of [["turn-start", "working"], ["blocked", "blocked"], ["unblocked", "working"], ["turn-end", "idle"]]) {
        tick += 1;
        const r = await run([kind], privateEnv(home, { AIFY_AGENT_ID: "hook-agent", AIFY_API_KEY: "must-not-leak",
          AIFY_ENV_KEY: "must-not-leak", AIFY_ENV_URL: "http://127.0.0.1:9", AIFY_HOOK_FIRED_AT: `${Math.floor(tick / 1000000)}.${String(tick % 1000000).padStart(6, "0")}` }));
        assert.deepEqual([r.code, r.out, r.err], [0, "", ""]);
        assert.equal(host.current("hook-agent", facts).state, state, `${mode} ${kind} did not reach env`);
        const req = env.requests.at(-1);
        assert.equal(req.url, "/agents/hook-agent/turn-event");
        assert.deepEqual(req.body, { instance: "test", lifetime: LIFETIME, kind, firedAtUs: tick });
        for (const header of ["origin", "authorization", "cookie", "x-api-key", "x-aify-env-key"]) assert.equal(req.headers[header], undefined);
      }
    } finally { await env.close(); fs.rmSync(home, { recursive: true, force: true }); }
  }
});

test("G5 rereads the descriptor per event and refuses malformed carriers without URL fallback", async () => {
  const home = privateHome();
  const first = await envServer();
  const second = await envServer();
  try {
    const env = privateEnv(home, { AIFY_AGENT_ID: "hook-agent", AIFY_ENV_URL: first.url });
    assert.equal(typeof poster.postEnvAgentState, "function", "env poster not implemented");
    descriptor(home, first.url);
    assert.equal(await poster.postEnvAgentState("turn-start", { env, firedAtUs: 1790451762694110 }), true);
    descriptor(home, second.url);
    assert.equal(await poster.postEnvAgentState("turn-end", { env, firedAtUs: 1790451762694111 }), true);
    assert.equal(first.requests.length, 1);
    assert.equal(second.requests.length, 1);
    const malformed = [null, [], {}, { instance: "other" }, { pid: 1.5 }, { pid: 0 }, { startedAt: "bad" },
      { url: "http://example.com:88" }, { url: "http://127.0.0.1:88/path" },
      { url: "http://user:password@127.0.0.1:88" }, { url: [first.url] },
      { url: second.url.replace("127.0.0.1", "localhost") }, { url: second.url + "/" }];
    for (const change of malformed) {
      descriptor(home, second.url, change || {});
      if (change === null || Array.isArray(change) || Object.keys(change).length === 0)
        fs.writeFileSync(join(home, ".aify", "env", "test.json"), JSON.stringify(change));
      assert.equal(await poster.postEnvAgentState("turn-start", { env, firedAtUs: 1790451762694112 }), false, JSON.stringify(change));
    }
    descriptor(home, second.url);
    for (const change of [{ AIFY_ENV_INSTANCE: "../test" }, { AIFY_ENV_INSTANCE: "" }, { AIFY_LIFETIME: "" }, { AIFY_LIFETIME: "bad" }])
      assert.equal(await poster.postEnvAgentState("turn-start", { env: { ...env, ...change }, firedAtUs: 1790451762694112 }), false);
    for (const firedAtUs of [0, NaN, "1790451762694112", 1.5])
      assert.equal(await poster.postEnvAgentState("turn-start", { env, firedAtUs }), false);
    fs.rmSync(join(home, ".aify", "env", "test.json"));
    assert.equal(await poster.postEnvAgentState("turn-start", { env, firedAtUs: 1790451762694112 }), false);
    assert.equal(first.requests.length, 1);
    assert.equal(second.requests.length, 1);
  } finally { await first.close(); await second.close(); fs.rmSync(home, { recursive: true, force: true }); }
});

test("G5 either destination can fail without suppressing the other, and env redirects are not followed", async () => {
  const home = privateHome();
  const service = await stub({ status: 401 });
  const target = await envServer();
  const redirect = await envServer(() => ({ status: 307, body: {}, headers: { location: target.url + "/stolen" } }));
  try {
    descriptor(home, target.url);
    await run(["blocked"], hookEnv(service.url, privateEnv(home)));
    assert.equal(target.requests.length, 1, "comms 401 suppressed env");
    descriptor(home, target.url, { instance: "other" });
    await run(["unblocked"], hookEnv(service.url, privateEnv(home)));
    assert.equal(service.requests.length, 2, "invalid env suppressed comms");
    descriptor(home, redirect.url);
    await run(["turn-end"], privateEnv(home, { AIFY_AGENT_ID: "hook-agent" }));
    assert.equal(redirect.requests.length, 1);
    assert.equal(target.requests.length, 1, "redirect forwarded identity");
  } finally { service.close(); await target.close(); await redirect.close(); fs.rmSync(home, { recursive: true, force: true }); }
});


test("G5 managed codex and hermes MCP configs forward the host-minted binding", async (t) => {
  const { managedCodexConfigText } = await import("../runtimes-codex.js");
  const { aifyEntryLines } = await import("../../../scripts/hermes-mcp-config.mjs");
  const codex = managedCodexConfigText({ serverUrl: "http://127.0.0.2:9" });
  const hermes = aifyEntryLines({ serverPath: SCRIPT }).join("\n");
  await t.test("codex", () => {
    assert.match(codex, /^hooks = false$/m, "managed Codex keeps its app-server turn source, not installed hooks");
    for (const name of ["AIFY_ENV_URL", "AIFY_ENV_INSTANCE", "AIFY_LIFETIME"])
      assert.ok(codex.match(/^env_vars = .+$/m)[0].includes(`"${name}"`), `codex ${name}`);
  });
  await t.test("hermes", () => {
    for (const name of ["AIFY_ENV_URL", "AIFY_ENV_INSTANCE", "AIFY_LIFETIME"])
      assert.ok(hermes.includes(`${name}: "\${${name}}"`), `hermes ${name}`);
  });
});

test("G5 test children cannot inherit a live env lifetime", () => {
  for (const name of ["AIFY_ENV_URL", "AIFY_ENV_INSTANCE", "AIFY_LIFETIME"]) {
    const saved = process.env[name];
    try {
      process.env[name] = "live-carrier";
      assert.equal(sealedChildEnv()[name], undefined, name);
    } finally {
      if (saved === undefined) delete process.env[name]; else process.env[name] = saved;
    }
  }
});

test("G5 gateway spawn preserves the binding without a comms-side allowlist", async () => {
  const { ensureGatewayHost } = await import("../hermes-gateway.mjs");
  const { EventEmitter } = await import("node:events");
  const binding = { AIFY_ENV_URL: "http://127.0.0.1:9", AIFY_ENV_INSTANCE: "test", AIFY_LIFETIME: LIFETIME };
  let spawnedEnv;
  const child = new EventEmitter();
  child.unref = () => {};
  await ensureGatewayHost({ agentId: "hook-agent", port: 9, probeFirst: false, verifyWs: false,
    readyTimeoutMs: 100, env: binding,
    spawn: (_cmd, _args, options) => { spawnedEnv = options.env; return child; },
    fetchImpl: async () => ({ ok: true, text: async () => '__HERMES_SESSION_TOKEN__="owned-test"' }),
  });
  for (const [name, value] of Object.entries(binding)) assert.equal(spawnedEnv[name], value);
});

test("G5 a descriptor's explicit default HTTP port is still a valid loopback endpoint", async () => {
  const home = privateHome();
  const saved = globalThis.fetch;
  let reached;
  try {
    descriptor(home, "http://127.0.0.1:80");
    globalThis.fetch = async url => { reached = url.href; return new Response(null, { status: 200 }); };
    assert.equal(await poster.postEnvAgentState("turn-start", {
      env: privateEnv(home, { AIFY_AGENT_ID: "hook-agent" }), firedAtUs: 1790451762694110,
    }), true);
    assert.equal(reached, "http://127.0.0.1/agents/hook-agent/turn-event");
  } finally { globalThis.fetch = saved; fs.rmSync(home, { recursive: true, force: true }); }
});
