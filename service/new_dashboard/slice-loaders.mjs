// Fetching ONE part of the dashboard's data, for a refresh that knows what changed.
//
// The full cycle (refresh-cycle.mjs) fetches all of these at once and stays the recovery path: on
// connect, on a missed change, when a hidden tab is shown again, and while the socket is down. This
// module is what runs in between, when the service has said which tables a commit touched
// (`data_changed`, service/change_feed.py) and only the parts that read them need fetching.
//
// Each loader fetches and applies its slice, and THROWS when the fetch failed, leaving the slice's
// last-good value in place -- the same rule the full cycle keeps, so a failed partial refresh never
// blanks a panel. The caller decides what a failure costs (change-refresh.mjs retries it).
//
// Which slices read which tables is slice-tables.mjs.

import { api } from './api-client.mjs';
import { asAgentArray, asArray } from './record-fields.mjs';
import { chatLoadChannels, chatLoadConversation } from './message-transport.mjs';
import { loadFiles } from './shared-files.mjs';
import { shouldLoadFiles, shouldLoadForPage } from './files-page.mjs';
import { loadContractsForState } from './work-loop-actions.mjs';
import { SETTINGS_SCHEMA, adoptSettingsSchema, refreshActiveTerminalTheme } from './settings-panel.mjs';
import { runQueryPath } from './run-helpers.mjs';
import { applyTheme } from './theme.js';
import { byId } from './ui.js';
import { state } from './state.mjs';
import { RECENT_PAGE_LIMIT } from './message-history.mjs';

/** A slice nobody on the current page reads is not fetched, exactly as the full cycle decides. */
export function sliceIsWanted(slice) {
  if (slice === 'spawnRequests') return shouldLoadForPage('environments');
  if (slice === 'files') return shouldLoadFiles();
  if (slice === 'conversation') return String(state.chat.selected || '').startsWith('channel:');
  return true;
}

export const SLICE_LOADERS = Object.freeze({
  async agents() {
    state.agents = asAgentArray(await api('/agents'));
    state.loaded = true;
  },
  async contracts() {
    const res = await api('/contracts?limit=80');
    state.contracts = res.contracts || [];
    state.contractsBase = state.contracts;
    // A non-default Work-loop State filter stays applied, as it does across a full cycle.
    const selected = byId('contract-state')?.value || '';
    if (selected && selected !== 'open') await loadContractsForState(selected, false);
  },
  async messages() {
    const res = await api(`/messages/recent?limit=${RECENT_PAGE_LIMIT}`);
    if (!res || !res.messages) throw new Error('recent messages returned no list');
    state.messages = res.messages;
    state.messageCounts = {
      showing: Number(res.showing ?? state.messages.length) || state.messages.length,
      truncated: Boolean(res.truncated) || Number(res.total ?? 0) > state.messages.length,
    };
  },
  async runs() {
    const res = await api(runQueryPath());
    state.runs = res.runs || [];
    state.runsTruncated = Boolean(res?.truncated);
  },
  async sessions() {
    const res = await api('/sessions?limit=80');
    state.sessions = asArray(res, 'sessions');
    state.sessionsTruncated = Boolean(res?.truncated);
    state.sessions.forEach((session) => {
      const terminalId = session.terminalId || session.terminal?.id;
      if (terminalId && session.agentId) state.terminalOwners.set(String(terminalId), String(session.agentId));
    });
  },
  async environments() {
    state.environments = asArray(await api('/environments'), 'environments');
  },
  async spawnRequests() {
    const res = await api('/spawn-requests?limit=200');
    state.spawnRequests = asArray(res, 'spawnRequests');
    state.spawnRequestsTruncated = Boolean(res?.truncated);
  },
  async stats() {
    state.stats = (await api('/stats')) || {};
  },
  async settings() {
    // The panel is drawn from the service's declarations; they change only with a deploy.
    if (!SETTINGS_SCHEMA.length) adoptSettingsSchema(await api('/settings/schema'));
    const res = await api('/settings');
    if (!res || typeof res !== 'object') throw new Error('settings returned no object');
    state.settings = res;
    applyTheme(state.settings);
    refreshActiveTerminalTheme();
  },
  async channels() {
    await chatLoadChannels();
  },
  async conversation() {
    const selected = String(state.chat.selected || '');
    if (selected.startsWith('channel:')) await chatLoadConversation(selected.slice('channel:'.length));
  },
  async files() {
    await loadFiles();
  },
});

/**
 * Load `slices` and repaint once. Resolves the slices whose fetch FAILED, so the caller can retry
 * them; a slice the current page does not read is skipped, as the full cycle skips it.
 */
export async function loadSlices(slices, { evaluateFlowGates, renderAll, refreshOpenInspector }) {
  const wanted = slices.filter((slice) => SLICE_LOADERS[slice] && sliceIsWanted(slice));
  const results = await Promise.allSettled(wanted.map((slice) => SLICE_LOADERS[slice]()));
  const failed = wanted.filter((_, index) => results[index].status === 'rejected');
  if (wanted.length > failed.length) {
    evaluateFlowGates();
    renderAll();
    refreshOpenInspector();
  }
  return failed;
}
