/**
 * The history panel says WHO asked for each spawn.
 *
 * THE OPERATOR'S QUESTION. "some agents still exited.. even tho i never stopped them". Answering it
 * took three tables: `terminal_controls.requested_by`, `spawn_requests.created_by`, and
 * `spawn_specs.metadata`. The dashboard had the deciding field the whole time -- `createdBy` is
 * serialised onto every spawn request by `_spawn_request_to_dict` -- and rendered nothing with it.
 *
 * WHAT THE LIVE DATA SAID, measured 2026-08-26 and used verbatim as the fixture below: the three
 * long-running hermes agents were each started by `dashboard`, and each produced a SECOND spawn
 * request about fifty seconds later whose `created_by` was the agent's own id. Same shape at
 * 14:51-14:53 and again at 18:41-18:42. They restart themselves; nobody stopped them.
 *
 * In the panel those two records were indistinguishable -- same mode, same agent, one minute apart --
 * so the panel could not tell an operator restart from a self-restart, which is exactly the
 * distinction they were trying to make.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { spawnRecordLineage } from './inspector-forms.mjs';

// Verbatim from `spawn_requests` on the operator's live database, 2026-08-26T18:41-18:42Z. Keeping the
// real pair means this test fails if the shape it was built from is ever misread again.
const OPERATOR_START = { agentId: 'mc-senior-dev', createdBy: 'dashboard', createdAt: '2026-08-26T18:41:33Z' };
const SELF_RESTART = { agentId: 'mc-senior-dev', createdBy: 'mc-senior-dev', createdAt: '2026-08-26T18:42:23Z' };

test('the operator start and the self-restart are told apart', () => {
  assert.equal(spawnRecordLineage(OPERATOR_START).requestedBy, 'dashboard');
  assert.equal(spawnRecordLineage(OPERATOR_START).selfRequested, false);

  assert.equal(spawnRecordLineage(SELF_RESTART).requestedBy, 'mc-senior-dev');
  assert.equal(spawnRecordLineage(SELF_RESTART).selfRequested, true);
});

test('a third party is named, not folded into either bucket', () => {
  // Real row: comms-tech-lead spawned comms-senior-dev at 15:23:16Z. Neither the operator nor the
  // agent itself, and reporting it as either would be a lie about who is driving the fleet.
  const byPeer = spawnRecordLineage({ agentId: 'comms-senior-dev', createdBy: 'comms-tech-lead' });
  assert.equal(byPeer.requestedBy, 'comms-tech-lead');
  assert.equal(byPeer.selfRequested, false);
});

test('snake_case rows work too', () => {
  // `/spawn-requests` is read through a serialiser that has flattened both ways in this file's
  // history; the sibling fields here are already read both ways for that reason.
  const r = spawnRecordLineage({ agent_id: 'sc-coder', created_by: 'sc-coder' });
  assert.equal(r.requestedBy, 'sc-coder');
  assert.equal(r.selfRequested, true);
});

test('an unrecorded requester is not reported as a self-request', () => {
  // THE FAIL-CLOSED CASE. Empty == empty is true, so a naive comparison would mark every record with
  // no requester as self-requested -- inventing the panel's most alarming answer out of missing data.
  for (const record of [{}, { agentId: '' }, { agentId: 'x' }, { agentId: '', createdBy: '' }, { agentId: 'x', createdBy: '   ' }]) {
    const r = spawnRecordLineage(record);
    assert.equal(r.selfRequested, false, `claimed a self-request from ${JSON.stringify(record)}`);
    assert.equal(r.requestedBy, '', `invented a requester for ${JSON.stringify(record)}`);
  }
});

test('whitespace does not hide a self-request', () => {
  assert.equal(spawnRecordLineage({ agentId: ' sc-coder ', createdBy: 'sc-coder' }).selfRequested, true);
});

test('the lineage fields it already carried are unchanged', () => {
  // The panel's original job. Adding a field must not disturb the vocabulary fix that preceded it.
  const compaction = {
    agentId: 'mc-senior-dev',
    createdBy: 'mc-senior-dev',
    spawnSpec: { metadata: { compactMode: 'handoff', compactedFromAgentId: 'mc-senior-dev', compactedFromSessionId: 's-9' } },
  };
  const r = spawnRecordLineage(compaction);
  assert.equal(r.mode, 'Compact');
  assert.equal(r.fromAgentId, 'mc-senior-dev');
  assert.equal(r.fromSessionId, 's-9');
  assert.equal(r.selfRequested, true);
});

test('it does not throw on the shapes a fetch can return', () => {
  for (const record of [undefined, {}, { spawnSpec: null }, { spawnSpec: {} }, { createdBy: 7 }]) {
    assert.equal(typeof spawnRecordLineage(record).selfRequested, 'boolean');
  }
});
