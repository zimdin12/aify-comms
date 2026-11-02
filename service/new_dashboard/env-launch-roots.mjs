// Where a host's aify-env will actually start work, drawn beside the roots the service lists for it.
//
// "Edit roots…" edits the SERVICE's list (cwdRoots). aify-env launches only under its own launch roots, which it
// reports as `metadata.launchRoots` and which the service can never widen; on 2026-11-02 the card listed D:/ while
// every agent under D: read misconfigured. The service decides which of its roots fall outside
// (`rootsOutsideLaunch`, by the same rule as the misconfigured note), and this module only draws that verdict.

import { esc } from './util.js';

const outside = (env) => new Set(Array.isArray(env?.rootsOutsideLaunch) ? env.rootsOutsideLaunch : []);

/** One service root, marked when this host's aify-env will not start work in it. */
export function rootChip(env, root) {
  if (!outside(env).has(root)) return `<code>${esc(root)}</code>`;
  return `<code class="mb-warn root-outside-launch" title="${esc(`aify-env on this host does not start work here. On that host, run: aify-env roots add ${root}`)}">${esc(root)} ⚠</code>`;
}

/** The host's own launch roots, or nothing when its aify-env reported none (no report is no evidence). */
export function launchRootsNote(env) {
  const roots = Array.isArray(env?.metadata?.launchRoots) ? env.metadata.launchRoots.filter(Boolean) : [];
  if (!roots.length) return '';
  const count = outside(env).size;
  const warn = count ? ` · <span class="mb mb-warn">${count} listed root${count === 1 ? '' : 's'} outside them</span>` : '';
  return `<p class="subtle env-launch-roots">aify-env starts work under ${roots.map((r) => `<code>${esc(r)}</code>`).join(' ')}${warn}</p>`;
}
