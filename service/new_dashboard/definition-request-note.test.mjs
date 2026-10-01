// The note names the request the service queued, and is empty for an edit that applied directly, so a
// direct edit keeps its own toast.

import test from 'node:test';
import assert from 'node:assert/strict';

import { requestedInAifyEnv } from './definition-request-note.mjs';

test('a queued change names its host', () => {
  assert.equal(requestedInAifyEnv({ request: { machineId: 'win32:host-a', patch: { mode: 'resident' } } }, 'lead'),
    'Change of lead requested in aify-env on win32:host-a; it applies when that host syncs');
});

test('a queued removal says it is a removal', () => {
  assert.equal(requestedInAifyEnv({ request: { machineId: 'win32:host-a', patch: { remove: true } } }, 'lead'),
    'Removal of lead requested in aify-env on win32:host-a; it applies when that host syncs');
});

test('an edit that applied directly gets no note', () => {
  for (const answer of [{ ok: true, herdrSpace: false }, { ok: true, request: null }, undefined]) {
    assert.equal(requestedInAifyEnv(answer, 'plain'), '');
  }
});
