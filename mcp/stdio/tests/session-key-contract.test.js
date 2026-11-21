import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { destinationKeyResolver } from '../aify-service-endpoint.mjs';
import { sameEndpoint } from '../registry-credential.mjs';
import { commsServiceSpec } from '../service-spec.mjs';
import { writeCredential } from './_owned-credential-store.mjs';

const bytes = fs.readFileSync(new URL('./fixtures/session-key-v1.json', import.meta.url));
const fixture = JSON.parse(bytes);
const { registration, store } = fixture;

// Wrapper vendors this data by Comms commit and exact SHA-256. Changing a pinned file is deliberate.
test('the vendored session-key contract is bound to the spec and its exact bytes', () => {
  assert.equal(fixture.schemaVersion, 1);
  assert.equal(createHash('sha256').update(bytes).digest('hex'),
    '192fc56b9b6603b936d164ff09fbdacdf2e10fbdaf2d11a466f0bbbdce5159dc');
  const { name, fixed, secrets } = commsServiceSpec({ version: 'fixture', source: { pending: 'fixture' } });
  assert.deepEqual(registration, { name, fixed, secrets });
});

function putCredential(home, kind, contents) {
  if (kind === 'missing') return;
  if (kind === 'private' || kind === 'shared') {
    writeCredential(home, store.credentialRef, contents, { shared: kind === 'shared' });
    return;
  }
  assert.equal(kind, 'symlink', `unrecognized fixture custody: ${kind}`);
  const file = writeCredential(home, 'fixture-target.key', contents);
  fs.symlinkSync(file, path.join(path.dirname(file), store.credentialRef), 'file');
}

for (const item of fixture.cases) test(`session key contract: ${item.id}`, t => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'comms-session-fixture-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const entry = { ...registration.fixed, endpoint: store.endpoint, credentialRef: store.credentialRef };
  fs.mkdirSync(path.join(home, '.aify'));
  fs.writeFileSync(path.join(home, '.aify', 'services.json'), JSON.stringify({
    version: 1, services: { [registration.name]: entry },
  }));
  try {
    putCredential(home, item.custody, item.bytes ?? store.bytes);
  } catch (error) {
    if (item.custody === 'symlink' && error.code === 'EPERM' && process.platform === 'win32') {
      t.skip('this Windows account cannot create a file symlink');
      return;
    }
    throw error;
  }
  const resolve = destinationKeyResolver(entry.endpoint, {
    env: item.request.env ?? {}, homeDir: home, onDiagnostic: () => {},
  });
  const key = resolve(item.request.url ?? entry.endpoint);
  assert.deepEqual(key === '' ? { refused: true } : { key }, item.expected);
});

for (const { left, right, same } of fixture.endpointCases) test(`session endpoint contract: ${left} vs ${right}`, () => {
  assert.equal(sameEndpoint(left, right), same);
});
