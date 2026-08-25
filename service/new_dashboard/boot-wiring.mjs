// BOOT WIRING: every control on the page that is NOT reached through the delegated click dispatcher.
//
// Keyboard, form submits, `change` and `input` handlers, and the elements whose listeners must be
// bound once at load rather than re-bound on every render. They are here as one function because they
// are one PHASE — everything in it runs at boot, in this order, and nothing in it is called again.
//
// Two things are load-bearing and easy to lose. The notification toggle asks for browser permission
// INSIDE its click handler, because that click IS the user gesture the prompt requires — asking at
// load gets the site denied permanently. And the `toggle` listener for the session env-groups is
// registered in the CAPTURE phase, because `toggle` does not bubble; moving it to the bubble phase
// makes collapse state stop persisting, silently.
//
// The bodies are byte-identical to those that stood in app.js; the only change is two spaces of
// indentation, which the reconstruction proof strips before comparing. The run contains no multi-line
// template literal, so that re-indentation cannot alter a string.

import { toggleFavorite } from './message-actions.mjs';
import { api } from './api-client.mjs';
import { applyCachedTheme, applyTheme } from './theme.js';
import { attachChatFile, uploadPastedImage, uploadSharedFile } from './shared-files.mjs';

import { codexConsoleSendTurn } from './codex-console.mjs';
import { createSpawnRequest, renderEnvironmentSpawnOptions } from './environments-panels.mjs';
import { disposeActiveXterm } from './xterm-lifecycle.mjs';
import { handleGlobalKeydown } from './keyboard-shortcuts.mjs';
import { loadContractsForState, renderContracts } from './work-loop-actions.mjs';
import { loadRunsForStatus, renderRuns } from './run-inspector.mjs';
import { notificationsEnabled, toggleNotifications } from './notifications.mjs';
import { loadVersionBadge } from './version-badge.mjs';
import { openIdentityDirectory } from './identity-directory.mjs';
import { updateStaticLinks } from './static-links.mjs';
import { persistChatDrafts, persistChatPrefs, syncChatChips, toggleChatCompact, toggleChatPeek } from './chat-prefs.mjs';
import { previewAppearance, refreshActiveTerminalTheme, renderSettings } from './settings-panel.mjs';
import { preferredAttentionCollapsed, preferredNavCollapsed, setAttentionCollapsed, setNavCollapsed, toggleSessionGroupCollapsed } from './layout-prefs.mjs';
import { state } from './state.mjs';
import { byId, installRejectionToast, toast, uiConfirm } from './ui.js';

/**
 * Bind every boot-time listener. Called once from app.js, where the rest of the boot sequence lives.
 */
export function wireGlobalControls({
  chatController,
  closeInspector,
  refresh,
  renderAll,
  renderSessionWorkspace,
  saveSettings,
  chatCreateChannel,
  inspect,
}) {
  document.addEventListener('keydown', (event) => {
    handleGlobalKeydown(event, closeInspector, toggleFavorite);
  });

  byId('refresh').addEventListener('click', refresh);

  // Notification toggle. The click IS the user gesture the Notification permission prompt requires —
  // asking on load gets a site denied permanently, so this is the only place permission is requested.
  (() => {
    const btn = byId('notify-toggle');
    if (!btn) return;
    const paint = () => {
      btn.textContent = notificationsEnabled ? '🔔 Notify' : '🔕 Notify';
      btn.setAttribute('aria-pressed', notificationsEnabled ? 'true' : 'false');
      btn.classList.toggle('active', notificationsEnabled);
    };
    paint();
    btn.addEventListener('click', async () => {
      const on = await toggleNotifications(!notificationsEnabled);
      paint();
      if (on) toast('Desktop notifications on — messages addressed to you, when this tab is not focused', 'ok');
      else if (notificationsEnabled === false) toast('Desktop notifications off');
    });
  })();
  byId('global-filter').addEventListener('input', (event) => {
    state.filter = event.target.value;
    renderAll();
    renderSessionWorkspace(); // WS-H6: Find also narrows the Sessions rail
  });
  // Persist session env-group collapse (WS-J). `toggle` doesn't bubble → capture phase.
  document.addEventListener('toggle', (event) => {
    const grp = event.target.closest?.('[data-env-group]');
    if (grp) toggleSessionGroupCollapsed(grp.dataset.envGroup, !grp.open);
  }, true);
  byId('contract-state')?.addEventListener('change', (event) => loadContractsForState(event.target.value));
  byId('contract-category')?.addEventListener('change', renderContracts);
  byId('run-status-filter')?.addEventListener('change', async (event) => {
    byId('api-status').textContent = 'filtering';
    byId('api-status').className = 'status-chip muted';
    try {
      await loadRunsForStatus(event.target.value);
      byId('api-status').textContent = 'live';
      byId('api-status').className = 'status-chip ok';
    } catch (error) {
      byId('api-status').textContent = 'live';
      byId('api-status').className = 'status-chip ok';
      toast(`Run filter failed: ${error?.message || error}`, 'error');
    }
  });
  byId('run-from-filter')?.addEventListener('change', (e) => { state.runFromFilter = e.target.value; renderRuns(); });
  byId('run-to-filter')?.addEventListener('change', (e) => { state.runToFilter = e.target.value; renderRuns(); });
  byId('run-runtime-filter')?.addEventListener('change', (e) => { state.runRuntimeFilter = e.target.value; renderRuns(); });
  byId('run-search')?.addEventListener('input', (e) => { state.runSearch = e.target.value; renderRuns(); });
  byId('env-spawn-environment')?.addEventListener('change', (event) => {
    byId('env-spawn-workspace').value = '';
    renderEnvironmentSpawnOptions(event.target.value);
  });
  byId('environment-spawn-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await createSpawnRequest();
    } catch (error) {
      toast(`Spawn request failed: ${error?.message || error}`, 'error');
    }
  });
  byId('send-reminders')?.addEventListener('click', async () => {
    if (!await uiConfirm('Send due reminders now? This pings agents with overdue work.', { confirmLabel: 'Send reminders' })) return;
    try {
      const result = await api('/contracts/reminders/run', { method: 'POST' });
      const n = Array.isArray(result?.reminded) ? result.reminded.length : (result?.sent ?? result?.count);
      toast(`Reminders: ${n != null ? `${n} sent` : 'done'}`, 'ok');
      await refresh();
    } catch (error) {
      toast(`Send reminders failed: ${error?.message || error}`, 'error');
    }
  });
  // Codex live-console input form: send turn/start via the existing WS
  // the operator opened with "Connect live console".
  document.addEventListener('submit', (event) => {
    const codexForm = event.target.closest('[data-action="codex-console-send"]');
    if (!codexForm) return;
    event.preventDefault();
    const agentId = codexForm.dataset.agentId;
    const input = codexForm.querySelector('input[type="text"]');
    const text = input?.value || '';
    codexConsoleSendTurn(agentId, text);
    if (input) input.value = '';
  });

  // Chat-first landing wiring (Phase 1).
  byId('chat-filter')?.addEventListener('input', (event) => {
    state.chat.filter = event.target.value;
    chatController.renderRail();
  });
  byId('chat-msg-search')?.addEventListener('input', (event) => {
    state.chat.msgFilter = event.target.value;
    chatController.renderConversation();
  });
  // Collapsible chat sort/filters/channels panel (kept out of the way until needed).
  // Chat tool tabs (2026-06-29): Find / Filters / Channels — replaces the ⚙ junk-drawer.
  document.querySelectorAll('[data-chat-tool]').forEach((tab) => {
    tab.addEventListener('click', () => {
      const which = tab.dataset.chatTool;
      document.querySelectorAll('[data-chat-tool]').forEach((t) => {
        const on = t.dataset.chatTool === which;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      document.querySelectorAll('[data-chat-tool-panel]').forEach((p) => {
        p.hidden = p.dataset.chatToolPanel !== which;
      });
    });
  });
  // Clear all rail filters (scope→all, toggles off, status set empty, sort→activity).
  byId('chat-clear-filters')?.addEventListener('click', () => {
    state.chat.scope = 'all';
    state.chat.unreadOnly = false; state.chat.liveOnly = false; state.chat.openOnly = false; state.chat.workingUp = false;
    state.chat.statusFilter = new Set();
    state.chat.sortMode = 'activity';
    const sortSel = byId('chat-sort'); if (sortSel) sortSel.value = 'activity';
    persistChatPrefs(); syncChatChips(); chatController.renderRail();
  });
  byId('chat-identity')?.addEventListener('change', (event) => {
    state.chat.identity = event.target.value || 'dashboard';
    if (state.chat.identity === 'all' && state.chat.view === 'console') {
      state.chat.view = 'messenger';
      disposeActiveXterm();
    }
    chatController.render();
  });
  byId('chat-identity-directory')?.addEventListener('click', () => openIdentityDirectory());
  // Persist the rail filter prefs so "live only" (which hides offline/archived agents) and the
  // other declutter toggles STICK across reloads — the old dashboard remembered these; not
  // persisting them is why the rail re-cluttered with offline conversations on every refresh.
  // persistChatPrefs moved to ./chat-prefs.mjs in v0.5.4.
  // Reflect filter state into the always-visible chip bar (chips are static markup; only their
  // active class tracks state, so the rail re-render never has to rebuild them).
  // syncChatChips moved to ./chat-prefs.mjs in v0.5.4.
  byId('chat-sort')?.addEventListener('change', (event) => {
    state.chat.sortMode = event.target.value || 'activity';
    persistChatPrefs();
    chatController.renderRail();
  });
  // Delegated handler for the filter-bar chips (scope / quick toggles / status filter).
  byId('page-chat')?.addEventListener('click', (event) => {
    const scopeBtn = event.target.closest('[data-chat-scope]');
    if (scopeBtn) {
      state.chat.scope = scopeBtn.dataset.chatScope || 'all';
      persistChatPrefs(); syncChatChips(); chatController.renderRail();
      return;
    }
    const toggleBtn = event.target.closest('[data-chat-toggle]');
    if (toggleBtn) {
      const key = toggleBtn.dataset.chatToggle;
      state.chat[key] = !state.chat[key];
      persistChatPrefs(); syncChatChips(); chatController.renderRail();
      return;
    }
    const compactBtn = event.target.closest('[data-chat-compact-toggle]');
    if (compactBtn) {
      toggleChatCompact();
      return;
    }
    const peekBtn = event.target.closest('[data-chat-peek-toggle]');
    if (peekBtn) {
      toggleChatPeek();
      return;
    }
    const statusBtn = event.target.closest('[data-chat-status]');
    if (statusBtn) {
      const kind = statusBtn.dataset.chatStatus;
      if (!(state.chat.statusFilter instanceof Set)) state.chat.statusFilter = new Set();
      if (state.chat.statusFilter.has(kind)) state.chat.statusFilter.delete(kind);
      else state.chat.statusFilter.add(kind);
      persistChatPrefs(); syncChatChips(); chatController.renderRail();
      return;
    }
  });
  byId('chat-composer')?.addEventListener('submit', (event) => {
    event.preventDefault();
    chatController.send(); // Enter / Send = ordinary send (steer if the target supports it). Never queues.
  });
  // The Queue half of the split Send button: same send, queueIfBusy forced on for THIS message only.
  byId('chat-send-queue')?.addEventListener('click', (event) => {
    event.preventDefault();
    chatController.send({ queue: true });
  });
  // Clicking composer chrome must not strand the operator unable to type (reported 2026-07-27:
  // "if i press click outside of the chat textarea (to element with class=composer-advanced) then my
  // cursor appears in front of textinput area and i cannot write").
  //
  // The Options panel is a <details>; its <summary> is focusable and the surrounding <div>s are not,
  // so a click on either BLURS the textarea — the browser either moves focus to the summary or drops
  // it entirely. Typing then goes nowhere, which reads as a dead composer.
  //
  // So: after a click anywhere in the composer that did NOT land on a real control, hand focus back to
  // the textarea. Interactive targets are left alone — stealing focus from a select mid-choice, or from
  // the file input, or from the Send/Queue buttons, would be its own bug. `closest()` covers clicks on
  // a <label>'s text, which forward to their control.
  byId('chat-composer')?.addEventListener('click', (event) => {
    const t = event.target;
    if (!t || typeof t.closest !== 'function') return;
    if (t.closest('input, textarea, select, button, a, label, summary, [contenteditable="true"]')) return;
    const bodyEl = byId('chat-composer-body');
    if (bodyEl && !bodyEl.disabled && document.activeElement !== bodyEl) bodyEl.focus();
  });
  // Toggling the Options disclosure leaves focus on the <summary>, so the very next keystroke is lost.
  // Return it to the textarea once the panel has finished opening/closing.
  byId('chat-composer')?.querySelector('.composer-advanced')?.addEventListener('toggle', () => {
    const bodyEl = byId('chat-composer-body');
    if (bodyEl && !bodyEl.disabled) bodyEl.focus();
  });
  // Draft persistence (2026-06-29 parity with old dashboard): mirror per-conversation drafts to
  // localStorage so a half-written message + its rail "draft" badge survive a page reload.
  // persistChatDrafts moved to ./chat-prefs.mjs in v0.5.4.
  try { const _d = JSON.parse(localStorage.getItem('aifyChatDrafts') || '{}'); if (_d && typeof _d === 'object') state.chat.drafts = _d; } catch { /* keep {} */ }
  // Draft preservation (WS-F): persist the composer body per conversation as the operator types.
  byId('chat-composer-body')?.addEventListener('input', (event) => {
    const key = state.chat.selected;
    if (key) { state.chat.drafts = state.chat.drafts || {}; state.chat.drafts[key] = event.target.value; persistChatDrafts(); }
  });
  // Enter-to-send in chat (Shift+Enter inserts a newline) — WS-I11.
  byId('chat-composer-body')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      chatController.send();
    }
  });
  // Chat artifact-attach (WS-F): upload the chosen file to /shared, insert a reference.
  byId('chat-attach-input')?.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    if (file) attachChatFile(file).finally(() => { event.target.value = ''; });
  });
  byId('files-upload-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    uploadSharedFile().catch((err) => toast(`Upload failed: ${err?.message || err}`, 'error'));
  });
  byId('chat-new-channel-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const input = byId('chat-new-channel');
    const name = (input?.value || '').trim();
    if (!name) return;
    chatCreateChannel(name).then(() => { if (input) input.value = ''; })
      .catch((err) => toast(`Create channel failed: ${err?.message || err}`, 'error'));
  });

  // WS-J: the Sessions composer was removed (it duplicated Chat). Messaging happens in Chat;
  // Sessions is terminal + lifecycle only.
  document.addEventListener('paste', (event) => {
    const target = event.target;
    // Image paste in the chat composer (the landing surface).
    if (!target || target.id !== 'chat-composer-body') return;
    const items = event.clipboardData?.items ? [...event.clipboardData.items] : [];
    const imageItem = items.find((item) => item.kind === 'file' && String(item.type || '').startsWith('image/'));
    if (!imageItem) return;
    const blob = imageItem.getAsFile();
    if (!blob) return;
    event.preventDefault();
    uploadPastedImage(blob, target).catch((error) => inspect('paste-error', { message: error.message || 'Image upload failed' }));
  });
  byId('close-inspector').addEventListener('click', closeInspector);
  byId('toggle-nav').addEventListener('click', () => {
    setNavCollapsed(!byId('app-shell')?.classList.contains('nav-collapsed'));
  });
}

/**
 * The inspector's swipe-to-close gesture. `inspectorTouchStartY` travels with it: it is written by
 * touchstart and read by touchend, so the two listeners and the variable are one unit.
 */
export function wireInspectorGestures() {
  let inspectorTouchStartY = 0;
  byId('inspector').addEventListener('touchstart', (event) => {
    inspectorTouchStartY = event.touches?.[0]?.clientY || 0;
  }, { passive: true });
  byId('inspector').addEventListener('touchend', (event) => {
    const endY = event.changedTouches?.[0]?.clientY || 0;
    if (byId('inspector')?.classList.contains('run-inspector-sheet') && endY - inspectorTouchStartY > 70) {
      closeInspector();
    }
  }, { passive: true });
}

/**
 * Restore everything the operator chose on a previous visit, then paint the landing page.
 *
 * Every read is wrapped in its own try/catch because localStorage THROWS in private mode — not
 * returns null — and one unavailable preference must not stop the rest of the boot. The
 * attention-strip block has a catch that still collapses: an unreadable preference means "no explicit
 * choice", and the default is collapsed.
 */
export function restorePersistedPreferences({ setPage }) {
  installRejectionToast();
  applyCachedTheme(); // paint cached theme/title immediately so no default-palette flash before /settings
  try { state.settingsTab = localStorage.getItem('aifySettingsTab') || ''; } catch { /* ignore */ }
  try { const sf = JSON.parse(localStorage.getItem('aifySessionStatusFilter') || '[]'); if (Array.isArray(sf)) state.sessionStatusFilter = new Set(sf); } catch { /* ignore */ }
  // Restore persisted chat rail prefs (sticky declutter) + reflect into the controls.
  try {
    const p = JSON.parse(localStorage.getItem('aify.next.chatPrefs') || '{}') || {};
    state.chat.liveOnly = !!p.liveOnly;
    state.chat.openOnly = !!p.openOnly;
    state.chat.workingUp = !!p.workingUp;
    state.chat.unreadOnly = !!p.unreadOnly;
    if (typeof p.scope === 'string') state.chat.scope = p.scope;
    if (Array.isArray(p.statusFilter)) state.chat.statusFilter = new Set(p.statusFilter);
    if (p.sortMode) state.chat.sortMode = p.sortMode;
    state.chat.compact = !!p.compact;
    state.chat.peek = !!p.peek;
    const so = byId('chat-sort'); if (so) so.value = state.chat.sortMode;
    syncChatChips();
  } catch { /* ignore */ }
  // Default-collapse Needs-Attention so chat is the hero on landing (operator UX request).
  // Honor an explicit user choice either way; with no saved preference, start collapsed (the
  // header + quick-jumps stay visible as a slim one-line banner; the ▾ toggle re-expands).
  // One call, so the class, aria-expanded, aria-controls and the title cannot disagree. Both
  // branches used to add the class and nothing else, which is how this toggle's state came to be
  // legible as a CSS rotation and in no other way.
  setAttentionCollapsed(preferredAttentionCollapsed());
  loadVersionBadge();
  setPage('chat'); // chat-first landing: sync the page title/subtitle with the default page
  updateStaticLinks();
  setNavCollapsed(preferredNavCollapsed());
  // Restore the saved Work page view (Both / Work Loop / Runs) — survives reloads.
  try {
    const wv = localStorage.getItem('aifyWorkView');
    if (wv && wv !== 'all') {
      document.querySelector('.diagnostics-grid')?.setAttribute('data-work-view', wv);
      document.querySelectorAll('button[data-work-view]').forEach((b) => b.classList.toggle('active', b.dataset.workView === wv));
    }
  } catch { /* private mode */ }
}

/**
 * The Settings page's own controls, plus the attention-strip collapse toggle.
 *
 * Separate from `wireGlobalControls` because these are the only listeners that write appearance
 * state, and one of them — the live Appearance preview — deliberately does NOT persist: it repaints
 * from unsaved form values, and Reset is what puts the saved ones back.
 */
export function wireSettingsControls({ saveSettings }) {
  byId('attention-collapse')?.addEventListener('click', () => {
    const strip = byId('attention-strip');
    if (!strip) return;
    setAttentionCollapsed(!strip.classList.contains('collapsed'));
  });
  byId('settings-save')?.addEventListener('click', () => {
    saveSettings().catch((err) => toast(`Save failed: ${err?.message || err}`, 'error'));
  });
  byId('settings-reset')?.addEventListener('click', () => {
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); // clear the edit-guard
    applyTheme(state.settings); // undo any live appearance preview
    refreshActiveTerminalTheme();
    renderSettings();           // repaint inputs from the last-saved settings
    toast('Reverted unsaved changes', 'ok');
  });
  // Live-preview Appearance edits (theme select, color pickers, title) without saving.
  byId('settings-form')?.addEventListener('input', (event) => {
    if (event.target.closest('.settings-appearance')) previewAppearance();
  });
  byId('settings-form')?.addEventListener('change', (event) => {
    if (event.target.closest('.settings-appearance')) previewAppearance();
  });
}
