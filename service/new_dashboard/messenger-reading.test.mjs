import test from 'node:test';
import assert from 'node:assert/strict';
import { createMessengerReading } from './messenger-reading.mjs';

function fixture(t, history) {
  const frames = [], calls = [], listeners = {};
  const box = { width: 100, height: 100, top: 0, bottom: 100, left: 0, right: 100 };
  const messages = [
    { id: 'visible', from: 'peer', to: 'viewer', read: false },
    { id: 'outside', from: 'peer', to: 'viewer', read: false },
    { id: 'sent', from: 'viewer', to: 'peer', read: false },
  ];
  const timeline = {
    getBoundingClientRect: () => box,
    addEventListener: (name, fn) => { listeners[name] = fn; },
    querySelectorAll: () => messages.map(m => ({ dataset: { id: m.id },
      getBoundingClientRect: () => m.id === 'outside' ? { ...box, top: 200, bottom: 300 } : box })),
  };
  const document = { hasFocus: () => true, visibilityState: 'visible', addEventListener() {} };
  const globals = { document, requestAnimationFrame: fn => frames.push(fn),
    getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
    innerHeight: 100, innerWidth: 100 };
  for (const [key, value] of Object.entries(globals)) {
    const old = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => old ? Object.defineProperty(globalThis, key, old) : delete globalThis[key]);
  }
  const state = { messages, chat: { identity: 'viewer', selected: 'dm:peer', view: 'messenger' } };
  let finish, renders = 0;
  const reading = createMessengerReading({ state, byId: () => timeline, history,
    render: () => renders++, markVisibleRead: (rows, identity) => {
      calls.push({ ids: rows.map(m => m.id), identity });
      return new Promise(resolve => { finish = resolve; });
    } });
  async function flush() {
    for (let i = 0; i < 8; i++) { frames.splice(0).forEach(fn => fn()); await Promise.resolve(); }
  }
  return { reading, state, document, calls, messages, listeners, flush,
    finish: value => finish(value), renders: () => renders };
}

test('createMessengerReading acknowledges only visible incoming rows once per pending request', async t => {
  const f = fixture(t);
  assert.equal(f.reading.visible(), true);
  f.reading.update(); await f.flush();
  assert.deepEqual(f.calls, [{ ids: ['visible'], identity: 'viewer' }]);
  f.listeners.scroll(); await f.flush();
  assert.equal(f.calls.length, 1);
  f.finish(true); await f.flush();
  assert.deepEqual(f.messages.map(m => m.read), [true, false, false]);
  assert.equal(f.renders(), 1);
  f.reading.update(); await f.flush();
  assert.equal(f.calls.length, 1);
});

test('createMessengerReading suppresses Peek and unfocused receipts, then retries refused receipts', async t => {
  const f = fixture(t);
  f.state.chat.peek = true;
  f.reading.update(); await f.flush();
  assert.equal(f.calls.length, 0);
  f.state.chat.peek = false; f.document.hasFocus = () => false;
  f.reading.update(); await f.flush();
  assert.equal(f.reading.visible(), false);
  assert.equal(f.calls.length, 0);
  f.document.hasFocus = () => true;
  f.reading.update(); await f.flush();
  assert.equal(f.calls.length, 1);
  f.finish(false); await f.flush();
  assert.equal(f.messages[0].read, false);
  assert.equal(f.renders(), 0);
  f.reading.update(); await f.flush();
  assert.equal(f.calls.length, 2);
  f.finish(true); await f.flush();
  assert.equal(f.messages[0].read, true);
});

test('createMessengerReading keeps failed history paused across scroll until explicit retry', async t => {
  let failed = true;
  const history = { exhausted: false, complete: true, combined: rows => rows,
    loadOlder: async () => { if (failed) throw Error('history unavailable'); history.exhausted = true; return 0; } };
  const f = fixture(t, history);
  f.state.chat.jumpUnread = true;
  f.reading.update(); await f.flush();
  assert.equal(f.reading.failed, true);
  assert.match(f.reading.notice, /Automatic read receipts are paused/);
  f.listeners.scroll(); await f.flush();
  assert.equal(f.calls.length, 0);
  failed = false;
  f.listeners.click({ target: { closest: () => true } }); await f.flush();
  assert.equal(f.reading.failed, false);
  assert.equal(f.calls.length, 1);
  f.finish(true); await f.flush();
  assert.equal(f.messages[0].read, true);
});
