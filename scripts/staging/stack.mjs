import { setTimeout as delay } from 'node:timers/promises';
import { assertRenderedIsolation, assertContainerIsolation } from './observed-isolation.mjs';

/** One run's images plus the live services' growth (lead decision, 2026-11-08, after the disk filled). */
export const MINIMUM_FREE_BYTES = 40 * 2 ** 30;
const LEFTOVER_QUERIES = [['ps', '-a', '--format', '{{.Names}}'], ['volume', 'ls', '--format', '{{.Name}}'],
  ['network', 'ls', '--format', '{{.Name}}'], ['images', '--format', '{{.Repository}}:{{.Tag}} {{.ID}}']];

export class StagingStack {
  constructor(session) { this.session = session; }

  qualifyDaemon() {
    const s = this.session;
    const format = '{"id":{{json .ID}},"osType":{{json .OSType}}}';
    const normal = JSON.parse(s.requireCommand(['info', '--format', format], 'Docker daemon observation'));
    const endpoint = process.platform === 'win32' ? 'npipe:////./pipe/docker_engine' : 'unix:///var/run/docker.sock';
    const explicit = JSON.parse(s.requireCommand(['--host', endpoint, 'info', '--format', format], 'explicit Docker endpoint control'));
    const missingEndpoint = process.platform === 'win32' ? `npipe:////./pipe/${s.layout.project}-absent`
      : `unix:///run/${s.layout.project}-absent.sock`;
    const absent = s.command(['--host', missingEndpoint, 'info']);
    s.check('daemon', normal.osType === 'linux' && typeof normal.id === 'string' && normal.id.length > 8
      && normal.id === explicit.id && absent.status === 1 && /cannot find the file specified|no such file or directory/i.test(absent.out),
    { daemon: normal, absentStatus: absent.status });
    const compose = s.requireCommand(['compose', 'version', '--short'], 'Compose CLI prerequisite');
    s.check('compose_cli', compose.trim().length > 0, { version: compose.trim(), executable: s.composeExecutable });
    this.qualifyCapacity();
  }

  /** Refuses before this run creates anything: a full daemon disk fails live comms writes, and a killed run leaves its labelled resources. */
  qualifyCapacity() {
    const s = this.session;
    const free = Number(s.requireCommand(['run', '--rm', '--network', 'none', '--pull', 'missing', s.manifest.runtimeVersions.nodeImage,
      'df', '-B1', '--output=avail', '/'], 'Docker disk free space').trim().split('\n').pop());
    if (!(free >= MINIMUM_FREE_BYTES)) throw new Error(`the Docker disk has ${(free / 2 ** 30).toFixed(1)} GB free; a run needs ${MINIMUM_FREE_BYTES / 2 ** 30} GB`);
    const leftovers = LEFTOVER_QUERIES.flatMap(query => s.requireCommand([...query, '--filter', 'label=aify.staging.run'],
      'labelled staging resources').split('\n').map(line => line.trim()).filter(Boolean));
    if (leftovers.length) throw new Error(`another staging run's resources exist; remove them first: ${leftovers.join(', ')}`);
    s.check('daemon_capacity', true, { freeBytes: free, leftovers });
  }

  create(phase) {
    const s = this.session;
    const rendered = s.writeCompose(phase);
    assertRenderedIsolation(s.layout, phase, rendered);
    s.save(`${phase}-rendered.json`, rendered);
    const intent = s.layout.compose(phase);
    s.resources.plan({ kind: 'network', name: s.layout.network });
    for (const volume of Object.values(intent.volumes)) s.resources.plan({ kind: 'volume', name: volume.name });
    for (const service of Object.values(intent.services)) s.resources.plan({ kind: 'container', name: service.container_name });
    this.assertImages(phase);
    s.requireCommand(s.composeArgs(phase, 'up', '--no-start', '--no-build', '--pull', 'never'), 'isolated container creation');
    this.assertActualIsolation(phase);
  }

  assertImages(phase) {
    const s = this.session;
    for (const imageName of new Set(Object.values(s.layout.compose(phase).services).map(service => service.image))) {
      const image = s.inspect('image', imageName);
      if (image.Config?.User !== 'staging' || image.Config?.Labels?.['aify.staging.run'] !== s.layout.runId
          || Object.keys(image.Config?.Volumes ?? {}).length) throw new Error('staging image identity or implicit mounts refused');
    }
  }

  assertActualIsolation(phase, observation = 'created') {
    const s = this.session;
    const network = s.inspect('network', s.layout.network);
    if (network.Internal !== true || network.Driver !== 'bridge' || network.Labels?.['aify.staging.run'] !== s.layout.runId) {
      throw new Error('actual staging network differs from run-owned intent');
    }
    const intent = s.layout.compose(phase);
    for (const volume of Object.values(intent.volumes)) {
      const actual = s.inspect('volume', volume.name);
      if (actual.Driver !== 'local' || Object.keys(actual.Options ?? {}).length || actual.Labels?.['aify.staging.run'] !== s.layout.runId) {
        throw new Error('actual staging volume differs from run-owned intent');
      }
    }
    for (const [role, service] of Object.entries(intent.services)) {
      assertContainerIsolation(s.layout, phase, role, s.inspect('container', service.container_name), s.inspect('image', service.image));
    }
    s.check(`${phase}_isolation_${observation}`, true, 'actual own images, mounts, environment, internal network and no published ports observed');
  }

  async start(phase) {
    const s = this.session;
    s.requireCommand(s.composeArgs(phase, 'start'), 'staging container start');
    this.assertActualIsolation(phase, 'after_start');
    await this.waitForInternalHealth(phase);
    await this.qualifyNetworkEndpoint(phase);
    this.qualifyRuntimeVersions();
  }

  async waitForInternalHealth(phase) {
    const s = this.session;
    const command = ['exec', `${s.layout.project}-service`, 'python', '-c',
      "import urllib.request;print(urllib.request.urlopen('http://localhost:8800/health',timeout=2).read().decode())"];
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      const result = s.command(command);
      if (result.status === 0) {
        const body = JSON.parse(result.out);
        if (body.status === 'healthy' && typeof body.build === 'string' && body.build.length >= 7
            && s.manifest[phase].comms.startsWith(body.build) && !body.buildIdentityOverriddenBy?.length) {
          s.check(`${phase}_internal_health`, true, body);
          return body;
        }
        throw new Error('staging internal health names the wrong build');
      }
      await delay(250);
    }
    s.command(['logs', `${s.layout.project}-service`]);
    throw new Error('staging service never reached internally observed health');
  }

  async qualifyNetworkEndpoint(phase) {
    const s = this.session;
    const health = await this.request('/health');
    const body = JSON.parse(health.body);
    const admitted = await this.request('/api/v1/agents', s.layout.fixtureKey);
    const refused = await this.request('/api/v1/agents', `absent-${s.layout.runId}`);
    s.check(`${phase}_network_endpoint`, health.status === 200 && body.status === 'healthy'
      && typeof body.build === 'string' && body.build.length >= 7 && s.manifest[phase].comms.startsWith(body.build)
      && admitted.status === 200 && refused.status === 401,
    { health, authenticatedStatus: admitted.status, wrongKeyStatus: refused.status });
  }

  async request(route, key, { method = 'GET', body, origin = 'http://service:8800' } = {}) {
    const s = this.session;
    const script = `const [route,key,method,body,origin]=process.argv.slice(1);
      const response=await fetch(origin+route,{method,signal:AbortSignal.timeout(5000),
        headers:{...(key?{'x-api-key':key}:{}),...(body?{'content-type':'application/json'}:{})},body:body||undefined});
      console.log(JSON.stringify({status:response.status,body:await response.text()}));`;
    return JSON.parse(s.requireCommand(['exec', `${s.layout.project}-host`, 'node', '--input-type=module', '-e', script,
      route, key ?? '', method, body === undefined ? '' : JSON.stringify(body), origin], 'staging-network HTTP request'));
  }

  qualifyRuntimeVersions() {
    const s = this.session;
    const host = `${s.layout.project}-host`;
    const versions = Object.fromEntries(['node', 'codex', 'claude'].map(binary => [binary,
      s.requireCommand(['exec', host, binary, '--version'], `native ${binary} version`).trim()]));
    const expectedNode = /:(\d+\.\d+\.\d+)/.exec(s.manifest.runtimeVersions.nodeImage)?.[1];
    s.check('runtime_versions', (!expectedNode || versions.node === `v${expectedNode}`)
      && versions.codex.split(/\s+/).includes(s.manifest.runtimeVersions.codex)
      && versions.claude.split(/\s+/).includes(s.manifest.runtimeVersions.claude), versions);
  }
}
