#!/usr/bin/env node
// Tests for cli-resume.mjs — the drawer's "Continue in CLI" command and its no-command REASON.
//
// Run: node --test service/new_dashboard/cli-resume.test.mjs

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { continueCliInfo, CLI_RESUME_RUNTIMES } from './cli-resume.mjs';

test('claude-code builds the claude-aify resume command', () => {
  const { command, reason } = continueCliInfo({ id: 'graph-tech-lead', runtime: 'claude-code', sessionHandle: 'd8ba8de3' });
  assert.equal(reason, '');
  assert.equal(command, 'claude-aify --aify-agent graph-tech-lead --dangerously-skip-permissions --resume d8ba8de3');
});

test('hermes builds the hermes-aify resume command', () => {
  const { command } = continueCliInfo({ id: 'sc-coder', runtime: 'hermes', sessionHandle: 'abc123' });
  assert.equal(command, 'hermes-aify --aify-agent sc-coder --resume abc123');
});

test('codex carries the env explicitly so a copied command is self-contained', () => {
  const { command } = continueCliInfo({ id: 'ef-tech-lead', runtime: 'codex', sessionHandle: 'thr-9' });
  assert.match(command, /^AIFY_RUNTIME=codex AIFY_AGENT_ID=ef-tech-lead /);
  assert.match(command, /CODEX_THREAD_ID=thr-9/);
  assert.match(command, /codex --no-alt-screen resume --include-non-interactive thr-9$/);
});

// THE OPERATOR REPORT (2026-07-28): "llama-manager does not have cli command that i can copy".
// It is runtime claude-code with an EMPTY sessionHandle — there is nothing to resume. Eleven agents
// on the live fleet are in that state. Rendering nothing made a legitimate "no session yet" look
// like a broken feature.
test('no session handle yields NO command but an explanatory reason', () => {
  const { command, reason } = continueCliInfo({ id: 'llama-manager', runtime: 'claude-code', sessionHandle: '' });
  assert.equal(command, '');
  assert.match(reason, /nothing to resume/i);
  assert.ok(reason.length > 20, 'the reason must actually explain, not just say "none"');
});

test('a missing handle is detected however the field is spelled/absent', () => {
  for (const agent of [
    { id: 'a', runtime: 'claude-code' },
    { id: 'a', runtime: 'claude-code', sessionHandle: null },
    { id: 'a', runtime: 'claude-code', sessionHandle: '   ' },
    { id: 'a', runtime: 'claude-code', session_handle: '' },
  ]) {
    assert.equal(continueCliInfo(agent).command, '', JSON.stringify(agent));
  }
});

test('the handle can come from the SESSION when the agent row lacks it', () => {
  const { command } = continueCliInfo({ id: 'a', runtime: 'hermes' }, { session_handle: 'from-session' });
  assert.equal(command, 'hermes-aify --aify-agent a --resume from-session');
});

// pi is deliberately unsupported: install.sh does not install a pi/omp resident wrapper, so there is
// no command to give. Inventing one would hand the operator something that cannot work.
test('an unsupported runtime explains itself rather than fabricating a command', () => {
  const { command, reason } = continueCliInfo({ id: 'graph-tester-pi', runtime: 'pi', sessionHandle: 'p-1' });
  assert.equal(command, '', 'must NOT invent a pi-aify command — that wrapper is not installed');
  assert.match(reason, /not supported/i);
  assert.match(reason, /pi/, 'name the runtime so the message is actionable');
  assert.equal(CLI_RESUME_RUNTIMES.has('pi'), false);
});

test('an unknown/blank runtime is handled without throwing', () => {
  for (const runtime of [undefined, '', 'opencode', 'something-new']) {
    const { command, reason } = continueCliInfo({ id: 'a', runtime, sessionHandle: 'h' });
    assert.equal(command, '');
    assert.ok(reason, `runtime=${String(runtime)} must still explain itself`);
  }
});

test('a missing agent id degrades to a command without the --aify-agent flag', () => {
  // The wrappers now recover the id from the handle, so a flagless command is still usable.
  const { command } = continueCliInfo({ runtime: 'hermes', sessionHandle: 'h9' });
  assert.equal(command, 'hermes-aify --resume h9');
});

test('null/undefined inputs never throw', () => {
  assert.equal(continueCliInfo(null, null).command, '');
  assert.equal(continueCliInfo(undefined, undefined).command, '');
  assert.ok(continueCliInfo(null, null).reason);
});
