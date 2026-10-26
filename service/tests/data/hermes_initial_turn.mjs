// Offline task #12 diagnostic: actual host and publisher, no runtime, process probe or network.
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [source, home] = process.argv.slice(2);
const load = name => import(pathToFileURL(path.join(source, 'lib', name)).href);
const { AgentStateHost } = await load('agent-state-host.mjs');
const { AgentStatePublisher } = await load('agent-state-publisher.mjs');
const now = 1_792_782_000_000_000;
const host = new AgentStateHost({ aifyHome: home, instance: 'fixture', population: 'managed',
  probe: () => new Map(), nowUs: () => now });
host.boot();
const lifetime = '11111111-1111-4111-8111-111111111111';
host.startManaged({ agentId: 'initial-hermes', lifetime, instance: 'fixture', pid: 4242, handle: 'owned-child' });
const given = { mode: 'managed', definition: 'valid', stoppedByOperator: false };
const publisher = new AgentStatePublisher({ machineId: 'test-host', instance: 'fixture', generation: 1,
  incarnationId: 'fixture-boot', reportEveryMs: 60_000 });
const publish = () => publisher.snapshot({ complete: true, inputs: {}, agents: [host.current('initial-hermes', given)] }).body;
const fresh = publish();
host.applyEvent({ agentId: 'initial-hermes', lifetime, kind: 'turn-start', firedAtUs: now - 2 });
const busy = publish();
host.applyEvent({ agentId: 'initial-hermes', lifetime, kind: 'turn-end', firedAtUs: now - 1 });
const closed = publish();
process.stdout.write(JSON.stringify({ fresh, busy, closed }));
