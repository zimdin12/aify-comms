import assert from 'node:assert/strict';
import { test } from 'node:test';

import * as terminalInput from './terminal-input.mjs';

test('waitForTerminalSize waits until the bridge-applied dimensions are visible', async () => {
  assert.equal(typeof terminalInput.waitForTerminalSize, 'function');
  const sizes = [{ cols: 154, rows: 24 }, { cols: 153, rows: 24 }];
  let reads = 0;

  await terminalInput.waitForTerminalSize({
    cols: 153,
    rows: 24,
    readSize: async () => sizes[Math.min(reads++, sizes.length - 1)],
    delay: async () => {},
  });

  assert.equal(reads, 2);
});

test('forceTerminalRepaint changes width before restoring it', async () => {
  assert.equal(typeof terminalInput.forceTerminalRepaint, 'function');
  const calls = [];

  await terminalInput.forceTerminalRepaint({
    cols: 154,
    rows: 24,
    resize: async (cols, rows) => calls.push([cols, rows]),
    waitForSize: async (cols, rows) => calls.push(['wait', cols, rows]),
  });

  assert.deepEqual(calls, [
    [153, 24], ['wait', 153, 24],
    [154, 24], ['wait', 154, 24],
  ]);
});

test('forceTerminalRepaint still changes width at the minimum column count', async () => {
  const calls = [];

  await terminalInput.forceTerminalRepaint({
    cols: 20,
    rows: 5,
    resize: async (cols, rows) => calls.push([cols, rows]),
    waitForSize: async (cols, rows) => calls.push(['wait', cols, rows]),
  });

  assert.deepEqual(calls, [
    [21, 5], ['wait', 21, 5],
    [20, 5], ['wait', 20, 5],
  ]);
});
