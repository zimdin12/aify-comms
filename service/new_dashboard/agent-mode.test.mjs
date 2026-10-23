// Definition mode must survive the real roster mapper, rendering and delegated actions.
import assert from 'node:assert/strict';
import test from 'node:test';
import { asAgentArray } from './record-fields.mjs';
import { agentModes } from './agent-mode.mjs';
import { state } from './state.mjs';
import { startOffer } from './agent-click-handlers.mjs';
import { openAgentDrawer } from './agent-drawer.mjs';
import { dispatchClick } from './click-dispatch.mjs';
import { renderModeSwitchChip, renderSessionModeLabel } from './session-rail.mjs';
import { renderSessionConsole } from './session-console.mjs';
import { chooseSessionConsoleWidget } from './console-chooser.js';
import { initMessageActions, mountChatConsole } from './message-actions.mjs';
import { initAgentSessionActions, switchAgentSessionMode } from './agent-session-actions.mjs';
import { _agentSig, renderSection } from './render-memo.mjs';

// The composed cases below cover rendering. This pins fallback metadata that must not govern a start.
test('agentModes ignores withdrawn and absent definition modes without changing its input', () => {
  for (const definition of [undefined, { state: 'defined', mode: null }, { state: 'withdrawn', mode: 'resident' }]) {
    const agent = { id: 'legacy-test', status: 'stopped', sessionMode: 'managed', definition };
    const before = JSON.stringify(agent);
    assert.deepEqual(agentModes(agent), { configuredMode: 'managed', displayMode: 'managed', pendingMode: '', hasLiveSession: false });
    assert.equal(JSON.stringify(agent), before);
  }
});

function page(t, mode = 'managed', registered = 'resident', status = 'stopped') {
  const saved = Object.fromEntries(['document', 'fetch', 'requestAnimationFrame', 'setTimeout'].map(k => [k, globalThis[k]]));
  const previousState = { ...state };
  const made = [], sent = [];
  const element = () => ({ innerHTML: '', dataset: {}, children: [], style: {}, offsetParent: {},
    classList: { add() {}, remove() {}, contains: () => false }, setAttribute() {},
    appendChild(child) { this.children.push(child); }, addEventListener() {}, remove() {},
    querySelector: () => null, querySelectorAll: () => [], contains: () => false });
  const elements = { inspector: element(), 'inspector-content': element() };
  globalThis.document = { getElementById: id => elements[id] || null,
    createElement() { const el = element(); made.push(el); return el; },
    body: { appendChild() {} }, querySelector: () => null, querySelectorAll: () => [] };
  globalThis.requestAnimationFrame = fn => fn();
  globalThis.setTimeout = () => 0;
  globalThis.fetch = async (url, options) => {
    sent.push({ url: String(url), ...options });
    assert.match(String(url), /^\/agents\/mode-test\/(control|session-mode)$/);
    return new Response(JSON.stringify(options.method === 'PATCH'
      ? { request: { machineId: 'test-host', patch: { mode: 'managed' } } }
      : { queued: true, request: { action: 'start', machineId: 'test-host' } }), { status: 200 });
  };
  state.agents = asAgentArray({ agents: { 'mode-test': { runtime: 'claude-code', status,
    sessionMode: registered, definitionState: 'defined', definition: { state: 'defined', mode } } } });
  state.sessions = []; state.environments = []; state.inspector = {}; state.activeXterm = null;
  state.sessionTerminals = new Map();
  t.after(() => { Object.assign(globalThis, saved); Object.assign(state, previousState); });
  return { agent: state.agents[0], elements, sent, made, element,
    text: () => made.map(el => el.textContent || '').join('\n') };
}

// Existing click-handler tests prove refusal/progress behavior. This proves a rendered Start reaches it.
test('a stopped defined-managed resident offers Start through the existing delegated click', async t => {
  const p = page(t);
  const before = JSON.stringify(p.agent);
  assert.equal(startOffer(p.agent).start, true);
  openAgentDrawer(p.agent.id);
  const html = p.elements['inspector-content'].innerHTML;
  const start = /<button[^>]*data-agent-action="start"[^>]*data-agent-id="([^"]+)"[^>]*>Start<\/button>/.exec(html);
  assert.ok(start, 'the mapped agent must render a Start button');
  assert.match(html, /<dt>Mode<\/dt><dd>managed<\/dd>/);
  const button = { dataset: { agentAction: 'start', agentId: start[1] }, textContent: 'Start', disabled: false };
  dispatchClick({ target: { closest: selector => selector === '[data-agent-action="start"]' ? button : null } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.sent.length, 1);
  assert.equal(p.sent[0].method, 'POST');
  assert.equal(p.sent[0].url, '/agents/mode-test/control');
  assert.deepEqual(JSON.parse(p.sent[0].body), { action: 'start', from_agent: 'dashboard' });
  assert.equal(JSON.stringify(p.agent), before, 'display and Start must not rewrite runtime mode');
});

test('a resident definition refuses Start despite a managed registration and offers the opposite switch', t => {
  const p = page(t, 'resident', 'managed');
  assert.equal(startOffer(p.agent).start, false);
  openAgentDrawer(p.agent.id);
  assert.match(p.elements['inspector-content'].innerHTML, /<dt>Mode<\/dt><dd>resident<\/dd>/);
  assert.match(renderModeSwitchChip(p.agent), /data-target-mode="managed"/);
});

test('a live resident keeps its mode and console route while showing the next managed start', t => {
  const p = page(t, 'managed', 'resident', 'online');
  const session = { id: 's-test', agentId: p.agent.id, mode: 'resident', ownerMode: 'resident', status: 'running' };
  state.sessions = [session];
  assert.equal(startOffer(p.agent, state.sessions).start, false);
  openAgentDrawer(p.agent.id);
  assert.match(p.elements['inspector-content'].innerHTML, /<dt>Mode<\/dt><dd>resident/);
  assert.match(p.elements['inspector-content'].innerHTML, /next start: managed/);
  assert.match(renderSessionModeLabel(p.agent), /resident.*next start: managed/);
  const host = p.element();
  renderSessionConsole(session, host, {}, { mountXtermForTerminal() { assert.fail('a resident must not become a managed console'); }, refresh() {}, resyncActiveConsole() {} });
  assert.match(host.innerHTML, /data-kind="console-resident"/);
  assert.equal(p.agent.sessionMode, 'resident');
  assert.equal(chooseSessionConsoleWidget({ agent: p.agent, sessionId: session.id, sessionMode: p.agent.sessionMode,
    sessionStatus: session.status, runtime: p.agent.runtime, runtimeConfig: {}, cache: {} }).kind, 'none');
});

test('a live session with a stale stopped status still withholds Start', t => {
  const p = page(t);
  state.sessions = [{ id: 's-test', agentId: p.agent.id, ownerMode: 'resident', mode: 'resident', status: 'running' }];
  assert.equal(startOffer(p.agent, state.sessions).start, false);
  openAgentDrawer(p.agent.id);
  assert.doesNotMatch(p.elements['inspector-content'].innerHTML, /data-agent-action="start"/);
});

test('definition-only changes invalidate the render memo in both directions', t => {
  const p = page(t);
  let renders = 0;
  const render = () => renders++;
  renderSection('task9-mode', _agentSig(), render);
  p.agent.definition.mode = 'resident';
  renderSection('task9-mode', _agentSig(), render);
  p.agent.definition.mode = 'managed';
  renderSection('task9-mode', _agentSig(), render);
  assert.equal(renders, 3);
});

test('a cold Chat follows definition mode without inventing a session', t => {
  const p = page(t, 'resident');
  const host = p.element();
  mountChatConsole(p.agent.id, host);
  assert.match(host.innerHTML, /Resident agent/);
  assert.doesNotMatch(host.innerHTML, /data-agent-action="start"/);
  p.agent.definition.mode = 'managed';
  mountChatConsole(p.agent.id, host);
  assert.match(host.innerHTML, /data-agent-action="start"/, 'a cold defined-managed agent still has no Start in Chat');
  p.agent.definition.mode = 'resident';
  mountChatConsole(p.agent.id, host);
  assert.match(host.innerHTML, /Resident agent/);
  assert.doesNotMatch(host.innerHTML, /data-agent-action="start"/);
  assert.equal(state.sessions.length, 0);
  assert.equal(p.agent.sessionMode, 'resident');
});

test('a definition-only update reaches the retained Chat console and reverses its mode chip', t => {
  const p = page(t, 'resident', 'resident', 'online');
  state.sessions = [{ id: 's-test', agentId: p.agent.id, mode: 'resident', ownerMode: 'resident', status: 'running' }];
  initMessageActions({ chatController: {}, refreshSoon() {},
    renderSessionConsole: (s, el, opts) => renderSessionConsole(s, el, opts, {
      mountXtermForTerminal() { assert.fail('a resident must not acquire a managed console'); }, refresh() {}, resyncActiveConsole() {},
    }) });
  const host = p.element();
  mountChatConsole(p.agent.id, host);
  assert.match(host.innerHTML, /data-target-mode="managed"/, 'control: resident initially offers managed');
  p.agent.definition.mode = 'managed';
  mountChatConsole(p.agent.id, host);
  assert.match(host.innerHTML, /data-target-mode="resident"/, 'the retained console kept its old mode chip');
  assert.match(host.innerHTML, /resident.*next start: managed/);
  assert.equal(p.agent.sessionMode, 'resident');
});

test('a definition-mode update repaints a live managed console without remounting its terminal', t => {
  const p = page(t, 'managed', 'managed', 'online');
  state.sessions = [{ id: 's-test', agentId: p.agent.id, mode: 'managed-warm', ownerMode: 'managed', status: 'running', terminalId: 't-test' }];
  let mounts = 0, bar = '';
  const host = p.element(), container = { id: 'xterm-t-test' };
  host.contains = el => el === container;
  host.querySelector = selector => selector === '.xterm-host' ? container
    : selector === '.session-actions-bar' ? Object.defineProperty({}, 'outerHTML', { set(value) { bar = value; } }) : null;
  initMessageActions({ chatController: {}, refreshSoon() {},
    renderSessionConsole: (s, el, opts) => renderSessionConsole(s, el, opts, {
      mountXtermForTerminal() { mounts++; return Promise.resolve(); }, refresh() {}, resyncActiveConsole() {},
    }) });
  mountChatConsole(p.agent.id, host);
  assert.equal(mounts, 1, 'control: a real render requested the initial terminal mount');
  const terminal = { terminalId: 't-test', container };
  state.activeXterm = terminal;
  // Auto-attach fills the cache on the first render. Stabilize that independent trigger first.
  mountChatConsole(p.agent.id, host);
  const steadySig = host.dataset.consoleSig;
  mountChatConsole(p.agent.id, host);
  assert.equal(host.dataset.consoleSig, steadySig);
  assert.equal(mounts, 1);
  const markup = host.innerHTML;
  p.agent.definition.mode = 'resident';
  mountChatConsole(p.agent.id, host);
  assert.match(bar, /managed.*next start: resident/, 'the definition update did not reach the mounted console');
  assert.equal(mounts, 1);
  assert.equal(host.innerHTML, markup);
  assert.equal(state.activeXterm, terminal);
});

test('the queued mode-switch toast describes a definition request without changing the running mode', async t => {
  const p = page(t, 'resident', 'resident', 'online');
  initAgentSessionActions({ chatController: { render() {} }, closeInspector() {}, markConversationRead() {},
    refresh() {}, refreshSoon() {}, renderSessionWorkspace() {}, setPage() {} });
  await switchAgentSessionMode(p.agent.id, 'managed');
  assert.equal(p.sent.length, 1);
  assert.deepEqual(JSON.parse(p.sent[0].body), { mode: 'managed', force: false, requestedBy: 'dashboard' });
  assert.match(p.text(), /requested in aify-env/);
  assert.match(p.text(), /definition/);
  assert.match(p.text(), /running session keeps its current mode until it stops/);
  assert.match(p.text(), /Start launches managed/);
  assert.doesNotMatch(p.text(), /Switched/);
  assert.equal(p.agent.sessionMode, 'resident');
  assert.equal(p.agent.definition.mode, 'resident', 'a queued request is not an applied definition');
});
