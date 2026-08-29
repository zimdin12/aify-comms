#!/usr/bin/env node
// Tests for cli-resume.mjs — the drawer's "Continue in CLI" command and its no-command REASON.
//
// Run: node --test service/new_dashboard/cli-resume.test.mjs

import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CLI_RESUME_RUNTIMES,
  continueCliCommand,
  continueCliDetails,
  continueCliInfo,
  resumeMachineNote,
} from "./cli-resume.mjs";

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

// ── N10 / N11 (bug-hunt 2026-07-31) ────────────────────────────────────────────────────────────
// Both defects shipped in v0.1.1, in the fix that was supposed to stop this surface lying.

test('N10: the machine that owns the session is carried out of the mapping', () => {
  const { command, machine } = continueCliInfo({
    id: 'lc-coder', runtime: 'codex', sessionHandle: 'h1', machineId: 'linux:laputa',
  });
  assert.ok(command, 'a command is still produced');
  assert.equal(machine, 'linux:laputa', 'the caller must be able to say WHERE this runs');
});

test('N10: the machine note names the host, so the command is true wherever it is pasted', () => {
  const note = resumeMachineNote('linux:laputa');
  assert.match(note, /linux:laputa/);
  assert.match(note, /not resume anywhere else/i);
});

test('N10: an unknown machine says so rather than implying "here"', () => {
  const note = resumeMachineNote('');
  assert.match(note, /unknown/i);
  assert.doesNotMatch(note, /undefined|null/);
});

test('N10: machine survives the snake_case and session-carried spellings', () => {
  assert.equal(continueCliInfo({ id: 'a', runtime: 'hermes', sessionHandle: 'h', machine_id: 'm-snake' }).machine, 'm-snake');
  assert.equal(
    continueCliInfo({ id: 'a', runtime: 'hermes', sessionHandle: 'h' }, { machineId: 'm-sess' }).machine,
    'm-sess',
    'the session row carries it when the agent row does not',
  );
});

test('N10: the no-command branches still report the machine', () => {
  // Whichever way the block renders, the operator should learn which host is involved.
  assert.equal(continueCliInfo({ id: 'a', runtime: 'claude-code', sessionHandle: '', machineId: 'm1' }).machine, 'm1');
  assert.equal(continueCliInfo({ id: 'a', runtime: 'pi', sessionHandle: 'h', machineId: 'm1' }).machine, 'm1');
});

// N11: 3 of the 4 live codex agents holding a handle are RESIDENT, and every one of them was handed
// the MANAGED CODEX_HOME — a store that cannot contain their rollout.
test('N11: a MANAGED codex session gets the managed CODEX_HOME', () => {
  const { command } = continueCliInfo({ id: 'gsd', runtime: 'codex', sessionHandle: 'h', sessionMode: 'managed' });
  assert.match(command, /CODEX_HOME="\$HOME\/\.local\/state\/aify-comms\/managed-codex-home"/);
});

test('N11: a RESIDENT codex session gets NO CODEX_HOME override', () => {
  const { command } = continueCliInfo({ id: 'tech-lead', runtime: 'codex', sessionHandle: 'h', sessionMode: 'resident' });
  assert.doesNotMatch(command, /CODEX_HOME/, 'the wrapper default ${CODEX_HOME:-$HOME/.codex} must apply');
  assert.match(command, /codex --no-alt-screen resume --include-non-interactive h$/);
});

test('N11: an UNKNOWN session mode does not assume managed', () => {
  // Absence of evidence is not evidence of managed. Overriding CODEX_HOME on a guess is what broke
  // the resident case; omitting it falls back to the wrapper default, which is right more often.
  for (const agent of [
    { id: 'x', runtime: 'codex', sessionHandle: 'h' },
    { id: 'x', runtime: 'codex', sessionHandle: 'h', sessionMode: '' },
    { id: 'x', runtime: 'codex', sessionHandle: 'h', session_mode: 'resident' },
  ]) {
    assert.doesNotMatch(continueCliInfo(agent).command, /CODEX_HOME/, JSON.stringify(agent));
  }
});

// ── continueCliDetails / continueCliCommand, moved from app.js in v0.5.4 ─────────────────────────
//
// These are the DEFAULT binding of the injection `continueCliInfo` exposes. The seam stays open — the
// tests above still supply their own readers — but callers no longer re-bind it at each call site, which
// is where a caller could quietly pass the wrong reader and get a command for the wrong runtime.

test("continueCliDetails binds the real record readers", () => {
  // A hermes session recorded in snake_case must resolve exactly as a camelCase one. If the binding passed
  // the wrong reader, the runtime would come back empty and the command would be for the wrong CLI.
  const agent = { id: "agent-a" };
  const camel = continueCliDetails(agent, { runtime: "claude-code", agentId: "agent-a" });
  const snake = continueCliDetails(agent, { runtime: "claude-code", agent_id: "agent-a" });
  assert.deepEqual(camel, snake, "both spellings must produce the same details");
});

test("continueCliCommand is exactly the command from the details", () => {
  // It exists so a caller wanting only the string does not have to know the shape of the details object.
  const agent = { id: "agent-a" };
  const session = { runtime: "claude-code", agentId: "agent-a" };
  assert.equal(continueCliCommand(agent, session), continueCliDetails(agent, session).command);
});

test("both survive an unknown runtime and a missing session", () => {
  // Rendered per agent row; one odd record must not blank the drawer.
  for (const session of [undefined, null, {}, { runtime: "nonsense" }]) {
    const details = continueCliDetails({ id: "a" }, session ?? {});
    assert.equal(typeof details, "object", `${JSON.stringify(session)} must still yield details`);
    const command = continueCliCommand({ id: "a" }, session ?? {});
    assert.ok(command === "" || typeof command === "string", "the command must be a string or empty");
  }
});

test("a session naming no runtime is explained as UNKNOWN, not as 'runtime'", () => {
  // `continueCliDetails` binds the real `sessionRuntime`, which used to answer the literal string
  // 'runtime' for a session that named none. So this sentence read "not supported for the runtime
  // runtime" and its own `|| 'unknown'` fallback was unreachable: the reader never returned falsy.
  const details = continueCliDetails({ id: "a", sessionHandle: "h-1" }, { id: "s1" });
  assert.equal(details.command, "", "an unknown runtime has no resume command");
  assert.match(details.reason, /unknown runtime/, "the sentence must name the gap, not repeat a sentinel");
  assert.doesNotMatch(details.reason, /the runtime runtime/);
});
