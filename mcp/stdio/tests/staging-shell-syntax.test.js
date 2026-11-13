import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { CHECK_SHELL_LAUNCHERS } from '../../../scripts/staging/shell-syntax.mjs';
import { sealedChildEnv } from './_child-env.mjs';

function probe(t, files) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-shell-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const bin = path.join(home, '.local', 'bin');
  fs.mkdirSync(bin, { recursive: true });
  for (const [name, content] of Object.entries(files)) fs.writeFileSync(path.join(bin, name), content);
  return spawnSync('bash', ['-c', CHECK_SHELL_LAUNCHERS], {
    env: sealedChildEnv({ HOME: home, USERPROFILE: home }), encoding: 'utf8', timeout: 10000,
  });
}
const shell = '#!/bin/bash\nprintf "not executed"\n';

test('healthy shell population passes without executing it', t => {
  const result = probe(t, { 'first-aify': shell, 'second-aify': shell });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.deepEqual(result.stdout.trim().split('\n'), ['syntax-ok first-aify', 'syntax-ok second-aify']);
});

test('a JavaScript command in the installed population is not parsed as Bash', t => {
  const result = probe(t, { 'first-aify': shell, 'herdr-aify': '#!/usr/bin/env node\nconst tool = { spawn(command, argv) {} };\n',
    'second-aify': shell });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '', 'the JS command must not reach bash -n');
  assert.deepEqual(result.stdout.trim().split('\n'), ['syntax-ok first-aify', 'syntax-ok second-aify']);
});

test('selection follows the Bash shebang, not a launcher-name suffix', t => {
  const result = probe(t, { 'different name': '#!/usr/bin/env bash\ntrue\n', 'plain-data': 'unrelated text\n' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.trim(), 'syntax-ok different name');
});

test('invalid Bash refuses the syntax probe', t => {
  const result = probe(t, { 'broken-aify': '#!/bin/bash\nif then\n', 'second-aify': shell });
  assert.notEqual(result.status, 0, 'a later healthy launcher must not hide the syntax failure');
  assert.match(result.stderr, /syntax error/);
  assert.doesNotMatch(result.stdout, /syntax-ok broken-aify/);
});
