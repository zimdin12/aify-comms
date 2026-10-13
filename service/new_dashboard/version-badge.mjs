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

export async function loadVersionBadge() {
  const badge = byId('version-badge');
  if (!badge) return;
  try {
    const res = await fetch(`${apiOrigin}/version`);
    if (!res.ok) throw new Error(String(res.status));
    const v = await res.json();
    const behind = Number(v?.update?.behind_by || 0);
    const short = esc(v.sha_short || v.sha || '?');
    const branch = esc(v.branch || '');
    badge.textContent = behind > 0 ? `${short} · ${behind} behind` : short;
    badge.classList.toggle('behind', behind > 0);
    badge.title = behind > 0
      ? `Running ${short} (${branch}) — ${behind} commit${behind === 1 ? '' : 's'} behind origin. git pull && rebuild to update.`
      : `Running ${short} (${branch}) — up to date with origin.`;
  } catch (_) {
    badge.textContent = '';
    badge.title = 'Build version unavailable';
  }
}
