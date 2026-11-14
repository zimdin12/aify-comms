import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { StagingDocker } from '../../../scripts/staging/docker.mjs';
import { MINIMUM_FREE_BYTES, StagingStack } from '../../../scripts/staging/stack.mjs';

const firstPull = JSON.parse(fs.readFileSync(new URL('./fixtures/staging-first-pull.json', import.meta.url), 'utf8'));

// Only the process is a fixture: the real Docker result mapper and capacity parser compose here.
function capacity({ free, labelled = {}, answer }) {
  const commands = [], checks = [];
  const docker = new StagingDocker({ environment: {}, dataVolume: 'aify-comms-staging-0123456789ab-data', timeoutMs: 100,
    execute(_binary, args) {
      commands.push(args);
      if (args.includes('df')) return answer ?? { status: 0, stdout: `Avail\n${free}\n`, stderr: '' };
      assert.ok(args.includes('label=aify.staging.run'), `every listing is filtered to staging resources: ${args.join(' ')}`);
      return { status: 0, stdout: (labelled[args[0]] ?? []).join('\n'), stderr: '' };
    } });
  const session = {
    manifest: { runtimeVersions: { nodeImage: 'node:22.20.0-bookworm' } },
    command(args) { return docker.run(args); },
    requireCommand(args) {
      const result = this.command(args);
      if (result.status !== 0 || result.signal || result.errorCode) throw new Error('Docker disk free space did not complete successfully');
      return result.out;
    },
    check(id, ok, detail) { checks.push({ id, ok, detail }); },
  };
  const run = () => new StagingStack(session).qualifyCapacity();
  return { run, commands, checks };
}

for (const [name, stderr] of [['first-pull run4 output', firstPull.stderr], ['cached-image control', '']]) {
  test(`${name} uses the observed df value, not pull diagnostics`, () => {
    const { run, commands, checks } = capacity({ answer: { ...firstPull, stderr } });
    assert.doesNotThrow(run, 'successful df stdout must survive a successful first image pull');
    assert.deepEqual(checks, [{ id: 'daemon_capacity', ok: true, detail: { freeBytes: 60691738624, leftovers: [] } }]);
    assert.equal(commands.length, 5, 'the capacity read and all four staging-resource listings ran');
  });
}

for (const failure of [{ status: 1 }, { status: null, error: { code: 'ETIMEDOUT' } }, { status: null, signal: 'SIGTERM' }]) {
  test(`an incomplete df command refuses without crediting capacity: ${JSON.stringify(failure)}`, () => {
    const { run, commands, checks } = capacity({ answer: { ...firstPull, ...failure } });
    let refusal;
    try { run(); } catch (error) { refusal = error.message; }
    assert.deepEqual([refusal, checks, commands.length], ['Docker disk free space did not complete successfully', [], 1]);
  });
}

test('numeric stderr cannot rescue malformed df stdout', () => {
  const { run, checks, commands } = capacity({ answer: { status: 0, stdout: 'Avail\nunreadable\n', stderr: firstPull.stdout } });
  let refusal;
  try { run(); } catch (error) { refusal = error.message; }
  assert.deepEqual([refusal, checks, commands.length], ['the Docker disk has NaN GB free; a run needs 40 GB', [], 1]);
});

test('a run refuses to start below 40 GB free, naming what is free', () => {
  const { run, checks } = capacity({ free: MINIMUM_FREE_BYTES - 1 });
  assert.throws(run, /has 40\.0 GB free; a run needs 40 GB/);
  assert.equal(checks.length, 0);
  assert.throws(capacity({ free: 6.2 * 2 ** 30 }).run, /has 6\.2 GB free/);
  assert.throws(capacity({ free: 'unreadable' }).run, /a run needs 40 GB/, 'an unparseable answer refuses');
});

test('a run refuses to start beside another run\'s labelled resources, listing them, and deletes nothing', () => {
  const { run, commands } = capacity({ free: MINIMUM_FREE_BYTES, labelled: {
    images: ['aify-comms-staging-f0fd8f9ee9aa-dependencies:latest 9ead2261a27f'], volume: ['aify-comms-staging-f0fd8f9ee9aa-data'] } });
  assert.throws(run, /remove them first: aify-comms-staging-f0fd8f9ee9aa-data, aify-comms-staging-f0fd8f9ee9aa-dependencies:latest 9ead2261a27f/);
  assert.ok(commands.every(args => !args.includes('rm') && !args.includes('prune')), 'the guard only reads');
});

test('enough space and no labelled leftovers pass, after asking all four resource kinds', () => {
  const { run, commands, checks } = capacity({ free: MINIMUM_FREE_BYTES });
  run();
  assert.deepEqual(commands.filter(args => !args.includes('df')).map(args => args[0]), ['ps', 'volume', 'network', 'images']);
  assert.deepEqual(checks.map(check => [check.id, check.ok]), [['daemon_capacity', true]]);
});
