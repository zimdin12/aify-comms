import assert from 'node:assert/strict';
import test from 'node:test';

const { StagingManifest } = await import('../../../scripts/staging/manifest.mjs');

const fixture = () => ({
  baseline: { comms: '1'.repeat(40), env: '2'.repeat(40), wrapper: '3'.repeat(40) },
  candidate: { comms: '4'.repeat(40), env: '5'.repeat(40), wrapper: '6'.repeat(40) },
  runtimeVersions: { nodeImage: 'node:22.20.0-bookworm', codex: '0.166.0', claude: '2.1.316' },
  seed: 'synthetic-linux-fleet', liveCopyEnabled: false,
});

test('a complete frozen tuple retains exact refs without consulting moving main', () => {
  const input = fixture();
  const spec = new StagingManifest(input);
  assert.deepEqual(spec.baseline, input.baseline);
  assert.deepEqual(spec.candidate, input.candidate);
  assert.deepEqual(spec.runtimeVersions, input.runtimeVersions);
  input.candidate.comms = '7'.repeat(40);
  assert.equal(spec.candidate.comms, '4'.repeat(40));
  assert.ok(Object.isFrozen(spec) && Object.isFrozen(spec.candidate));
});

for (const phase of ['baseline', 'candidate']) {
  for (const repo of Object.keys(fixture()[phase])) {
    for (const ref of [undefined, '', 'main', '0ae107d0', 'z'.repeat(40), `${'1'.repeat(40)}\n`]) {
      test(`${phase}.${repo} refuses incomplete or moving ref ${String(ref)}`, () => {
        const input = fixture();
        input[phase][repo] = ref;
        assert.throws(() => new StagingManifest(input), new RegExp(`${phase}\\.${repo}`));
      });
    }
  }
  test(`${phase} must not silently omit a source tuple`, () => {
    const input = fixture();
    delete input[phase];
    assert.throws(() => new StagingManifest(input), new RegExp(phase));
  });
}

for (const field of Object.keys(fixture().runtimeVersions)) {
  for (const value of [undefined, '', 'latest']) {
    test(`runtimeVersions.${field} requires an explicit qualified version`, () => {
      const input = fixture();
      input.runtimeVersions[field] = value;
      assert.throws(() => new StagingManifest(input), new RegExp(field));
    });
  }
}

for (const [label, change] of [
  ['missing runtimes', { runtimeVersions: null }], ['live-copy seed', { seed: 'live-copy' }],
  ['missing seed', { seed: undefined }], ['live-copy enabled', { liveCopyEnabled: true }],
  ['missing live-copy flag', { liveCopyEnabled: undefined }],
]) {
  test(`${label} refuses before any runner exists`, () => {
    assert.throws(() => new StagingManifest({ ...fixture(), ...change }), /runtimeVersions|synthetic|liveCopyEnabled/);
  });
}
