// Offline fixture: synthetic managed child identity and harness/definition observations.
// Real AgentStateHost, readAgentStates and AgentStatePublisher. No child or network.
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [source, home] = process.argv.slice(2);
const load = name => import(pathToFileURL(path.join(source, 'lib', name)).href);
const { AgentStateHost } = await load('agent-state-host.mjs');
const { AgentStatePublisher } = await load('agent-state-publisher.mjs');
const { readAgentStates } = await load('agent-state-read.mjs');
const host = new AgentStateHost({ aifyHome: home, instance: 'fixture', probe: () => new Map(), nowUs: () => 1700000000000000 });
host.boot();
let ids = ['alpha', 'nullable'];
const definitions = { list: async () => ({ definitions: ids.map(id => ({ id, problems: [], agent: { mode: 'managed', harness: 'fixture' } })), unreadable: [], conflict: false, enumerationFailed: false }) };
const read = async () => (await readAgentStates({ stateHost: host, definitions, observedHarnesses: async () => new Set(['fixture']),
  // No operator stops recorded: D9a reads the stop map, and an unreadable one makes the whole read unavailable.
  lifecycle: { stopFacts: () => new Map() } })).body;
const record = lifetime => ({ agentId: 'alpha', instance: 'fixture', lifetime, pid: 4242 });
const old = '11111111-1111-4111-8111-111111111111';
const newer = '22222222-2222-4222-8222-222222222222';
host.startManaged(record(old));
host.applyEvent({ agentId: 'alpha', lifetime: old, kind: 'turn-start', firedAtUs: 1699999999000000 });
const publisher = new AgentStatePublisher({ machineId: 'offline-fixture', instance: 'fixture', generation: 1700000000000, incarnationId: 'synthetic-incarnation' });
const snapshot = publisher.snapshot(await read());
host.endManaged(old);
host.startManaged(record(newer));
const changes = publisher.changes(await read(), snapshot.view);
host.endManaged(newer);
ids = [];
const removal = publisher.changes(await read(), changes.view);
definitions.list = async () => { throw new Error('offline incomplete observation'); };
const unavailable = publisher.snapshot(await read());
process.stdout.write(JSON.stringify([snapshot, changes, removal, unavailable].map(publication => JSON.stringify(publication.body))));
