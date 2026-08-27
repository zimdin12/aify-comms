// A session control sends no body, because the body becomes an instruction to the agent.
//
// THE LOOP, traced end to end on the operator's live database:
//
//   1. Operator clicks Restart. This module POSTed `/sessions/{id}/control` with
//      `body: "Session {action} requested from Dashboard Next."`.
//   2. `session_restart.py` stores that as the spawn request's `initial_message`, with
//      `subject = f"{action.title()} {agent_id}"` -- "Restart mc-vulkan-manager".
//   3. The spawn settles to `running` and `_hand_settled_spawn_to_dispatch` turns a NON-EMPTY
//      `initial_message` into a real `type=request` message plus a dispatch run, addressed to the
//      agent that just came up.
//   4. The agent gets "Restart mc-vulkan-manager / Session restart requested from Dashboard Next."
//      as a request owing a reply, reads it as an instruction, and calls comms_restart on itself.
//   5. Go to 2.
//
// MEASURED, and RE-MEASURED 2026-08-27 because the first reading was both stale and too strong. It
// said "all 21 ... Every one". The table now holds 23, and 16 of them are preceded 33 to 76 seconds
// earlier by a dashboard restart message addressed to that SAME agent. The other seven match nothing.
//
// That is a better result than the absolute claim, not a weaker one. All 16 matches are members of the
// six BURSTS this loop produces; all seven non-matches are singletons 39+ minutes apart. So the loop
// explains the CLUSTERING specifically -- which was the part nobody could account for -- rather than
// every self-restart that has ever happened. And the messages table reaches back to 2026-04-28, before
// the oldest unmatched spawn, so those seven are genuinely unexplained rather than simply unrecorded.
//
// THE SERVICE IS NOT THE DEFECT. `initial_message` is for a BRIEF, and turning it into a message is
// deliberate -- so the new worker's inbox is not empty and it has an id to thread a reply to. The
// mistake was sending a RECEIPT where a brief goes. `comms_restart` on the bridge sends no body, and
// this now matches it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, 'agent-session-actions.mjs'), 'utf8');

/** The control POST's payload literal, with `//` comments stripped.
 *
 *  Comments are removed because the explanation of why the body is gone NAMES the string it forbids
 *  -- and a raw match would read its own reasoning as the defect. That mistake has been made five
 *  times in this repo today; it is cheaper to strip than to keep rediscovering.
 */
function controlPayload() {
  const call = /await api\(`\/sessions\/\$\{encodeURIComponent\(sessionId\)\}\/control`[\s\S]*?\n    \}\);/.exec(SRC);
  assert.ok(call, 'positive control: the /sessions/{id}/control call was not found');
  return call[0].split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
}

test('the control POST carries NO body', () => {
  const payload = controlPayload();
  assert.doesNotMatch(
    payload, /\bbody:\s*`/,
    'a control is sending a brief again; it will be delivered to the restarted agent as a request',
  );
  assert.doesNotMatch(payload, /requested from Dashboard Next/, 'the receipt string is back in the payload');
});

test('it still sends what a control actually needs', () => {
  // ANTI-VACUITY. Removing the whole payload would satisfy the test above and break Restart
  // entirely -- the route needs the action, and `from_agent` is what stamps the audit trail.
  const payload = controlPayload();
  assert.match(payload, /action,/, 'the control no longer says which action');
  assert.match(payload, /from_agent: 'dashboard'/, 'the control no longer says who asked');
});

test('the REASON is written where the next person will change it back', () => {
  // This is a one-line omission whose absence is invisible: adding a friendly note to the payload
  // looks harmless and reopens the loop. The comment is the only thing standing in the way, so its
  // presence is asserted rather than assumed.
  const call = /await api\(`\/sessions\/\$\{encodeURIComponent\(sessionId\)\}\/control`[\s\S]*?\n    \}\);/.exec(SRC);
  assert.ok(call);
  assert.match(call[0], /initial_message/, 'the note no longer explains what the body becomes');
  assert.match(call[0], /comms_restart on itself|restart on itself/i, 'the note no longer names the loop');
});

test('every OTHER call in this module is untouched by the rule', () => {
  // Scoped on purpose. `body:` is how every other api() call in this file sends its payload, and a
  // module-wide ban on the word would be wrong -- this rule is about ONE route.
  const others = (SRC.match(/await api\(/g) || []).length;
  assert.ok(others > 1, `positive control: only ${others} api() call found in this module`);
  assert.match(SRC, /body: JSON\.stringify/, 'other calls still send bodies, as they must');
});

test('there is exactly ONE control POST, which is the only reason the BULK path is safe', () => {
  // THE CLUSTERS. `requestBulkSessionControl` restarts every selected session, and it is safe purely
  // because it DELEGATES to `requestSessionControl` rather than issuing its own POST. Nothing asserted
  // that, so a future bulk path with its own `api(.../control)` would reintroduce the brief for N
  // agents at once and every test above would still pass.
  //
  // That is not hypothetical -- it is what the clusters were. Re-measured on the live database
  // 2026-08-27, self-issued spawn requests (`created_by = agent_id`) arrive in SIX bursts of two or
  // three agents within 1-48 seconds, separated by 38 to 229 minutes of quiet:
  //
  //     12:02:17 mc-senior-dev  +17s comms-senior-dev
  //     13:07:27 mc-senior-dev  +6s  comms-senior-dev  +31s mc-vulkan-manager
  //     13:46:08 mc-senior-dev  +13s comms-senior-dev  +48s graph-senior-dev
  //     14:52:46 graph-senior   +2s  comms-senior-dev  +22s mc-senior-dev
  //     18:42:23 mc-senior-dev  +1s  comms-senior-dev  +4s  graph-senior-dev
  //     19:08:16 comms-senior   +6s  graph-senior-dev
  //
  // Every one of those 16 follows a dashboard restart message addressed to that SAME agent, 33 to 76
  // seconds earlier. One bulk restart reached ELEVEN agents inside 48 seconds. The remaining seven
  // self-issued spawns in the table are all SINGLETONS 39+ minutes apart and match no message, so the
  // loop explains the clustering specifically rather than every self-restart -- and the message table
  // reaches back to 2026-04-28, so those seven are unexplained rather than merely unrecorded.
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'agent-session-actions.mjs'), 'utf8');
  const controlPosts = source.match(/\/control`/g) || [];
  assert.equal(
    controlPosts.length, 1,
    `${controlPosts.length} control POST sites. The bulk path must delegate to requestSessionControl, `
    + 'not build its own request: a second site is a second place to reintroduce the brief.',
  );
});

test('the bulk path reaches the control through the single-session function', () => {
  // The other half of the pair above. One POST site proves there is nowhere else to put a body; this
  // proves BULK actually goes through it rather than having quietly stopped calling it.
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'agent-session-actions.mjs'), 'utf8');
  const bulk = source.slice(source.indexOf('export async function requestBulkSessionControl'));
  const body = bulk.slice(0, bulk.indexOf('\n}\n'));
  assert.match(body, /requestSessionControl\(/, 'the bulk path no longer delegates to the fixed function');
});
