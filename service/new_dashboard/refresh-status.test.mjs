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

import { readFileSync } from 'node:fs';

import {
  AGENTS_SLICE, OUT_OF_BAND_SLICES, REFRESH_SLICES, noteSliceFailure, refreshChipState,
  rejectedSlices, resetRefreshHistory,
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

// ── the fetches that are not in the allSettled array ───────────────────────────────────────────
//
// The cycle issues MORE requests than the ten it settles together. Observed on the running dashboard
// 2026-08-25 by watching the browser's network panel rather than reading the array: twelve to thirteen
// requests per cycle. The contracts re-filter, the channel list, an open conversation and the shared
// files list are separate awaits, each wrapped in `try { ... } catch (_) {}` that swallowed the failure
// whole. They could fail for ever and the chip would still read `live` — the exact defect the chip was
// rewritten to end, one layer over, and invisible from the source of refreshChipState.

test('an out-of-band failure is counted like any other slice', () => {
  resetRefreshHistory();
  const clean = REFRESH_SLICES.map(() => OK);
  noteSliceFailure('files');
  assert.equal(refreshChipState(clean).text, 'live', 'a first out-of-band miss should still be a blip');
  noteSliceFailure('files');
  const state = refreshChipState(clean);
  assert.equal(state.text, '1 stale', 'a repeated out-of-band failure never reached the chip');
  assert.deepEqual(state.stale, ['files']);
});

test('it is drained per paint, so one failure is not counted twice', () => {
  // The out-of-band awaits run BEFORE the chip is painted, so what they report belongs to THIS cycle.
  // Carrying it forward would make a single failure look like two consecutive ones and trip the
  // two-cycle rule on its own — turning the blip tolerance into no tolerance at all.
  resetRefreshHistory();
  const clean = REFRESH_SLICES.map(() => OK);
  noteSliceFailure('channels');
  assert.equal(refreshChipState(clean).text, 'live');
  assert.equal(refreshChipState(clean).text, 'live', 'one failure was counted in a second cycle');
});

test('out-of-band and settled failures combine', () => {
  resetRefreshHistory();
  const withStats = REFRESH_SLICES.map((n) => (n === 'stats' ? NO : OK));
  noteSliceFailure('files');
  refreshChipState(withStats);                 // first cycle: both are blips
  noteSliceFailure('files');
  const state = refreshChipState(withStats);   // second: both sustained
  assert.equal(state.text, '2 stale');
  assert.deepEqual(state.stale.slice().sort(), ['files', 'stats']);
});

test('every out-of-band name is really reported by a call site', () => {
  // DERIVED FROM refresh-cycle.mjs, because a hand-written list is what let the alias slip past the
  // env-hygiene fix earlier today. A name declared here and never reported is a slice nobody watches.
  const source = readFileSync(new URL('./refresh-cycle.mjs', import.meta.url), 'utf8');
  const reported = new Set(
    [...source.matchAll(/noteSliceFailure\(\s*'([^']+)'\s*\)/g)].map((m) => m[1]),
  );
  assert.ok(reported.size >= 4, `only ${reported.size} call sites found; the scan is broken`);
  for (const name of OUT_OF_BAND_SLICES) {
    assert.ok(reported.has(name), `${name} is declared out-of-band but no catch reports it`);
  }
  for (const name of reported) {
    assert.ok(OUT_OF_BAND_SLICES.includes(name), `${name} is reported but not declared`);
  }
});

test('no swallowing catch is left unreported', () => {
  // The other half, and the one that would have caught this: a `catch (_) {}` inside the cycle that
  // does NOT report is a fetch whose failure is invisible again.
  const source = readFileSync(new URL('./refresh-cycle.mjs', import.meta.url), 'utf8');
  const silent = source
    .split(String.fromCharCode(10))
    .map((line, i) => [i + 1, line])
    .filter(([, line]) => /catch\s*\(_\)/.test(line) && !line.includes('noteSliceFailure'));
  assert.deepEqual(
    silent.map(([n, l]) => `${n}: ${l.trim().slice(0, 70)}`), [],
    'these catches swallow a fetch failure without telling the chip',
  );
});
