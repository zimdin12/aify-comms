// The dashboard's copy of the service key: what it stores, what it attaches, and what it does when
// the browser refuses to store anything at all.
//
// EVERY ACCESS IS GUARDED FOR A REASON THAT IS NOT THEORETICAL. `localStorage` throws outright when
// a browser is set to block site data, and this module is imported at load by `api-client`, so an
// unguarded read would take the whole dashboard down instead of degrading to "no key stored" -- a
// state the prompt already handles.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  readApiKey, writeApiKey, clearApiKey, apiKeyHeader, withApiKey,
  adoptKeyFromLocation, resetAdoptionForTests, credentialOrigin,
} from './api-key.mjs';

// Every key is stored for an origin (0.7.1). These tests use one; the binding rule has its own test
// below and the whole path has a-key-is-sent-only-to-the-origin-it-was-entered-for.test.mjs.
const SERVICE = 'http://h:8800';
const STORED_AS = 'aify.apiKey@http://h:8800';

test('a key is read back only for the origin it was stored for', () => {
  workingStore();
  writeApiKey('banana', `${SERVICE}/api/v1/agents`);
  assert.equal(readApiKey(`${SERVICE}/api/v1/other`), 'banana', 'any URL on the same origin reads it');
  assert.equal(readApiKey('ws://h:8800/ws'), 'banana', 'the socket is the same service');
  assert.equal(readApiKey('http://h:9000'), '', 'another port is another service');
  assert.equal(readApiKey('https://h:8800'), '', 'another scheme is another service');
  assert.equal(readApiKey('/api/v1/agents'), '', 'a URL with no origin reads nothing');
  assert.equal(writeApiKey('banana', ''), false, 'and nothing can be stored without one');
  assert.equal(credentialOrigin('wss://h/ws'), 'https://h');
});

/** A localStorage that works. Returned so a test can inspect what was written. */
function workingStore(initial = {}) {
  const data = { ...initial };
  globalThis.localStorage = {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
  return data;
}

/** A localStorage that throws on every access, which is a real browser configuration. */
function hostileStore() {
  const boom = () => { throw new Error('The operation is insecure.'); };
  globalThis.localStorage = { getItem: boom, setItem: boom, removeItem: boom };
}

test('a stored key is read back', () => {
  workingStore();
  assert.equal(writeApiKey('banana', SERVICE), true);
  assert.equal(readApiKey(SERVICE), 'banana');
});

test('no key stored reads as empty, not as undefined or null', () => {
  workingStore();
  assert.equal(readApiKey(SERVICE), '');
});

test('whitespace is trimmed, because a pasted key brings a newline with it', () => {
  const data = workingStore();
  writeApiKey('  banana\n', SERVICE);
  assert.equal(data[STORED_AS], 'banana');
});

test('an empty key is refused rather than stored', () => {
  workingStore();
  assert.equal(writeApiKey('   ', SERVICE), false);
  assert.equal(readApiKey(SERVICE), '');
});

test('clearing removes it', () => {
  workingStore();
  writeApiKey('banana', SERVICE);
  clearApiKey(SERVICE);
  assert.equal(readApiKey(SERVICE), '');
});

test('a storage that throws degrades instead of crashing the dashboard', () => {
  hostileStore();
  // THE POINT OF THE GUARDS. Each of these would otherwise propagate out of a module imported at
  // load by api-client, so the dashboard would not render at all.
  assert.equal(readApiKey(SERVICE), '');
  assert.equal(writeApiKey('banana', SERVICE), false);
  assert.doesNotThrow(() => clearApiKey(SERVICE));
  assert.equal(apiKeyHeader(SERVICE), null);
});

test('the header is the one the service reads, and is absent when there is no key', () => {
  workingStore();
  assert.equal(apiKeyHeader(SERVICE), null, 'no key must mean no header, not an empty one');
  writeApiKey('banana', SERVICE);
  // NAMED EXACTLY. `main.py` reads `X-API-Key`; a near-miss here is a dashboard that authenticates
  // nowhere and reports only 401.
  assert.deepEqual(apiKeyHeader(SERVICE), { 'X-API-Key': 'banana' });
});

test('the socket url carries the key, since a WebSocket cannot carry a header', () => {
  workingStore();
  writeApiKey('banana', SERVICE);
  assert.equal(withApiKey('ws://h:8800/ws'), 'ws://h:8800/ws?api_key=banana');
});

test('the socket url appends to an existing query rather than starting a second one', () => {
  workingStore();
  writeApiKey('banana', SERVICE);
  // NOT `agent_id` here, deliberately. `test_the_agent_addressed_websocket_half_has_no_client`
  // scans the source for anything connecting to /ws WITH an agent id, because the agent-addressed
  // half of ConnectionManager has never had a client and three `notify_agent` call sites are
  // writing to nobody. A fixture string is not a client, but it reads as one to a source scan --
  // and a gate that cries wolf on test data is a gate somebody eventually widens.
  assert.equal(withApiKey('ws://h:8800/ws?tab=agents'), 'ws://h:8800/ws?tab=agents&api_key=banana');
});

test('the key is url-encoded, so a key with punctuation does not truncate the parameter', () => {
  workingStore();
  writeApiKey('a b&c=d', SERVICE);
  assert.equal(withApiKey('ws://h:8800/ws'), 'ws://h:8800/ws?api_key=a%20b%26c%3Dd');
});

test('an unprotected service is untouched: no key means the url is returned unchanged', () => {
  workingStore();
  const url = 'ws://h:8800/ws';
  assert.equal(withApiKey(url), url);
});

// --- Arriving with the key in the URL -----------------------------------------------------------
// The operator's own bookmark is `http://host:8801/?api_key=...`, which is the shape the SERVICE
// port documents. The dashboard port never exchanged it, so that URL rendered a dashboard and then
// 401'd on every call, with the credential sitting in the address bar the whole time.

function fakeLocation(href) {
  const replaced = [];
  globalThis.location = { href };
  globalThis.history = { replaceState: (_s, _t, url) => replaced.push(url) };
  return replaced;
}

test('a key in the URL is adopted', () => {
  workingStore();
  resetAdoptionForTests();
  fakeLocation('http://192.168.100.10:8801/?api_key=banana');
  adoptKeyFromLocation(SERVICE);
  assert.equal(readApiKey(SERVICE), 'banana');
});

test('and is then stripped from the URL, so it leaves no trace behind', () => {
  workingStore();
  resetAdoptionForTests();
  const replaced = fakeLocation('http://192.168.100.10:8801/?api_key=banana&tab=agents');
  adoptKeyFromLocation(SERVICE);
  assert.equal(replaced.length, 1, 'the URL was never rewritten');
  assert.ok(!replaced[0].includes('api_key'), `the key is still in the URL: ${replaced[0]}`);
  assert.ok(replaced[0].includes('tab=agents'), 'stripping the key threw away the other parameters');
});

test('it runs once per page, not on every request', () => {
  workingStore();
  resetAdoptionForTests();
  const replaced = fakeLocation('http://h:8801/?api_key=banana');
  adoptKeyFromLocation(SERVICE);
  adoptKeyFromLocation(SERVICE);
  assert.equal(replaced.length, 1, 'adoption repeated, so it would fight a later navigation');
});

test('a URL with no key changes nothing', () => {
  // NEGATIVE CONTROL: adoption must not clear or rewrite anything when there is nothing to adopt.
  workingStore();
  writeApiKey('already-here', SERVICE);
  resetAdoptionForTests();
  const replaced = fakeLocation('http://h:8801/');
  adoptKeyFromLocation(SERVICE);
  assert.equal(readApiKey(SERVICE), 'already-here', 'an unrelated load discarded the stored key');
  assert.equal(replaced.length, 0, 'the URL was rewritten for no reason');
});

test('no location at all is survivable, because this module also loads under Node', () => {
  workingStore();
  resetAdoptionForTests();
  delete globalThis.location;
  delete globalThis.history;
  assert.doesNotThrow(() => adoptKeyFromLocation(SERVICE));
});

test('the per-request carriers never adopt a URL key, because their target may be a link-chosen origin', () => {
  // v0.7.2 (external review, item 1). The carriers adopted for the origin they were about to call, so
  // a stored `?apiOrigin=` from an earlier link received the key of an ordinary `?api_key=` bookmark.
  // The key in the URL belongs to the service that served the page; app.js adopts it for that origin
  // at boot (app.test.mjs holds the call site).
  workingStore();
  resetAdoptionForTests();
  const replaced = fakeLocation('http://h:8801/?api_key=from-the-url');
  assert.equal(apiKeyHeader(SERVICE), null, 'the request carrier adopted the URL key for its own target');
  assert.equal(withApiKey('ws://h:8800/ws'), 'ws://h:8800/ws', 'the socket carrier adopted the URL key for its own target');
  assert.equal(readApiKey(SERVICE), '');
  assert.equal(replaced.length, 0, 'a carrier rewrote the URL');
});
