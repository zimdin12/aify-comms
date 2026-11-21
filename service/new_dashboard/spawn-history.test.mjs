import assert from 'node:assert/strict';
import test from 'node:test';
import { setApiBase } from './api-client.mjs';
import { loadSpawnHistory, renderLifecycleSpawnStatus } from './spawn-history.mjs';
import { state } from './state.mjs';

test('a failed history refresh rejects without replacing the cached rows', async () => {
  const saved = { spawnRequests: state.spawnRequests, spawnRequestsStatus: state.spawnRequestsStatus };
  const rows = [{ id: 'receipt-one', action: 'spawn', status: 'done' }];
  let body = { requests: rows };
  const realFetch = globalThis.fetch;
  setApiBase('');
  try {
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
    await loadSpawnHistory();
    assert.deepEqual(state.spawnRequests, rows);
    assert.equal(state.spawnRequestsStatus, 'ready');
    const cached = state.spawnRequests;

    body = { requests: {} };
    await assert.rejects(loadSpawnHistory(), /Spawn history returned no list/);
    assert.equal(state.spawnRequestsStatus, 'unavailable');
    assert.equal(state.spawnRequests, cached);
  } finally {
    globalThis.fetch = realFetch;
    Object.assign(state, saved);
  }
});

test('a refused spawn receipt shows its own status and escaped outcome', () => {
  const html = renderLifecycleSpawnStatus('refused', '<launcher refused>');
  assert.match(html, /data-status-kind="refused"/);
  assert.match(html, /data-tone="bad"/);
  assert.match(html, /data-status-why="&lt;launcher refused&gt;"/);
  assert.match(html, /<\/span>refused<\/span>$/);
});

test('a future receipt status stays visible without pretending to be a known status', () => {
  const html = renderLifecycleSpawnStatus('awaiting-review');
  assert.match(html, /data-status-kind="unknown"/);
  assert.match(html, /data-tone="muted"/);
  assert.match(html, /<\/span>awaiting-review<\/span>$/);
});
