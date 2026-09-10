// Test-owned TLS only. No browser, live service, OS trust changes, or production certificate.
import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('dashboard API and realtime use one verified HTTPS origin', { timeout: 30000 }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'aify-https-test-'));
  try {
    const cert = join(dir, 'cert.pem');
    const key = join(dir, 'key.pem');
    const generated = spawnSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
      '-keyout', key, '-out', cert, '-days', '1', '-subj', '/CN=localhost',
      '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'], { encoding: 'utf8', timeout: 15000 });
    assert.equal(generated.status, 0, `OpenSSL fixture generation failed: ${generated.error || generated.stderr}`);
    const env = { ...process.env, NODE_EXTRA_CA_CERTS: cert };
    delete env.NODE_TLS_REJECT_UNAUTHORIZED;
    delete env.NODE_OPTIONS;
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./fixtures/https-origin-check.mjs', import.meta.url)), key, cert],
      { env, encoding: 'utf8', timeout: 15000 });
    assert.equal(result.status, 0, `${result.error || ''}\n${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /verified HTTPS API, terminal input, WSS frame; untrusted and wrong-host TLS refused/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
