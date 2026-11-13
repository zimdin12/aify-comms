import { fileURLToPath } from 'node:url';
import { StagingStack } from '../stack.mjs';
import { StagingHost } from '../host.mjs';
import { runDoctors } from './e4.mjs';
import { CHECK_SHELL_LAUNCHERS } from '../shell-syntax.mjs';

// The README's sources are GitHub; staging has no internet, so the documented overrides name the staged checkouts.
const SOURCES = ['AIFY_INSTALL_ENV_SOURCE=/src/candidate/env', 'AIFY_INSTALL_WRAPPER_SOURCE=/src/candidate/wrapper',
  'NPM_CONFIG_OFFLINE=true', 'NPM_CONFIG_AUDIT=false', 'NPM_CONFIG_FUND=false'];
const CODEX_HOME = '/work/codex-home';
const HOST_ENV = [`CODEX_HOME=${CODEX_HOME}/.codex`, `PATH=/home/staging/.local/bin:${CODEX_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin`];
const freshEnv = home => [...SOURCES, `HOME=${home}`, `NPM_CONFIG_PREFIX=${home}/.local`, 'PATH=/usr/local/bin:/usr/bin:/bin'];
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

/** E1: distinct fresh homes prove explicit tty consent and honest unattended refusal, then run the installed host. */
export async function run(s) {
  const stack = new StagingStack(s);
  const host = new StagingHost(s, stack);
  s.keepGoing = true;
  try {
    stack.qualifyDaemon();
    s.build();
    stack.create('candidate');
    await stack.start('candidate');
    s.requireCommand(['cp', fileURLToPath(new URL('../fixtures/tty-consent.py', import.meta.url)), `${host.name}:/work/tty-consent.py`], 'copy tty prompt driver');
    const absent = inHost(s, host, 'test -x /usr/bin/sudo && test -f /src/candidate/comms/install.sh && test ! -e /etc/claude-code/managed-settings.json');
    s.check('fresh_policy_absent_with_present_file_controls', absent.status === 0, absent);
    const denied = inHost(s, host, "sudo -n /usr/local/bin/node -e 'process.exit(0)'");
    s.check('sudo_other_command_refused', denied.status === 1, denied);
    const refused = inHost(s, host, 'cd /src/candidate/comms; bash install.sh --client claude http://service:8800 </dev/null; rc=$?; test ! -e "$HOME/.aify/services.json"; echo "registry-absent $?"; exit "$rc"', freshEnv('/work/refusal-home'));
    s.save('fresh-install-no-consent.json', refused);
    s.check('no_consent_one_command_no_publication (comms#49)', refused.status === 78 && refused.out.includes('registry-absent 0')
      && refused.out.split('\n').filter(line => line.trim().startsWith("'aify-env' 'install' ")).length === 1
      && /'--approve' '[0-9a-f]{64}'/.test(refused.out) && !refused.out.includes(s.layout.fixtureKey), refused);
    for (const [client, home] of [['claude', '/home/staging'], ['codex', CODEX_HOME]]) {
      if (client === 'codex') {
        const path = inHost(s, host, 'command -v node && command -v npm && case ":$PATH:" in *:/home/staging/.local/bin:*) exit 1;; esac', freshEnv(home));
        s.check('fresh_codex_path_excludes_claude_prefix', path.status === 0, path);
      }
      const prompts = [['approve comms-bridge from .* for aify-comms \\(tuple [0-9a-f]{64}\\)\\? \\[y/N\\]', 'y\n']];
      if (client === 'claude') prompts.push(['prepare only the channel policy with .*prepare-policy.*\\? \\[y/N\\]', 'y\n']);
      const installed = inHost(s, host, `mkdir -p "$HOME"; test ! -e "$HOME/.aify" && test ! -e "$HOME/.aify-comms" && python3 -I /work/tty-consent.py '${JSON.stringify(prompts)}' bash -c 'cd /src/candidate/comms && exec bash install.sh --client ${client} http://service:8800'`, freshEnv(home));
      s.save(`fresh-install-${client}.json`, installed);
      s.check(`fresh_install_${client}_in_one_run (comms#49)`, installed.status === 0 && installed.out.includes('chain: complete')
        && installed.out.includes(`TTY_CONSENT answered=${prompts.length} expected=${prompts.length}`), { status: installed.status, tail: installed.tail });
    }
    const envInstall = inHost(s, host, 'cd /src/candidate/env && bash install.sh --no-prompt', HOST_ENV);
    s.save('env-install-after-chain.json', envInstall);
    s.check('env_install_after_chain_no_prompt (env#32)', envInstall.status === 0, { status: envInstall.status, tail: envInstall.tail });
    host.installRuntime();
    host.defineFleet('candidate', HOST_ENV);
    await host.startBaseline('candidate', HOST_ENV);
    await host.waitForDefinitions('candidate');
    await host.startWorker('candidate');
    s.save('claude-config-key-names.json', s.command(['exec', host.name, 'node', '-e', 'try { const value = JSON.parse(require("fs").readFileSync(require("path").join(require("os").homedir(), ".claude.json"), "utf8")); console.log(JSON.stringify({keys:Object.keys(value)})); } catch (error) { console.log(JSON.stringify({error:error.code||"invalid-json"})); process.exitCode=1; }']));
    try { runDoctors(s, host, HOST_ENV); } catch (error) { s.check('fresh_doctors_reported', false, { error: error.message }); }
    const key = s.layout.fixtureKey;
    const exempt = statuses(s, host, { health: ['/health', 'service'], docsWithoutKey: ['/openapi.json', 'service'],
      docsWithKey: ['/openapi.json', 'service', key], lookalike: ['/healthz-admin', 'service'] });
    s.check('auth_exempt_paths_exact (comms#43)', exempt.health === '200' && exempt.docsWithoutKey === '401'
      && exempt.docsWithKey === '200' && exempt.lookalike === '401', exempt);
    const gateway = statuses(s, host, { gateway: ['/api/v1/agents', 'host.docker.internal:8800', key],
      attacker: ['/api/v1/agents', 'host.docker.internal.evil.example:8800', key] });
    s.check('gateway_host_admitted_attacker_refused (comms#27)', gateway.gateway === '200' && /^4\d\d$/.test(gateway.attacker), gateway);
    const launchers = inHost(s, host, 'for l in claude-aify codex-aify; do "$l" --check; echo "exit $l $?"; done; '
      + CHECK_SHELL_LAUNCHERS + '; echo "syntax-exit $?"; '
      + 'bash /src/candidate/wrapper/install.sh --client claude --endpoint; echo "missing-value $?"', HOST_ENV);
    s.save('launchers.json', launchers);
    const lines = launchers.out.split('\n');
    s.check('check_reports_wrapper_permissions (wrapper#21)', lines.filter(line => line.includes('wrapper-selected permissions:')).length === 2
      && lines.includes('exit claude-aify 0') && lines.includes('exit codex-aify 0'), { tail: launchers.tail });
    s.check('installed_launchers_parse (wrapper#9)', lines.includes('syntax-exit 0')
      && lines.filter(line => line.startsWith('syntax-ok ')).length >= 2,
      lines.filter(line => line.startsWith('syntax-ok ')));
    s.check('missing_option_value_refused (wrapper#18)', lines.includes('missing-value 78') && launchers.out.includes('--endpoint'), { tail: launchers.tail });
    for (const [item, reason] of Object.entries(NOT_RUN)) console.log(`NOT_RUN ${item}: ${reason}`);
    return { checks: s.checks, qualified: true };
  } finally {
    s.keepGoing = false;
  }
}
