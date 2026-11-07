// Source and runtime inputs for one staging run. No environment lookup, Git call or Docker action.
const SHA = /^[0-9a-f]{40}$/;
const VERSION = /^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/;
const IMAGE = /^(?:[^\s]+@sha256:[0-9a-f]{64}|[^\s]+:\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)$/;

function sourceTuple(value, phase) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${phase} tuple is required`);
  const result = { comms: value.comms, env: value.env, wrapper: value.wrapper };
  for (const [repo, ref] of Object.entries(result)) {
    if (typeof ref !== 'string' || !SHA.test(ref)) throw new TypeError(`${phase}.${repo} requires a full lowercase commit SHA`);
  }
  return Object.freeze(result);
}

function runtimeVersions(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('runtimeVersions is required');
  const result = { nodeImage: value.nodeImage, codex: value.codex, claude: value.claude };
  for (const [field, version] of Object.entries(result)) {
    const pattern = field === 'nodeImage' ? IMAGE : VERSION;
    if (typeof version !== 'string' || !pattern.test(version)) throw new TypeError(`runtimeVersions.${field} requires an explicit version`);
  }
  return Object.freeze(result);
}

/** Frozen syntactic inputs, NOT proof that a ref exists or a runtime is qualified on a platform. */
export class StagingManifest {
  constructor(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('staging manifest is required');
    this.baseline = sourceTuple(value.baseline, 'baseline');
    this.candidate = sourceTuple(value.candidate, 'candidate');
    this.runtimeVersions = runtimeVersions(value.runtimeVersions);
    if (value.seed !== 'synthetic-linux-fleet') throw new TypeError('first E4 requires a synthetic-linux-fleet seed');
    if (value.liveCopyEnabled !== false) throw new TypeError('liveCopyEnabled must be explicitly false');
    this.seed = value.seed;
    this.liveCopyEnabled = value.liveCopyEnabled;
    Object.freeze(this);
  }
}
