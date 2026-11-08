import assert from 'node:assert/strict';
import test from 'node:test';
import { StagingBuild } from '../../../scripts/staging/build.mjs';

function fixture(answer = { status: 0, signal: null, errorCode: null }) {
  const calls = [];
  const build = new StagingBuild({ directory: '.',
    manifest: { runtimeVersions: { nodeImage: 'node:22.20.0-bookworm', codex: '0.166.0', claude: '2.1.316' } },
    layout: { runId: '0123456789ab' },
    resources: { plan: value => calls.push({ step: 'plan', ...value }) },
    docker: args => { calls.push({ step: 'docker', args: [...args] }); return answer; } });
  build.images = { dependencies: 'fixture-dependencies', host: 'fixture-host' };
  build.context = 'fixture-owned-context';
  return { build, calls };
}

test('each image is admitted before its build', () => {
  const { build, calls } = fixture();
  build.buildImages();
  assert.deepEqual(calls.map(item => item.step), ['plan', 'docker', 'plan', 'docker']);
  assert.deepEqual(calls[0], { step: 'plan', kind: 'image', name: 'fixture-dependencies' });
});

test('image builds receive the exact frozen runtime inputs', () => {
  const { build, calls } = fixture();
  build.buildImages();
  const commands = calls.filter(item => item.step === 'docker');
  assert.equal(commands.length, 2);
  for (const { args } of commands) {
    assert.ok(args.includes('NODE_IMAGE=node:22.20.0-bookworm'));
    assert.ok(args.includes('CODEX_VERSION=0.166.0'));
    assert.ok(args.includes('CLAUDE_VERSION=2.1.316'));
    assert.ok(args.includes('aify.staging.run=0123456789ab'));
    assert.equal(args.at(-1), 'fixture-owned-context');
  }
});

for (const answer of [{ status: null }, { status: 1 }, { status: 0, errorCode: 'ETIMEDOUT' }, { status: 0, signal: 'SIGTERM' }]) {
  test(`an unsuccessful image build stops before admitting another image: ${JSON.stringify(answer)}`, () => {
    const { build, calls } = fixture(answer);
    assert.throws(() => build.buildImages(), /did not complete/);
    assert.deepEqual(calls.map(item => item.step), ['plan', 'docker']);
  });
}

test('refused resource admission never reaches Docker build', () => {
  const { build, calls } = fixture();
  build.resources = { plan: () => { throw new Error('resource already exists'); } };
  assert.throws(() => build.buildImages(), /already exists/);
  assert.deepEqual(calls, []);
});
