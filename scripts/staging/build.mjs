import fs from 'node:fs';
import path from 'node:path';
import { StagingSources } from './sources.mjs';

/** Acquires exact sources and builds only names admitted by the run's existing resource ledger. */
export class StagingBuild {
  constructor({ manifest, layout, repositories, directory, docker, resources, environment }) {
    Object.assign(this, { manifest, layout, repositories, directory: path.resolve(directory), docker, resources, environment });
  }

  run() {
    this.prepareContext();
    this.buildImages();
    return { context: this.context, snapshots: this.snapshots, images: this.images };
  }

  prepareContext() {
    this.context = path.join(this.directory, 'build-context');
    fs.mkdirSync(this.context, { recursive: false });
    const sources = new StagingSources({ manifest: this.manifest, repositories: this.repositories,
      directory: path.join(this.context, 'sources'), dataVolume: this.layout.dataVolume, environment: this.environment });
    this.snapshots = sources.prepare();
    fs.copyFileSync(new URL('./Dockerfile', import.meta.url), path.join(this.context, 'Dockerfile'));
    fs.writeFileSync(path.join(this.context, '.dockerignore'), '**/.git/index\n**/.git/logs\n**/.git/hooks\n**/.git/FETCH_HEAD\n');
    this.images = Object.freeze({ dependencies: `${this.layout.project}-dependencies`,
      baseline: this.layout.compose('baseline').services.service.image,
      candidate: this.layout.compose('candidate').services.service.image,
      host: this.layout.compose('candidate').services.host.image });
  }

  buildImages() {
    const versions = this.manifest.runtimeVersions;
    for (const [target, image] of Object.entries(this.images)) {
      this.resources.plan({ kind: 'image', name: image });
      const args = ['build', '--target', target, '--label', `aify.staging.run=${this.layout.runId}`,
        '--build-arg', `NODE_IMAGE=${versions.nodeImage}`, '--build-arg', `CODEX_VERSION=${versions.codex}`,
        '--build-arg', `CLAUDE_VERSION=${versions.claude}`, '--tag', image, this.context];
      const result = this.docker(args);
      if (result.status !== 0 || result.errorCode || result.signal) throw new Error(`staging ${target} image build did not complete successfully`);
    }
  }
}
