import assert from 'node:assert/strict';
import test from 'node:test';
import { StagingLayout } from '../../../scripts/staging/layout.mjs';
import { assertRenderedIsolation, assertContainerIsolation } from '../../../scripts/staging/observed-isolation.mjs';

function fixture(role = 'service') {
  const layout = new StagingLayout({ runId: '0123456789ab', fixtureKey: 'fixture-key', ports: { service: 8820, dashboard: 8821 } });
  const rendered = layout.compose('baseline');
  for (const service of Object.values(rendered.services)) service.networks = { staging: null };
  const intent = layout.compose('baseline');
  const service = intent.services[role];
  const image = { Id: `sha256:fixture-${role}`, Config: { Env: ['PATH=fixture-bin', 'HOME=/home/staging'], Labels: { 'aify.staging.run': layout.runId } } };
  const container = { Name: `/${service.container_name}`, Image: image.Id,
    Config: { User: 'staging', Labels: { 'aify.staging.run': layout.runId },
      Env: Object.entries({ PATH: 'fixture-bin', HOME: '/home/staging', ...service.environment }).map(([name, value]) => `${name}=${value}`) },
    HostConfig: { Privileged: false, NetworkMode: layout.network, PidMode: '', Devices: [], DeviceRequests: [], PortBindings: {}, PublishAllPorts: false },
    NetworkSettings: { Networks: { [layout.network]: {} } },
    Mounts: (service.volumes ?? []).map(item => ({ Type: item.type, Name: intent.volumes[item.source].name, Destination: item.target, RW: true })) };
  return { layout, rendered, container, image, role };
}

for (const role of ['service', 'dashboard', 'host']) {
  test(`rendered defaults and actual own containers retain isolation for ${role}`, () => {
    const f = fixture(role);
    assert.equal(assertRenderedIsolation(f.layout, 'baseline', f.rendered), true);
    assert.equal(assertContainerIsolation(f.layout, 'baseline', role, f.container, f.image), true);
  });
}

for (const role of ['host', 'dashboard']) {
  test(`actual container inspection refuses an additional host bind for ${role}`, () => {
    const f = fixture(role);
    f.container.Mounts.push({ Type: 'bind', Source: '/home/operator', Destination: '/credential-input', RW: false });
    assert.throws(() => assertContainerIsolation(f.layout, 'baseline', role, f.container, f.image), /staging/);
  });
}

test('actual host inspection refuses a foreign persistent home volume', () => {
  const f = fixture('host');
  f.container.Mounts[0].Name = 'operator-home';
  assert.throws(() => assertContainerIsolation(f.layout, 'baseline', f.role, f.container, f.image), /staging/);
});

const renderedChanges = {
  'ambient environment': c => { c.services.host.environment.OPENAI_API_KEY = 'CREDENTIALX'; },
  'host bind': c => { c.services.service.volumes[0] = { type: 'bind', source: '/home/operator', target: '/data' }; },
  'public port': c => { c.services.service.ports = [{ target: 8800, published: '8820', host_ip: '0.0.0.0', protocol: 'tcp' }]; },
  'loopback publication': c => { c.services.service.ports = [{ target: 8800, published: '8820', host_ip: '127.0.0.1', protocol: 'tcp' }]; },
  'external data volume': c => { c.volumes['service-data'].external = true; },
  'noninternal network': c => { c.networks.staging.internal = false; },
  'privileged service': c => { c.services.service.privileged = true; },
  'ambient env file': c => { c.services.service.env_file = ['.env']; },
  'missing service': c => { delete c.services.service; },
};
for (const [name, change] of Object.entries(renderedChanges)) {
  test(`actual Compose rendering refuses ${name}`, () => {
    const f = fixture();
    change(f.rendered);
    assert.throws(() => assertRenderedIsolation(f.layout, 'baseline', f.rendered), error => {
      assert.doesNotMatch(String(error), /CREDENTIALX/);
      return /staging/.test(String(error));
    });
  });
}

const containerChanges = {
  'foreign data volume': c => { c.Mounts[0].Name = 'aify-comms-data'; },
  'host bind': c => { c.Mounts[0].Type = 'bind'; },
  'additional mount': c => { c.Mounts.push({ Type: 'bind', Name: '', Destination: '/keys', RW: false }); },
  'foreign image': c => { c.Image = 'sha256:foreign'; },
  'foreign labels': c => { c.Config.Labels['aify.staging.run'] = 'foreign'; },
  'host network': c => { c.HostConfig.NetworkMode = 'host'; },
  'privileged runtime': c => { c.HostConfig.Privileged = true; },
  'loopback host binding': c => { c.HostConfig.PortBindings = { '8800/tcp': [{ HostIp: '127.0.0.1', HostPort: '8820' }] }; },
  'automatic port publication': c => { c.HostConfig.PublishAllPorts = true; },
  'unobserved port bindings': c => { delete c.HostConfig.PortBindings; },
  'host pid namespace': c => { c.HostConfig.PidMode = 'host'; },
  'ambient credential': c => { c.Config.Env.push('OPENAI_API_KEY=CREDENTIALX'); },
  'unobserved metadata': c => { delete c.Config; },
};
for (const [name, change] of Object.entries(containerChanges)) {
  test(`actual container inspection refuses ${name}`, () => {
    const f = fixture();
    change(f.container);
    assert.throws(() => assertContainerIsolation(f.layout, 'baseline', f.role, f.container, f.image), error => {
      assert.doesNotMatch(String(error), /CREDENTIALX/);
      return /staging/.test(String(error));
    });
  });
}
