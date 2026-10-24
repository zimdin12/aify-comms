// The real ensure-host producer and import-time MCP consumer share a home, not TEMP.
import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { sealedChildEnv } from "./_child-env.mjs";
import { tmpDir } from "./_tmpdir.js";

const moduleUrl = (name) => new URL(`../${name}`, import.meta.url).href;
const agentId = "issue21-fixture";
const token = "issue21-fixture-token";
const tokenEnv = "ISSUE21_FIXTURE_TOKEN";
function child(code, env) {
  return execFileSync(process.execPath, ["--input-type=module", "-e", code], {
    env: sealedChildEnv(env), encoding: "utf8", timeout: 15000, stdio: ["ignore", "pipe", "pipe"],
  });
}

test("runEnsureHostCli publishes a gateway the MCP child finds with a different TEMP", () => {
  const root = tmpDir("aify-issue21-cross-process-");
  const home = path.join(root, "home");
  const writerTemp = path.join(root, "writer");
  const readerTemp = path.join(root, "reader");
  for (const dir of [home, writerTemp, readerTemp]) fs.mkdirSync(dir);
  const env = {
    HOME: home, USERPROFILE: home, HERMES_HOME: path.join(home, ".hermes"),
    AIFY_HERMES_GATEWAY_TOKEN_ENV: tokenEnv,
  };
  // Refuse accidental real-home access before importing any product modules in either process.
  const guard = `import assert from 'node:assert/strict'; import os from 'node:os';
    assert.equal(os.homedir(), ${JSON.stringify(home)});`;
  const producer = child(`${guard}
    const { resolveGatewayPort, readGatewayUrlMarker } = await import(${JSON.stringify(moduleUrl("hermes-endpoint.js"))});
    const { runEnsureHostCli } = await import(${JSON.stringify(moduleUrl("hermes-managed-host.js"))});
    // Claim through the real resolver with a fake portFree. No real socket probe or runtime spawn.
    let probes = 0;
    const port = await resolveGatewayPort(${JSON.stringify(agentId)}, { portFree: async () => { probes++; return true; } });
    assert.equal(probes, 1);
    let fetches = 0, opens = 0, closes = 0;
    const result = await runEnsureHostCli(${JSON.stringify(agentId)}, {
      spawnImpl: () => { throw new Error('fixture forbids spawning a runtime'); },
      attach: () => { throw new Error('fixture forbids lease attachment'); },
      fetchImpl: async (url, opts) => {
        assert.equal(url, 'http://127.0.0.1:' + port + '/'); assert.equal(opts.method, 'GET'); fetches++;
        return { ok: true, text: async () => '<script>__HERMES_SESSION_TOKEN__="${token}"</script>' };
      },
      openWsImpl: async (url) => {
        assert.equal(url, 'ws://127.0.0.1:' + port + '/api/ws?token=${token}'); opens++;
        return { close() { closes++; } };
      }, out: () => {}, err: () => {},
    });
    assert.equal(fetches, 1); assert.equal(opens, 1); assert.equal(closes, 1);
    const marker = readGatewayUrlMarker(${JSON.stringify(agentId)});
    assert.equal(marker?.gatewayUrl, result.wsUrl, 'producer must publish before consumer starts');
    assert.equal(marker?.gatewayTokenEnv, ${JSON.stringify(tokenEnv)});
    process.stdout.write(JSON.stringify({ port: result.port }));
  `, { ...env, TEMP: writerTemp, TMP: writerTemp, TMPDIR: writerTemp });
  const { port } = JSON.parse(producer);
  const consume = (extra = {}) => JSON.parse(child(`${guard}
    const m = await import(${JSON.stringify(moduleUrl("hermes-gateway-config.mjs"))});
    process.stdout.write(JSON.stringify({ url: m.AIFY_HERMES_GATEWAY_URL, tokenEnv: m.AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER }));
  `, { ...env, TEMP: readerTemp, TMP: readerTemp, TMPDIR: readerTemp,
    AIFY_AGENT_ID: agentId, AIFY_HERMES_GATEWAY_URL: "${AIFY_HERMES_GATEWAY_URL}", ...extra }));
  assert.deepEqual(consume(), { url: `ws://127.0.0.1:${port}/api/ws?token=${token}`, tokenEnv });
  assert.deepEqual(consume({ AIFY_HERMES_GATEWAY_URL: "wss://explicit.invalid/ws" }), {
    url: "wss://explicit.invalid/ws", tokenEnv: "",
  });
  assert.deepEqual(consume({ AIFY_AGENT_ID: "issue21-other-fixture" }), { url: "", tokenEnv: "" });
  assert.ok(path.resolve(home).startsWith(path.resolve(os.homedir(), "..")), "test home must remain under its sealed scratch");
});

test("a fresh new reader accepts an old publisher's TEMP marker while the explicit pins remain", () => {
  const root = tmpDir("aify-issue21-mixed-version-");
  const home = path.join(root, "home");
  const legacyTemp = path.join(root, "legacy-temp");
  for (const dir of [home, legacyTemp]) fs.mkdirSync(dir);
  const id = "issue21-old-publisher-fixture";
  const payload = { gatewayUrl: "ws://127.0.0.1:1/ws?token=legacy-fixture", gatewayTokenEnv: "LEGACY_FIXTURE_TOKEN" };
  const env = { HOME: home, USERPROFILE: home, HERMES_HOME: path.join(home, ".hermes"),
    TEMP: legacyTemp, TMP: legacyTemp, TMPDIR: legacyTemp };
  // The pre-stable publisher's JSON wire format, executed in its own process. No new writer API.
  child(`import fs from 'node:fs'; import path from 'node:path';
    fs.writeFileSync(path.join(process.env.TEMP, 'aify-hermes-gateway-${id}'), ${JSON.stringify(JSON.stringify(payload))});`, env);
  const legacyFile = path.join(legacyTemp, `aify-hermes-gateway-${id}`);
  assert.deepEqual(JSON.parse(fs.readFileSync(legacyFile, "utf8")), payload, "old publisher completed");
  const result = JSON.parse(child(`import assert from 'node:assert/strict'; import os from 'node:os';
    assert.equal(os.homedir(), ${JSON.stringify(home)});
    const m = await import(${JSON.stringify(moduleUrl("hermes-gateway-config.mjs"))});
    process.stdout.write(JSON.stringify({ gatewayUrl: m.AIFY_HERMES_GATEWAY_URL,
      gatewayTokenEnv: m.AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER }));`,
    { ...env, AIFY_AGENT_ID: id, AIFY_HERMES_GATEWAY_URL: "${AIFY_HERMES_GATEWAY_URL}" }));
  assert.deepEqual(result, payload);
  assert.equal(fs.existsSync(path.join(home, ".aify", "hermes", "gateways")), false, "reader creates no stable store");
  assert.deepEqual(JSON.parse(fs.readFileSync(legacyFile, "utf8")), payload, "legacy is not migrated or modified");
});
