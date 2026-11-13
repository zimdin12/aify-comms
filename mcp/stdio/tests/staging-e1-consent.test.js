// E1 reuses the real host starter without accidentally starting the baseline source or losing client-home selection.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { StagingHost } from '../../../scripts/staging/host.mjs';

test('definition commands retain the independently installed client environment', () => {
  const commands = [];
  const session = { layout: { runId: 'fixture' }, requireCommand(args) { commands.push(args); return ''; }, save() {} };
  const host = new StagingHost(session, {});
  host.readDefinitions = () => [];
  const environment = ['PATH=/work/independent/.local/bin:/usr/local/bin:/usr/bin:/bin', 'CODEX_HOME=/work/independent/.codex'];
  host.defineFleet('candidate', environment);
  assert.equal(commands.length, 3);
  for (const args of commands) {
    for (const pair of environment) assert.ok(args.some((arg, index) => arg === '-e' && args[index + 1] === pair), `actual definition omitted ${pair}`);
    assert.ok(args.includes('/src/candidate/env/bin/aify-env.mjs'));
  }
});

for (const phase of ['baseline', 'candidate']) test(`host starter preserves the ${phase} source and explicit client environment`, async () => {
  const commands = [];
  let started = '';
  const session = {
    layout: { project: 'owned-e1-fixture', fixtureKey: 'synthetic' },
    requireCommand(args) {
      commands.push(args);
      if (args.includes('-d')) started = args.at(-1);
      return args.includes('ps') ? `44 S ${started}` : '';
    },
    check(id, ok) { assert.equal(ok, true, id); },
    save() {},
  };
  const stack = { async request() { return { status: 200, body: '{"environments":[]}' }; } };
  const host = new StagingHost(session, stack);
  let calls = 0;
  host.localHealth = () => ++calls === 1 ? { status: 1, out: 'ECONNREFUSED' }
    : { status: 0, out: JSON.stringify({ status: 'healthy', pid: 44, build: 'fixture', codeOnDisk: 'fixture' }) };
  await host.startBaseline(phase, phase === 'candidate' ? ['CODEX_HOME=/work/independent/.codex'] : []);
  assert.ok(started.includes(`/src/${phase}/env/bin/aify-env.mjs`), 'requested source must be the source actually started');
  if (phase === 'candidate') assert.ok(commands.some(args => args.includes('CODEX_HOME=/work/independent/.codex')), 'the actual start must retain the independent Codex home');
});
