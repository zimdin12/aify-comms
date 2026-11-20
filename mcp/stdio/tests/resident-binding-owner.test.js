// The HTTP resident's exit (Claude channel, hermes loop) removes the bindings it wrote under whichever parent each
// write named, and only while they still name this process. The remembering and the owner match are
// binding-file.js's, covered in full by bridge-binding-owner.test.js; this proves ResidentDuties uses them.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const duties = new URL('../resident-duties.mjs', import.meta.url).href;
const bindingFile = new URL('../binding-file.js', import.meta.url).href;
const instance = new URL('../bridge-instance.mjs', import.meta.url).href;
const UNRELATED = JSON.stringify({ agentId: 'other-agent', bridgeId: 'other-bridge', pid: 73429 });

function leave(replacement, { at = 'aify-agent-73427' } = {}) {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aify-resident-binding-'));
  try {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      /^(SYSTEMROOT|SYSTEMDRIVE|WINDIR|PATH|PATHEXT|COMSPEC)$/i.test(key)));
    Object.assign(env, { HOME: home, USERPROFILE: home, TEMP: home, TMP: home, TMPDIR: home,
      APPDATA: home, LOCALAPPDATA: home, AIFY_HOME: home, AIFY_AGENT_ID: '',
      AIFY_SERVICE_REGISTRY: path.join(home, 'services.json'),
      AIFY_SERVER_URL: 'http://127.0.0.2:1', AIFY_SERVER_FALLBACK_URLS: 'http://127.0.0.2:1' });
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import { writeFileSync } from 'node:fs';
      globalThis.fetch = () => assert.fail('unexpected service request');
      let parent = 73427;
      Object.defineProperty(process, 'ppid', { get: () => parent });
      const { BRIDGE_INSTANCE_ID } = await import(${JSON.stringify(instance)});
      const { writeAgentBindingFile } = await import(${JSON.stringify(bindingFile)});
      const { ResidentDuties } = await import(${JSON.stringify(duties)});
      const residency = new ResidentDuties({ agentId: '', register: async () => {}, call: async () => ({}) });
      writeFileSync('aify-agent-73429', ${JSON.stringify(UNRELATED)});
      assert.ok(writeAgentBindingFile({ pid: parent, agentId: 'fixture-agent', bridgeId: BRIDGE_INSTANCE_ID }));
      parent = 73428;  // reparented before exit
      ${replacement === undefined ? '' : `writeFileSync(${JSON.stringify(at)}, ${JSON.stringify(replacement)});`}
      residency.dropBinding();
    `], { cwd: home, env, encoding: 'utf8', timeout: 10000 });
    assert.equal(child.status, 0, child.error?.message ?? child.stderr);
    return Object.fromEntries(readdirSync(home).filter(name => name.startsWith('aify-agent-'))
      .map(name => [name, readFileSync(path.join(home, name), 'utf8')]));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

test('a resident leaving after reparenting removes the binding it wrote under its first parent', () => {
  assert.deepEqual(leave(), { 'aify-agent-73429': UNRELATED });
});

test("a resident leaving after reparenting leaves the new parent's id-less binding alone", () => {
  assert.deepEqual(leave('legacy-agent', { at: 'aify-agent-73428' }), { 'aify-agent-73428': 'legacy-agent', 'aify-agent-73429': UNRELATED });
});
