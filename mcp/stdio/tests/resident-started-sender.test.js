import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as sender from "../agent-state-event.mjs";

const SCRIPT = fileURLToPath(new URL("../agent-state-event.mjs", import.meta.url));
const PRELOAD = new URL("./fixtures/resident-start-fetch.mjs", import.meta.url).href;
const ENDPOINT = "http://127.0.0.1:43123"; // Intercepted, never opened.
const ROUTE = ENDPOINT + "/agents/hook%2Fagent/resident-started";
const LIFETIME = "7f3c9e2a-1111-4111-8111-111111111111";
const PAYLOAD = { hook_event_name: "SessionStart", session_id: "native-session", source: "resume" };
const BODY = { instance: "residents", lifetime: LIFETIME, harness: "claude", producer: "SessionStart", sessionId: "native-session" };

function fixture() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "resident-sender-"));
  const env = {
    HOME: home, USERPROFILE: home, APPDATA: path.join(home, "roaming"),
    LOCALAPPDATA: path.join(home, "local"), HERMES_HOME: path.join(home, "hermes"),
    AIFY_HOME: path.join(home, "aify"), TEMP: home, TMP: home, TMPDIR: home,
    AIFY_AGENT_ID: "hook/agent", AIFY_ENV_INSTANCE: "residents", AIFY_LIFETIME: LIFETIME,
    AIFY_ENV_URL: "http://unowned.invalid:1", AIFY_COMMS_URL: "http://unowned.invalid:2",
    AIFY_SERVER_URL: "http://unowned.invalid:3", AIFY_API_KEY: "fixture-secret-must-not-leak",
  };
  const file = path.join(home, ".aify", "env", "residents.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const descriptor = (changes = {}) => fs.writeFileSync(file, JSON.stringify({
    instance: "residents", pid: 123, startedAt: "2026-10-24T00:00:00.000Z", url: ENDPOINT, ...changes,
  }));
  descriptor();
  return { home, env, file, descriptor, close: () => fs.rmSync(home, { recursive: true, force: true }) };
}

async function captureApi(run, { status = 200, fail = false } = {}) {
  const saved = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), ...options, body: JSON.parse(options.body) });
    assert.ok([ROUTE, ENDPOINT + "/agents/hook%2Fagent/turn-event", "http://127.0.0.1/agents/hook%2Fagent/resident-started"].includes(String(url)), "unowned route");
    if (fail) throw new Error("fixture transport failure");
    return new Response(null, { status });
  };
  try { await run(requests); } finally { globalThis.fetch = saved; }
}

function post(harness, payload, env) {
  assert.equal(typeof sender.postEnvResidentStarted, "function", "resident startup API must exist");
  return sender.postEnvResidentStarted(harness, payload, { env });
}

function native(f, args = ["resident-started", "claude"], input = JSON.stringify(PAYLOAD), { open = false, routes = [ROUTE], extra = {} } = {}) {
  return new Promise((resolve, reject) => {
    const capture = path.join(f.home, "capture.jsonl");
    fs.writeFileSync(capture, "");
    // Allowlist OS essentials, not the invoking agent's session, lease or Node preload.
    const essentials = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PATH|SYSTEMROOT|WINDIR|COMSPEC|PATHEXT)$/i.test(key)));
    const child = spawn(process.execPath, ["--import", PRELOAD, SCRIPT, ...args], {
      cwd: f.home, env: { ...essentials, ...f.env, RESIDENT_TEST_CAPTURE: capture,
        RESIDENT_TEST_ROUTES: JSON.stringify(routes), ...extra }, stdio: ["pipe", "pipe", "pipe"],
    });
    const started = Date.now();
    let stdout = "", stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    child.stdin.on("error", () => {});
    child.on("error", reject);
    child.on("close", code => {
      const requests = fs.readFileSync(capture, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse);
      resolve({ code, stdout, stderr, requests, ms: Date.now() - started });
    });
    child.stdin.write(input);
    if (!open) child.stdin.end();
  });
}

function silent(result) { assert.deepEqual([result.code, result.stdout, result.stderr], [0, "", ""]); }

test("startup API preserves native provenance and inherited lifetime only on the descriptor route", async () => {
  const f = fixture();
  try {
    await captureApi(async requests => {
      assert.equal(await post("claude", { ...PAYLOAD, lifetime: "forged", producer: "forged", extra: "secret" }, f.env), true);
      assert.equal(requests.length, 1);
      assert.equal(requests[0].url, ROUTE);
      assert.deepEqual(requests[0].body, BODY);
      assert.deepEqual(requests[0].headers, { "content-type": "application/json" });
      assert.equal(requests[0].method, "POST");
      assert.equal(requests[0].redirect, "manual");
      assert.equal(requests[0].credentials, "omit");
      assert.ok(requests[0].signal instanceof AbortSignal);
    });
  } finally { f.close(); }
});

for (const [label, harness, payload, changes] of [
  ["hermes", "hermes", PAYLOAD, {}], ["codex", "codex", PAYLOAD, {}],
  ["missing hook", "claude", { session_id: "s" }, {}],
  ["turn end", "claude", { ...PAYLOAD, hook_event_name: "Stop" }, {}],
  ["prompt submit", "claude", { ...PAYLOAD, hook_event_name: "UserPromptSubmit" }, {}],
  ["wrong case", "claude", { ...PAYLOAD, hook_event_name: "sessionstart" }, {}],
  ["empty session", "claude", { ...PAYLOAD, session_id: " " }, {}],
  ["numeric session", "claude", { ...PAYLOAD, session_id: 1 }, {}],
  ["long session", "claude", { ...PAYLOAD, session_id: "é".repeat(513) }, {}],
  ["array", "claude", [], {}], ["null", "claude", null, {}],
  ["missing identity", "claude", PAYLOAD, { AIFY_AGENT_ID: "" }],
  ["padded identity", "claude", PAYLOAD, { AIFY_AGENT_ID: " hook/agent " }],
  ["non-string identity", "claude", PAYLOAD, { AIFY_AGENT_ID: ["hook/agent"] }],
  ["missing lifetime", "claude", PAYLOAD, { AIFY_LIFETIME: "" }],
  ["malformed lifetime", "claude", PAYLOAD, { AIFY_LIFETIME: "bad" }],
  ["newline lifetime", "claude", PAYLOAD, { AIFY_LIFETIME: LIFETIME + "\n" }],
  ["non-string lifetime", "claude", PAYLOAD, { AIFY_LIFETIME: [LIFETIME] }],
  ["other instance", "claude", PAYLOAD, { AIFY_ENV_INSTANCE: "test" }],
  ["path instance", "claude", PAYLOAD, { AIFY_ENV_INSTANCE: "../residents" }],
]) {
  test(`startup API refuses ${label} without any fetch`, async () => {
    const f = fixture();
    try { await captureApi(async requests => {
      assert.equal(await post(harness, payload, { ...f.env, ...changes }), false);
      assert.deepEqual(requests, []);
    }); } finally { f.close(); }
  });
}

test("startup API rereads and strictly validates raw descriptors before URL normalization", async () => {
  const f = fixture();
  try { await captureApi(async requests => {
    assert.equal(await post("claude", PAYLOAD, f.env), true);
    for (const changes of [{ instance: "other" }, { pid: 0 }, { pid: 1.5 }, { startedAt: "bad" },
      ...["http://127.1:43123", "http://localhost:43123", "http://127.0.0.1:043123", ENDPOINT + "/",
        ENDPOINT + "/../", ENDPOINT + "?", ENDPOINT + "#", ENDPOINT + "\n", "http://127.0.0.1:65536",
        "http://user:secret@127.0.0.1:43123", "https://127.0.0.1:43123", "http://example.com:43123"].map(url => ({ url }))]) {
      f.descriptor(changes);
      assert.equal(await post("claude", PAYLOAD, f.env), false, JSON.stringify(changes));
    }
    for (const raw of ["{", "null", "[]", "{}"] ) {
      fs.writeFileSync(f.file, raw);
      assert.equal(await post("claude", PAYLOAD, f.env), false, raw);
    }
    fs.rmSync(f.file);
    assert.equal(await post("claude", PAYLOAD, f.env), false);
    assert.equal(requests.length, 1);
    f.descriptor({ url: "http://127.0.0.1:80" });
    assert.equal(await post("claude", PAYLOAD, f.env), true);
    assert.equal(requests.at(-1).url, "http://127.0.0.1/agents/hook%2Fagent/resident-started");
  }); } finally { f.close(); }
});

for (const options of [{ status: 307 }, { status: 401 }, { fail: true }]) {
  test(`startup API fails closed on transport ${JSON.stringify(options)}`, async () => {
    const f = fixture();
    try { await captureApi(async requests => {
      assert.equal(await post("claude", PAYLOAD, f.env), false);
      assert.equal(requests.length, 1, "no retry or service fallback");
    }, options); } finally { f.close(); }
  });
}

test("native Claude SessionStart command sends exactly one env-only acknowledgment silently", async () => {
  const f = fixture();
  try {
    const result = await native(f);
    silent(result);
    assert.equal(result.requests.length, 1, "actual hook command must reach env");
    assert.deepEqual(result.requests[0], { url: ROUTE, method: "POST", headers: { "content-type": "application/json" },
      body: BODY, redirect: "manual", credentials: "omit", hasSignal: true });
  } finally { f.close(); }
});

test("native startup command bounds stdin bytes and refuses invalid or unsupported hook input", async () => {
  const f = fixture();
  try {
    for (const [args, input] of [
      [["resident-started", "hermes"], JSON.stringify(PAYLOAD)],
      [["resident-started", "codex"], JSON.stringify(PAYLOAD)],
      [["resident-started"], JSON.stringify(PAYLOAD)],
      [["resident-started", "claude"], "{"],
      [["resident-started", "claude"], Buffer.concat([
        Buffer.from('{"hook_event_name":"SessionStart","session_id":"'), Buffer.from([0xff]), Buffer.from('"}'),
      ])],
      [["resident-started", "claude"], JSON.stringify({ ...PAYLOAD, hook_event_name: "Stop" })],
      [["resident-started", "claude"], JSON.stringify({ ...PAYLOAD, session_id: "" })],
      [["resident-started", "claude"], JSON.stringify({ ...PAYLOAD, extra: "x".repeat(65536) })],
    ]) {
      const result = await native(f, args, input);
      silent(result);
      assert.deepEqual(result.requests, [], input.slice(0, 80));
    }
    const raw = JSON.stringify({ ...PAYLOAD, extra: "é" });
    const atLimit = raw + " ".repeat(65536 - Buffer.byteLength(raw));
    const admitted = await native(f, undefined, atLimit);
    silent(admitted);
    assert.equal(admitted.requests.length, 1, "exact byte-limit native JSON is admitted");
    const refused = await native(f, undefined, atLimit + " ");
    silent(refused);
    assert.deepEqual(refused.requests, [], "one byte over bound must not post");
  } finally { f.close(); }
});

test("native startup waits for complete stdin but has a deadline and never invents provenance", async () => {
  const f = fixture();
  try {
    const result = await native(f, undefined, JSON.stringify(PAYLOAD), { open: true });
    silent(result);
    assert.deepEqual(result.requests, [], "a never-completed hook payload is not evidence");
    assert.ok(result.ms < 4500, `stdin deadline took ${result.ms}ms`);
  } finally { f.close(); }
});

test("native startup bounds the env request and cannot retry to a service", async () => {
  const f = fixture();
  try {
    const result = await native(f, undefined, undefined, { extra: { RESIDENT_TEST_HANG: "1" } });
    silent(result);
    assert.equal(result.requests.length, 1);
    assert.ok(result.ms >= 1800, `fixture exited before the fetch deadline: ${result.ms}ms`);
    assert.ok(result.ms < 4500, `fetch deadline took ${result.ms}ms`);
  } finally { f.close(); }
});

test("the four native turn events go to aify-env only and never acknowledge startup", async () => {
  const f = fixture();
  const firedAtUs = 1790451762694110;
  try {
    for (const event of ["turn-start", "turn-end", "blocked", "unblocked"]) {
      const servicePath = ["blocked", "unblocked"].includes(event) ? "status-event" : event;
      const service = "http://127.0.0.2:43124/api/v1/agents/hook%2Fagent/" + servicePath;
      const envRoute = ENDPOINT + "/agents/hook%2Fagent/turn-event";
      const result = await native(f, [event], JSON.stringify(PAYLOAD), {
        routes: [envRoute, service], extra: { AIFY_SERVER_URL: "http://127.0.0.2:43124", AIFY_HOOK_FIRED_AT: "1790451762.694110" },
      });
      silent(result);
      const envPost = result.requests.find(request => request.url === envRoute);
      assert.deepEqual(envPost?.body, { instance: "residents", lifetime: LIFETIME, kind: event, firedAtUs }, event);
      // Since 0.9.9 S2 the comms service is not a destination: aify-env owns turns.
      assert.equal(result.requests.find(request => request.url === service), undefined, `${event} reached comms`);
      assert.equal(result.requests.length, 1, event);
    }
  } finally { f.close(); }
});
