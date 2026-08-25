// Click-handler bodies for navigation and the analytics range.
//
// All three lived inside app.js's delegated click handler and were unreachable by any test. Each is a
// few lines, and each pairs a navigation with a side effect that has to happen in the same click —
// which is the part worth pinning, because half of one of these looks exactly like a working button.
//
// `setPage`, `loadAnalytics` and `renderEnvironmentSpawnOptions` are INJECTED: they stay in app.js.
// Parameters of the same names leave every body byte-identical to the branch it left.

import { loadFiles, renderFiles } from './shared-files.mjs';
import { loadSpawnRequests } from './environments-panels.mjs';
import { rangeDef } from './analytics.js';
import { state } from './state.mjs';
import { byId } from './ui.js';

export function selectAnalyticsRange(analyticsRange, loadAnalytics) {
  state.analytics.range = rangeDef(analyticsRange.dataset.analyticsRange).key;
  loadAnalytics(true);
}

export function navigateToPage(page, setPage, loadAnalytics) {
  setPage(page);
  // Lazy-load the analytics page the first time it's opened (and refresh on re-open).
  if (page === 'analytics') loadAnalytics(true);
  // Files is polled only while its page is open (see files-page.mjs), so opening it must fetch once.
  // Without this the page would show whatever was last seen -- or nothing at all on a first open --
  // for up to a full refresh interval, 15 seconds by default.
  if (page === 'files') loadFiles().then(renderFiles);
  // Same rule, larger slice: /spawn-requests is polled only while the Environments page is open
  // (414,690 of a 1,419,728 byte cycle), so opening it must fetch once or the table shows its
  // previous contents -- or nothing at all on a first open -- for up to a full refresh interval.
  if (page === 'environments') loadSpawnRequests();
}

export function openEnvironmentSpawn(envSpawn, setPage, renderEnvironmentSpawnOptions) {
  setPage('environments');
  renderEnvironmentSpawnOptions(envSpawn.dataset.envSpawn);
  byId('env-spawn-agent-id')?.focus();
}

// The Hermes tab opener. `noopener,noreferrer` is the part worth keeping honest: without it the opened
// page gets a handle on this one through `window.opener`.
export function openHermesTabFromRow(openHermesTab) {
  const url = openHermesTab.dataset.url;
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}
