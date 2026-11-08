import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { StagingManifest } from './manifest.mjs';
import { StagingDocker } from './docker.mjs';
import { StagingResources } from './resources.mjs';
import { StagingBuild } from './build.mjs';

/** One native invocation's output, exact command receipts and existing ownership ledger. */
export class StagingSession {
  constructor({ manifest, layout, options }) {
    this.manifest = manifest;
    this.layout = layout;
    this.options = Object.freeze({ ...options });
    this.environment = Object.freeze({ DATA_VOLUME_NAME: layout.dataVolume });
    this.out = path.resolve(options.out);
    fs.mkdirSync(this.out, { recursive: false });
    this.configDirectory = path.join(this.out, 'private-docker-config');
    fs.mkdirSync(this.configDirectory);
    this.emptyEnvironmentFile = path.join(this.out, 'empty-compose.env');
    fs.writeFileSync(this.emptyEnvironmentFile, '');
    this.docker = new StagingDocker({ environment: process.env, dataVolume: layout.dataVolume, timeoutMs: 480_000 });
    const searchPath = Object.entries(this.docker.environment).find(([name]) => name.toUpperCase() === 'PATH')?.[1] ?? '';
    const composeName = process.platform === 'win32' ? 'docker-compose.exe' : 'docker-compose';
    const compose = searchPath.split(path.delimiter).filter(Boolean).map(directory => path.join(directory, composeName))
      .find(file => fs.existsSync(file) && fs.statSync(file).isFile());
    this.composeExecutable = compose ? fs.realpathSync(compose) : null;
    const configuration = this.composeExecutable ? { cliPluginsExtraDirs: [path.dirname(this.composeExecutable)] } : {};
    fs.writeFileSync(path.join(this.configDirectory, 'config.json'), JSON.stringify(configuration) + '\n');
    this.receipts = [];
    this.checks = [];
    this.resources = new StagingResources({ layout, docker: args => this.command(args) });
    this.freezeInputs();
  }

  freezeInputs() {
    const refs = fs.readFileSync(this.options.refs);
    const reread = new StagingManifest(JSON.parse(refs.toString('utf8')));
    if (!isDeepStrictEqual(reread, this.manifest)) throw new Error('staging refs changed after validation');
    fs.writeFileSync(path.join(this.out, 'refs.json'), refs);
    const root = new URL('./', import.meta.url);
    const capsule = path.join(this.out, 'executed-staging-source');
    const destination = path.join(capsule, 'scripts', 'staging');
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.cpSync(root, destination, { recursive: true });
    const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    const files = fs.readdirSync(root, { recursive: true, withFileTypes: true }).filter(entry => entry.isFile());
    this.sourceHashes = Object.fromEntries(files.map(entry => {
      const file = path.join(entry.parentPath, entry.name);
      return [file, hash(file)];
    }));
    const readback = new URL('../sandbox/docker-readback.mjs', import.meta.url);
    const sandbox = path.join(capsule, 'scripts', 'sandbox');
    fs.mkdirSync(sandbox);
    fs.copyFileSync(readback, path.join(sandbox, 'docker-readback.mjs'));
    this.sourceHashes[fileURLToPath(readback)] = hash(readback);
    const entry = path.resolve(process.argv[1]);
    fs.copyFileSync(entry, path.join(capsule, `executed-entry${path.extname(entry)}`));
    this.sourceHashes[entry] = hash(entry);
    if (this.composeExecutable) this.sourceHashes[this.composeExecutable] = hash(this.composeExecutable);
    this.sourceHashes[path.join(this.configDirectory, 'config.json')] = hash(path.join(this.configDirectory, 'config.json'));
    this.save('invocation.json', { argv: [...process.argv], cwd: process.cwd(), environmentNames: Object.keys(this.docker.environment) });
    this.save('input-hashes.json', this.sourceHashes);
  }

  save(name, data) {
    const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2) + '\n';
    fs.writeFileSync(path.join(this.out, name), text.replaceAll(this.layout.fixtureKey, '<fixture-key>'));
  }

  command(args) {
    const copied = ['--config', this.configDirectory, ...args];
    const result = this.docker.run(copied);
    this.receipts.push({ args: [...copied], ...result });
    this.save('docker-receipts.json', this.receipts);
    return result;
  }

  requireCommand(args, description) {
    const result = this.command(args);
    if (result.status !== 0 || result.signal || result.errorCode) throw new Error(`${description} did not complete successfully; see Docker receipts`);
    return result.out;
  }

  inspect(kind, name) {
    const parsed = JSON.parse(this.requireCommand([kind, 'inspect', name], `inspect ${kind}`));
    if (!Array.isArray(parsed) || parsed.length !== 1) throw new Error(`staging ${kind} metadata is not one exact record`);
    return parsed[0];
  }

  build() {
    const root = path.resolve(fileURLToPath(new URL('../../', import.meta.url)));
    const repositories = { comms: root,
      env: this.options['env-repo'] ?? path.join(path.dirname(root), 'aify-env'),
      wrapper: this.options['wrapper-repo'] ?? path.join(path.dirname(root), 'aify-wrapper') };
    return new StagingBuild({ manifest: this.manifest, layout: this.layout, repositories,
      directory: this.out, docker: args => this.command(args), resources: this.resources, environment: process.env }).run();
  }

  composeArgs(phase, ...args) {
    return ['compose', '--project-name', this.layout.project, '--project-directory', this.out,
      '--env-file', this.emptyEnvironmentFile, '--file', path.join(this.out, `${phase}-compose.json`), ...args];
  }

  writeCompose(phase) {
    const intent = this.layout.compose(phase);
    this.layout.assertIsolation({ config: intent, environment: this.environment });
    // This operational file contains only this run's synthetic API key. Receipts redact it.
    fs.writeFileSync(path.join(this.out, `${phase}-compose.json`), JSON.stringify(intent, null, 2) + '\n');
    return JSON.parse(this.requireCommand(this.composeArgs(phase, 'config', '--format', 'json'), 'Compose rendering'));
  }

  check(id, ok, detail) {
    this.checks.push({ id, ok: ok === true, detail: structuredClone(detail) });
    this.save('checks.json', this.checks);
    console.log(`${ok === true ? 'PASS' : 'FAIL'} ${id}`);
    if (ok !== true) throw new Error(`staging check failed: ${id}`);
  }

  finish(cleanup) {
    const unchanged = Object.entries(this.sourceHashes).every(([file, before]) =>
      createHash('sha256').update(fs.readFileSync(file)).digest('hex') === before);
    this.save('result.json', { manifest: this.manifest, project: this.layout.project,
      checks: this.checks, cleanup, inputsUnchanged: unchanged, settlement: this.docker.settlement(),
      environmentNames: Object.keys(this.docker.environment), qualification: 'Linux synthetic fleet only' });
    return unchanged;
  }
}
