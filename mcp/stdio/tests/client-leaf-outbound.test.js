import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { promisify } from 'node:util';
import { sealedChildEnv } from './_child-env.mjs';
import { writeCredential } from './_owned-credential-store.mjs';
import { legacyExportedKey } from '../aify-service-endpoint.mjs';
import { transportBase } from '../registry-credential.mjs';

test('the comms adapter normalizes its loopback transport and refuses an invalid first explicit key', () => {
  assert.equal(transportBase('http://localhost:8800/path'), 'http://127.0.0.1:8800/path');
  const rejected = legacyExportedKey('http://127.0.0.1:1', { CLAUDE_MCP_API_KEY: ' ', AIFY_API_KEY: 'later-fixture-key' });
  assert.equal(rejected.state, 'CREDENTIAL_INVALID');
  assert.equal(rejected.value, '');
});

const runChild = promisify(execFile);
const moduleUrl = new URL('../aify-service-endpoint.mjs', import.meta.url).href;
const STORE_KEY = 'stored-fixture-key';
const EXPORT_KEY = 'explicit-fixture-key';

async function listener(t, status, prefix) {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push({ path: req.url, key: req.headers['x-api-key'] ?? null });
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: status === 200 }));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { requests, url: `http://127.0.0.1:${server.address().port}${prefix}` };
}

for (const explicit of [false, true]) test(`comms outbound failover: ${explicit ? 'legacy exported key is allowed' : 'stored key stays at the registered base'}`, async t => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'comms-client-leaf-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const primary = await listener(t, 503, '/primary');
  const fallback = await listener(t, 200, '/secondary');
  writeCredential(home, 'bridge.key', `${STORE_KEY}\n`);
  const registry = path.join(home, '.aify', 'services.json');
  fs.writeFileSync(registry, JSON.stringify({ services: { 'aify-comms': { endpoint: primary.url, credentialRef: 'bridge.key' } } }));
  const child = await runChild(process.execPath, ['--input-type=module', '-e', `
    const { httpCall } = await import(${JSON.stringify(moduleUrl)});
    const result = await httpCall('GET', '/owned');
    process.stdout.write(JSON.stringify(result));
  `], { env: sealedChildEnv({ HOME: home, USERPROFILE: home, AIFY_SERVICE_REGISTRY: registry,
    AIFY_SERVER_URL: primary.url, AIFY_SERVER_FALLBACK_URLS: fallback.url, ...(explicit ? { AIFY_API_KEY: EXPORT_KEY } : {}) }) });
  assert.deepEqual(JSON.parse(child.stdout), { ok: true });
  assert.deepEqual(primary.requests, [{ path: '/primary/api/v1/owned', key: explicit ? EXPORT_KEY : STORE_KEY }]);
  assert.deepEqual(fallback.requests, [{ path: '/secondary/api/v1/owned', key: explicit ? EXPORT_KEY : null }],
    'foreign destination receives no stored key');
});
