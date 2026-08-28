// The build-version badge in the header: which commit this dashboard is serving, and whether origin has
// moved on.
//
// Extracted from app.js in v0.5.4. It fetches `/version` from `apiOrigin` — the service ROOT — not through
// `api()`, because that endpoint is not under the `/api/v1` prefix. The origin arrives as a live binding
// from api-client.mjs for the same reason `apiBase` does: computing it here would read `location` at load
// and make the module unimportable in Node.

import { apiOrigin } from './api-client.mjs';
import { byId } from './ui.js';
import { esc } from './util.js';

// WHAT THE SERVICE IS BUILT FROM, remembered by the module that already fetches it.
//
// It lives here rather than in `state` for two reasons. This module is the only thing that reads
// `/version`, so it is state with an owner rather than state at large -- and `state` is
// reconstructed byte-for-byte by extraction-proof.test.mjs, so a field added there is a change
// outside a declared span.
//
// EMPTY UNTIL A FETCH SUCCEEDS, and empty is never compared against. A reader that treated the
// absence of a build as a mismatch would warn on every dashboard load before the first poll.
let serviceBuild = '';

/** The service's short build sha, or '' if `/version` has not answered yet. */
export function serviceBuildShort() {
  return serviceBuild;
}

export async function loadVersionBadge() {
  const badge = byId('version-badge');
  if (!badge) return;
  try {
    const res = await fetch(`${apiOrigin}/version`);
    if (!res.ok) throw new Error(String(res.status));
    const v = await res.json();
    const behind = Number(v?.update?.behind_by || 0);
    const short = esc(v.sha_short || v.sha || '?');
    // Recorded before the badge is painted, so a reader that runs in the same tick sees it.
    serviceBuild = String(v.sha_short || v.sha || '').trim();
    const branch = esc(v.branch || '');
    badge.textContent = behind > 0 ? `${short} · ${behind} behind` : short;
    badge.classList.toggle('behind', behind > 0);
    badge.title = behind > 0
      ? `Running ${short} (${branch}) — ${behind} commit${behind === 1 ? '' : 's'} behind origin. git pull && rebuild to update.`
      : `Running ${short} (${branch}) — up to date with origin.`;
  } catch (_) {
    badge.textContent = '';
    badge.title = 'Build version unavailable';
    // FORGOTTEN TOO. The badge blanks itself here rather than leaving the last good value on screen,
    // for the reason this file opens with: the failure that matters is showing something REASSURING
    // when it knows nothing. The remembered build is read by `staleBridgeBadge` to decide whether a
    // bridge is on a different build, so keeping a stale one would have it compare against a service
    // sha that may no longer be what is running. Empty is never compared against.
    serviceBuild = '';
  }
}
