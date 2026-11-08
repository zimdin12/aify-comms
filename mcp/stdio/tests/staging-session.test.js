import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { StagingManifest } from '../../../scripts/staging/manifest.mjs';
import { StagingLayout } from '../../../scripts/staging/layout.mjs';
import { StagingSession } from '../../../scripts/staging/session.mjs';

function world(work) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-session-'));
  const saved = Object.fromEntries(['PATH', 'HOME', 'USERPROFILE'].map(name => [name, process.env[name]]));
  try {
    const bin = path.join(root, 'fixture tools');
    const home = path.join(root, 'ambient-home');
    fs.mkdirSync(bin);
    fs.mkdirSync(path.join(home, '.docker'), { recursive: true });
    const ambient = JSON.stringify({ auths: { fixture: { auth: 'CREDENTIALX' } }, currentContext: 'host-context' });
    fs.writeFileSync(path.join(home, '.docker', 'config.json'), ambient);
    const compose = path.join(bin, process.platform === 'win32' ? 'docker-compose.exe' : 'docker-compose');
    fs.writeFileSync(compose, 'fixture-never-executed');
    Object.assign(process.env, { PATH: bin, HOME: home, USERPROFILE: home });
    const value = { baseline: { comms: '1'.repeat(40), env: '2'.repeat(40), wrapper: '3'.repeat(40) },
      candidate: { comms: '4'.repeat(40), env: '5'.repeat(40), wrapper: '6'.repeat(40) },
      runtimeVersions: { nodeImage: 'node:22.20.0-bookworm', codex: '0.166.0', claude: '2.1.316' },
      seed: 'synthetic-linux-fleet', liveCopyEnabled: false };
    const refs = path.join(root, 'refs.json');
    fs.writeFileSync(refs, JSON.stringify(value));
    const layout = new StagingLayout({ runId: '0123456789ab', fixtureKey: 'fixture-api-key', ports: { service: 8820, dashboard: 8821 } });
    const session = new StagingSession({ manifest: new StagingManifest(value), layout, options: { refs, out: path.join(root, 'out') } });
    work({ session, compose, home, ambient, bin });
  } finally {
    for (const [name, value] of Object.entries(saved)) { if (value === undefined) delete process.env[name]; else process.env[name] = value; }
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test('private Docker config discovers the existing Compose tool without importing ambient auth or context', () => world(({ session, compose, home, ambient }) => {
  const configured = JSON.parse(fs.readFileSync(path.join(session.configDirectory, 'config.json'), 'utf8'));
  assert.deepEqual(configured, { cliPluginsExtraDirs: [path.dirname(fs.realpathSync(compose))] });
  assert.equal(session.docker.environment.HOME, undefined);
  assert.equal(session.docker.environment.USERPROFILE, undefined);
  assert.equal(fs.readFileSync(path.join(home, '.docker', 'config.json'), 'utf8'), ambient);
  assert.doesNotMatch(JSON.stringify(configured), /CREDENTIALX|host-context/);
  assert.equal(session.receipts.length, 0, 'discovery executed Docker');
  assert.equal(session.finish({ ok: true, receipts: [] }), true, 'healthy unchanged-input control failed');
}));

test('replacing the selected Compose binary invalidates the native input binding', () => world(({ session, compose }) => {
  fs.writeFileSync(compose, 'changed-fixture-never-executed');
  assert.equal(session.finish({ ok: true, receipts: [] }), false);
}));
