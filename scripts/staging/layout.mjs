import { isDeepStrictEqual } from 'node:util';

/** One run's resource identity and generated isolation intent. No Docker or filesystem access. */
export class StagingLayout {
  constructor({ runId, fixtureKey, dashboardKey }) {
    if (typeof runId !== 'string' || runId.length !== 12 || !/^[0-9a-f]{12}$/.test(runId)) {
      throw new TypeError('staging run identity must be twelve lowercase hex characters');
    }
    if (typeof fixtureKey !== 'string' || !fixtureKey.trim()) throw new TypeError('a run-owned fixture key is required');
    this.runId = runId;
    this.project = `aify-comms-staging-${runId}`;
    this.dataVolume = `${this.project}-data`;
    this.homeVolume = `${this.project}-home`;
    this.network = `${this.project}-network`;
    this.fixtureKey = fixtureKey;
    if (dashboardKey !== undefined && (typeof dashboardKey !== 'string' || !dashboardKey.trim() || dashboardKey === fixtureKey)) {
      throw new TypeError('a staged dashboard needs its own run-owned key');
    }
    this.dashboardKey = dashboardKey;
    this.dashboardOrigin = 'http://aify-dashboard:9700';
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
        },
        host: {
          image: `${this.project}-host`, container_name: `${this.project}-host`, restart: 'no',
          // A real host's init reaps orphans; without one a zombie keeps a stopped process group present.
          init: true, labels: { ...labels }, networks: ['staging'], command: ['sleep', 'infinity'],
          environment: { HOME: '/home/staging' },
          volumes: [{ type: 'volume', source: 'host-home', target: '/home/staging' }],
        },
        // E5 only: the dashboard's own server from the staged source, on the host image, with its own volume and key.
        ...(this.dashboardKey ? { 'aify-dashboard': {
          image: `${this.project}-host`, container_name: `${this.project}-aify-dashboard`, restart: 'no',
          labels: { ...labels }, networks: ['staging'], working_dir: `/src/${phase}/dashboard`,
          command: ['node', '--experimental-strip-types', 'server/src/main.ts'],
          environment: { PORT: '9700', BIND_HOST: '0.0.0.0', API_KEY: this.dashboardKey,
            PUBLIC_ORIGINS: this.dashboardOrigin, DATABASE_PATH: '/data/dashboard.db' },
          volumes: [{ type: 'volume', source: 'dashboard-data', target: '/data' }],
        } } : {}),
      },
      volumes: {
        'service-data': { name: this.dataVolume, labels: { ...labels } },
        'host-home': { name: this.homeVolume, labels: { ...labels } },
        ...(this.dashboardKey ? { 'dashboard-data': { name: `${this.project}-dashboard-data`, labels: { ...labels } } } : {}),
      },
      networks: { staging: { name: this.network, internal: true, labels: { ...labels } } },
    };
  }

  /** Checks the generated intent BEFORE Compose sees it, not a claim about actual Docker mounts. */
  assertIsolation({ config, environment }) {
    if (environment?.DATA_VOLUME_NAME !== this.dataVolume) throw new Error('DATA_VOLUME_NAME must explicitly name this run-owned staging volume');
    const expected = ['baseline', 'candidate'].map((phase) => this.compose(phase));
    if (!expected.some((intent) => isDeepStrictEqual(intent, config))) {
      throw new Error('staging isolation refused: configuration differs from the run-owned intent');
    }
    return true;
  }
}
