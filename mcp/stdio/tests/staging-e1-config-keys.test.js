// Execute E1's actual diagnostic literal against private config files, never the real home.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

const source = fs.readFileSync(new URL('../../../scripts/staging/scenarios/e1.mjs', import.meta.url), 'utf8');
const matches = [...source.matchAll(/s\.save\('claude-config-key-names\.json'.*?'node', '-e', '([^']+)'/g)];
assert.equal(matches.length, 1, 'one actual doctor-time diagnostic command must be present');
const program = matches[0][1];
const secret = 'synthetic-value-must-never-be-printed';

for (const fixture of [
  { name: 'valid config emits names only', text: JSON.stringify({ mcpServers: { fixture: secret }, syntheticSecret: secret }), status: 0, expected: { keys: ['mcpServers', 'syntheticSecret'] } },
  { name: 'missing config reports only the error code', status: 1, expected: { error: 'ENOENT' } },
  { name: 'malformed config never prints input or parser detail', text: `{"syntheticSecret":"${secret}`, status: 1, expected: { error: 'invalid-json' } },
]) test(fixture.name, () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'e1-key-names-'));
  try {
    if (fixture.text !== undefined) fs.writeFileSync(path.join(home, '.claude.json'), fixture.text);
    const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => /^(PATH|PATHEXT|SYSTEMROOT|WINDIR|COMSPEC)$/i.test(name)));
    Object.assign(env, { HOME: home, USERPROFILE: home });
    const result = spawnSync(process.execPath, ['-e', program], { env, cwd: home, encoding: 'utf8', timeout: 5000 });
    assert.equal(result.status, fixture.status, result.stderr);
    assert.equal(result.stderr, '');
    assert.deepEqual(JSON.parse(result.stdout), fixture.expected);
    assert.equal(result.stdout.includes(secret), false, 'configuration values must never reach the evidence');
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});
