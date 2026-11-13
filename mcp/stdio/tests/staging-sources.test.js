import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { StagingManifest } from '../../../scripts/staging/manifest.mjs';
import { StagingSources } from '../../../scripts/staging/sources.mjs';
import { dockerEnvironment } from '../../../scripts/staging/docker.mjs';

const volume = 'aify-comms-staging-0123456789ab-data';
function world(work) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-sources-'));
  const env = { ...dockerEnvironment(process.env, volume), HOME: root, USERPROFILE: root,
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: path.join(root, 'empty-config'),
    GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'core.hooksPath', GIT_CONFIG_VALUE_0: path.join(root, 'hooks') };
  fs.writeFileSync(env.GIT_CONFIG_GLOBAL, '');
  fs.mkdirSync(env.GIT_CONFIG_VALUE_0);
  const repositories = {}, baseline = {}, candidate = {};
  function gitAt(directory, ...args) {
    const result = spawnSync('git', ['-C', directory, ...args], { env, encoding: 'utf8', timeout: 15_000 });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    return result.stdout.trim();
  }
  try {
    for (const role of ['comms', 'env', 'wrapper']) {
      const directory = repositories[role] = path.join(root, role);
      fs.mkdirSync(directory);
      const git = (...args) => gitAt(directory, ...args);
      git('init', '--quiet');
      git('remote', 'add', 'origin', `https://github.com/fixture/${role}.git`);
      fs.mkdirSync(path.join(directory, 'mcp', 'stdio'), { recursive: true });
      for (const entry of ['server.js', 'claude-channel.js']) fs.writeFileSync(path.join(directory, 'mcp', 'stdio', entry), '// fixture bridge entry\n');
      for (const [phase, tuple] of [['baseline', baseline], ['candidate', candidate]]) {
        fs.writeFileSync(path.join(directory, 'tracked.txt'), `${role} ${phase}\n`);
        git('add', 'tracked.txt', 'mcp/stdio');
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', phase);
        tuple[role] = git('rev-parse', 'HEAD');
      }
    }
    const manifest = new StagingManifest({ baseline, candidate,
      runtimeVersions: { nodeImage: 'node:22.20.0-bookworm', claude: '2.1.316', codex: '0.166.0' },
      seed: 'synthetic-linux-fleet', liveCopyEnabled: false });
    work({ root, repositories, gitAt, manifest });
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

function sources({ root, repositories, manifest }, extra = {}) {
  return new StagingSources({ manifest, repositories, directory: path.join(root, 'snapshots'),
    dataVolume: volume, environment: process.env, ...extra });
}

function assertSnapshots(snapshots, manifest, inspect) {
  for (const [phase, tuple] of Object.entries({ baseline: manifest.baseline, candidate: manifest.candidate })) {
    for (const role of Object.keys(tuple)) inspect(snapshots[phase][role], role, phase);
  }
}

test('exact snapshots use each role and phase, exclude dirty bytes and preserve the input', () => world(context => {
  const { repositories, manifest, gitAt } = context;
  const input = path.join(repositories.comms, 'tracked.txt');
  fs.writeFileSync(input, 'dirty caller data\n');
  const before = gitAt(repositories.comms, 'status', '--porcelain');
  const reader = sources(context);
  const snapshots = reader.prepare();
  assertSnapshots(snapshots, manifest, (directory, role, phase) => {
    assert.equal(fs.readFileSync(path.join(directory, 'tracked.txt'), 'utf8'), `${role} ${phase}\n`);
    assert.ok(fs.existsSync(path.join(directory, '.git', 'shallow')), 'snapshot brought reachable history');
  });
  assert.equal(gitAt(repositories.comms, 'status', '--porcelain'), before);
  assert.equal(fs.readFileSync(input, 'utf8'), 'dirty caller data\n');
  assert.ok(reader.receipts.some(item => item.command.includes('fetch') && item.status === 0));
}));

test('the actual stamp binds a snapshot artifact rather than silently declaring it pending', () => world(context => {
  const snapshots = sources(context).prepare();
  const stamp = (repository, output) => {
    const run = spawnSync(process.execPath, [fileURLToPath(new URL('../service-spec-cli.mjs', import.meta.url)),
      repository, context.manifest.candidate.comms, '0.0.0-fixture', output],
    { env: dockerEnvironment(process.env, volume), encoding: 'utf8', timeout: 15_000 });
    assert.equal(run.status, 0, run.stderr);
    return JSON.parse(fs.readFileSync(output, 'utf8'));
  };
  const original = stamp(context.repositories.comms, path.join(context.root, 'original-stamp.json'));
  assert.equal(original.spec.artifacts[0].source.pending, undefined, 'known-origin control must bind');
  const copy = stamp(snapshots.candidate.comms, path.join(context.root, 'snapshot-stamp.json'));
  assert.equal(copy.spec.artifacts[0].source.pending, undefined, 'snapshot must retain the real origin before stamping');
  assert.deepEqual(copy.spec.artifacts, original.spec.artifacts);
  assert.deepEqual(copy.manifests, original.manifests);
  context.gitAt(snapshots.candidate.comms, 'remote', 'remove', 'origin');
  const absent = stamp(snapshots.candidate.comms, path.join(context.root, 'absent-stamp.json'));
  assert.equal(absent.spec.artifacts[0].source.pending, 'the build checkout has no origin remote');
  assert.deepEqual(absent.manifests, {});
}));

test('untracked credential-like input is excluded while tracked source is materialized', () => world(context => {
  for (const directory of Object.values(context.repositories)) fs.writeFileSync(path.join(directory, '.env'), 'fixture-credential-never-copy');
  const snapshots = sources(context).prepare();
  assertSnapshots(snapshots, context.manifest, (directory, role, phase) => {
    assert.equal(fs.existsSync(path.join(directory, '.env')), false);
    assert.equal(fs.readFileSync(path.join(directory, 'tracked.txt'), 'utf8'), `${role} ${phase}\n`);
  });
  for (const directory of Object.values(context.repositories)) assert.equal(fs.readFileSync(path.join(directory, '.env'), 'utf8'), 'fixture-credential-never-copy');
}));

for (const origin of ['https://fixture:synthetic@github.com/fixture/comms.git', 'https://example.invalid/fixture/comms.git']) {
  test('a noncanonical or credential-bearing origin is refused before snapshot fetch', () => world(context => {
    context.gitAt(context.repositories.comms, 'remote', 'set-url', 'origin', origin);
    const reader = sources(context);
    assert.throws(() => reader.prepare(), /canonical credential-free GitHub origin/);
    assert.equal(reader.receipts.some(item => item.command.includes('fetch')), false);
    assert.equal(context.gitAt(context.repositories.comms, 'remote', 'get-url', 'origin'), origin);
  }));
}

for (const phase of ['baseline', 'candidate']) {
  test(`an unresolved ${phase} commit is refused before any snapshot fetch`, () => world(context => {
    const manifest = new StagingManifest({ ...context.manifest,
      [phase]: { ...context.manifest[phase], env: 'f'.repeat(40) } });
    const reader = sources(context, { manifest });
    assert.throws(() => reader.prepare(), /Git source operation failed/);
    assert.equal(reader.receipts.some(item => item.command.includes('fetch')), false);
    assert.ok(reader.receipts.some(item => item.command.includes('cat-file') && item.status === 0), 'healthy commit was never resolved');
  }));
}

test('a pre-existing output directory is refused without changing its contents', () => world(context => {
  const reader = sources(context);
  fs.mkdirSync(reader.directory);
  fs.writeFileSync(path.join(reader.directory, 'sentinel'), 'keep');
  assert.throws(() => reader.prepare(), /EEXIST/);
  assert.equal(fs.readFileSync(path.join(reader.directory, 'sentinel'), 'utf8'), 'keep');
  assert.equal(reader.receipts.length, 0);
}));
