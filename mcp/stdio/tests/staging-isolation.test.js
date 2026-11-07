import assert from 'node:assert/strict';
import test from 'node:test';

const { StagingLayout } = await import('../../../scripts/staging/layout.mjs');
const options = () => ({ runId: '0123456789ab', fixtureKey: 'run-owned-fixture-key', ports: { service: 8820, dashboard: 8821 } });
const fixture = () => {
  const layout = new StagingLayout(options());
  return { layout, config: layout.compose('baseline'), environment: { DATA_VOLUME_NAME: layout.dataVolume } };
};

test('the generated staging layout has no host bind or credential input', () => {
  const { layout, config, environment } = fixture();
  assert.equal(layout.assertIsolation({ config, environment }), true);
  assert.equal(config.networks.staging.internal, true);
  assert.equal(config.volumes['service-data'].name, layout.dataVolume);
  assert.equal(config.services.host.environment.HOME, '/home/staging');
  assert.equal(config.services.service.environment.API_KEY, options().fixtureKey);
  assert.equal(config.services.service.ports[0].host_ip, '127.0.0.1');
});

for (const value of [undefined, '', 'aify-comms-data', 'another-run-data']) {
  test(`DATA_VOLUME_NAME must be explicitly owned, not ${String(value)}`, () => {
    const { layout, config } = fixture();
    assert.throws(() => layout.assertIsolation({ config, environment: { DATA_VOLUME_NAME: value } }), /DATA_VOLUME_NAME/);
  });
}

const corruptions = {
  'live volume': (c) => { c.volumes['service-data'].name = 'aify-comms-data'; },
  'external volume': (c) => { c.volumes['service-data'].external = true; },
  'host credential mount': (c) => { c.services.service.volumes.push({ type: 'bind', source: '/home/operator/.claude', target: '/home/service/.claude' }); },
  'Docker socket mount': (c) => { c.services.host.volumes.push({ type: 'bind', source: '/var/run/docker.sock', target: '/var/run/docker.sock' }); },
  'ambient provider key': (c) => { c.services.host.environment.OPENAI_API_KEY = 'host-value-must-not-be-printed'; },
  'ambient env file': (c) => { c.services.service.env_file = ['.env']; },
  'host network': (c) => { c.services.host.network_mode = 'host'; },
  'privileged container': (c) => { c.services.host.privileged = true; },
  'external network': (c) => { c.networks.staging.internal = false; },
  'public port': (c) => { c.services.service.ports[0].host_ip = '0.0.0.0'; },
  'foreign image': (c) => { c.services.host.image = 'foreign-image:latest'; },
  'surprise service': (c) => { c.services.foreign = { image: 'foreign-image:latest' }; },
  'missing service': (c) => { delete c.services.service; },
};
for (const [label, corrupt] of Object.entries(corruptions)) {
  test(`the real isolation validator refuses ${label}`, () => {
    const { layout, config, environment } = fixture();
    corrupt(config);
    assert.throws(() => layout.assertIsolation({ config, environment }), /staging isolation/);
  });
}

test('isolation diagnostics do not echo unexpected credential values', () => {
  const { layout, config, environment } = fixture();
  corruptions['ambient provider key'](config);
  assert.throws(() => layout.assertIsolation({ config, environment }), (error) => {
    assert.doesNotMatch(String(error), /host-value-must-not-be-printed/);
    return true;
  });
});

for (const runId of [undefined, '', 'aify-comms', '../live', '0123456789ab\n']) {
  test(`invalid run identity ${JSON.stringify(runId)} refuses`, () => {
    assert.throws(() => new StagingLayout({ ...options(), runId }), /run identity/);
  });
}
