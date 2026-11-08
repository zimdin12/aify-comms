import { isDeepStrictEqual } from 'node:util';

function same(actual, expected) {
  if (!isDeepStrictEqual(actual, expected)) throw new Error('observed staging isolation differs from run-owned intent');
}
const keys = value => Object.keys(value ?? {}).sort();
const mounts = value => (value ?? []).map(item => ({ type: item.type, source: item.source,
  target: item.target, readOnly: Boolean(item.read_only) })).sort((a, b) => a.target.localeCompare(b.target));

/** Compares actual Compose rendering, not the raw object already checked by StagingLayout. */
export function assertRenderedIsolation(layout, phase, rendered) {
  const expected = layout.compose(phase);
  same(rendered.name, layout.project);
  same(keys(rendered.services), keys(expected.services));
  same(keys(rendered.volumes), keys(expected.volumes));
  same(keys(rendered.networks), keys(expected.networks));
  same(keys(rendered.secrets), []);
  same(keys(rendered.configs), []);
  for (const [name, volume] of Object.entries(expected.volumes)) {
    same(rendered.volumes[name].name, volume.name);
    same(Boolean(rendered.volumes[name].external), false);
    same(rendered.volumes[name].labels?.['aify.staging.run'], layout.runId);
  }
  same(rendered.networks.staging.name, layout.network);
  same(rendered.networks.staging.internal, true);
  same(Boolean(rendered.networks.staging.external), false);
  same(rendered.networks.staging.labels?.['aify.staging.run'], layout.runId);
  for (const [name, service] of Object.entries(expected.services)) {
    const actual = rendered.services[name];
    same(actual.image, service.image);
    same(actual.container_name, service.container_name);
    same(actual.environment, service.environment);
    same(keys(actual.networks), ['staging']);
    same(mounts(actual.volumes), mounts(service.volumes));
    same(actual.ports ?? [], []);
    same(actual.labels?.['aify.staging.run'], layout.runId);
    same(actual.restart, 'no');
    for (const field of ['privileged', 'network_mode', 'pid', 'ipc', 'volumes_from', 'devices', 'secrets', 'configs', 'env_file', 'build']) {
      if (actual[field] && (!Array.isArray(actual[field]) || actual[field].length)) {
        throw new Error('observed staging configuration adds an unapproved container input');
      }
    }
  }
  return true;
}

const environment = values => Object.fromEntries((values ?? []).map(value => {
  const split = value.indexOf('=');
  return [value.slice(0, split), value.slice(split + 1)];
}));

/** Actual daemon-side container metadata must match the rendered mounts, image and environment. */
export function assertContainerIsolation(layout, phase, role, container, image) {
  const intent = layout.compose(phase);
  const expected = intent.services[role];
  if (!expected || !container?.Config || !container?.HostConfig || !image?.Id) throw new Error('staging container isolation could not be observed');
  same(container.Name, `/${expected.container_name}`);
  same(container.Image, image.Id);
  same(container.Config.User, 'staging');
  same(container.Config.Labels?.['aify.staging.run'], layout.runId);
  same(image.Config?.Labels?.['aify.staging.run'], layout.runId);
  same(environment(container.Config.Env), { ...environment(image.Config.Env), ...expected.environment });
  same(container.HostConfig.Privileged, false);
  same(container.HostConfig.PortBindings, {});
  same(container.HostConfig.PublishAllPorts, false);
  same(container.HostConfig.NetworkMode, layout.network);
  same(keys(container.NetworkSettings?.Networks), [layout.network]);
  same(container.HostConfig.PidMode || '', '');
  same(container.HostConfig.Devices ?? [], []);
  same(container.HostConfig.DeviceRequests ?? [], []);
  const actualMounts = (container.Mounts ?? []).map(item => ({ type: item.Type, name: item.Name,
    target: item.Destination, writable: item.RW })).sort((a, b) => a.target.localeCompare(b.target));
  const expectedMounts = (expected.volumes ?? []).map(item => ({ type: item.type, name: intent.volumes[item.source].name,
    target: item.target, writable: !item.read_only })).sort((a, b) => a.target.localeCompare(b.target));
  same(actualMounts, expectedMounts);
  return true;
}
