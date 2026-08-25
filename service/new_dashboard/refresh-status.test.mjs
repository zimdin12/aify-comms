// A sustained partial refresh must not be reported as a complete one, and a blip must not alarm.
//
// The code this replaces made one decision for both cases:
//
//     if (failed === 0)   → 'live'  (green)
//     else if (ok(0))     → 'live'  (green)   ← nine of ten fetches could fail here
//     else                → 'reconnecting'
//
// The middle branch was deliberate, and half right. refresh-cycle.test.mjs recorded the reasoning:
// "agents are the core slice. Stats/settings blipping is noise." True — a chip that flickers amber on
// every transient miss is one the operator stops reading. It dates from the era when the single-worker
// service dropped requests under poll load, which is why the poll keeps each slice's last-good value.
//
// The half it missed: that same branch painted a near-total outage green. And because the poll shows
// last-good data, a stale slice renders identically to one where nothing changed — the chip was the
// only possible tell, and it said everything was fine.
//
// So the rule is PERSISTENCE. Miss once, blip, stay green. Miss twice running, stale, be named. Both
// concerns served rather than one traded for the other, at a cost of one cycle (~15s) before a real
// outage shows.
//
// Measured before changing it: 120 requests over 15 cycles against the live service, 0 non-200. Blips
// are not currently common — one quiet sample, not proof they never happen, which is why the tolerance
// stays.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  AGENTS_SLICE, REFRESH_SLICES, refreshChipState, rejectedSlices,
} from './refresh-status.mjs';

const OK = { status: 'fulfilled' };
const NO = { status: 'rejected' };

/** A full cycle with the named slices failed. Built from REFRESH_SLICES so it cannot drift. */
function cycle(...failedNames) {
  return REFRESH_SLICES.map((name) => (failedNames.includes(name) ? NO : OK));
}

// ── the blip half, which already worked and must keep working ──────────────────────────────────

test('everything refreshed reads live', () => {
  const state = refreshChipState(cycle());
  assert.equal(state.text, 'live');
  assert.equal(state.className, 'status-chip ok');
  assert.deepEqual(state.stale, []);
});

test('a first miss is a blip: green, but visible in the tooltip', () => {
  const state = refreshChipState(cycle('stats', 'settings'));
  assert.equal(state.text, 'live', 'a single blip moved the chip');
  assert.equal(state.className, 'status-chip ok');
  assert.deepEqual(state.stale, []);
  assert.match(state.title, /Retrying: stats, settings/);
});

test('a slice that recovers is not held against it', () => {
  const state = refreshChipState(cycle(), { previouslyFailed: ['stats'] });
  assert.equal(state.text, 'live');
  assert.deepEqual(state.stale, []);
});

test('a DIFFERENT slice failing next cycle is still two separate blips', () => {
  // Persistence is per slice, not a global counter. Two unrelated single misses are not an outage,
  // and a counter would have reported one.
  const state = refreshChipState(cycle('settings'), { previouslyFailed: ['stats'] });
  assert.equal(state.text, 'live');
});

// ── the half that did not exist ────────────────────────────────────────────────────────────────

test('the exact case that used to say live now does not', () => {
  const others = REFRESH_SLICES.filter((n) => n !== 'agents');
  const state = refreshChipState(cycle(...others), { previouslyFailed: others });
  assert.notEqual(state.text, 'live', 'nine sustained failures still reported as live');
  assert.equal(state.className, 'status-chip warn');
});

test('a slice missing twice running is named, not just counted', () => {
  const state = refreshChipState(cycle('spawn requests'), { previouslyFailed: ['spawn requests'] });
  assert.equal(state.text, '1 stale');
  assert.deepEqual(state.stale, ['spawn requests']);
  assert.match(state.title, /spawn requests/);
});

test('several sustained failures are all named', () => {
  const failing = ['messages', 'stats', 'settings'];
  const state = refreshChipState(cycle(...failing), { previouslyFailed: failing });
  assert.equal(state.text, '3 stale');
  assert.deepEqual(state.stale, failing);
  for (const name of failing) assert.match(state.title, new RegExp(name));
});

// ── losing the roster is its own fact ──────────────────────────────────────────────────────────

test('losing agents is reconnecting immediately, with no blip tolerance', () => {
  // Deliberately not persistence-gated. Losing the roster is never noise, `state.loaded` already
  // turns on it, and waiting a cycle to say so would leave the rail claiming "No agents."
  const state = refreshChipState(cycle('agents'));
  assert.equal(state.text, 'reconnecting');
  assert.equal(state.className, 'status-chip warn');
  assert.match(state.title, /Cannot reach the service/);
  assert.deepEqual(state.stale, [], 'an unreachable service was also reported as stale slices');
});

test('the agents slice is the one that decides reconnecting', () => {
  // Pinned against reordering the fetch array: if agents moves and this constant does not, a dead
  // service reads as "stale" and one stale panel reads as "reconnecting".
  assert.equal(REFRESH_SLICES[AGENTS_SLICE], 'agents');
});

// ── the shape the caller depends on ────────────────────────────────────────────────────────────

test('rejectedSlices returns what the next cycle needs, in slice names', () => {
  assert.deepEqual(rejectedSlices(cycle('stats', 'runs')), ['runs', 'stats']);
  assert.deepEqual(rejectedSlices(cycle()), []);
});

test('every slice has a distinct name, so none renders as "slice 7"', () => {
  assert.equal(REFRESH_SLICES.length, 10, 'the fetch array and its names have drifted apart');
  for (const name of REFRESH_SLICES) assert.ok(name && name.trim().length > 2, name);
  assert.equal(new Set(REFRESH_SLICES).size, REFRESH_SLICES.length, 'two slices share a name');
});

test('an unnamed extra slice degrades to a label rather than undefined', () => {
  const extra = [...cycle(), NO];
  const state = refreshChipState(extra, { previouslyFailed: ['slice 10'] });
  assert.match(state.title, /slice 10/);
});

test('missing or empty input does not throw', () => {
  // The chip is painted at the end of every cycle; throwing here would leave the previous state on
  // screen, which is the exact failure this module exists to prevent.
  assert.equal(refreshChipState([]).text, 'live');
  assert.equal(refreshChipState(undefined).text, 'live');
  assert.deepEqual(rejectedSlices(undefined), []);
});
