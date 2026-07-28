#!/usr/bin/env node
// Tests for sessions-list.mjs — collapsing an agent's superseded session rows in the list.
//
// Operator report 2026-07-28: "i see multiple sc-manager sessions why? (must be a bug)".
// Measured: sc-manager has 10 rows in agent_sessions but only ONE live. The server already hides
// pure history (ended/completed/cancelled), so the dashboard received the live `running` row plus a
// `stopped` row from eight weeks earlier. That stopped row is served on PURPOSE — hiding
// stopped/failed/lost once broke comms_restart / comms_compact / the drawer's Restart-Reset-Compact,
// because a non-live session is exactly what those act on.
//
// Run: node --test service/new_dashboard/sessions-list.test.mjs

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { collapseSupersededSessions, countSupersededSessions, sessionRowIsLive } from './sessions-list.mjs';

const S = (agentId, status, id = `${agentId}-${status}`) => ({ id, agentId, status });

test('THE REPORT: an old stopped row is hidden once the agent has a live one', () => {
  const rows = [S('sc-manager', 'running', 'live'), S('sc-manager', 'stopped', 'old')];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['live']);
});

test('an agent with NO live row keeps its stopped row — Restart must still work', () => {
  // This is the case that broke last time the list hid stopped rows. It must stay visible.
  const rows = [S('ef-tech-lead', 'stopped', 'restartable')];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['restartable']);
});

test('failed and lost rows survive when nothing live exists for that agent', () => {
  for (const status of ['failed', 'lost', 'stopped']) {
    const rows = [S('a', status, 'keep')];
    assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['keep'], status);
  }
});

test('collapsing is PER AGENT — one agent going live never hides another agent rows', () => {
  const rows = [S('a', 'running', 'a-live'), S('a', 'stopped', 'a-old'), S('b', 'stopped', 'b-old')];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['a-live', 'b-old']);
});

test('every live status counts as live, so none of them is ever collapsed away', () => {
  for (const status of ['starting', 'running', 'recovering', 'restarting', 'cli-takeover', 'attached', 'active', 'idle']) {
    assert.equal(sessionRowIsLive({ status }), true, status);
    const rows = [S('a', status, 'live'), S('a', 'stopped', 'old')];
    assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['live'], status);
  }
});

test('two live rows for one agent are BOTH kept — duplicates there are a real signal', () => {
  // A genuine duplicate-worker leak must remain visible; this filter only removes dead rows.
  const rows = [S('a', 'running', 'l1'), S('a', 'attached', 'l2')];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['l1', 'l2']);
});

test('order is preserved', () => {
  const rows = [S('b', 'stopped', 'b1'), S('a', 'running', 'a1'), S('c', 'stopped', 'c1')];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['b1', 'a1', 'c1']);
});

test('a row with no attributable agent is never hidden', () => {
  const rows = [S('a', 'running', 'a-live'), { id: 'orphan', status: 'stopped' }];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['a-live', 'orphan']);
});

test('snake_case agent_id is understood, and status matching is case/space tolerant', () => {
  const rows = [{ id: 'live', agent_id: 'a', status: ' Running ' }, { id: 'old', agent_id: 'A', status: 'STOPPED' }];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['live']);
});

test('degenerate inputs return something safe', () => {
  assert.deepEqual(collapseSupersededSessions([]), []);
  assert.deepEqual(collapseSupersededSessions(null), []);
  assert.deepEqual(collapseSupersededSessions(undefined), []);
  assert.equal(sessionRowIsLive(null), false);
  assert.equal(sessionRowIsLive({}), false);
});

test('a custom agentIdOf accessor is honoured (app.js injects sessionAgentId)', () => {
  const rows = [
    { id: 'live', meta: { who: 'a' }, status: 'running' },
    { id: 'old', meta: { who: 'a' }, status: 'stopped' },
  ];
  const out = collapseSupersededSessions(rows, { agentIdOf: (s) => s.meta.who });
  assert.deepEqual(out.map((r) => r.id), ['live']);
});

test('ONE AGENT = ONE ENTRY: with no live row, only the NEWEST dead row shows', () => {
  // The operator's model: "for me i know only one sc-manager... seeing 2 makes me misunderstand."
  const rows = [
    { id: 'old', agentId: 'a', status: 'stopped', lastSeen: '2026-06-02T16:10:47Z' },
    { id: 'newer', agentId: 'a', status: 'failed', lastSeen: '2026-07-20T10:00:00Z' },
    { id: 'oldest', agentId: 'a', status: 'lost', lastSeen: '2026-05-01T00:00:00Z' },
  ];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['newer']);
  assert.equal(countSupersededSessions(rows), 2, 'the other two must be REPORTED, not silently dropped');
});

test('undatable rows fall back to list order rather than vanishing', () => {
  const rows = [S('a', 'stopped', 'first'), S('a', 'stopped', 'second')];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['first']);
  assert.equal(countSupersededSessions(rows), 1);
});

test('a malformed lastSeen never wins over a real timestamp', () => {
  const rows = [
    { id: 'real', agentId: 'a', status: 'stopped', lastSeen: '2026-07-20T10:00:00Z' },
    { id: 'junk', agentId: 'a', status: 'stopped', lastSeen: 'not-a-date' },
  ];
  assert.deepEqual(collapseSupersededSessions(rows).map((r) => r.id), ['real']);
});

test('countSupersededSessions reports 0 when nothing is collapsed', () => {
  assert.equal(countSupersededSessions([S('a', 'running', 'l')]), 0);
  assert.equal(countSupersededSessions([]), 0);
  assert.equal(countSupersededSessions(null), 0);
});
