import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { StagingStack } from '../stack.mjs';
import { StagingHost } from '../host.mjs';
import { assertRenderedIsolation } from '../observed-isolation.mjs';

export const alive = process => !['absent', 'unknown', 'Z', 'X'].includes(process.state);
export const ended = (before, after) => after.state === 'absent' || ['Z', 'X'].includes(after.state)
  || (after.state !== 'unknown' && after.startTicks !== before.startTicks);

export function runtimeWitnesses(s, host) {
  const text = s.requireCommand(['exec', host.name, 'node', '-e',
    "process.stdout.write(require('fs').readFileSync('/work/e4-runtime/runtime.jsonl','utf8'))"], 'runtime process identities');
  const records = text.trim().split('\n').map(line => JSON.parse(line));
  const pids = [...new Set([...host.ownedProcesses.map(row => row.pid),
    ...records.flatMap(row => [row.pid, row.bridgePid]).filter(Number.isSafeInteger)])];
  const witnesses = host.observeProcesses(pids).filter(alive);
  assert.ok(witnesses.length >= 3, 'real wrapper/runtime/MCP process witnesses are required');
  return witnesses;
}

/** A process this environment does not own; recovery must leave it running. */
export function startUnrelated(s, host) {
  s.requireCommand(['exec', '-d', host.name, 'node', '-e',
    "require('fs').writeFileSync('/work/unrelated.pid',String(process.pid));setInterval(()=>{},1000)"], 'unrelated process');
  const unrelatedPid = Number(s.requireCommand(['exec', host.name, 'node', '-e',
    "process.stdout.write(require('fs').readFileSync('/work/unrelated.pid','utf8'))"], 'unrelated process identity'));
  const unrelated = host.observeProcesses([unrelatedPid])[0];
  s.check('unrelated_initially_live', alive(unrelated), unrelated);
  return unrelated;
}

export async function waitEnded(s, host, before, id) {
  const deadline = Date.now() + 30_000;
  do {
    const after = host.observeProcesses(before.map(row => row.pid));
    if (after.every((row, index) => ended(before[index], row))) {
      s.check(id, true, { before, after });
      return;
    }
    await delay(250);
  } while (Date.now() < deadline);
  s.check(id, false, { before, after: host.observeProcesses(before.map(row => row.pid)) });
}

async function switchService(s, stack) {
  const rendered = s.writeCompose('candidate');
  assertRenderedIsolation(s.layout, 'candidate', rendered);
  s.save('candidate-rendered.json', rendered);
  stack.assertImages('candidate');
  s.requireCommand(s.composeArgs('baseline', 'stop', 'service', 'dashboard'), 'stop only staging service containers');
  s.requireCommand(s.composeArgs('candidate', 'up', '--no-start', '--no-deps', '--force-recreate',
    '--no-build', '--pull', 'never', 'service', 'dashboard'), 'candidate service creation, preserving host and volumes');
  stack.assertActualIsolation('candidate');
  s.requireCommand(s.composeArgs('candidate', 'start', 'service', 'dashboard'), 'candidate service start');
  stack.assertActualIsolation('candidate', 'after_start');
  await stack.waitForInternalHealth('candidate');
  await stack.qualifyNetworkEndpoint('candidate');
}

function installCandidate(s, host) {
  // Upgrade order, unlike cold bootstrap: service already rebuilt, then bridge assets, env, wrapper.
  const script = `set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export NPM_CONFIG_OFFLINE=true NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false
bash /src/candidate/comms/install.sh --standalone --client claude http://service:8800
bash /src/candidate/comms/install.sh --standalone --client codex http://service:8800 --with-hook
bash /src/candidate/env/install.sh --no-prompt
for client in claude codex; do
  bash /src/candidate/wrapper/install.sh --client "$client" --endpoint http://service:8800 --registry "$HOME/.aify/services.json" --bridge-dir "$HOME/.aify-comms/mcp/stdio" --native-base "$HOME/.aify-comms" --wrapper-dir /src/candidate/wrapper --host-repo /src/candidate/comms
done
for role in comms env wrapper; do printf 'source %s ' "$role"; git -C "/src/candidate/$role" rev-parse HEAD; done`;
  const output = s.requireCommand(['exec', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`, host.name,
    'bash', '-c', script], 'candidate owner installers');
  s.save('candidate-install.txt', output);
  for (const [role, ref] of Object.entries(s.manifest.candidate)) {
    s.check(`candidate_installed_source_${role}`, output.includes(`source ${role} ${ref}`), 'actual owner source HEAD');
  }
}

async function takeover(s, stack, host, previous) {
  s.requireCommand(['cp', fileURLToPath(new URL('../fixtures/observe-bind.mjs', import.meta.url)),
    `${host.name}:/work/observe-bind.mjs`], 'copy candidate bind observer');
  // Same real start without force must refuse and leave the incumbent/managed child alive.
  const refused = s.command(['exec', '-w', '/work', '-e', 'AIFY_NO_DASHBOARD=1', host.name,
    'node', '/src/candidate/env/bin/aify-env.mjs', '--port', '8802']);
  s.save('takeover-disabled.json', refused);
  s.check('takeover_disabled_control', refused.status === 69 && /--force/.test(refused.out)
    && JSON.parse(host.localHealth().out).pid === previous.pid
    && host.observeProcesses(host.workerWitnesses.map(row => row.pid)).every(alive),
  'without force, actual candidate start refuses beside the still-live predecessor');
  s.requireCommand(['exec', '-d', '-w', '/work', '-e', 'AIFY_NO_DASHBOARD=1',
    '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`, host.name, 'bash', '-c',
    'exec node --import /work/observe-bind.mjs /src/candidate/env/bin/aify-env.mjs --port 8802 --force > /work/candidate-env.log 2>&1'],
  'candidate environment takeover');
  const deadline = Date.now() + 75_000;
  while (Date.now() < deadline) {
    const result = host.localHealth();
    if (result.status === 0) {
      const health = JSON.parse(result.out);
      if (health.pid !== previous.pid && health.build && health.build === health.codeOnDisk) {
        const response = await stack.request('/api/v1/environments', s.layout.fixtureKey);
        const environments = response.status === 200 ? JSON.parse(response.body).environments : [];
        const row = environments.find(item => item.id === previous.environment.id
          && item.machineId === previous.environment.machineId && item.bridgeId
          && item.bridgeId !== previous.environment.bridgeId && item.spawnClaim?.canClaim === true);
        if (row) {
          const bind = JSON.parse(s.requireCommand(['exec', host.name, 'node', '-e',
            "process.stdout.write(require('fs').readFileSync('/work/candidate-bind.json','utf8'))"], 'actual candidate bind event'));
          s.save('candidate-advertisement-observation.json', { bind, observedAcceptedAtMs: Date.now(),
            bindToObservedAcceptedUpperBoundMs: Date.now() - bind.atMs, environment: row,
            limitation: 'polling upper bound to observation of a new accepted bridge, not exact first heartbeat receive time' });
          const process = s.requireCommand(['exec', host.name, 'ps', '-p', String(health.pid), '-o', 'pid=,stat=,args='], 'candidate owner OS identity');
          s.check('candidate_owner', bind.pid === health.pid && process.includes('/src/candidate/env/bin/aify-env.mjs'), { health, process, environment: row });
          return health;
        }
      }
    }
    await delay(250);
  }
  throw new Error('candidate never became the same-machine answering owner with a new accepted bridge');
}

export async function stopWorker(s, stack, host, witnesses, label = 'candidate', index = 0) {
  const response = await stack.request(`/api/v1/agents/${host.ids[index]}/control`, s.layout.fixtureKey,
    { method: 'POST', body: { action: 'stop', from_agent: 'dashboard' } });
  s.save(`${label}-stop-response.json`, response);
  assert.ok(response.status >= 200 && response.status < 300, 'real stop was refused');
  await waitEnded(s, host, witnesses, `${label}_stop_os_exit`);
  const health = JSON.parse(host.localHealth().out);
  s.check(`${label}_stop_owner_readback`, health.processes.length === 0, health.processes);
}

async function stoppedDefinition(s, stack, host, label) {
  const deadline = Date.now() + 30_000;
  do {
    const response = await stack.request(`/api/v1/agents/${host.ids[1]}`, s.layout.fixtureKey);
    const agent = response.status === 200 ? JSON.parse(response.body).agent : null;
    if (agent?.status === 'stopped') {
      s.check(`${label}_stopped_definition`, true, agent);
      return;
    }
    await delay(250);
  } while (Date.now() < deadline);
  throw new Error(`${label}: the actually stopped definition is not reported stopped`);
}

async function waitForStoppedBridgeExpiry(s, stack, host) {
  const first = await stack.request('/api/v1/bridges', s.layout.fixtureKey);
  assert.equal(first.status, 200, 'bridge population could not be read before doctor');
  const initial = JSON.parse(first.body);
  const window = initial.liveWithinSeconds;
  assert.ok(Number.isSafeInteger(window) && window > 0, 'bridge endpoint declares no liveness window');
  const startedAtMs = Date.now();
  const deadline = startedAtMs + Math.min(window * 1000, s.docker.timeoutMs);
  let last = initial;
  while (Date.now() < deadline) {
    const active = last.bridges?.some(row => row.agentId === host.ids[0]);
    const stopped = last.bridges?.some(row => row.agentId === host.ids[1]);
    if (active && !stopped) {
      s.check('stopped_bridge_settled_before_doctors', true, { waitedMs: Date.now() - startedAtMs,
        declaredLiveWithinSeconds: window, initial, final: last });
      return;
    }
    await delay(1000);
    const response = await stack.request('/api/v1/bridges', s.layout.fixtureKey);
    assert.equal(response.status, 200, 'bridge population became unreadable');
    last = JSON.parse(response.body);
  }
  s.save('doctor-bridge-settle-failure.json', { initial, final: last });
  throw new Error('stopped bridge did not expire within its declared liveness window, or active control disappeared');
}

export function runDoctors(s, host) {
  // Both doctors run in the owned home. No provider auth was provisioned or mounted.
  const commands = {
    comms: ['/home/staging/.aify-comms/mcp/stdio/doctor.js', '--repo', '/src/candidate/comms'],
    env: ['/src/candidate/env/bin/aify-env-doctor.mjs'],
  };
  const notRun = new Set(['usage-openai', 'claude-login', 'context-window']);
  const summaries = [];
  for (const [name, args] of Object.entries(commands)) {
    const result = s.command(['exec', '-w', '/work', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`,
      '-e', 'AIFY_COMMS_URL=http://service:8800', '-e', 'AIFY_ENV_ENDPOINT=http://127.0.0.1:8802',
      host.name, 'node', ...args, '--json', '--strict']);
    s.save(`${name}-doctor-output.json`, result);
    assert.ok(Number.isInteger(result.status) && !result.signal && !result.errorCode, `${name} doctor did not settle`);
    const report = JSON.parse(result.out);
    assert.ok(report.checks?.length, `${name} doctor returned no checks`);
    const excluded = report.checks.filter(row => notRun.has(row.id));
    for (const row of excluded) console.log(`NOT_RUN ${name}_${row.id}: provider qualification excluded`);
    const applicable = report.checks.filter(row => !notRun.has(row.id));
    const failures = applicable.filter(row => row.skipped || row.code === 'skipped'
      || (name === 'comms' ? row.ok !== true : row.state !== 'passed'));
    summaries.push({ name, exit: result.status, applicable, failures,
      notRun: excluded.map(row => ({ ...row, reason: 'provider qualification excluded' })) });
    s.save('doctor-summary.json', summaries);
  }
  for (const { name, exit, applicable, failures, notRun } of summaries) {
    s.check(`${name}_doctor_applicable`, applicable.length > 0 && failures.length === 0,
      { exit, failures, checked: applicable.map(row => row.id), notRun: notRun.map(row => row.id) });
  }
}

/** One actual switch. Helpers above name steps, not another orchestration framework. */
export async function run(s) {
  const stack = new StagingStack(s);
  const host = new StagingHost(s, stack);
  try {
    stack.qualifyDaemon();
    s.build();
    stack.create('baseline');
    await stack.start('baseline');
    host.install('baseline');
    host.installRuntime();
    host.defineFleet('baseline');
    const baseline = await host.startBaseline();
    await host.waitForDefinitions('baseline');
    await host.startWorker('baseline-stopped', 1);
    await stopWorker(s, stack, host, runtimeWitnesses(s, host), 'baseline-stopped', 1);
    await stoppedDefinition(s, stack, host, 'baseline');
    await host.startWorker();
    await host.probeWorkloop('baseline');
    const owned = runtimeWitnesses(s, host);
    const owner = host.observeProcesses([baseline.pid])[0];
    const control = host.observeProcesses([owner.pid, 2147483646]);
    s.check('os_probe_controls', alive(control[0]) && control[1].state === 'absent', control);
    const unrelated = startUnrelated(s, host);
    const environments = JSON.parse((await stack.request('/api/v1/environments', s.layout.fixtureKey)).body).environments;
    baseline.environment = environments.find(row => row.bridgeId && row.spawnClaim?.canClaim === true);
    assert.ok(baseline.environment, 'baseline accepted environment identity absent');
    s.save('takeover-before.json', { baseline, owner, owned, unrelated, definitions: host.definitions });
    await switchService(s, stack);
    installCandidate(s, host);
    await takeover(s, stack, host, baseline);
    await waitEnded(s, host, [owner, ...owned], 'predecessor_and_owned_children_ended');
    const survivor = host.observeProcesses([unrelated.pid])[0];
    s.check('unrelated_survived', alive(survivor) && survivor.startTicks === unrelated.startTicks, { before: unrelated, after: survivor });
    const definitions = host.readDefinitions('candidate');
    s.save('candidate-definitions.json', definitions);
    assert.deepEqual(definitions, host.definitions, 'definition contents/incarnations/revisions changed');
    s.check('definitions_preserved', true, definitions.map(row => ({ id: row.id, incarnation: row.incarnation, revision: row.revision })));
    const records = await Promise.all(host.ids.map(id => stack.request(`/api/v1/agents/${id}`, s.layout.fixtureKey)));
    s.save('candidate-agent-records.json', records);
    s.check('agent_identities_preserved', records.every((response, index) => response.status === 200
      && JSON.parse(response.body).agentId === host.ids[index]), host.ids);
    await stoppedDefinition(s, stack, host, 'candidate');
    await host.startWorker('candidate');
    await host.probeWorkloop('candidate');
    await waitForStoppedBridgeExpiry(s, stack, host);
    runDoctors(s, host);
    await stopWorker(s, stack, host, runtimeWitnesses(s, host));
    return { checks: s.checks, qualified: true };
  } finally {
    host.captureRuntime();
    s.command(['exec', host.name, 'node', '-e',
      "const fs=require('fs');for(const file of ['/work/baseline-env.log','/work/candidate-env.log'])if(fs.existsSync(file))console.log(file+'\\n'+fs.readFileSync(file,'utf8'))"]);
  }
}
