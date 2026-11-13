// The wrapper calls the real helper from a private installed bridge, never the user's bridge or a stub.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { siblingCheckout } from "./_sibling-checkout.mjs";

const STDIO = fileURLToPath(new URL("../", import.meta.url));
const PIN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const OTHER = "11111111-2222-3333-4444-555555555555";
const KEY = "synthetic-pin-lookup-key";
const root = mkdtempSync(path.join(os.tmpdir(), "aify-session-pin-"));
const bridge = path.join(root, ".aify-comms", "mcp", "stdio");
mkdirSync(bridge, { recursive: true });
for (const entry of readdirSync(STDIO, { withFileTypes: true })) {
  if (entry.isFile() && /\.(?:m?js|json)$/.test(entry.name)) copyFileSync(path.join(STDIO, entry.name), path.join(bridge, entry.name));
}
const CLI = path.join(bridge, "session-for-agent.mjs");
const env = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, HOME: root, USERPROFILE: root,
  AIFY_SERVICE_REGISTRY: path.join(root, "absent-registry.json"), AIFY_API_KEY: KEY };
let answer = { status: 200, body: { runtime: "claude-code", sessionHandle: PIN } };
const requests = [];
const server = http.createServer((req, res) => {
  requests.push({ url: req.url, key: req.headers["x-api-key"] || "" });
  const selected = req.headers["x-api-key"] === KEY ? answer : { status: 401, body: { detail: "not authenticated" } };
  if (selected.hang) return;
  res.writeHead(selected.status, { "content-type": "application/json", ...selected.headers });
  res.end(typeof selected.body === "string" ? selected.body : JSON.stringify(selected.body));
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const endpoint = `http://127.0.0.1:${server.address().port}`;
test.after(() => { server.close(); rmSync(root, { recursive: true, force: true }); });

function run(file, args, extraEnv = {}) {
  return new Promise(resolve => execFile(process.execPath, [file, ...args], {
    cwd: root, env: { ...env, ...extraEnv }, encoding: "utf8", timeout: 10000, maxBuffer: 4096,
  }, (error, stdout, stderr) => resolve({ code: error?.code || 0, stdout, stderr })));
}
const lookup = (agent = "fixture-agent", extraEnv = {}) => run(CLI, [endpoint, "claude-code", agent], extraEnv);

// The parent keeps serving HTTP while this child invokes the wrapper's synchronous nested helper spawn.
async function wrapperLookup(agent = "fixture-agent") {
  const peer = siblingCheckout("aify-wrapper", "lib/claude-sessions.mjs");
  assert.ok(peer.dir, `wrapper source missing: ${peer.looked.join(", ")}`);
  const script = `import { ClaudePinLookup } from ${JSON.stringify(pathToFileURL(path.join(peer.dir, "lib/claude-sessions.mjs")).href)};
    console.log(JSON.stringify(new ClaudePinLookup({bridge:process.argv[1],endpoint:process.argv[2],agentId:process.argv[3],env:process.env,home:process.env.HOME}).lookup()));`;
  const result = await run("--input-type=module", ["-e", script, bridge, endpoint, agent]);
  assert.equal(result.code, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test("installed bridge helper selects the named pin through the real wrapper, not a neighboring agent", async () => {
  requests.length = 0;
  answer = { status: 200, body: { runtime: "claude-code", sessionHandle: PIN } };
  const projects = path.join(root, ".claude", "projects", "fixture-project");
  mkdirSync(projects, { recursive: true });
  writeFileSync(path.join(projects, `${PIN}.jsonl`), "{}\n");
  writeFileSync(path.join(projects, `${OTHER}.jsonl`), "{}\n");
  assert.ok(existsSync(path.join(bridge, "agent-for-handle.mjs")), "known bridge helper was copied");
  const direct = await lookup();
  assert.equal(direct.code, 0, "installed helper must exist and exit successfully");
  assert.equal(direct.stdout, `found\t${PIN}\n`);
  assert.equal(direct.stderr, "");
  assert.deepEqual(await wrapperLookup(), { status: "found", session: PIN });
  answer = { status: 200, body: { runtime: "claude-code", sessionHandle: OTHER } };
  assert.deepEqual(await wrapperLookup("neighbor"), { status: "found", session: OTHER });
  assert.ok(requests.every(r => r.key === KEY));
  assert.deepEqual(requests.map(r => r.url), ["/api/v1/agents/fixture-agent", "/api/v1/agents/fixture-agent", "/api/v1/agents/neighbor"]);
  rmSync(path.join(projects, `${OTHER}.jsonl`));
  assert.deepEqual(await wrapperLookup("neighbor"), { status: "missing-transcript", session: "" });
});

test("controlled replies keep missing, foreign, malformed and failed pins out of the selector", async t => {
  const cases = [
    ["no handle", 200, { runtime: "claude-code", sessionHandle: "" }, "none"],
    ["null handle", 200, { runtime: "claude-code", sessionHandle: null }, "none"],
    ["absent handle", 200, { runtime: "claude-code" }, "none"],
    ["unknown agent", 404, {}, "none"],
    ["other runtime", 200, { runtime: "codex", sessionHandle: PIN }, "wrong-runtime"],
    ["missing runtime", 200, { sessionHandle: PIN }, "unavailable"],
    ["invalid UUID", 200, { runtime: "claude-code", sessionHandle: "latest" }, "unavailable"],
    ["output injection", 200, { runtime: "claude-code", sessionHandle: `${PIN}\nfound\t${OTHER}` }, "unavailable"],
    ["trailing newline", 200, { runtime: "claude-code", sessionHandle: `${PIN}\n` }, "unavailable"],
    ["wrong handle type", 200, { runtime: "claude-code", sessionHandle: [PIN] }, "unavailable"],
    ["bad JSON", 200, "{private invalid input", "unavailable"],
    ["null body", 200, null, "unavailable"],
    ["array body", 200, [], "unavailable"],
    ["service error", 503, { detail: "private failure" }, "unavailable"],
    ["wrapped legacy shape", 200, { agent: { runtime: "claude-code", session_handle: PIN } }, `found\t${PIN}`],
  ];
  for (const [name, status, body, expected] of cases) await t.test(name, async () => {
    answer = { status, body };
    const result = await lookup();
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout, `${expected}\n`);
    assert.equal(result.stderr, "", "no raw response or parser diagnostic reaches the launcher");
  });
});

test("the helper loads settings-only credentials; no credential produces unavailable", async () => {
  answer = { status: 200, body: { runtime: "claude-code", sessionHandle: PIN } };
  const noKey = await lookup("fixture-agent", { AIFY_API_KEY: "" });
  assert.equal(noKey.stdout, "unavailable\n");
  mkdirSync(path.join(root, ".claude"), { recursive: true });
  const settings = path.join(root, ".claude", "settings.local.json");
  writeFileSync(settings, JSON.stringify({ env: { AIFY_API_KEY: KEY } }));
  try {
    assert.equal((await lookup("fixture-agent", { AIFY_API_KEY: "" })).stdout, `found\t${PIN}\n`);
  } finally { rmSync(settings); }
});

test("redirects do not forward the credential to another origin", async () => {
  const seen = [];
  const other = http.createServer((req, res) => { seen.push(req.url); res.end("{}"); });
  await new Promise(resolve => other.listen(0, "127.0.0.1", resolve));
  try {
    answer = { status: 302, body: {}, headers: { location: `http://127.0.0.1:${other.address().port}/leak` } };
    assert.equal((await lookup()).stdout, "unavailable\n");
    assert.deepEqual(seen, [], "redirect receiver must see no request");
    const control = await fetch(`http://127.0.0.1:${other.address().port}/control`);
    assert.equal(control.status, 200);
    assert.deepEqual(seen, ["/control"], "the receiver is reachable, not silently broken");
  } finally { other.close(); }
});

test("a host that never answers is bounded below the wrapper spawn timeout", async () => {
  answer = { status: 200, hang: true };
  const result = await lookup();
  assert.equal(result.code, 0, "the helper's own request bound must finish, not the test's process timeout");
  assert.equal(result.stdout, "unavailable\n");
  assert.equal(result.stderr, "");
});

test("unreachable endpoint and invalid invocation answer only unavailable", async () => {
  assert.equal((await run(CLI, ["http://127.0.0.1:9", "claude-code", "fixture-agent"])).stdout, "unavailable\n");
  assert.equal((await run(CLI, [])).stdout, "unavailable\n");
  assert.ok(existsSync(path.join(bridge, "agent-for-handle.mjs")), "the copied bridge contains a known existing helper");
});
