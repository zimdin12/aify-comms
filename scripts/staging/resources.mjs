import { classifyReadBack, inspectArgs } from '../sandbox/docker-readback.mjs';
import { StagingLayout } from './layout.mjs';

/** Intents are recorded BEFORE creation; cleanup never interprets an unknown answer as permission. */
export class StagingResources {
  #intents = [];

  constructor({ layout, docker }) {
    if (!(layout instanceof StagingLayout) || typeof docker !== 'function') throw new TypeError('staging layout and Docker runner are required');
    this.layout = layout;
    this.docker = docker;
    Object.freeze(this);
  }

  plan({ kind, name }) {
    inspectArgs({ kind, name });
    if (!name.startsWith(`${this.layout.project}-`)) throw new Error('resource name is outside this staging run');
    if (this.#intents.some((item) => item.kind === kind && item.name === name)) throw new Error('resource intent is already recorded');
    const initial = classifyReadBack({ kind, name }, this.docker(inspectArgs({ kind, name })));
    if (initial !== 'absent') throw new Error(initial === 'present'
      ? 'staging resource already exists; it cannot be adopted' : 'staging resource absence could not be confirmed');
    const intent = Object.freeze({ kind, name, plannedAfter: initial });
    this.#intents.push(intent);
    return intent;
  }

  snapshot() {
    return [...this.#intents];
  }

  cleanup() {
    const receipts = [];
    for (const target of [...this.#intents].reverse()) {
      try {
        const before = this.docker(inspectArgs(target));
        const state = classifyReadBack(target, before);
        if (state === 'absent') { receipts.push({ ...target, before: state, after: state }); continue; }
        if (state !== 'present') { receipts.push({ ...target, before: state, after: 'unknown' }); continue; }
        const inspected = JSON.parse(before.out);
        const record = Array.isArray(inspected) ? inspected[0] : inspected;
        const labels = record?.Labels ?? record?.Config?.Labels;
        if (labels?.['aify.staging.run'] !== this.layout.runId) {
          receipts.push({ ...target, before: 'foreign', after: 'present' });
          continue;
        }
        const immutable = record.Id ?? record.ID;
        if (target.kind !== 'volume' && (typeof immutable !== 'string' || !immutable)) {
          receipts.push({ ...target, before: 'unidentified', after: 'unknown' });
          continue;
        }
        // Containers/networks/images are removed by observed immutable ID, not a reusable name.
        // Docker volumes expose a name only. Its run label is checked above; no broad prune exists.
        const handle = target.kind === 'volume' ? target.name : immutable;
        const args = target.kind === 'container' ? ['rm', '-f', '-v', handle] : [target.kind, 'rm', handle];
        const removal = this.docker(args);
        const after = classifyReadBack(target, this.docker(inspectArgs(target)));
        receipts.push({ ...target, before: state, removeStatus: removal.status, after });
      } catch {
        // Docker inspect JSON can carry credentials. Never echo it or an exception containing it.
        receipts.push({ ...target, before: 'unknown', after: 'unknown' });
      }
    }
    return { ok: receipts.every((item) => item.after === 'absent'), receipts };
  }
}
