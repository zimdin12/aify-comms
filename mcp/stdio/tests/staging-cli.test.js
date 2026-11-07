import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { dockerEnvironment, StagingDocker } from '../../../scripts/staging/docker.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const entry = path.join(root, 'scripts', 'staging', 'main.mjs');
const volume = 'aify-comms-staging-0123456789ab-data';
const manifest = () => ({
  baseline: { comms: '1'.repeat(40), env: '2'.repeat(40), wrapper: '3'.repeat(40) },
  candidate: { comms: '4'.repeat(40), env: '5'.repeat(40), wrapper: '6'.repeat(40) },
  runtimeVersions: { nodeImage: 'node:22.20.0-bookworm', codex: '0.166.0', claude: '2.1.316' },
  seed: 'synthetic-linux-fleet', liveCopyEnabled: false,
});

function invoke({ input = manifest(), content, preflight = true, shell = false } = {}) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-cli-'));
  try {
    const refs = path.join(temporary, 'refs.json');
    fs.writeFileSync(refs, content ?? JSON.stringify(input));
    const args = ['E4', '--refs', refs, ...(preflight ? ['--preflight'] : [])];
    const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
      ['PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'].includes(name.toUpperCase())));
    for (const name of ['HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'TMPDIR']) env[name] = temporary;
    Object.assign(env, { MSYS_NO_PATHCONV: '1', OPENAI_API_KEY: 'fixture-host-secret-never-serialize' });
    const result = spawnSync(shell ? 'bash' : process.execPath,
      shell ? ['scripts/staging-e2e.sh', ...args] : [entry, ...args],
      { cwd: root, env, encoding: 'utf8', timeout: 15_000 });
    assert.equal(result.error, undefined, result.error?.message);
    return { status: result.status, out: `${result.stdout}${result.stderr}` };
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

for (const shell of [false, true]) {
  test(`real ${shell ? 'shell' : 'Node'} entry checks inputs without claiming a switch`, () => {
    const result = invoke({ shell });
    assert.equal(result.status, 0, result.out);
    assert.match(result.out, /PASS manifest_syntax/);
    assert.match(result.out, /NOT_RUN switch: preflight/);
    assert.doesNotMatch(result.out, /PASS E4|fixture-host-secret-never-serialize/);
  });
}

test('the real entry refuses incomplete version inputs instead of filling from this host', () => {
  const result = invoke({ input: { ...manifest(), runtimeVersions: null } });
  assert.notEqual(result.status, 0);
  assert.match(result.out, /runtimeVersions/);
  assert.doesNotMatch(result.out, /PASS E4/);
});

test('an unavailable or unconfigured scenario never becomes a successful preflight placeholder', () => {
  const result = invoke({ preflight: false });
  assert.notEqual(result.status, 0, result.out);
  assert.doesNotMatch(result.out, /PASS E4/);
});

test('the Docker process environment drops live carriers and credentials but retains OS plumbing', () => {
  const result = dockerEnvironment({ PATH: 'fixture-bin', SystemRoot: 'C:/Windows',
    AIFY_AGENT_ID: 'live-agent', OPENAI_API_KEY: 'fixture-credential', DATA_VOLUME_NAME: 'aify-comms-data' }, volume);
  assert.equal(result.PATH, 'fixture-bin');
  assert.equal(result.SystemRoot, 'C:/Windows');
  assert.equal(result.DATA_VOLUME_NAME, volume);
  assert.equal(result.AIFY_AGENT_ID, undefined);
  assert.equal(result.OPENAI_API_KEY, undefined);
});

test('a malformed refs file is refused without echoing its credential-like bytes', () => {
  const result = invoke({ content: 'CREDENTIALX' });
  assert.notEqual(result.status, 0);
  assert.doesNotMatch(result.out, /CREDENTIALX/);
  assert.match(result.out, /manifest could not be read as JSON/);
});

test('a later successful inspection cannot settle an earlier timed-out Docker command', () => {
  let calls = 0;
  const docker = new StagingDocker({ environment: {}, dataVolume: volume, timeoutMs: 100,
    execute: () => ++calls === 1 ? { status: null, error: { code: 'ETIMEDOUT' } } : { status: 0 } });
  docker.run(['build', '.']);
  assert.equal(docker.run(['image', 'inspect', 'fixture']).status, 0, 'control: later inspection did not succeed');
  assert.equal(docker.settlement().settled, false);
});

// The live volume and Compose's default are the same name; undefined covers an omitted value.
for (const [label, dataVolume] of [['live/default', 'aify-comms-data'], ['empty', ''], ['undefined', undefined]]) {
  test(`dockerEnvironment refuses ${label} DATA_VOLUME_NAME`, () => {
    assert.throws(() => dockerEnvironment({}, dataVolume), /DATA_VOLUME_NAME/);
  });
  test(`StagingDocker refuses ${label} DATA_VOLUME_NAME`, () => {
    assert.throws(() => new StagingDocker({ environment: {}, dataVolume, timeoutMs: 100 }), /DATA_VOLUME_NAME/);
  });
}

test('direct Docker execution retains status, uses a timeout and copies argv', () => {
  const calls = [];
  const docker = new StagingDocker({ environment: { PATH: 'fixture-bin' }, dataVolume: volume, timeoutMs: 100,
    execute: (...args) => { calls.push(args); return { status: 0, stdout: '[]', stderr: '' }; } });
  const args = ['volume', 'inspect', volume];
  const answer = docker.run(args);
  args.push('changed-later');
  assert.equal(answer.status, 0);
  assert.equal(answer.out, '[]');
  assert.deepEqual(calls[0][1], ['volume', 'inspect', volume]);
  assert.equal(calls[0][2].timeout, 100);
  assert.equal(docker.settlement().settled, true);
});

for (const failure of [
  { status: null, error: { code: 'ETIMEDOUT' } },
  {},
]) {
  test(`missing/timeout Docker status cannot become settled: ${JSON.stringify(failure)}`, () => {
    const docker = new StagingDocker({ environment: {}, dataVolume: volume, timeoutMs: 100, execute: () => failure });
    docker.run(['info']);
    assert.equal(docker.settlement().settled, false);
  });
}

test('an executor exception is also unsettled, not an acknowledgement of completion', () => {
  const docker = new StagingDocker({ environment: {}, dataVolume: volume, timeoutMs: 100,
    execute: () => { throw new Error('fixture spawn failure'); } });
  assert.throws(() => docker.run(['info']));
  assert.equal(docker.settlement().settled, false);
});
