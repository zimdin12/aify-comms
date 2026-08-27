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
// MEASURED: all 21 self-issued spawn requests on that fleet are preceded, 45 to 75 seconds earlier,
// by exactly one of these messages. Every one.
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
