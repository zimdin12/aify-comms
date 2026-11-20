// The stdio bridge's exit removes every binding it wrote, whichever parent each write was keyed on.
// Linux reparents an orphaned bridge, so process.ppid can change before a write (a late registration
// answer) or after it (before exit). An unrelated binding, and a binding a successor bridge owns, stay.
// The writers are shared with ResidentDuties and keep keying on process.ppid at write time.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const server = new URL('../server.js', import.meta.url).href;
const bindingFile = new URL('../binding-file.js', import.meta.url).href;
const instance = new URL('../bridge-instance.mjs', import.meta.url).href;
const autoRegistration = new URL('../auto-registration.mjs', import.meta.url).href;
const registrationTool = new URL('../registration-tool.mjs', import.meta.url).href;
const endpoint = 'http://127.0.0.2:1';
const UNRELATED = { agentId: 'other-agent', bridgeId: 'other-bridge', pid: 73429 };

function withBridge({ agentId = '', script }) {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aify-binding-owner-'));
  try {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      /^(SYSTEMROOT|SYSTEMDRIVE|WINDIR|PATH|PATHEXT|COMSPEC)$/i.test(key)));
    Object.assign(env, { HOME: home, USERPROFILE: home, TEMP: home, TMP: home, TMPDIR: home,
      APPDATA: home, LOCALAPPDATA: home, AIFY_HOME: home, AIFY_AGENT_ID: agentId,
      AIFY_RUNTIME: 'generic', AIFY_SESSION_MODE: 'resident',
      AIFY_SERVICE_REGISTRY: path.join(home, 'services.json'),
      AIFY_SERVER_URL: endpoint, AIFY_SERVER_FALLBACK_URLS: endpoint });
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import { existsSync, writeFileSync } from 'node:fs';
      import { createRequire } from 'node:module';
      globalThis.fetch = () => assert.fail('unexpected service request');
      let parent = 73427;
      Object.defineProperty(process, 'ppid', { get: () => parent });
      const { BRIDGE_INSTANCE_ID } = await import(${JSON.stringify(instance)});
      const { writeAgentBindingFile } = await import(${JSON.stringify(bindingFile)});
      await import(${JSON.stringify(server)});
      // Another process's binding, written as that process would write it.
      writeFileSync('aify-agent-73429', JSON.stringify(${JSON.stringify(UNRELATED)}));
      ${script}
    `], { cwd: home, env, encoding: 'utf8', timeout: 10000 });
    assert.equal(child.status, 0, child.error?.message ?? child.stderr);
    const left = readdirSync(home).filter(name => name.startsWith('aify-agent-'));
    assert.deepEqual(left, ['aify-agent-73429'], 'exit left a binding this bridge wrote');
    assert.deepEqual(JSON.parse(readFileSync(path.join(home, 'aify-agent-73429'), 'utf8')), UNRELATED);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

for (const currentParent of [73427, 73428]) {
  test(`exit releases an early binding when the parent PID at exit is ${currentParent}`, () => {
    withBridge({ script: `
      assert.ok(writeAgentBindingFile({ pid: parent, agentId: 'fixture-agent', bridgeId: BRIDGE_INSTANCE_ID }));
      assert.ok(existsSync('aify-agent-73427'));
      parent = ${currentParent};
    ` });
  });
}

for (const mode of ['automatic', 'explicit']) {
  test(`exit releases a binding ${mode} registration wrote after the parent changed`, () => {
    withBridge({ agentId: 'fixture-agent', script: `
      const allowed = new Set(['POST /api/v1/agents', 'GET /api/v1/agents/fixture-agent',
        'PATCH /api/v1/agents/fixture-agent/runtime-state', 'GET /api/v1/agents']);
      globalThis.fetch = async (url, { method, body }) => {
        const target = new URL(url);
        const request = method + ' ' + target.pathname;
        assert.equal(target.origin, ${JSON.stringify(endpoint)});
        assert.ok(allowed.has(request), request);
        // The original parent exits while the registration POST is outstanding.
        if (method === 'POST') { assert.equal(JSON.parse(body).agentId, 'fixture-agent'); parent = 73428; }
        return { ok: true, json: async () => ({ agentId: 'fixture-agent', role: 'coder' }) };
      };
      const ensureDispatchLoop = () => {};
      if (${mode === 'automatic'}) {
        const { makeAutoRegister } = await import(${JSON.stringify(autoRegistration)});
        await makeAutoRegister({ ensureDispatchLoop })();
      } else {
        const toolUrl = new URL(${JSON.stringify(registrationTool)});
        const { registerRegistrationTool } = await import(toolUrl);
        const { z } = createRequire(toolUrl)('zod');
        let register;
        registerRegistrationTool({ tool: (_name, _description, _schema, handler) => { register = handler; } }, z, { ensureDispatchLoop });
        // The tool writes before its POST, so its late case is a call made after the parent changed.
        parent = 73428;
        const result = await register({ agentId: 'fixture-agent', role: 'coder', runtime: 'generic' });
        assert.match(result.content[0].text, /Registered "fixture-agent"/);
      }
      assert.ok(existsSync('aify-agent-73428'), 'the late write did not land under the current parent');
    ` });
  });
}

const REPLACEMENTS = {
  'a successor bridge': JSON.stringify({ agentId: 'fixture-agent', bridgeId: 'successor', pid: 73427 }),
  'an id-less legacy writer': 'legacy-agent',
  'a writer without a bridge id': JSON.stringify({ agentId: 'legacy-agent', pid: 73427 }),
  'unreadable data': '{not json',
};

for (const [who, replacement] of Object.entries(REPLACEMENTS)) test(`exit leaves a binding ${who} put in its place`, () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aify-binding-owner-'));
  try {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      /^(SYSTEMROOT|SYSTEMDRIVE|WINDIR|PATH|PATHEXT|COMSPEC)$/i.test(key)));
    Object.assign(env, { HOME: home, USERPROFILE: home, TEMP: home, TMP: home, TMPDIR: home,
      APPDATA: home, LOCALAPPDATA: home, AIFY_HOME: home, AIFY_AGENT_ID: '',
      AIFY_SERVER_URL: endpoint, AIFY_SERVER_FALLBACK_URLS: endpoint });
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import { writeFileSync } from 'node:fs';
      globalThis.fetch = () => assert.fail('unexpected service request');
      let parent = 73427;
      Object.defineProperty(process, 'ppid', { get: () => parent });
      const { BRIDGE_INSTANCE_ID } = await import(${JSON.stringify(instance)});
      const { writeAgentBindingFile } = await import(${JSON.stringify(bindingFile)});
      await import(${JSON.stringify(server)});
      assert.ok(writeAgentBindingFile({ pid: 73427, agentId: 'fixture-agent', bridgeId: BRIDGE_INSTANCE_ID }));
      // Reparented, and the original parent's path now holds someone else's binding.
      parent = 73428;
      writeFileSync('aify-agent-73427', ${JSON.stringify(replacement)});
    `], { cwd: home, env, encoding: 'utf8', timeout: 10000 });
    assert.equal(child.status, 0, child.error?.message ?? child.stderr);
    assert.equal(readFileSync(path.join(home, 'aify-agent-73427'), 'utf8'), replacement, 'exit removed a binding it no longer owns');
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
