import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { StagingManifest } from '../../../scripts/staging/manifest.mjs';
import { StagingLayout } from '../../../scripts/staging/layout.mjs';
import { StagingSession } from '../../../scripts/staging/session.mjs';

const refs = (dashboard = {}) => ({
  baseline: { comms: '1'.repeat(40), env: '2'.repeat(40), wrapper: '3'.repeat(40), ...dashboard.baseline },
  candidate: { comms: '4'.repeat(40), env: '5'.repeat(40), wrapper: '6'.repeat(40), ...dashboard.candidate },
  runtimeVersions: { nodeImage: 'node:22.20.0-bookworm', codex: '0.166.0', claude: '2.1.316' },
  seed: 'synthetic-linux-fleet', liveCopyEnabled: false,
});
const both = { baseline: { dashboard: '7'.repeat(40) }, candidate: { dashboard: '8'.repeat(40) } };
const layout = (dashboardKey) => new StagingLayout({ runId: '0123456789ab', fixtureKey: 'run-owned-fixture-key', dashboardKey });

test('the dashboard role is optional, and kept exactly when both phases name it', () => {
  assert.equal('dashboard' in new StagingManifest(refs()).candidate, false);
  const staged = new StagingManifest(refs(both));
  assert.deepEqual([staged.baseline.dashboard, staged.candidate.dashboard], ['7'.repeat(40), '8'.repeat(40)]);
});

for (const [label, dashboard, pattern] of [
  ['a moving dashboard ref', { ...both, candidate: { dashboard: 'main' } }, /candidate\.dashboard/],
  ['a dashboard in one phase only', { candidate: both.candidate }, /both phases or neither/],
]) {
  test(`${label} is refused`, () => assert.throws(() => new StagingManifest(refs(dashboard)), pattern));
}

test('a run without a dashboard key generates no dashboard service or volume', () => {
  const config = layout().compose('candidate');
  assert.deepEqual(Object.keys(config.services), ['service', 'dashboard', 'host']);
  assert.deepEqual(Object.keys(config.volumes), ['service-data', 'host-home']);
});

test('a staged dashboard has its own key, its own run volume, no published port, and passes isolation', () => {
  const staged = layout('run-owned-dashboard-key');
  const config = staged.compose('candidate');
  const service = config.services['aify-dashboard'];
  assert.equal(service.environment.API_KEY, 'run-owned-dashboard-key');
  assert.equal(service.environment.PUBLIC_ORIGINS, staged.dashboardOrigin);
  assert.equal(service.working_dir, '/src/candidate/dashboard');
  assert.equal(service.ports, undefined);
  assert.deepEqual(service.volumes, [{ type: 'volume', source: 'dashboard-data', target: '/data' }]);
  assert.equal(config.volumes['dashboard-data'].name, `${staged.project}-dashboard-data`);
  assert.equal(config.volumes['dashboard-data'].labels['aify.staging.run'], staged.runId);
  assert.equal(staged.assertIsolation({ config, environment: { DATA_VOLUME_NAME: staged.dataVolume } }), true);
});

for (const key of ['', '  ', 'run-owned-fixture-key', 7]) {
  test(`a dashboard key ${JSON.stringify(key)} is refused`, () => assert.throws(() => layout(key), /its own run-owned key/));
}

test('saved receipts redact the dashboard key as well as the fixture key', () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-e5-'));
  try {
    const self = { out, layout: layout('run-owned-dashboard-key') };
    StagingSession.prototype.save.call(self, 'r.json', { args: ['-e', 'API_KEY=run-owned-dashboard-key', 'run-owned-fixture-key'] });
    const text = fs.readFileSync(path.join(out, 'r.json'), 'utf8');
    assert.ok(text.includes('<dashboard-key>') && text.includes('<fixture-key>'), text);
    assert.ok(!text.includes('run-owned-dashboard-key') && !text.includes('run-owned-fixture-key'), text);
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
});

const { meetsEngine } = await import('../../../scripts/staging/scenarios/e5.mjs');
for (const [version, engine, expected] of [
  ['v24.22.1', '>=24.15', true], ['v24.15.0', '>=24.15', true], ['v25.0.0', '>=24.15', true], ['v24.15.3', '>=24.15.3', true],
  ['v22.20.0', '>=24.15', false], ['v24.14.9', '>=24.15', false], ['v24.15.2', '>=24.15.3', false],
  ['v24.22.1', null, false], ['v24.22.1', '^24.15', false], ['24.22.1', '>=24.15', false],
]) {
  test(`node ${version} against engine ${engine} is ${expected ? 'admitted' : 'refused'}`, () => assert.equal(meetsEngine(version, engine), expected));
}

const { publicationHolds } = await import('../../../scripts/staging/scenarios/e5.mjs');
const IDS = ['a', 'b'];
const RES = 'residents-1';
const pub = (instance, agents, fresh = true) => ({ instance, fresh, agents });
const read = (publishers, conflicts = {}) => ({ status: 200, publishers,
  agents: IDS.map(agentId => ({ agentId, conflict: conflicts[agentId] ?? false })) });
for (const [label, value, residents, expected] of [
  ['the managed publisher alone', read([pub('m', IDS)]), RES, true],
  ['the managed publisher beside an empty residents one', read([pub('m', IDS), pub(RES, [])]), RES, true],
  ['two managed publishers', read([pub('m', IDS), pub('m2', IDS)]), RES, false],
  ['a second publisher while no residents role is held', read([pub('m', IDS), pub(RES, [])]), null, false],
  ['a defined agent only the residents publisher names', read([pub('m', ['a']), pub(RES, ['b'])]), RES, false],
  ['a stale publisher', read([pub('m', IDS), pub(RES, [], false)]), RES, false],
  ['an agent read as a conflict', read([pub('m', IDS)], { b: true }), RES, false],
  ['a refused read', { status: 401, publishers: null, agents: [] }, RES, false],
  ['no publishers', read([]), RES, false],
]) {
  test(`publication: ${label} is ${expected ? 'admitted' : 'refused'}`, () => assert.equal(publicationHolds(value, residents, IDS), expected));
}
