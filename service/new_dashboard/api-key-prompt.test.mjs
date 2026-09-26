// The login prompt, driven by a fake document.
//
// WHY A FAKE DOCUMENT AND NOT A BROWSER. A login form that only a browser can execute is a login
// form nobody tests until an operator cannot get in -- which is exactly how this one was needed.
// `ensureApiKeyPrompt` takes its document as an argument for that reason, so every branch here runs
// in Node: it mounts once, it stores what was typed, and it stops the form from navigating.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ensureApiKeyPrompt, isMounted, PROMPT_ID } from './api-key-prompt.mjs';
import { readApiKey, writeApiKey } from './api-key.mjs';

// The service that refused the request: the prompt stores what is typed for it alone (0.7.1).
const SERVICE = 'http://h:8800';
const STORED_AS = 'aify.apiKey@http://h:8800';

test('with no origin to bind a key to, nothing is mounted', () => {
  storeThatWorks();
  const doc = fakeDocument();
  assert.equal(ensureApiKeyPrompt('', doc, () => {}), null);
  assert.equal(isMounted(doc), false, 'a key typed here could only be stored for no service');
});

/** Enough of a DOM for this module: elements, one listener, a body, and getElementById. */
function fakeDocument() {
  const byId = new Map();
  const make = (tag) => ({
    tagName: tag,
    style: { cssText: '' },
    children: [],
    attributes: {},
    value: '',
    _listeners: {},
    set id(v) { this._id = v; byId.set(v, this); },
    get id() { return this._id; },
    setAttribute(k, v) { this.attributes[k] = v; },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(type, fn) { this._listeners[type] = fn; },
    focus() {},
  });
  const body = make('body');
  return {
    body,
    createElement: (tag) => make(tag),
    getElementById: (id) => byId.get(id) || null,
    _byId: byId,
  };
}

function storeThatWorks(initial = {}) {
  const data = { ...initial };
  globalThis.localStorage = {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
  return data;
}

/** Walk the mounted tree for the form, which is where the submit listener lives. */
function formOf(overlay) {
  return overlay.children.find((c) => c.tagName === 'form');
}
function inputOf(overlay) {
  return formOf(overlay).children.find((c) => c.tagName === 'input');
}

test('it mounts onto the page', () => {
  storeThatWorks();
  const doc = fakeDocument();
  const overlay = ensureApiKeyPrompt(SERVICE, doc, () => {});
  assert.ok(overlay, 'nothing was mounted');
  assert.equal(overlay.id, PROMPT_ID);
  assert.equal(doc.body.children.length, 1);
  assert.equal(isMounted(doc), true);
});

test('it mounts ONCE however many requests fail together', () => {
  // THE CASE THAT MADE THE GUARD NECESSARY: the 401 path runs per request, and the dashboard polls
  // several endpoints at once, so an unguarded mount stacks an overlay per failed poll for ever.
  storeThatWorks();
  const doc = fakeDocument();
  ensureApiKeyPrompt(SERVICE, doc, () => {});
  const second = ensureApiKeyPrompt(SERVICE, doc, () => {});
  assert.equal(second, null, 'a second call mounted another overlay');
  assert.equal(doc.body.children.length, 1);
});

test('a key the service just refused is cleared, so the prompt is not pre-filled with it', () => {
  storeThatWorks({ [STORED_AS]: 'the-wrong-one' });
  ensureApiKeyPrompt(SERVICE, fakeDocument(), () => {});
  assert.equal(readApiKey(SERVICE), '', 'the refused key survived, so it will be retried for ever');
});

test('submitting stores the typed key and reports acceptance', () => {
  storeThatWorks();
  const doc = fakeDocument();
  let accepted = 0;
  const overlay = ensureApiKeyPrompt(SERVICE, doc, () => { accepted += 1; });
  inputOf(overlay).value = 'banana';
  formOf(overlay)._listeners.submit({ preventDefault() {} });
  assert.equal(readApiKey(SERVICE), 'banana');
  assert.equal(accepted, 1);
});

test('submitting stops the form navigating, which would put the key in the URL', () => {
  // The prompt exists to keep the credential OUT of history, the address bar and the Referer. A
  // form that navigates puts it in all three -- reintroducing the leak through its own fix.
  storeThatWorks();
  const doc = fakeDocument();
  const overlay = ensureApiKeyPrompt(SERVICE, doc, () => {});
  let prevented = 0;
  inputOf(overlay).value = 'banana';
  formOf(overlay)._listeners.submit({ preventDefault() { prevented += 1; } });
  assert.equal(prevented, 1);
});

test('an empty submission neither stores nor reports acceptance', () => {
  storeThatWorks();
  const doc = fakeDocument();
  let accepted = 0;
  const overlay = ensureApiKeyPrompt(SERVICE, doc, () => { accepted += 1; });
  inputOf(overlay).value = '   ';
  formOf(overlay)._listeners.submit({ preventDefault() {} });
  assert.equal(readApiKey(SERVICE), '');
  assert.equal(accepted, 0, 'an empty key reloaded the page, which would loop');
});

test('the field is a password field and is labelled', () => {
  // Not decoration: the key is a secret typed into a shared screen, and the overlay has no visible
  // label for a screen reader to attach to the input.
  storeThatWorks();
  const overlay = ensureApiKeyPrompt(SERVICE, fakeDocument(), () => {});
  const input = inputOf(overlay);
  assert.equal(input.type, 'password');
  assert.equal(input.attributes['aria-label'], 'API key');
});

test('no document means no crash', () => {
  storeThatWorks();
  assert.equal(ensureApiKeyPrompt(SERVICE, null, () => {}), null);
  assert.equal(isMounted(null), false);
});

test('CONTROL: the fake document can actually report an absence', () => {
  // Without this, every "mounted" assertion above would pass against a getElementById that always
  // returned an element, and every "mounted once" test would be vacuous.
  const doc = fakeDocument();
  assert.equal(doc.getElementById(PROMPT_ID), null);
  assert.equal(isMounted(doc), false);
  storeThatWorks();
  ensureApiKeyPrompt(SERVICE, doc, () => {});
  assert.notEqual(doc.getElementById(PROMPT_ID), null);
});

test('CONTROL: writeApiKey is the thing being observed, not a coincidence', () => {
  const data = storeThatWorks();
  writeApiKey('sentinel', SERVICE);
  assert.equal(data[STORED_AS], 'sentinel');
});
