// Static links and the Help card's install snippet, moved out of app.js in v0.5.4.
//
// Both write the ORIGIN the operator actually opened the dashboard on into the page. The install
// snippet used to hard-code one machine's LAN IP, which was wrong for every other reader — so the value
// being live rather than baked in is the property that matters, and `apiOrigin` is imported from
// api-client.mjs where it already has an owner.

import { apiOrigin } from './api-client.mjs';
import { byId } from './ui.js';

export function renderInstallSnippet() {
  const el = document.getElementById('help-install-cmd');
  if (el) el.textContent = `bash install.sh --client claude \
  ${apiOrigin} --with-hook`;
}

export function updateStaticLinks() {
  const legacy = byId('legacy-dashboard-link');
  if (legacy) legacy.href = `${apiOrigin}/api/v1/dashboard`;
}
