import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

export function writeCredential(home, name, bytes, { shared = false } = {}) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name)) throw new Error('fixture name must be a basename');
  const dir = path.join(home, '.aify', 'credentials');
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, name);
  fs.writeFileSync(target, bytes, { mode: 0o600 });
  if (process.platform === 'win32') {
    const owner = process.env.USERDOMAIN ? `${process.env.USERDOMAIN}\\${process.env.USERNAME}` : process.env.USERNAME;
    execFileSync('icacls', [target, '/inheritance:r', '/grant:r', `${owner}:F`], { stdio: 'ignore' });
    if (shared) execFileSync('icacls', [target, '/grant', '*S-1-1-0:R'], { stdio: 'ignore' });
  } else fs.chmodSync(target, shared ? 0o644 : 0o600);
  return target;
}

// Registry and .env discovery may be injected. Credential custody always uses an owned real home.
export function withCredentialHome(source, fixtureHome, run, options) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'comms-owned-credential-'));
  const prefix = `${fixtureHome}/.aify/credentials/`;
  try {
    for (const [file, bytes] of Object.entries(source)) if (file.startsWith(prefix)) {
      const ref = file.slice(prefix.length);
      if (/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(ref)) writeCredential(home, ref, bytes, options);
    }
    return run(home);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
}
