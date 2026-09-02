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
  adoptKeyFromLocation, resetAdoptionForTests,
} from './api-key.mjs';

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
  assert.equal(writeApiKey('banana'), true);
  assert.equal(readApiKey(), 'banana');
});

test('no key stored reads as empty, not as undefined or null', () => {
  workingStore();
  assert.equal(readApiKey(), '');
});

test('whitespace is trimmed, because a pasted key brings a newline with it', () => {
  const data = workingStore();
  writeApiKey('  banana\n');
  assert.equal(data['aify.apiKey'], 'banana');
});

test('an empty key is refused rather than stored', () => {
  workingStore();
  assert.equal(writeApiKey('   '), false);
  assert.equal(readApiKey(), '');
});

test('clearing removes it', () => {
  workingStore();
  writeApiKey('banana');
  clearApiKey();
  assert.equal(readApiKey(), '');
});

test('a storage that throws degrades instead of crashing the dashboard', () => {
  hostileStore();
  // THE POINT OF THE GUARDS. Each of these would otherwise propagate out of a module imported at
  // load by api-client, so the dashboard would not render at all.
  assert.equal(readApiKey(), '');
  assert.equal(writeApiKey('banana'), false);
  assert.doesNotThrow(() => clearApiKey());
  assert.equal(apiKeyHeader(), null);
});

test('the header is the one the service reads, and is absent when there is no key', () => {
  workingStore();
  assert.equal(apiKeyHeader(), null, 'no key must mean no header, not an empty one');
  writeApiKey('banana');
  // NAMED EXACTLY. `main.py` reads `X-API-Key`; a near-miss here is a dashboard that authenticates
  // nowhere and reports only 401.
  assert.deepEqual(apiKeyHeader(), { 'X-API-Key': 'banana' });
});

test('the socket url carries the key, since a WebSocket cannot carry a header', () => {
  workingStore();
  writeApiKey('banana');
  assert.equal(withApiKey('ws://h:8800/ws'), 'ws://h:8800/ws?api_key=banana');
});

test('the socket url appends to an existing query rather than starting a second one', () => {
  workingStore();
  writeApiKey('banana');
  // NOT `agent_id` here, deliberately. `test_the_agent_addressed_websocket_half_has_no_client`
  // scans the source for anything connecting to /ws WITH an agent id, because the agent-addressed
  // half of ConnectionManager has never had a client and three `notify_agent` call sites are
  // writing to nobody. A fixture string is not a client, but it reads as one to a source scan --
  // and a gate that cries wolf on test data is a gate somebody eventually widens.
  assert.equal(withApiKey('ws://h:8800/ws?tab=agents'), 'ws://h:8800/ws?tab=agents&api_key=banana');
});

test('the key is url-encoded, so a key with punctuation does not truncate the parameter', () => {
  workingStore();
  writeApiKey('a b&c=d');
  assert.equal(withApiKey('ws://h/ws'), 'ws://h/ws?api_key=a%20b%26c%3Dd');
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
  adoptKeyFromLocation();
  assert.equal(readApiKey(), 'banana');
});

test('and is then stripped from the URL, so it leaves no trace behind', () => {
  workingStore();
  resetAdoptionForTests();
  const replaced = fakeLocation('http://192.168.100.10:8801/?api_key=banana&tab=agents');
  adoptKeyFromLocation();
  assert.equal(replaced.length, 1, 'the URL was never rewritten');
  assert.ok(!replaced[0].includes('api_key'), `the key is still in the URL: ${replaced[0]}`);
  assert.ok(replaced[0].includes('tab=agents'), 'stripping the key threw away the other parameters');
});

test('it runs once per page, not on every request', () => {
  workingStore();
  resetAdoptionForTests();
  const replaced = fakeLocation('http://h:8801/?api_key=banana');
  adoptKeyFromLocation();
  adoptKeyFromLocation();
  assert.equal(replaced.length, 1, 'adoption repeated, so it would fight a later navigation');
});

test('a URL with no key changes nothing', () => {
  // NEGATIVE CONTROL: adoption must not clear or rewrite anything when there is nothing to adopt.
  workingStore();
  writeApiKey('already-here');
  resetAdoptionForTests();
  const replaced = fakeLocation('http://h:8801/');
  adoptKeyFromLocation();
  assert.equal(readApiKey(), 'already-here', 'an unrelated load discarded the stored key');
  assert.equal(replaced.length, 0, 'the URL was rewritten for no reason');
});

test('no location at all is survivable, because this module also loads under Node', () => {
  workingStore();
  resetAdoptionForTests();
  delete globalThis.location;
  delete globalThis.history;
  assert.doesNotThrow(() => adoptKeyFromLocation());
});

test('the per-request carriers adopt, so nothing has to call adoption explicitly', () => {
  // THE CALL SITE. Adoption that only works when someone remembers to call it is adoption that does
  // not work: every path into the dashboard goes through one of these two.
  workingStore();
  resetAdoptionForTests();
  fakeLocation('http://h:8801/?api_key=from-the-url');
  assert.deepEqual(apiKeyHeader(), { 'X-API-Key': 'from-the-url' });

  workingStore();
  resetAdoptionForTests();
  fakeLocation('http://h:8801/?api_key=for-the-socket');
  assert.equal(withApiKey('ws://h/ws'), 'ws://h/ws?api_key=for-the-socket');
});
