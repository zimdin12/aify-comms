// Where a host's aify-env will actually start work, drawn beside the roots the service lists for it.
//
// "Edit roots…" edits the SERVICE's list (cwdRoots). aify-env launches only under its own launch roots, which it
// reports as `metadata.launchRoots` and which the service can never widen; on 2026-11-02 the card listed D:/ while
// every agent under D: read misconfigured. The service decides which of its roots fall outside, and with which command
// (`rootsOutsideLaunch: [{root, add}]`, by the same rule and quoting as the misconfigured note); this module only draws
// that verdict, so neither the containment rule nor the shell quoting has a copy here.

import { esc } from './util.js';

const outside = (env) => new Map((Array.isArray(env?.rootsOutsideLaunch) ? env.rootsOutsideLaunch : [])
  .filter((entry) => entry && typeof entry.root === 'string')
  .map((entry) => [entry.root, String(entry.add || '')]));

/** One service root, marked when this host's aify-env will not start work in it. */
export function rootChip(env, root) {
  const marked = outside(env);
  if (!marked.has(root)) return `<code>${esc(root)}</code>`;
  const add = marked.get(root);
  const why = add
    ? `aify-env on this host does not start work here. On that host, run: ${add}`
    : 'aify-env on this host does not start work here, and cannot take this folder as a launch root (a UNC, device, relative or dot-segment path).';
  return `<code class="mb-warn root-outside-launch" title="${esc(why)}">${esc(root)} ⚠</code>`;
}

/** The host's own launch roots, or nothing when its aify-env reported none (no report is no evidence). */
export function launchRootsNote(env) {
  const roots = Array.isArray(env?.metadata?.launchRoots) ? env.metadata.launchRoots.filter(Boolean) : [];
  if (!roots.length) return '';
  const count = outside(env).size;
  const warn = count ? ` · <span class="mb mb-warn">${count} listed root${count === 1 ? '' : 's'} outside them</span>` : '';
  return `<p class="subtle env-launch-roots">aify-env starts work under ${roots.map((r) => `<code>${esc(r)}</code>`).join(' ')}${warn}</p>`;
}
