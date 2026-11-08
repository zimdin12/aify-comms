import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { StagingStack } from '../stack.mjs';
import { StagingHost } from '../host.mjs';
import { waitEnded } from './e4.mjs';
import { startEnv } from './e2.mjs';

const NOT_RUN = { 'comms MCP child with the forwarded token': 'Codex starts MCP children only on a model turn; checked on the live switch',
  'record names the token variable only': 'the bridge publishes the runtime marker, and it starts only on a turn' };
/** Starts the defined Codex agent through comms and returns the first observation of its authenticated app-server. */
async function launch(s, stack, host) {
  const started = await stack.request(`/api/v1/agents/${host.ids[0]}/control`, s.layout.fixtureKey,
    { method: 'POST', body: { action: 'start', from_agent: 'dashboard' } });
  s.save('e3-start-response.json', started);
  if (started.status < 200 || started.status >= 300) throw new Error('real defined-agent start was refused');
  let owned = [];
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    const health = JSON.parse(host.localHealth().out);
    const probe = JSON.parse(s.requireCommand(['exec', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`, host.name,
      'node', '/work/codex-auth-probe.mjs', host.ids[0]], 'app-server authentication observation'));
    if (probe.server) return { probe, owned: health.processes };
    if (owned.length && !health.processes.length) {
      s.save('e3-launch-ended.json', { owned, health });
      throw new Error('the managed Codex launch ended before its app-server was observed; see e3-launch-ended.json');
    }
    owned = health.processes;
    await delay(500);
  }
  throw new Error('no authenticated Codex app-server appeared for the managed launch');
}
/** E3: real codex-aify under aify-env, its app-server's authentication, and the comms allowlist the installer wrote. */
export async function run(s) {
  const stack = new StagingStack(s);
  const host = new StagingHost(s, stack);
  try {
    stack.qualifyDaemon();
    s.build();
    stack.create('candidate');
    await stack.start('candidate');
    host.install('candidate');
    host.defineFleet('candidate');
    for (const file of ['observe-bind.mjs', 'codex-auth-probe.mjs']) {
      s.requireCommand(['cp', fileURLToPath(new URL(`../fixtures/${file}`, import.meta.url)), `${host.name}:/work/${file}`], 'E3 fixture');
    }
    s.check('real_codex_on_path', /codex-cli/.test(s.requireCommand(['exec', host.name, 'bash', '-lc', 'codex --version'], 'codex on PATH')), 'no runtime double');
    await startEnv(s, host, 'e3');
    await host.waitForDefinitions('candidate');
    const { probe, owned } = await launch(s, stack, host);
    s.save('e3-probe.json', probe);
    s.check('app_server_requires_capability_token', probe.server.wsAuth === 'capability-token' && probe.tokenShaped
      && probe.verifierIsTokenSha256, probe.server);
    s.check('token_in_no_argv_or_record', Array.isArray(probe.argvHolders) && probe.argvHolders.length === 0
      && probe.recordHoldsToken === false, { argvHolders: probe.argvHolders, recordHoldsToken: probe.recordHoldsToken });
    s.check('unauthenticated_upgrade_refused', probe.noToken === 401 && probe.wrongToken === 401, { noToken: probe.noToken, wrongToken: probe.wrongToken });
    s.check('launch_token_accepted', probe.rightToken === 101 && probe.initialize === 'answered', { rightToken: probe.rightToken, initialize: probe.initialize });
    s.check('comms_allowlist_names_both_carriers', ['AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV', 'AIFY_CODEX_APP_SERVER_TOKEN']
      .every(name => probe.envVars.includes(name)) && probe.tomlHoldsToken === false, { envVars: probe.envVars, tomlHoldsToken: probe.tomlHoldsToken });
    const before = host.observeProcesses([...new Set([probe.server.pid, ...owned.map(row => row.pid)])]);
    const stopped = await stack.request(`/api/v1/agents/${host.ids[0]}/control`, s.layout.fixtureKey,
      { method: 'POST', body: { action: 'stop', from_agent: 'dashboard' } });
    s.save('e3-stop-response.json', stopped);
    await waitEnded(s, host, before, 'e3_stop_ended_app_server_and_launcher');
    for (const [item, reason] of Object.entries(NOT_RUN)) console.log(`NOT_RUN ${item}: ${reason}`);
    return { checks: s.checks, qualified: true };
  } finally {
    s.command(['exec', host.name, 'bash', '-c', 'for f in /work/*-env.log; do echo "$f"; cat "$f"; done']);
  }
}
