// Every event the service broadcasts has a declared disposition, and every declaration names a real one.
//
// THE DEFECT. applyRealtimeEvent ended with an inline array of eleven event names; anything else fell
// off the end of the function and was dropped with no branch and no log. Measured 2026-08-25 by reading
// both sides: the service broadcasts 49 distinct names, three were handled in place, eleven were in the
// array, and 35 were discarded — channel_message, terminal_stopped, message_deleted,
// conversation_cleared, file_shared and all three spawn_request_* among them.
//
// Not data loss, because the ~15s poll catches up. But it is a realtime channel that mostly is not one,
// and a chat message could sit up to a poll behind for no reason anyone had decided.
//
// The list was the defect, not its contents: a hand-maintained allowlist against a producer set written
// in another language in another directory, with nothing comparing the two. A broadcast added
// service-side joined the dropped set silently and stayed there.
//
// This test is the comparison that was missing. It reads the PRODUCER — service/**/*.py — rather than a
// copy of it, so it cannot agree with a stale duplicate.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { GRANULAR, IGNORED, dispositionOf, ignoredReason } from './realtime-dispositions.mjs';

const SERVICE = join(process.cwd(), '..', '..', 'service');

/** Every .py under service/, skipping tests and caches. */
function pythonSources(dir = SERVICE, out = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === 'tests' || entry === '__pycache__' || entry === 'node_modules') continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) pythonSources(path, out);
    else if (entry.endsWith('.py')) out.push(path);
  }
  return out;
}

/** Event names the service broadcasts, read from its own source. */
function broadcastNames() {
  const names = new Set();
  const quoted = /["']event["']\s*:\s*["']([a-z][a-z0-9_]{2,40})["']/g;
  const called = /broadcast(?:_event)?\(\s*["']([a-z][a-z0-9_]{2,40})["']/g;
  for (const path of pythonSources()) {
    const text = readFileSync(path, 'utf8');
    for (const m of text.matchAll(quoted)) names.add(m[1]);
    for (const m of text.matchAll(called)) names.add(m[1]);
  }
  return names;
}

test('the producer scan finds the broadcasts', () => {
  // The control. An empty producer set agrees with any disposition table, and the assertions below
  // would all pass while proving nothing — which is the shape of the bug this file is about.
  const names = broadcastNames();
  assert.ok(names.size > 30, `only ${names.size} broadcast names found; the scan is broken`);
  assert.ok(names.has('agent_status'), 'the scan missed an event that certainly exists');
  assert.ok(names.has('terminal_output'), 'the scan missed an event that certainly exists');
});

test('the producer scan can say no', () => {
  assert.ok(!broadcastNames().has('zzz_not_an_event'));
});

test('every broadcast event has a disposition, and none falls through silently', () => {
  const undecided = [...broadcastNames()].filter((name) => {
    const d = dispositionOf(name);
    return d !== 'granular' && d !== 'refresh' && d !== 'ignore';
  });
  assert.deepEqual(undecided, [], `these events have no disposition: ${undecided.join(', ')}`);
});

test('an unknown event refreshes rather than being dropped', () => {
  // The default is the whole fix: a name nobody has classified is more likely a NEW broadcast than a
  // mistake, and one debounced refetch is a cheaper failure than a panel that never updates.
  assert.equal(dispositionOf('an_event_added_next_week'), 'refresh');
  assert.equal(dispositionOf(''), 'refresh');
  assert.equal(dispositionOf(undefined), 'refresh');
});

test('every ignored event states why, at length', () => {
  // A bare name with no argument is how the original eleven-name array grew. A reason is the review.
  for (const [name, reason] of Object.entries(IGNORED)) {
    assert.equal(typeof reason, 'string', name);
    assert.ok(reason.length > 60, `${name} is ignored without a real reason: ${reason}`);
    assert.equal(ignoredReason(name), reason);
  }
  assert.equal(ignoredReason('agent_status'), null, 'a handled event reported an ignore reason');
});

test('nothing is ignored that the service never sends', () => {
  // The other direction. An ignore entry for a name that no longer exists is a decision about nothing,
  // and it hides that the real event may now be arriving under a different name.
  const names = broadcastNames();
  const ghosts = Object.keys(IGNORED).filter((n) => !names.has(n));
  assert.deepEqual(ghosts, [], `ignored but never broadcast: ${ghosts.join(', ')}`);
});

test('nothing is marked granular that the service never sends', () => {
  const names = broadcastNames();
  const ghosts = GRANULAR.filter((n) => !names.has(n));
  assert.deepEqual(ghosts, [], `granular but never broadcast: ${ghosts.join(', ')}`);
});

test('the events that used to be dropped now refresh', () => {
  // Named explicitly. A general rule that quietly stopped covering the cases that produced it would
  // still pass every other test here.
  for (const name of [
    'channel_message', 'terminal_stopped', 'message_deleted', 'conversation_cleared',
    'file_shared', 'spawn_request_created', 'spawn_request_updated', 'spawn_request_claimed',
    'agent_removed', 'agent_renamed',
  ]) {
    assert.equal(dispositionOf(name), 'refresh', `${name} is being dropped again`);
  }
});

test('the highest-frequency event is still not refreshing', () => {
  // The reason the fix is not "refresh everything". Measured at 2 events in 60s with the fleet idle,
  // and it scales with bridge count.
  assert.equal(dispositionOf('environment_heartbeat'), 'ignore');
});

test('the three granular handlers stay granular', () => {
  // They patch state in place without a refetch — the dashboard's biggest poll-load reduction. Moving
  // one to refresh would be a silent performance regression that no test would otherwise notice.
  for (const name of ['agent_status', 'terminal_output', 'terminal_started']) {
    assert.equal(dispositionOf(name), 'granular', `${name} lost its in-place handler`);
  }
});
