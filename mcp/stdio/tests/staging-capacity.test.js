import assert from 'node:assert/strict';
import test from 'node:test';

import { MINIMUM_FREE_BYTES, StagingStack } from '../../../scripts/staging/stack.mjs';

// A fake session answers the real queries by argv, so the refusals run through qualifyCapacity's own parsing.
function capacity({ free, labelled = {} }) {
  const commands = [], checks = [];
  const session = {
    manifest: { runtimeVersions: { nodeImage: 'node:22.20.0-bookworm' } },
    requireCommand(args) {
      commands.push(args);
      if (args.includes('df')) return `Avail\n${free}\n`;
      assert.ok(args.includes('label=aify.staging.run'), `every listing is filtered to staging resources: ${args.join(' ')}`);
      return (labelled[args[0]] ?? []).join('\n');
    },
    check(id, ok, detail) { checks.push({ id, ok, detail }); },
  };
  const run = () => new StagingStack(session).qualifyCapacity();
  return { run, commands, checks };
}

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
