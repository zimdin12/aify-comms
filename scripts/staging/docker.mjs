import { spawnSync } from 'node:child_process';

// CLI process plumbing only. None of the launching agent's identity, lease or credentials is inherited.
const operatingSystemFields = new Set(['PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE']);

export function dockerEnvironment(source, dataVolume) {
  const environment = Object.fromEntries(Object.entries(source)
    .filter(([name]) => operatingSystemFields.has(name.toUpperCase())));
  if (typeof dataVolume !== 'string' || !dataVolume.startsWith('aify-comms-staging-')) {
    throw new Error('an explicit staging DATA_VOLUME_NAME is required');
  }
  return { ...environment, MSYS_NO_PATHCONV: '1', DATA_VOLUME_NAME: dataVolume };
}

/** Bounded direct Docker commands. A timeout is not evidence that daemon-side work finished. */
export class StagingDocker {
  #unsettled = false;

  constructor({ environment, dataVolume, timeoutMs, execute = spawnSync }) {
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 600_000) throw new TypeError('a bounded Docker timeout is required');
    if (typeof execute !== 'function') throw new TypeError('Docker process executor is required');
    this.environment = Object.freeze(dockerEnvironment(environment, dataVolume));
    this.timeoutMs = timeoutMs;
    this.execute = execute;
    Object.freeze(this);
  }

  run(args) {
    if (!Array.isArray(args) || !args.length || args.some((arg) => typeof arg !== 'string')) {
      throw new TypeError('Docker argv must be a nonempty string array');
    }
    let answer;
    try {
      answer = this.execute('docker', [...args], {
        encoding: 'utf8', env: { ...this.environment }, timeout: this.timeoutMs, maxBuffer: 4 << 20,
      });
    } catch {
      this.#unsettled = true;
      throw new Error('Docker process execution failed; completion is unconfirmed');
    }
    if (!Number.isInteger(answer?.status) || answer?.error) this.#unsettled = true;
    return { status: Number.isInteger(answer?.status) ? answer.status : null,
      signal: answer?.signal ?? null, errorCode: answer?.error?.code ?? null,
      out: `${answer?.stdout ?? ''}${answer?.stderr ?? ''}` };
  }

  settlement() {
    return { settled: !this.#unsettled };
  }
}
