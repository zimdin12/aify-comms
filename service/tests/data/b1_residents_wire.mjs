// Offline fixture for the B1 receiver controls: REAL residents roles of private temp homes produce the publications.
//
// The aify-env checkout's own DaemonResidents takes each home's role (a real OS lock and generation), its residents host
// reads real lifetime records, and its real AgentStateSender builds and "posts" every body; the post is captured here
// instead of sent. Synthetic: the OS probe's answer for each resident pid, the definitions, and the launcher sighting.
//
//   node b1_residents_wire.mjs <envRepo> <tmp>         prints {bodies, keys} for the Python receiver tests
//   node b1_residents_wire.mjs <envRepo> <tmp> holder  internal: holder A, a separate process the fixture crashes
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [source, tmp, role] = process.argv.slice(2);
const load = (name) => import(pathToFileURL(path.join(source, 'lib', name)).href);
const { DaemonResidents } = await load('daemon-residents.mjs');
const { AgentStateSender } = await load('agent-state-sender.mjs');
const { AgentStateHost } = await load('agent-state-host.mjs');

const MACHINE = 'win32:b1-fixture';
const WRITTEN = 1_790_950_000_000_000;
const T2 = WRITTEN + 2_000_000;
const lifetimeOf = (n) => `7f3c9e2a-0000-4000-8000-${String(n).padStart(12, '0')}`;
const pidOf = (n) => 40000 + n;

/** A private aify home with one `residents` record and a generation, as a home that has run before. */
function home(name, agent, n, generation) {
  const dir = path.join(tmp, name, '.aify');
  fs.mkdirSync(path.join(dir, 'residents'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'env'), { recursive: true });
  if (!fs.existsSync(path.join(dir, 'env', 'residents.generation'))) fs.writeFileSync(path.join(dir, 'env', 'residents.generation'), `${generation}\n`);
  fs.writeFileSync(path.join(dir, 'residents', `${agent}.${lifetimeOf(n)}.json`), JSON.stringify({ agentId: agent, lifetime: lifetimeOf(n),
    instance: 'residents', harness: 'claude', pid: pidOf(n), launcher: process.execPath, writtenAtUs: WRITTEN }));
  return dir;
}
const probe = (pids) => new Map(pids.map((pid) => [pid, pid > 40000
  ? { alive: true, createdAtUs: WRITTEN - 1, commandLine: `node "${process.execPath}"` } : { alive: false, createdAtUs: null, commandLine: null }]));
const definitions = (agents) => ({ list: async () => ({ storeId: 'fixture-store', conflict: false, enumerationFailed: false, unreadable: [],
  definitions: agents.map(([id, mode]) => ({ id, incarnation: 1, revision: 1, problems: [], agent: { mode, harness: 'claude' } })) }) });

/** A real sender for one population, capturing each body it would post. */
function capturing(captured, agents) {
  return ({ instance, generation, ...population }) => new AgentStateSender({
    identity: { machineId: MACHINE, instance, generation }, definitions: definitions(agents),
    lifecycle: { stopFacts: async () => new Map() }, observedHarnesses: async () => new Set(['claude']),
    readRegistry: () => JSON.stringify({ services: { comms: { endpoint: 'http://receiver.invalid/', agentState: { path: '/api/v1/agent-state' } } } }),
    credentialOptions: () => ({ env: {}, root: path.join(tmp, 'credentials') }),
    fetchImpl: async (_url, options) => { captured.push(options.body); return new Response(null, { status: 204 }); },
    ...population });
}
async function publish(residents, captured) {
  const before = captured.length;
  await residents.tick();
  for (const end = Date.now() + 5000; captured.length === before && Date.now() < end;) await new Promise((r) => setTimeout(r, 5));
  if (captured.length === before) throw new Error('no publication');
  return captured[captured.length - 1];
}
/** A home's holder: the role, the residents host, the descriptor and the sender, as a daemon has them. */
async function holder(dir, agents, nowUs = () => T2 + 10_000_000) {
  const captured = [];
  const residents = new DaemonResidents({ aifyHome: dir, url: 'http://127.0.0.1:1', pid: process.pid, probe, nowUs });
  residents.attachSender(capturing(captured, agents));
  if (!(await residents.acquire())) throw new Error(`no role: ${residents.report().reason}`);
  return { residents, captured, apply: (agentId, n, kind, firedAtUs) => {
    const entered = residents.enter();
    try { return entered.host.applyEvent({ agentId, lifetime: lifetimeOf(n), kind, firedAtUs }, { guard: entered.op.guard }); }
    finally { entered.op.done(); }
  } };
}

if (role === 'holder') {
  // A: publishes R working, then R blocked; the second body is the one the network will deliver late.
  const a = await holder(home('h1', 'res-r', 1, 100), [['res-r', 'resident']]);
  a.apply('res-r', 1, 'turn-start', T2);
  const first = await publish(a.residents, a.captured);
  a.apply('res-r', 1, 'blocked', T2 + 1);
  const delayed = await publish(a.residents, a.captured);
  process.stdout.write(`${JSON.stringify({ first, delayed })}\n`);
  setInterval(() => {}, 1000);
} else {
  const child = spawn(process.execPath, [process.argv[1], source, tmp, 'holder'], { stdio: ['ignore', 'pipe', 'inherit'] });
  let out = '';
  child.stdout.on('data', (chunk) => { out += chunk; });
  for (const end = Date.now() + 20000; !out.includes('\n') && Date.now() < end;) await new Promise((r) => setTimeout(r, 10));
  const a = JSON.parse(out.split('\n')[0]);
  child.kill('SIGKILL'); // the crash: no release, no withdrawal
  await new Promise((resolve) => child.on('exit', resolve));
  // B: the successor for the same home restores R's open turn, then sees it end: R idle.
  const b = await holder(home('h1', 'res-r', 1, 100), [['res-r', 'resident']]);
  b.apply('res-r', 1, 'turn-end', T2 + 5);
  const successor = await publish(b.residents, b.captured);
  // Two more homes on the SAME machine: one whose generation equals B's, one above it.
  const c = await holder(home('h2', 'res-r2', 2, Number(JSON.parse(successor).generation) - 1), [['res-r2', 'resident']]);
  c.apply('res-r2', 2, 'turn-start', T2);
  const equal = await publish(c.residents, c.captured);
  const d = await holder(home('h3', 'res-r3', 3, 500), [['res-r3', 'resident']]);
  d.apply('res-r3', 3, 'turn-start', T2);
  const unequal = await publish(d.residents, d.captured);
  // A non-holder's own managed key beside them: a managed lifetime of a daemon whose role is elsewhere.
  const managedHome = path.join(tmp, 'h1', '.aify');
  const own = new AgentStateHost({ aifyHome: managedHome, instance: 'default', probe, nowUs: () => T2 + 10_000_000, population: 'managed' });
  own.boot();
  own.startManaged({ agentId: 'worker-m', lifetime: lifetimeOf(9), instance: 'default', pid: 99, handle: 'p1' });
  own.applyEvent({ agentId: 'worker-m', lifetime: lifetimeOf(9), kind: 'turn-start', firedAtUs: T2 });
  const managedCaptured = [];
  const managedSender = capturing(managedCaptured, [['worker-m', 'managed'], ['res-r', 'resident']])({ instance: 'default', generation: 7, stateHost: own });
  await managedSender.tick();
  for (const end = Date.now() + 5000; !managedCaptured.length && Date.now() < end;) await new Promise((r) => setTimeout(r, 5));
  for (const h of [b, c, d]) await h.residents.release({ waitMs: 0 });
  process.stdout.write(JSON.stringify({ first: a.first, delayed: a.delayed, successor, equal, unequal, managed: managedCaptured[0] }));
}
