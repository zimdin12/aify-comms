import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { StagingStack } from '../stack.mjs';
import { StagingHost } from '../host.mjs';
import { alive, runtimeWitnesses, startUnrelated, stopWorker, waitEnded } from './e4.mjs';

const MARKER = '#!/usr/bin/env bash\nHARNESS_WRAPPER_VERSION="staging-e2"\nsleep 777 &\necho "$$ $!" > "$1"\nwait\n';
const NOT_RUN = { 'env#31': 'the frozen env has no start-failed report', 'comms#31': 'the frozen comms still closes terminal runs from screen text',
  'env#15 service bound': 'the comms plugin refuses outside-root starts first; no service path reaches the core check' };

async function startEnv(s, host, label) {
  s.requireCommand(['exec', '-d', '-w', '/work', '-e', 'AIFY_NO_DASHBOARD=1', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`, host.name,
    'bash', '-c', `exec node --import /work/observe-bind.mjs /src/candidate/env/bin/aify-env.mjs --port 8802 > /work/${label}-env.log 2>&1`],
  `${label} environment start`);
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    const health = host.localHealth();
    const bind = s.command(['exec', host.name, 'cat', '/work/candidate-bind.json']);
    if (health.status === 0 && bind.status === 0 && JSON.parse(bind.out).pid === JSON.parse(health.out).pid) return JSON.parse(health.out);
    await delay(250);
  }
  throw new Error(`${label} environment never answered from the process that bound its port`);
}

async function inboxHas(s, stack, id, text) {
  const response = await stack.request(`/api/v1/messages/inbox/${id}?filter=all&peek=true`, s.layout.fixtureKey);
  if (response.status !== 200) throw new Error('inbox could not be read');
  return JSON.parse(response.body).messages.some(message => message.body?.includes(text));
}

async function refuseMixedTargets(s, stack, host) {
  const nonce = `E2-MIXED-${s.layout.runId}`;
  const sent = await stack.request('/api/v1/messages/send', s.layout.fixtureKey, { method: 'POST', body: {
    from_agent: 'dashboard', to: host.ids[0], toRole: 'staging-fixture', type: 'request', subject: 'mixed', body: nonce } });
  const reached = await Promise.all(host.ids.map(id => inboxHas(s, stack, id, nonce)));
  const control = await inboxHas(s, stack, host.ids[0], `E4-PROBE-${s.layout.runId}`);
  s.check('mixed_targets_refused_unstored', sent.status === 400 && /not both/.test(sent.body) && control && !reached.some(Boolean),
    { sent, reached, control });
}

const LOCAL = 'http://127.0.0.1:8802';

async function startMarker(s, stack, host, label, launcher = '/work/e2-marker.sh') {
  const started = await stack.request('/processes', null, { method: 'POST', origin: LOCAL,
    body: { service: 'staging-e2', launcher, args: [`/work/${label}.pids`], cwd: '/tmp' } });
  if (started.status !== 201) return { started };
  const deadline = Date.now() + 10_000;
  let pids;
  do { await delay(100); pids = s.command(['exec', host.name, 'cat', `/work/${label}.pids`]); } while (pids.status !== 0 && Date.now() < deadline);
  return { started, id: JSON.parse(started.body).id, group: host.observeProcesses(pids.out.trim().split(' ').map(Number)) };
}

async function pipedSpawnAndStop(s, stack, host) {
  const refused = await startMarker(s, stack, host, 'noexec', '/work/e2-marker-noexec.sh');
  const listed = JSON.parse((await stack.request('/processes', null, { origin: LOCAL })).body).processes;
  s.check('spawn_failure_names_errno', refused.started.status === 422 && /spawn failed: EACCES/.test(refused.started.body)
    && listed.every(row => row.pid > 0), { refused, listed });
  const marker = await startMarker(s, stack, host, 'stopped-marker');
  s.check('raw_start_outside_roots_live', marker.started.status === 201 && JSON.parse(marker.started.body).pid === marker.group?.[0].pid
    && marker.group.length === 2 && marker.group.every(alive), marker);
  const stopped = await stack.request(`/processes/${marker.id}`, null, { method: 'DELETE', origin: LOCAL });
  s.check('piped_stop_answered', stopped.status === 204, stopped);
  await waitEnded(s, host, marker.group, 'piped_stop_ended_group');
}

async function crashAndRecover(s, stack, host, piped, unrelated) {
  const orphan = await startMarker(s, stack, host, 'orphan-marker');
  s.requireCommand(['exec', host.name, 'kill', '-KILL', String(piped.pid)], 'hard kill of the environment');
  await waitEnded(s, host, host.observeProcesses([piped.pid]), 'environment_crashed');
  s.check('orphan_outlived_crash', host.observeProcesses(orphan.group.map(row => row.pid)).every(alive), orphan.group);
  const successor = await startEnv(s, host, 'successor');
  await waitEnded(s, host, orphan.group, 'successor_reaped_orphan_group');
  const survivor = host.observeProcesses([unrelated.pid])[0];
  s.check('recovery_spared_unrelated', alive(survivor) && survivor.startTicks === unrelated.startTicks, { unrelated, survivor });
  const health = JSON.parse(host.localHealth().out);
  s.check('successor_owns_nothing', health.pid === successor.pid && health.processes.length === 0, health);
}

/** E2: the managed work loop, then piped spawn/stop and crash recovery on the same candidate host. */
export async function run(s) {
  const stack = new StagingStack(s);
  const host = new StagingHost(s, stack);
  try {
    stack.qualifyDaemon();
    s.build();
    stack.create('candidate');
    await stack.start('candidate');
    host.install('candidate');
    host.installRuntime();
    host.defineFleet('candidate');
    s.requireCommand(['cp', fileURLToPath(new URL('../fixtures/observe-bind.mjs', import.meta.url)), `${host.name}:/work/observe-bind.mjs`], 'bind observer');
    s.requireCommand(['exec', host.name, 'node', '-e', `const fs=require('fs');fs.writeFileSync('/work/e2-marker.sh',${JSON.stringify(MARKER)},{mode:0o755});
      fs.writeFileSync('/work/e2-marker-noexec.sh',${JSON.stringify(MARKER)},{mode:0o644})`], 'marker launchers');
    const terminal = await startEnv(s, host, 'terminal');
    await host.waitForDefinitions('candidate');
    await host.startWorker('e2');
    await host.probeWorkloop('e2');
    await refuseMixedTargets(s, stack, host);
    await stopWorker(s, stack, host, runtimeWitnesses(s, host), 'e2');
    s.requireCommand(['exec', host.name, 'kill', '-TERM', String(terminal.pid)], 'terminal environment shutdown');
    await waitEnded(s, host, host.observeProcesses([terminal.pid]), 'terminal_environment_ended');
    s.requireCommand(['exec', host.name, 'mv', '/src/candidate/env/node_modules/node-pty', '/work/node-pty-aside'], 'optional node-pty absent');
    const unrelated = startUnrelated(s, host);
    const piped = await startEnv(s, host, 'piped');
    s.check('piped_environment', piped.terminals?.available === false, piped.terminals);
    await pipedSpawnAndStop(s, stack, host);
    await crashAndRecover(s, stack, host, piped, unrelated);
    for (const [issue, reason] of Object.entries(NOT_RUN)) console.log(`NOT_RUN ${issue}: ${reason}`);
    return { checks: s.checks, qualified: true };
  } finally {
    host.captureRuntime();
    s.command(['exec', host.name, 'bash', '-c', 'for f in /work/*-env.log; do echo "$f"; cat "$f"; done']);
  }
}
