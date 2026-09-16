// Loading one part of the dashboard, tested by CALLING the loaders against a stubbed `fetch`.

import assert from 'node:assert/strict';
import test from 'node:test';

import { setApiBase } from './api-client.mjs';
import { state } from './state.mjs';
import { loadSlices, sliceIsWanted } from './slice-loaders.mjs';

async function withStubs({ reject = [], pages = {} } = {}, run) {
  const saved = { document: globalThis.document, fetch: globalThis.fetch, state: { ...state } };
  const requested = [];
  globalThis.document = {
    getElementById: (id) => {
      const page = id.startsWith('page-') ? pages[id.slice('page-'.length)] : undefined;
      if (page) return { classList: { contains: (c) => c === 'active' && page === 'open' } };
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  globalThis.fetch = async (url) => {
    const path = String(url).replace(/^https?:\/\/[^/]*/, '');
    requested.push(path);
    if (reject.some((p) => path.startsWith(p))) throw new TypeError('Failed to fetch');
    const body = path.startsWith('/agents') ? { agents: [{ id: 'fresh' }] }
      : path.startsWith('/environments') ? { environments: [{ id: 'env-fresh' }] } : {};
    return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(body) };
  };
  setApiBase('');
  Object.assign(state, { agents: [{ id: 'old' }], environments: [{ id: 'env-old' }], loaded: false });
  const calls = { evaluateFlowGates: 0, renderAll: 0, refreshOpenInspector: 0 };
  const deps = {
    evaluateFlowGates: () => { calls.evaluateFlowGates += 1; },
    renderAll: () => { calls.renderAll += 1; },
    refreshOpenInspector: () => { calls.refreshOpenInspector += 1; },
  };
  try {
    return await run({ requested, calls, deps });
  } finally {
    globalThis.document = saved.document;
    globalThis.fetch = saved.fetch;
    Object.assign(state, saved.state);
  }
}

test('only the named slices are fetched, and the page repaints once', async () => {
  await withStubs({}, async ({ requested, calls, deps }) => {
    const failed = await loadSlices(['agents', 'environments'], deps);
    assert.deepEqual(failed, []);
    assert.deepEqual(requested.sort(), ['/agents', '/environments']);
    assert.deepEqual(state.agents.map((a) => a.id), ['fresh']);
    assert.equal(state.loaded, true);
    assert.deepEqual(calls, { evaluateFlowGates: 1, renderAll: 1, refreshOpenInspector: 1 });
  });
});

test('a failed slice is reported and keeps its last-good value', async () => {
  await withStubs({ reject: ['/agents'] }, async ({ calls, deps }) => {
    const failed = await loadSlices(['agents', 'environments'], deps);
    assert.deepEqual(failed, ['agents']);
    assert.deepEqual(state.agents.map((a) => a.id), ['old'], 'a failed fetch blanked the roster');
    assert.deepEqual(state.environments.map((e) => e.id), ['env-fresh']);
    assert.equal(calls.renderAll, 1, 'what did load was not painted');
  });
});

test('nothing loaded, nothing repainted', async () => {
  await withStubs({ reject: ['/agents'] }, async ({ calls, deps }) => {
    assert.deepEqual(await loadSlices(['agents'], deps), ['agents']);
    assert.equal(calls.renderAll, 0);
  });
});

test('a slice for a closed page is not fetched, as the full cycle decides', async () => {
  await withStubs({ pages: { files: 'closed', environments: 'open' } }, async ({ requested, deps }) => {
    assert.equal(sliceIsWanted('files'), false);
    assert.equal(sliceIsWanted('spawnRequests'), true);
    assert.equal(sliceIsWanted('agents'), true);
    state.chat = { ...(state.chat || {}), selected: 'dm:someone' };
    assert.equal(sliceIsWanted('conversation'), false, 'a DM conversation is not a channel conversation');
    state.chat = { ...state.chat, selected: 'channel:room' };
    assert.equal(sliceIsWanted('conversation'), true);
    await loadSlices(['files'], deps);
    assert.deepEqual(requested, [], 'a closed page was fetched');
  });
});
