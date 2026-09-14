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
import { postAgentState } from "../agent-state-event.mjs";

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
    const child = spawn(process.execPath, [SCRIPT, ...args], { env: sealedChildEnv(env), stdio: ["pipe", "pipe", "pipe"] });
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
      const r = await run([event], hookEnv(s.url));
      assert.equal(r.code, 0);
      assert.equal(s.requests.length, 1, JSON.stringify(s.requests));
      const [req] = s.requests;
      assert.equal(req.method, "POST");
      assert.equal(req.url, path);
      assert.equal(req.key, "test-key", "the request must authenticate");
      // No bridgeId, so the service takes a turn signal as the authoritative harness one.
      assert.deepEqual(JSON.parse(req.body), body);
      assert.equal(r.out, "", "a hook's stdout is parsed by codex; it must stay empty");
      assert.equal(r.err, "");
    } finally {
      s.close();
    }
  });
}

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
