import { randomBytes } from 'node:crypto';
import fs from 'node:fs';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { StagingManifest } from './manifest.mjs';
import { StagingLayout } from './layout.mjs';
import { StagingDocker } from './docker.mjs';
import { StagingResources } from './resources.mjs';

const scenarios = Object.freeze({ E4: new URL('./scenarios/e4.mjs', import.meta.url) });

function readOptions(argv) {
  const parsed = parseArgs({ args: argv, allowPositionals: true, strict: true, options: {
    refs: { type: 'string' }, out: { type: 'string' }, preflight: { type: 'boolean', default: false },
    'service-port': { type: 'string', default: '8820' }, 'dashboard-port': { type: 'string', default: '8821' },
  } });
  const [run, ...extra] = parsed.positionals;
  if (extra.length || (!Object.hasOwn(scenarios, run) && run !== 'all') || !parsed.values.refs) {
    throw new Error('usage: staging-e2e.sh E4|all --refs <manifest> [--preflight] [--out <new evidence directory>]');
  }
  return { ...parsed.values, selected: run === 'all' ? Object.keys(scenarios) : [run] };
}

function readManifest(file) {
  let input;
  try { input = JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { throw new Error('refs manifest could not be read as JSON'); }
  return new StagingManifest(input);
}

function prepareContext(options) {
  const manifest = readManifest(options.refs);
  const layout = new StagingLayout({ runId: randomBytes(6).toString('hex'), fixtureKey: randomBytes(24).toString('hex'),
    ports: { service: Number(options['service-port']), dashboard: Number(options['dashboard-port']) } });
  const environment = { DATA_VOLUME_NAME: layout.dataVolume };
  layout.assertIsolation({ config: layout.compose('baseline'), environment });
  layout.assertIsolation({ config: layout.compose('candidate'), environment });
  const docker = new StagingDocker({ environment: process.env, dataVolume: layout.dataVolume, timeoutMs: 120_000 });
  const resources = new StagingResources({ layout, docker: (args) => docker.run(args) });
  return { manifest, layout, environment, options, docker, resources };
}

async function executeSelected(context) {
  for (const id of context.options.selected) {
    const location = scenarios[id];
    if (!fs.existsSync(fileURLToPath(location))) {
      console.error(`NOT_RUN ${id}: scenario is not yet available; input preflight is not switch qualification`);
      return 2;
    }
    if (!context.options.out) throw new Error('an explicit new evidence directory is required for execution');
    const { run } = await import(location.href);
    let result, cleanup;
    try { result = await run(context); }
    finally {
      cleanup = context.resources.cleanup();
      if (!cleanup.ok || !context.docker.settlement().settled) console.error('FAIL cleanup: a resource or daemon-side command remains unconfirmed');
    }
    const complete = Array.isArray(result?.checks) && result.checks.length > 0
      && result.checks.every((check) => check.ok === true) && cleanup.ok && context.docker.settlement().settled;
    if (!complete) { console.error(`FAIL ${id}: scenario or cleanup is incomplete`); return 1; }
    console.log(`PASS ${id}`);
  }
  return 0;
}

async function main(argv) {
  const options = readOptions(argv);
  const context = prepareContext(options);
  console.log('PASS manifest_syntax');
  console.log('PASS generated_isolation_intent');
  if (options.preflight) {
    console.log('NOT_RUN switch: preflight checks syntax and generated intent only; no Git, Docker or runtime qualification');
    return 0;
  }
  return executeSelected(context);
}

try { process.exitCode = await main(process.argv.slice(2)); }
catch (error) { console.error(`FAIL staging: ${error.message}`); process.exitCode = 1; }
