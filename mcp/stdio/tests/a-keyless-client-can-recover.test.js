// Item 4: a long-lived client must not retain a startup miss or a rejected store key.
import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { sealedChildEnv } from "./_child-env.mjs";
import { destinationKeyResolver } from "../aify-service-endpoint.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const endpoint = "http://127.0.0.1:1";

function runClient(mode, client) {
  const home = mkdtempSync(path.join(tmpdir(), "aify-key-recovery-"));
  const dir = path.join(home, ".aify", "credentials");
  mkdirSync(dir, { recursive: true });
  const credential = path.join(dir, "aify-comms-fixture.key");
  writeFileSync(credential, "old-fixture-key\n", { mode: 0o600 });
  // This is only the test's file. The real reader still checks its custody.
  if (process.platform === "win32") {
    execFileSync("icacls", [credential, "/inheritance:r", "/grant:r", `${process.env.USERNAME}:(F)`],
      { stdio: "ignore" });
  }
  const registry = path.join(home, ".aify", "services.json");
  const entry = JSON.stringify({ services: { "aify-comms": {
    endpoint, credentialRef: "aify-comms-fixture.key",
  } } });
  if (mode === "rejected") writeFileSync(registry, entry);
  const script = `
    import assert from 'node:assert/strict';
    import { writeFileSync } from 'node:fs';
    const service = await import(${JSON.stringify(pathToFileURL(path.join(root, "aify-service-endpoint.mjs")).href)});
    const http = await import(${JSON.stringify(pathToFileURL(path.join(root, "aify-http.mjs")).href)});
    const call = ${client === "hermes" ? "http.makeAifyHttpCall(http.AIFY_SERVER_URL)" : "service.httpCall"};
    const method = ${JSON.stringify(client === "hermes" ? "POST" : "GET")};
    const route = ${JSON.stringify(client === "hermes" ? "/dispatch/claim" : "/health")};
    const mode = ${JSON.stringify(mode)};
    const seen = [];
    globalThis.fetch = async (url, options) => {
      assert.ok([${JSON.stringify(endpoint + "/api/v1")}, 'http://localhost:1/api/v1'].includes(url.slice(0, -route.length)));
      assert.equal(url.slice(-route.length), route);
      assert.equal(options.method, method);
      assert.equal(options.redirect, 'manual');
      seen.push(options.headers['X-API-Key'] || '');
      if (mode === 'rejected' && seen.length === 1) {
        writeFileSync(${JSON.stringify(credential)}, 'new-fixture-key\\n');
        return new Response('fixture network refusal', { status: 401 });
      }
      return Response.json({ claimed: true });
    };
    if (mode === 'late') {
      assert.equal(service.API_KEY, '');
      writeFileSync(${JSON.stringify(registry)}, ${JSON.stringify(entry)});
      writeFileSync(${JSON.stringify(credential)}, 'new-fixture-key\\n');
    } else {
      await assert.rejects(() => call(method, route), { status: 401 });
      assert.equal(seen.length, 1, '401 must not replay a request in the same call');
      assert.equal(seen[0], 'old-fixture-key', 'the initial credential was not actually used');
    }
    assert.deepEqual(await call(method, route), { claimed: true });
    assert.equal(seen.at(-1), 'new-fixture-key', 'the existing client retained an empty or rejected key');
    assert.equal(service.API_KEY, 'new-fixture-key', 'the primary export retained the startup snapshot');
    assert.equal(http.AIFY_API_KEY, service.API_KEY, 'the alias stopped following its owner');
    assert.equal(service.keyForUrl(http.AIFY_SERVER_URL), service.API_KEY, 'the public lookup disagrees with its owner');
    assert.equal(service.keyForUrl('http://127.0.0.1:2'), '', 'the public lookup leaked the store key');
    await service.httpCall(method, route);
    assert.equal(seen.at(-1), 'new-fixture-key', 'MCP tools and delivery disagree');
    process.stdout.write('recovered');
  `;
  try {
    return spawnSync(process.execPath, ["--input-type=module", "-e", script], {
      encoding: "utf8", timeout: 15000,
      env: sealedChildEnv({ HOME: home, USERPROFILE: home,
        AIFY_SERVER_URL: endpoint, AIFY_SERVICE_REGISTRY: registry }),
    });
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

for (const client of ["hermes", "mcp"]) for (const mode of ["late", "rejected"]) {
  test(`the existing ${client} client recovers a ${mode} store credential without replaying a request`, () => {
    const result = runClient(mode, client);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, "recovered");
    if (mode === "late") assert.match(result.stderr, /registry_unreadable/);
    assert.doesNotMatch(result.stderr, /old-fixture-key|new-fixture-key|fixture network refusal/,
      "credential diagnostics printed secrets or the service's untrusted body");
  });
}

test("both Hermes consumers ask the factory for a live key rather than passing a snapshot", () => {
  for (const name of ["hermes-delivery-loop.mjs", "hermes-active-session.mjs"]) {
    const source = readFileSync(path.join(root, name), "utf8");
    assert.match(source, /makeAifyHttpCall\(AIFY_SERVER_URL\)/, name);
    assert.doesNotMatch(source, /makeAifyHttpCall\(AIFY_SERVER_URL,\s*AIFY_API_KEY\)/, name);
  }
});

test("an empty resolver retries all custody checks and reports fixed reasons without raw details", () => {
  const diagnostics = [];
  let refusal = "secret-bearing custody detail";
  let bytes = "bad\r\n";
  const resolve = destinationKeyResolver(endpoint, {
    env: {}, homeDir: "/fixture", joinPath: (...parts) => parts.join("/"), realpath: x => x,
    readFile: file => file.endsWith("services.json")
      ? JSON.stringify({ services: { "aify-comms": { endpoint, credentialRef: "fixture.key" } } })
      : bytes,
    custody: () => refusal, onDiagnostic: message => diagnostics.push(message),
  });
  assert.equal(resolve(endpoint), "");
  assert.equal(resolve(endpoint), "");
  assert.equal(diagnostics.length, 1, "unchanged failures must not flood each poll");
  assert.match(diagnostics[0], /credential_custody_refused/);
  refusal = "";
  assert.equal(resolve(endpoint), "");
  assert.match(diagnostics.at(-1), /credential_bytes_refused/);
  bytes = "usable-fixture-key\n";
  assert.equal(resolve(endpoint), "usable-fixture-key");
  assert.equal(resolve("http://127.0.0.1:2"), "", "recovery leaked the store key to another destination");
  assert.doesNotMatch(diagnostics.join("\n"), /secret-bearing|usable-fixture-key|fixture.key/);
});
