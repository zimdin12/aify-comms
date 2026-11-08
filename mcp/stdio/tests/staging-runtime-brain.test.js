import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { buildUserPrompt } from '../runtimes-prompts.js';
import { TurnBrain } from '../../../scripts/staging/fixtures/turn-brain.mjs';

async function world(work) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-brain-'));
  const fields = ['AIFY_AGENT_ID', 'STAGING_REPLY_TO'];
  const before = Object.fromEntries(fields.map(name => [name, process.env[name]]));
  const calls = [];
  const brain = new TurnBrain();
  brain.attach({ callTool: async value => { calls.push(structuredClone(value)); return {}; } }, directory);
  Object.assign(process.env, { AIFY_AGENT_ID: 'fixture-worker', STAGING_REPLY_TO: 'dashboard' });
  try { await work({ brain, calls, directory }); }
  finally {
    for (const [name, value] of Object.entries(before)) { if (value === undefined) delete process.env[name]; else process.env[name] = value; }
    fs.rmSync(directory, { recursive: true, force: true });
  }
}
const prompt = overrides => ({ input: [{ type: 'text', text: buildUserPrompt({ from: 'dashboard', type: 'request',
  messageId: 'actual-produced-message', subject: 'Fixture', body: 'E4-PROBE-abc123', requireReply: true, ...overrides }) }] });

test('the actual prompt producer supplies the exact parent and nonce used by the fixture reply', () => world(async ({ brain, calls }) => {
  await brain.respond(prompt());
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { name: 'comms_send', arguments: { from: 'fixture-worker', to: 'dashboard', type: 'response',
    subject: 'Staging work-loop reply', body: 'E4-REPLY-abc123', inReplyTo: 'actual-produced-message', requireReply: false } });
}));

test('a non-probe turn sends nothing', () => world(async ({ brain, calls }) => {
  await brain.respond(prompt({ body: 'ordinary startup', subject: 'No probe' }));
  assert.deepEqual(calls, []);
}));

test('a probe without the real producer message ID is refused before sending', () => world(async ({ brain, calls }) => {
  await assert.rejects(brain.respond(prompt({ messageId: '' })), /message identity/);
  assert.deepEqual(calls, []);
}));

for (const field of ['AIFY_AGENT_ID', 'STAGING_REPLY_TO']) {
  test(`a probe with missing ${field} is refused before sending`, () => world(async ({ brain, calls }) => {
    delete process.env[field];
    await assert.rejects(brain.respond(prompt()), /message identity/);
    assert.deepEqual(calls, []);
  }));
}

test('a refused real MCP send never becomes a successful fixture reply', () => world(async ({ brain, directory }) => {
  brain.attach({ callTool: async () => ({ isError: true }) }, directory);
  await assert.rejects(brain.respond(prompt()), /MCP reply was refused/);
}));
