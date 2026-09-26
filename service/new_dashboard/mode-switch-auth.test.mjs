import assert from 'node:assert/strict';
import test from 'node:test';
import { setApiBase, setOperatorKey } from './api-client.mjs';
import { resetAdoptionForTests, writeApiKey } from './api-key.mjs';
import { PROMPT_ID } from './api-key-prompt.mjs';
import { initAgentSessionActions, switchAgentSessionMode } from './agent-session-actions.mjs';
import { uploadPastedImage } from './shared-files.mjs';
import { state } from './state.mjs';

// Real actions, request owner, key store and dialogs. Only browser I/O is fake.
function harness(t, replies, confirm = true) {
  const saved = Object.fromEntries(['document', 'localStorage', 'fetch', 'requestAnimationFrame', 'setTimeout'].map(k => [k, globalThis[k]]));
  const store = new Map();
  const sent = [], dialogs = [], nodes = [];
  let refreshes = 0, renders = 0;
  const element = () => {
    const listeners = new Map();
    const el = { children: [], style: {}, classList: { add() {}, remove() {} },
      setAttribute() {}, focus() {}, remove() {}, querySelectorAll: () => [],
      addEventListener: (event, fn) => listeners.set(event, fn),
      appendChild: child => el.children.push(child),
    };
    const buttons = new Map();
    el.querySelector = sel => {
      if (sel === '.dialog-input') return null;
      if (!buttons.has(sel)) buttons.set(sel, element());
      return buttons.get(sel);
    };
    el.answer = () => buttons.get(confirm ? '.dialog-confirm' : '.dialog-cancel').fire();
    el.fire = () => listeners.get('click')();
    return el;
  };
  globalThis.document = {
    createElement: element, getElementById: id => nodes.find(n => n.id === id) || (id === PROMPT_ID ? null : element()),
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, removeEventListener() {},
    body: { appendChild(el) { nodes.push(el); if (el.className === 'dialog-overlay') { dialogs.push(el); queueMicrotask(el.answer); } } },
  };
  globalThis.localStorage = { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) };
  globalThis.requestAnimationFrame = fn => fn();
  globalThis.setTimeout = () => 0;
  setApiBase('https://synthetic.invalid/api/v1');
  setOperatorKey('synthetic-operator', 'https://synthetic.invalid');
  resetAdoptionForTests();
  writeApiKey('synthetic-service', 'https://synthetic.invalid');
  globalThis.fetch = async (url, options) => {
    sent.push({ url, ...options });
    assert.ok(replies.length, 'unexpected request, never use a real network');
    const reply = replies.shift();
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status });
  };
  state.agents = [{ id: 'coder /one', sessionMode: 'resident' }];
  state.sessions = [];
  initAgentSessionActions({ chatController: { render() { renders++; } }, closeInspector() {},
    markConversationRead() {}, refresh() {},
    refreshSoon() { refreshes++; }, renderSessionWorkspace() {}, setPage() {} });
  t.after(() => { Object.assign(globalThis, saved); setOperatorKey(''); setApiBase(''); resetAdoptionForTests(); });
  // What the operator is told: toasts land in a host appended to the body (ui.js).
  const toasts = () => nodes.flatMap((n) => n.children || []).map((c) => c.textContent).filter(Boolean);
  return { sent, dialogs, toasts, nodes, store, refreshes: () => refreshes, renders: () => renders };
}
function requestIsAuthenticated(request, force = false) {
  assert.equal(request.url, 'https://synthetic.invalid/api/v1/agents/coder%20%2Fone/session-mode');
  assert.equal(request.method, 'PATCH');
  assert.equal(request.body, JSON.stringify({ mode: 'managed', force, requestedBy: 'dashboard' }));
  assert.deepEqual(request.headers, { 'Content-Type': 'application/json', 'X-Aify-Operator-Key': 'synthetic-operator', 'X-API-Key': 'synthetic-service' });
}
const success = () => ({ status: 200, body: { mode: 'managed', agent: { status: 'available', sessionMode: 'managed' } } });
const conflict = () => ({ status: 409, body: { detail: 'Active run synthetic-run' } });

test('mode switch sends both stored credentials with the exact payload and applies success', async t => {
  const h = harness(t, [success()]);
  const answer = await switchAgentSessionMode('coder /one', 'managed');
  requestIsAuthenticated(h.sent[0]);
  assert.equal(h.sent.length, 1);
  assert.equal(answer.mode, 'managed');
  assert.equal(state.agents[0].status, 'available');
  assert.equal(h.refreshes(), 1);
  assert.equal(h.renders(), 1);
  assert.equal(h.dialogs.length, 0);
});
test('401 prompts for a key without forcing, retrying, or painting success', async t => {
  const h = harness(t, [{ status: 401, body: { detail: 'Invalid key' } }]);
  assert.equal(await switchAgentSessionMode('coder /one', 'managed'), null);
  assert.ok(h.nodes.some(n => n.id === PROMPT_ID), 'the real key prompt must mount');
  requestIsAuthenticated(h.sent[0]);
  assert.equal(h.store.has('aify.apiKey@https://synthetic.invalid'), false);
  assert.equal(h.sent.length, 1);
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.refreshes(), 0);
  assert.equal(state.agents[0].sessionMode, 'resident');
  assert.ok(h.toasts().includes('Mode switch failed: Invalid key'), `told: ${h.toasts()}`);
});
for (const confirmed of [false, true]) test(`409 ${confirmed ? 'confirmation' : 'cancellation'} preserves force consent and credentials`, async t => {
  const h = harness(t, [conflict(), success()], confirmed);
  const answer = await switchAgentSessionMode('coder /one', 'managed');
  assert.equal(h.dialogs.length, 1);
  assert.match(h.dialogs[0].innerHTML, /Active run synthetic-run/);
  assert.match(h.dialogs[0].innerHTML, /Force the switch to managed anyway/);
  assert.equal(h.sent.length, confirmed ? 2 : 1);
  requestIsAuthenticated(h.sent[0]);
  if (confirmed) { requestIsAuthenticated(h.sent[1], true); assert.equal(answer.mode, 'managed'); }
  else { assert.equal(answer, null); assert.equal(state.agents[0].sessionMode, 'resident'); }
  assert.equal(h.refreshes(), confirmed ? 1 : 0);
});
test('a forced 409 stops after one confirmed retry', async t => {
  const h = harness(t, [conflict(), conflict()]);
  assert.equal(await switchAgentSessionMode('coder /one', 'managed'), null);
  assert.equal(h.sent.length, 2);
  requestIsAuthenticated(h.sent[1], true);
  assert.equal(h.dialogs.length, 1);
  assert.ok(h.toasts().includes('Mode switch failed: Active run synthetic-run'), `told: ${h.toasts()}`);
  assert.equal(h.refreshes(), 0);
});
test('network failure is reported without retry or optimistic state', async t => {
  const h = harness(t, [new TypeError('synthetic network down')]);
  assert.equal(await switchAgentSessionMode('coder /one', 'managed'), null);
  requestIsAuthenticated(h.sent[0]);
  assert.equal(h.sent.length, 1);
  assert.equal(h.dialogs.length, 0);
  assert.ok(h.toasts().includes('Mode switch failed: synthetic network down'), `told: ${h.toasts()}`);
  assert.equal(state.agents[0].sessionMode, 'resident');
  assert.equal(h.refreshes(), 0);
});
test('pasted image uses both credentials without a JSON content type', async t => {
  const h = harness(t, [{ status: 200, body: { ok: true } }]);
  const target = { value: '', dispatchEvent() {}, focus() {} };
  await uploadPastedImage(new Blob(['fake image'], { type: 'image/png' }), target);
  assert.equal(h.sent[0].url, 'https://synthetic.invalid/api/v1/shared');
  assert.equal(h.sent[0].method, 'POST');
  assert.deepEqual(h.sent[0].headers, { 'X-Aify-Operator-Key': 'synthetic-operator', 'X-API-Key': 'synthetic-service' });
  assert.ok(h.sent[0].body instanceof FormData);
  assert.equal(h.sent[0].body.get('from_agent'), 'dashboard');
  assert.equal(h.sent[0].body.get('description'), 'Pasted image from Dashboard Next');
  assert.equal(await h.sent[0].body.get('file').text(), 'fake image');
  assert.match(target.value, /\[image:/);
});
