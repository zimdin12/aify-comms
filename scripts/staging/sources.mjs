import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { dockerEnvironment } from './docker.mjs';

/** Clean, shallow, run-owned Git snapshots. Never stamps or builds an input checkout. */
export class StagingSources {
  constructor({ manifest, repositories, directory, dataVolume, environment = process.env }) {
    this.manifest = manifest;
    this.repositories = Object.freeze(Object.fromEntries(Object.keys(manifest.baseline).map((role) => {
      if (typeof repositories?.[role] !== 'string') throw new Error(`source repository is required: ${role}`);
      return [role, fs.realpathSync(repositories[role])];
    })));
    this.directory = path.resolve(directory);
    this.baseEnvironment = dockerEnvironment(environment, dataVolume);
    this.receipts = [];
  }

  prepare() {
    fs.mkdirSync(this.directory, { recursive: false });
    const home = path.join(this.directory, 'git-home');
    const hooks = path.join(this.directory, 'empty-hooks');
    fs.mkdirSync(home);
    fs.mkdirSync(hooks);
    const globalConfig = path.join(home, 'gitconfig');
    fs.writeFileSync(globalConfig, '');
    this.environment = { ...this.baseEnvironment, HOME: home, USERPROFILE: home,
      GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: globalConfig, GIT_TERMINAL_PROMPT: '0',
      GIT_CONFIG_COUNT: '2', GIT_CONFIG_KEY_0: 'core.hooksPath', GIT_CONFIG_VALUE_0: hooks,
      GIT_CONFIG_KEY_1: 'core.autocrlf', GIT_CONFIG_VALUE_1: 'false' };
    const tuples = { baseline: this.manifest.baseline, candidate: this.manifest.candidate };
    try {
      this.resolveAll(tuples);
      const snapshots = this.copyAll(tuples);
      return Object.freeze(snapshots);
    } finally {
      fs.writeFileSync(path.join(this.directory, 'git-receipts.json'), JSON.stringify(this.receipts, null, 2) + '\n');
    }
  }

  resolveAll(tuples) {
    for (const [phase, tuple] of Object.entries(tuples)) {
      for (const [role, ref] of Object.entries(tuple)) {
        if (this.git(['-C', this.repositories[role], 'cat-file', '-t', ref]).trim() !== 'commit') {
          throw new Error(`${phase}.${role} is not a commit`);
        }
      }
    }
  }

  copyAll(tuples) {
    const snapshots = {};
    for (const [phase, tuple] of Object.entries(tuples)) {
      const phaseDirectory = path.join(this.directory, phase);
      fs.mkdirSync(phaseDirectory);
      snapshots[phase] = Object.freeze(Object.fromEntries(Object.entries(tuple).map(([role, ref]) => {
        const destination = path.join(phaseDirectory, role);
        fs.mkdirSync(destination);
        this.git(['-C', destination, 'init', '--quiet']);
        this.git(['-C', destination, 'fetch', '--quiet', '--no-tags', '--no-recurse-submodules', '--depth=1', this.repositories[role], ref]);
        this.git(['-C', destination, 'checkout', '--quiet', '--detach', ref]);
        if (this.git(['-C', destination, 'rev-parse', 'HEAD']).trim() !== ref
            || this.git(['-C', destination, 'status', '--porcelain']).trim()) {
          throw new Error(`${phase}.${role} snapshot does not match its clean exact commit`);
        }
        return [role, destination];
      })));
    }
    return snapshots;
  }

  git(args) {
    const result = spawnSync('git', [...args], { env: this.environment, encoding: 'utf8', timeout: 60_000, maxBuffer: 4 << 20 });
    this.receipts.push({ command: ['git', ...args], status: result.status, signal: result.signal,
      errorCode: result.error?.code ?? null, out: result.stdout ?? '', error: result.stderr ?? '' });
    if (result.status !== 0 || result.error) throw new Error('staging Git source operation failed; see run-owned git-receipts.json');
    return result.stdout;
  }
}
