import assert from 'node:assert/strict';
import test from 'node:test';

import { classifyReadBack, inspectArgs } from '../../../scripts/sandbox/docker-readback.mjs';
import { StagingLayout } from '../../../scripts/staging/layout.mjs';
import { StagingResources } from '../../../scripts/staging/resources.mjs';

// E4 needs a named volume to survive the service rebuild. Existing container/network/image
// controls keep a volume-only fix from weakening Docker's exact-name, three-state readback.
const name = 'aify-comms-staging-0123456789ab-data';

test('a staging volume is inspected as a volume, never as a container', () => {
  assert.deepEqual(inspectArgs({ kind: 'volume', name }), ['volume', 'inspect', name]);
});

test('Docker not-found for this volume proves ABSENT', () => {
  assert.equal(classifyReadBack({ kind: 'volume', name }, {
    status: 1, out: `Error response from daemon: get ${name}: no such volume\n`,
  }), 'absent');
});

for (const kind of ['container', 'network', 'image', 'volume']) {
  test(`a successful ${kind} inspection is PRESENT`, () => {
    assert.equal(classifyReadBack({ kind, name }, { status: 0, out: '[{}]' }), 'present');
  });
  for (const [label, answer] of [
    ['daemon unavailable', { status: 1, out: 'Cannot connect to the Docker daemon' }],
    ['timeout', { status: null, out: '' }],
    ['another resource missing', { status: 1, out: 'Error: No such object: another-name' }],
  ]) {
    test(`${kind} ${label} is UNKNOWN, not ABSENT`, () => {
      assert.equal(classifyReadBack({ kind, name }, answer), 'unknown');
    });
  }
}

for (const [kind, out] of [
  ['container', `Error: No such container: ${name}`],
  ['network', `Error response from daemon: network ${name} not found`],
  ['image', `Error response from daemon: No such image: ${name}:latest`],
]) {
  test(`existing ${kind} exact-name absence stays supported`, () => {
    assert.equal(classifyReadBack({ kind, name }, { status: 1, out }), 'absent');
  });
  test(`cancelled ${kind} inspection cannot prove absence from residual output`, () => {
    assert.equal(classifyReadBack({ kind, name }, { status: null, out }), 'unknown');
  });
}

for (const kind of [undefined, '', 'not-a-docker-resource']) {
  test(`unsupported resource kind ${String(kind)} fails closed`, () => {
    assert.throws(() => inspectArgs({ kind, name }), /resource kind/);
    assert.throws(() => classifyReadBack({ kind, name }, { status: 0, out: '[{}]' }), /resource kind/);
  });
}

class DockerFixture {
  records = new Map();
  calls = [];
  unavailable = new Set();

  add(target, runId) {
    const labels = { 'aify.staging.run': runId };
    const record = { Name: target.name, Id: `immutable-${target.kind}-${target.name}` };
    if (target.kind === 'container' || target.kind === 'image') record.Config = { Labels: labels };
    else record.Labels = labels;
    this.records.set(`${target.kind}:${target.name}`, record);
  }

  run(args) {
    this.calls.push([...args]);
    if (args[1] === 'inspect') {
      const [kind, , resource] = args;
      const record = this.records.get(`${kind}:${resource}`);
      if (this.unavailable.has(resource)) return { status: null, out: JSON.stringify(record ?? {}) };
      if (record) return { status: 0, out: JSON.stringify(kind === 'volume' ? record : [record]) };
      const messages = {
        container: `No such container: ${resource}`, image: `No such image: ${resource}`,
        network: `network ${resource} not found`, volume: `get ${resource}: no such volume`,
      };
      return { status: 1, out: `Error response from daemon: ${messages[kind]}` };
    }
    const kind = args[0] === 'rm' ? 'container' : args[0];
    const handle = args.at(-1);
    for (const [key, record] of this.records) {
      if (key.startsWith(`${kind}:`) && (record.Id === handle || record.Name === handle)) this.records.delete(key);
    }
    return { status: 0, out: '' };
  }
}

const resourceFixture = () => {
  const layout = new StagingLayout({ runId: '0123456789ab', fixtureKey: 'fixture', ports: { service: 8820, dashboard: 8821 } });
  const docker = new DockerFixture();
  const ledger = new StagingResources({ layout, docker: (args) => docker.run(args) });
  return { layout, docker, ledger };
};

for (const kind of ['container', 'network', 'image', 'volume']) {
  test(`owned ${kind} is witnessed PRESENT before removal and ABSENT after`, () => {
    const { layout, docker, ledger } = resourceFixture();
    const target = ledger.plan({ kind, name: `${layout.project}-${kind}` });
    docker.add(target, layout.runId);
    const result = ledger.cleanup();
    assert.equal(result.ok, true);
    assert.deepEqual(result.receipts.map(({ before, after }) => [before, after]), [['present', 'absent']]);
    const removal = docker.calls.find((args) => args[0] === 'rm' || args[1] === 'rm');
    assert.equal(removal.at(-1), kind === 'volume' ? target.name : `immutable-${kind}-${target.name}`);
    assert.equal(docker.records.size, 0);
  });

  test(`a foreign ${kind} beside an owned resource survives`, () => {
    const { layout, docker, ledger } = resourceFixture();
    const target = ledger.plan({ kind, name: `${layout.project}-foreign-${kind}` });
    docker.add(target, 'another-run');
    const healthy = ledger.plan({ kind: 'volume', name: `${layout.project}-healthy` });
    docker.add(healthy, layout.runId);
    const result = ledger.cleanup();
    assert.equal(result.ok, false);
    assert.ok(docker.records.has(`${kind}:${target.name}`));
    assert.equal(docker.records.has(`volume:${healthy.name}`), false, 'healthy control: cleanup did not run');
    assert.ok(result.receipts.some((entry) => entry.before === 'foreign'));
  });
}

test('UNKNOWN readback fails cleanup and does not authorize a removal', () => {
  const { layout, docker, ledger } = resourceFixture();
  const target = ledger.plan({ kind: 'volume', name: layout.dataVolume });
  docker.add(target, layout.runId);
  docker.unavailable.add(target.name);
  assert.equal(ledger.cleanup().ok, false);
  assert.ok(docker.records.has(`volume:${target.name}`));
  assert.equal(docker.calls.some((args) => args[1] === 'rm'), false);
});

test('a name outside this run is refused before any Docker command', () => {
  const { docker, ledger } = resourceFixture();
  assert.throws(() => ledger.plan({ kind: 'volume', name: 'aify-comms-data' }), /outside this staging run/);
  assert.equal(docker.calls.length, 0);
  assert.deepEqual(ledger.snapshot(), []);
});

test('a pre-existing resource cannot be adopted even when its run label matches', () => {
  const { layout, docker, ledger } = resourceFixture();
  const target = { kind: 'volume', name: layout.dataVolume };
  docker.add(target, layout.runId);
  assert.throws(() => ledger.plan(target), /already exists/);
  assert.deepEqual(ledger.snapshot(), []);
});

test('refused reservation never authorizes cleanup of a pre-existing volume', () => {
  const { layout, docker, ledger } = resourceFixture();
  const healthy = ledger.plan({ kind: 'volume', name: `${layout.project}-healthy` });
  docker.add(healthy, layout.runId);
  const target = { kind: 'volume', name: layout.dataVolume };
  docker.add(target, layout.runId);
  try { ledger.plan(target); } catch { /* cleanup runs after refusal, as in the entry point */ }
  ledger.cleanup();
  assert.equal(docker.records.has(`volume:${healthy.name}`), false, 'control: actual cleanup did not run');
  assert.ok(docker.records.has(`volume:${target.name}`), 'pre-existing volume was removed');
  assert.equal(docker.calls.some((args) => args[0] === 'volume' && args[1] === 'rm' && args[2] === target.name), false);
});

test('UNKNOWN pre-creation inspection cannot reserve a resource', () => {
  const { layout, docker, ledger } = resourceFixture();
  docker.unavailable.add(layout.dataVolume);
  assert.throws(() => ledger.plan({ kind: 'volume', name: layout.dataVolume }), /absence/);
  assert.deepEqual(ledger.snapshot(), []);
});
