// The command an operator PASTES to start a bridge for an environment.
//
// It is the answer to `aify-comms doctor`'s "no environment bridge is ONLINE — start one on the host", and
// the dashboard shows it per environment because the right command depends on which host that environment
// is: a Windows box wants `cd /d`, a mac wants `cd "$HOME"`, and WSL wants the /mnt path. Getting it wrong
// does not fail loudly — the operator pastes something that lands in the wrong directory and the bridge
// registers roots nobody asked for.
//
// QUOTING IS THE PART THAT BITES. Workspace roots come from operator configuration and routinely contain
// spaces; an unquoted `cd C:/Program Files/x` silently becomes two arguments. Anything containing
// whitespace, a quote or a backtick is JSON-quoted, which is both shell-safe here and stable to read.
//
// Pure: an environment record in, a string out. It reads that record through `environmentRoots`, the shared
// field reader, so an environment spelled `cwd_roots` produces the same command as one spelled `cwdRoots`.

import { environmentRoots } from './record-fields.mjs';

export function environmentStartCommand(env) {
  const roots = environmentRoots(env).filter(Boolean);
  const firstRoot = roots[0] || '';
  const extras = roots.slice(1);
  const os = String(env.os || env.kind || '').toLowerCase();
  const quote = (v) => /[\s"'`]/.test(v) ? JSON.stringify(v) : v;
  if (os.includes('win')) {
    const cd = firstRoot ? `cd /d ${quote(firstRoot)}` : 'cd /d C:\\Docker';
    const args = extras.map(quote).join(' ');
    return `${cd}\naify-comms${args ? ' ' + args : ''}`;
  }
  const cd = firstRoot ? `cd ${quote(firstRoot)}` : (os.includes('mac') || os.includes('darwin') ? 'cd "$HOME"' : 'cd /mnt/c/Docker');
  const args = extras.map(quote).join(' ');
  return `${cd}\naify-comms${args ? ' ' + args : ''}`;
}
