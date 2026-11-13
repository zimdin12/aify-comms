import { StagingStack } from '../stack.mjs';
import { StagingHost } from '../host.mjs';
import { runDoctors } from './e4.mjs';

// The README's sources are GitHub; staging has no internet, so the documented overrides name the staged checkouts.
const SOURCES = ['AIFY_INSTALL_ENV_SOURCE=/src/candidate/env', 'AIFY_INSTALL_WRAPPER_SOURCE=/src/candidate/wrapper',
  'NPM_CONFIG_OFFLINE=true', 'NPM_CONFIG_AUDIT=false', 'NPM_CONFIG_FUND=false'];
const NOT_RUN = { 'GitHub fetch of env and wrapper': 'staging has no internet; the work-PC install proves the fetch',
  'comms#27 dashboard container half': 'no staging dashboard; checked live' };
const STATUS = `const http=require('http');const [path,host,key]=process.argv.slice(1);
  http.get({host:'service',port:8800,path,headers:{Host:host,...(key?{'x-api-key':key}:{})}},r=>{r.resume();console.log(r.statusCode)})
    .on('error',e=>console.log(e.code));`;

function inHost(s, host, script, environment = []) {
  const result = s.command(['exec', '-w', '/work', '-e', `AIFY_API_KEY=${s.layout.fixtureKey}`,
    ...environment.flatMap(pair => ['-e', pair]), host.name, 'bash', '-c', `export PATH="$HOME/.local/bin:$PATH"; ${script}`]);
  return { status: result.status, out: result.out, tail: result.out.trim().split('\n').slice(-8) };
}

function statuses(s, host, requests) {
  return Object.fromEntries(Object.entries(requests).map(([name, [path, hostHeader, key]]) => [name,
    s.requireCommand(['exec', host.name, 'node', '-e', STATUS, path, hostHeader, key ?? ''], 'staging HTTP status').trim()]));
}

/** E1: a fresh host runs the README install once per client, then reports what its doctors and endpoints say. */
export async function run(s) {
  const stack = new StagingStack(s);
  const host = new StagingHost(s, stack);
  s.keepGoing = true; // A fresh host's result is every row as found, not the first red one.
  try {
    stack.qualifyDaemon();
    s.build();
    stack.create('candidate');
    await stack.start('candidate');
    for (const client of ['claude', 'codex']) {
      const installed = inHost(s, host, `cd /src/candidate/comms && bash install.sh --client ${client} http://service:8800 </dev/null`, SOURCES);
      s.save(`fresh-install-${client}.json`, installed);
      s.check(`fresh_install_${client}_in_one_run (comms#49)`, installed.status === 0, { status: installed.status, tail: installed.tail });
    }
    const envInstall = inHost(s, host, 'cd /src/candidate/env && bash install.sh --no-prompt', SOURCES);
    s.save('env-install-after-chain.json', envInstall);
    s.check('env_install_after_chain_no_prompt (env#32)', envInstall.status === 0, { status: envInstall.status, tail: envInstall.tail });
    try { runDoctors(s, host); } catch (error) { s.check('fresh_doctors_reported', false, { error: error.message }); }
    const key = s.layout.fixtureKey;
    const exempt = statuses(s, host, { health: ['/health', 'service'], docsWithoutKey: ['/openapi.json', 'service'],
      docsWithKey: ['/openapi.json', 'service', key], lookalike: ['/healthz-admin', 'service'] });
    s.check('auth_exempt_paths_exact (comms#43)', exempt.health === '200' && exempt.docsWithoutKey === '401'
      && exempt.docsWithKey === '200' && exempt.lookalike === '401', exempt);
    const gateway = statuses(s, host, { gateway: ['/api/v1/agents', 'host.docker.internal:8800', key],
      attacker: ['/api/v1/agents', 'host.docker.internal.evil.example:8800', key] });
    s.check('gateway_host_admitted_attacker_refused (comms#27)', gateway.gateway === '200' && /^4\d\d$/.test(gateway.attacker), gateway);
    const launchers = inHost(s, host, 'for l in claude-aify codex-aify; do "$l" --check; echo "exit $l $?"; done; '
      + 'for f in "$HOME"/.local/bin/*-aify; do bash -n "$f" && echo "syntax-ok $(basename "$f")"; done; '
      + 'bash /src/candidate/wrapper/install.sh --client claude --endpoint; echo "missing-value $?"');
    s.save('launchers.json', launchers);
    const lines = launchers.out.split('\n');
    s.check('check_reports_wrapper_permissions (wrapper#21)', lines.filter(line => line.includes('wrapper-selected permissions:')).length === 2
      && lines.includes('exit claude-aify 0') && lines.includes('exit codex-aify 0'), { tail: launchers.tail });
    s.check('installed_launchers_parse (wrapper#9)', lines.filter(line => line.startsWith('syntax-ok ')).length >= 2,
      lines.filter(line => line.startsWith('syntax-ok ')));
    s.check('missing_option_value_refused (wrapper#18)', lines.includes('missing-value 78') && launchers.out.includes('--endpoint'), { tail: launchers.tail });
    for (const [item, reason] of Object.entries(NOT_RUN)) console.log(`NOT_RUN ${item}: ${reason}`);
    return { checks: s.checks, qualified: true };
  } finally {
    s.keepGoing = false;
  }
}
