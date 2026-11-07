import { isDeepStrictEqual } from 'node:util';

/** One run's resource identity and generated isolation intent. No Docker or filesystem access. */
export class StagingLayout {
  constructor({ runId, fixtureKey, ports }) {
    if (typeof runId !== 'string' || runId.length !== 12 || !/^[0-9a-f]{12}$/.test(runId)) {
      throw new TypeError('staging run identity must be twelve lowercase hex characters');
    }
    if (typeof fixtureKey !== 'string' || !fixtureKey.trim()) throw new TypeError('a run-owned fixture key is required');
    const published = { service: ports?.service, dashboard: ports?.dashboard };
    if (Object.values(published).some((port) => !Number.isSafeInteger(port) || port < 1024 || port > 65535)
        || published.service === published.dashboard) throw new TypeError('distinct staging ports are required');
    this.runId = runId;
    this.project = `aify-comms-staging-${runId}`;
    this.dataVolume = `${this.project}-data`;
    this.homeVolume = `${this.project}-home`;
    this.network = `${this.project}-network`;
    this.fixtureKey = fixtureKey;
    this.ports = Object.freeze(published);
    Object.freeze(this);
  }

  compose(phase) {
    if (phase !== 'baseline' && phase !== 'candidate') throw new TypeError('staging phase must be baseline or candidate');
    const labels = { 'aify.staging.run': this.runId };
    const service = {
      image: `${this.project}-comms-${phase}`, container_name: `${this.project}-service`,
      restart: 'no', labels: { ...labels }, networks: ['staging'],
      environment: { API_KEY: this.fixtureKey, DATA_DIR: '/data', SERVICE_PORT: '8800', TRUSTED_HOSTS: 'service,localhost' },
      volumes: [{ type: 'volume', source: 'service-data', target: '/data' }],
      ports: [{ target: 8800, published: String(this.ports.service), host_ip: '127.0.0.1', protocol: 'tcp' }],
    };
    return {
      name: this.project,
      services: {
        service,
        dashboard: {
          image: service.image, container_name: `${this.project}-dashboard`, restart: 'no',
          labels: { ...labels }, networks: ['staging'],
          command: ['python', '-m', 'uvicorn', 'service.new_dashboard_app:app', '--host', '0.0.0.0', '--port', '8801', '--no-access-log'],
          environment: { DATA_DIR: '/data', API_KEY: this.fixtureKey },
          ports: [{ target: 8801, published: String(this.ports.dashboard), host_ip: '127.0.0.1', protocol: 'tcp' }],
        },
        host: {
          image: `${this.project}-host`, container_name: `${this.project}-host`, restart: 'no',
          labels: { ...labels }, networks: ['staging'], command: ['sleep', 'infinity'],
          environment: { HOME: '/home/staging' },
          volumes: [{ type: 'volume', source: 'host-home', target: '/home/staging' }],
        },
      },
      volumes: {
        'service-data': { name: this.dataVolume, labels: { ...labels } },
        'host-home': { name: this.homeVolume, labels: { ...labels } },
      },
      networks: { staging: { name: this.network, internal: true, labels: { ...labels } } },
    };
  }

  /** Checks the generated intent BEFORE Compose sees it, not a claim about actual Docker mounts. */
  assertIsolation({ config, environment }) {
    if (environment?.DATA_VOLUME_NAME !== this.dataVolume) throw new Error('DATA_VOLUME_NAME must explicitly name this run-owned staging volume');
    const expected = ['baseline', 'candidate'].map((phase) => this.compose(phase));
    if (!expected.some((intent) => isDeepStrictEqual(intent, config))) {
      // Never stringify untrusted configuration: it may be the credential-bearing input refused here.
      throw new Error('staging isolation refused: configuration differs from the run-owned intent');
    }
    return true;
  }
}
