import { randomBytes } from 'node:crypto';
import fs from 'node:fs';
import { parseArgs } from 'node:util';
import { StagingManifest } from './manifest.mjs';
import { StagingLayout } from './layout.mjs';
import { StagingSession } from './session.mjs';

const scenarios = Object.freeze(Object.fromEntries(['E2', 'E3', 'E4'].map(id => [id, new URL(`./scenarios/${id.toLowerCase()}.mjs`, import.meta.url)])));

function readOptions(argv) {
  const parsed = parseArgs({ args: argv, allowPositionals: true, strict: true, options: {
    refs: { type: 'string' }, out: { type: 'string' }, preflight: { type: 'boolean', default: false },
    'env-repo': { type: 'string' }, 'wrapper-repo': { type: 'string' },
  } });
  const [run, ...extra] = parsed.positionals;
  if (extra.length || !Object.hasOwn(scenarios, run) || !parsed.values.refs) {
    throw new Error(`usage: staging-e2e.sh ${Object.keys(scenarios).join('|')} --refs <manifest> [--preflight] [--out <new evidence directory>]`);
  }
  return { ...parsed.values, selected: run };
}

function readManifest(file) {
  let input;
  try { input = JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { throw new Error('refs manifest could not be read as JSON'); }
  return new StagingManifest(input);
}

async function executeSelected(manifest, layout, options) {
  if (!options.out) throw new Error('an explicit new evidence directory is required for execution');
  const session = new StagingSession({ manifest, layout, options });
  let result, cleanup, unchanged;
  try {
    const { run } = await import(scenarios[options.selected].href);
    result = await run(session);
  } catch (error) {
    session.save('failure.json', { name: error.name, message: error.message });
    throw error;
  } finally {
    cleanup = session.resources.cleanup();
    unchanged = session.finish(cleanup);
    console.log(`${cleanup.ok && unchanged && session.docker.settlement().settled ? 'PASS' : 'FAIL'} cleanup`);
  }
  if (!result?.checks?.length || result.checks.some(check => check.ok !== true)
      || !cleanup.ok || !unchanged || !session.docker.settlement().settled) return 1;
  if (result.qualified !== true) {
    console.error(`NOT_RUN ${options.selected} qualification: completed checks are not full acceptance`);
    return 2;
  }
  console.log(`PASS ${options.selected}`);
  return 0;
}

async function main(argv) {
  const options = readOptions(argv);
  const manifest = readManifest(options.refs);
  const layout = new StagingLayout({ runId: randomBytes(6).toString('hex'), fixtureKey: randomBytes(24).toString('hex') });
  const environment = { DATA_VOLUME_NAME: layout.dataVolume };
  for (const phase of ['baseline', 'candidate']) layout.assertIsolation({ config: layout.compose(phase), environment });
  console.log('PASS manifest_syntax');
  console.log('PASS generated_isolation_intent');
  if (options.preflight) {
    console.log('NOT_RUN switch: preflight checks syntax and generated intent only; no Git, Docker or runtime qualification');
    return 0;
  }
  return executeSelected(manifest, layout, options);
}

try { process.exitCode = await main(process.argv.slice(2)); }
catch (error) { console.error(`FAIL staging: ${error.message}`); process.exitCode = 1; }
