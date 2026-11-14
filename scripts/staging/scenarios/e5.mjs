import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { StagingStack } from '../stack.mjs';
import { StagingHost } from '../host.mjs';

// E5: the env-to-dashboard seam. A fresh host registers a staged dashboard; env's publication then reaches it.
const HOST_ENV = ['PATH=/home/staging/.local/bin:/usr/local/bin:/usr/bin:/bin',
  'NPM_CONFIG_OFFLINE=true', 'NPM_CONFIG_AUDIT=false', 'NPM_CONFIG_FUND=false'];
const NOT_RUN = { 'the dashboard image': 'the staged dashboard runs its own server source on the host image, not its Dockerfile',
  'the dashboard bridge in an agent session': 'cut by the lead; E5 proves registration and publication only' };
const READ = `const [origin,key]=process.argv.slice(1);
  const r=await fetch(origin+'/api/v1/agent-state',{signal:AbortSignal.timeout(5000),headers:key?{'x-api-key':key}:{}});
  const body=r.ok?await r.json():null;
  console.log(JSON.stringify({status:r.status,publishers:body?.publishers?.length??null,agents:(body?.agents??[]).map(a=>a.agentId)}));`;

function inHost(s, host, script, environment = []) {
  const result = s.command(['exec', '-w', '/work', ...[...HOST_ENV, ...environment].flatMap(pair => ['-e', pair]),
    host.name, 'bash', '-c', script]);
  return { status: result.status, out: result.out, tail: result.out.trim().split('\n').slice(-8) };
}

function readAgentState(s, host, key) {
  return JSON.parse(s.requireCommand(['exec', host.name, 'node', '--input-type=module', '-e', READ,
    s.layout.dashboardOrigin, key ?? ''], 'staged dashboard agent-state read').trim().split('\n').pop());
}

async function dashboardUp(s, host) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const result = s.command(['exec', host.name, 'node', '--input-type=module', '-e', READ, s.layout.dashboardOrigin, s.layout.dashboardKey]);
    if (result.status === 0) return;
    await delay(500);
  }
  s.command(['logs', `${s.layout.project}-aify-dashboard`]);
  throw new Error('the staged dashboard never answered');
}

/** E5: fresh env install, dashboard registration, then env's own publication arrives at the staged dashboard. */
export async function run(s) {
  if (!s.layout.dashboardKey || !('dashboard' in s.manifest.candidate)) throw new Error('E5 requires a dashboard source role in the refs');
  const stack = new StagingStack(s);
  const host = new StagingHost(s, stack);
  stack.qualifyDaemon();
  s.build();
  stack.create('candidate');
  await stack.start('candidate');
  await dashboardUp(s, host);
  // The fresh host E4 starts from: env, comms for both clients and the wrapper's launchers, by their own installers.
  // host.install would also demand a source line for the dashboard role, which the bootstrap does not install.
  s.requireCommand(['cp', fileURLToPath(new URL('../fixtures/bootstrap-host.sh', import.meta.url)), `${host.name}:/work/bootstrap-host.sh`], 'copy staging bootstrap');
  const installed = inHost(s, host, 'bash /work/bootstrap-host.sh /src/candidate', [`AIFY_API_KEY=${s.layout.fixtureKey}`]);
  s.save('candidate-install.txt', installed.out);
  s.check('fresh_host_install', installed.status === 0 && ['comms', 'env', 'wrapper'].every(role =>
    installed.out.includes(`source ${role} ${s.manifest.candidate[role]}`)), { status: installed.status, tail: installed.tail });
  host.defineFleet('candidate', HOST_ENV);
  await host.startBaseline('candidate', HOST_ENV);
  const wrongKey = readAgentState(s, host, `absent-${s.layout.runId}`);
  const before = readAgentState(s, host, s.layout.dashboardKey);
  s.check('dashboard_refuses_wrong_key_and_holds_nothing_before_registration', wrongKey.status === 401
    && before.status === 200 && before.publishers === 0, { wrongKey, before });
  const registered = inHost(s, host, 'cd /src/candidate/dashboard && node --experimental-strip-types bin/register.mjs',
    [`PUBLIC_ORIGINS=${s.layout.dashboardOrigin}`, `API_KEY=${s.layout.dashboardKey}`]);
  s.save('register.json', registered);
  const entry = JSON.parse(s.requireCommand(['exec', host.name, 'node', '-e',
    "const r=JSON.parse(require('fs').readFileSync(require('os').homedir()+'/.aify/services.json','utf8'));"
    + "const e=r.services?.['aify-dashboard']??null;console.log(JSON.stringify(e&&{endpoint:e.endpoint,agentState:e.agentState?.path??null,credentialRef:typeof e.credentialRef}))"],
  'registry entry readback'));
  s.check('register_writes_entry_with_stored_key', registered.status === 0 && !registered.out.includes(s.layout.dashboardKey)
    && entry?.endpoint === s.layout.dashboardOrigin && entry.agentState === '/api/v1/agent-state' && entry.credentialRef === 'string',
  { status: registered.status, tail: registered.tail, entry });
  const deadline = Date.now() + 150_000;
  let after;
  do {
    after = readAgentState(s, host, s.layout.dashboardKey);
    if (after.publishers > 0 && host.ids.every(id => after.agents.includes(id))) break;
    await delay(2000);
  } while (Date.now() < deadline);
  s.save('agent-state-after.json', after);
  s.check('env_publication_arrives_at_registered_dashboard', after.publishers > 0 && host.ids.every(id => after.agents.includes(id)),
    { after, expected: host.ids });
  for (const [item, reason] of Object.entries(NOT_RUN)) console.log(`NOT_RUN ${item}: ${reason}`);
  return { checks: s.checks, qualified: true };
}
