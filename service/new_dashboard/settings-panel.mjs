// The Settings page: its schema, its tabs, and the appearance preview that drives the terminal theme.
//
// Distinct from `settings-fields.mjs`, which owns how ONE field renders. This module owns what the page is
// made of — the schema of every setting, the tab labels and descriptions, which tab is active, and the
// live appearance preview that recolours open terminals as the operator drags a swatch.
//
// Extracted from app.js in v0.5.4 as a measured closure: thirteen declarations that need nothing from
// app.js at all, only sibling leaf modules imported downward. That became possible once `state` and `byId`
// were given owners of their own; before that every render group in app.js read at least one name app.js
// itself declared, and a module extracted from app.js cannot import those back without the upward import
// this series forbids — which here would also be a cycle.
//
// The constants come along because nothing outside the closure reads them: the ownership test used
// throughout the series is a count of DIRECT readers, not a guess at where a name belongs.
//
// Every declaration is byte-identical to the one that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Leading comments stayed behind in
// app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its comments
// could not round-trip through the proof.


import { settingsFieldHtml } from './settings-fields.mjs';
import { state } from './state.mjs';
import { THEMES, previewTheme } from './theme.js';
import { byId } from './ui.js';
import { esc } from './util.js';

export const EFFORT_OPTS = ['low', 'medium', 'high', 'xhigh'];
export const PI_EFFORT_OPTS = ['', 'low', 'medium', 'high', 'xhigh'];
export const SETTINGS_SCHEMA = [
  { group: 'Appearance', appearance: true, items: [
    { key: 'dashboard_theme', label: 'Color scheme', type: 'theme' },
    { key: 'dashboard_primary_color', label: 'Primary color', type: 'color', hint: 'Actions, brand, focus.' },
    { key: 'dashboard_secondary_color', label: 'Secondary color', type: 'color', hint: 'Selection, links.' },
    { key: 'dashboard_tertiary_color', label: 'Tertiary color', type: 'color', hint: 'Depth, charts.' },
    { key: 'dashboard_title', label: 'Dashboard title', type: 'text' },
  ] },
  { group: 'Status & lifecycle', items: [
    { key: 'resident_lease_seconds', label: 'Resident bridge lease (s)', type: 'number', min: 30, max: 3600 },
    { key: 'environment_offline_seconds', label: 'Environment offline after (s)', type: 'number', min: 30, max: 3600 },
    { key: 'agent_liveness_seconds', label: 'Agent offline after no heartbeat (s)', type: 'number', min: 30, max: 600 },
    { key: 'worker_idle_close_enabled', label: 'Auto-close idle managed workers', type: 'toggle' },
    { key: 'worker_idle_close_minutes', label: 'Idle close after (min)', type: 'number', min: 0, max: 1440 },
    { key: 'auto_confirm_session_id', label: 'Auto-confirm new session IDs', type: 'toggle' },
    { key: 'manual_session_mode', label: 'Show resident↔managed switch chips', type: 'toggle' },
  ] },
  { group: 'Reply contracts', items: [
    { key: 'reply_contracts_enabled', label: 'Reply contracts enabled', type: 'toggle' },
    { key: 'reply_reminder_minutes', label: 'First reminder after (min)', type: 'number', min: 1, max: 240 },
    { key: 'reply_reminder_repeat_minutes', label: 'Reminder repeat (min)', type: 'number', min: 1, max: 1440 },
    { key: 'reply_reminder_max_count', label: 'Max reminders (0 = unlimited)', type: 'number', min: 0, max: 20 },
    { key: 'reply_reminder_full_every', label: 'Full reminder every Nth (0 = always full)', type: 'number', min: 0, max: 20 },
    { key: 'contract_stale_hours', label: 'Contract history window (h)', type: 'number', min: 1, max: 720 },
  ] },
  { group: 'Managed runtimes', items: [
    { key: 'managed_terminal_backing_enabled', label: 'Terminal-backed managed sessions', type: 'toggle' },
    { key: 'insert_messages_via_console', label: 'Legacy PTY-input delivery', type: 'toggle', hint: 'Default off — scrambles concurrent typing. Channel delivery is preferred.' },
    { key: 'managed_pty_eager_spawn', label: 'Eager-spawn managed PTY', type: 'toggle' },
    { key: 'managed_via_wrapper', label: 'Wrapper-backed managed runtimes', type: 'csv', hint: 'Comma-separated, e.g. codex, hermes.' },
    { key: 'managed_claude_model', label: 'Managed claude model', type: 'text' },
    { key: 'managed_claude_effort', label: 'Managed claude effort', type: 'select', options: EFFORT_OPTS },
    { key: 'managed_codex_model', label: 'Managed codex model', type: 'text' },
    { key: 'managed_codex_effort', label: 'Managed codex effort', type: 'select', options: EFFORT_OPTS },
    { key: 'managed_pi_model', label: 'Managed pi model', type: 'text' },
    { key: 'managed_pi_effort', label: 'Managed pi effort', type: 'select', options: PI_EFFORT_OPTS, optionLabels: { '': 'OMP default' } },
  ] },
  { group: 'Retention & rotation', items: [
    { key: 'rotation_enabled', label: 'Rotation enabled', type: 'toggle' },
    { key: 'retention_days', label: 'Retention (days)', type: 'number', min: 1, max: 3650 },
    { key: 'max_messages_per_agent', label: 'Max messages / agent', type: 'number', min: 10, max: 100000 },
    { key: 'max_shared_size_mb', label: 'Max shared file size (MB)', type: 'number', min: 10, max: 100000 },
    { key: 'active_run_stale_minutes', label: 'Terminal run stale cleanup (min)', type: 'number', min: 5, max: 240 },
    { key: 'active_managed_run_stale_minutes', label: 'Managed run stale cleanup (min)', type: 'number', min: 1, max: 120 },
  ] },
  { group: 'Dashboard', items: [
    { key: 'dashboard_refresh_seconds', label: 'Poll fallback (s)', type: 'number', min: 5, max: 300, hint: 'A safety net only — live updates arrive over WebSocket.' },
  ] },
];
export const SETTINGS_TAB_LABELS = {
  'Appearance': 'Appearance', 'Status & lifecycle': 'Status', 'Reply contracts': 'Contracts',
  'Managed runtimes': 'Runtimes', 'Retention & rotation': 'Retention', 'Dashboard': 'Dashboard',
};
export const SETTINGS_TAB_DESC = {
  'Appearance': 'Theme, accent colors, and the dashboard title.',
  'Status & lifecycle': 'How liveness is derived and when agents are marked idle/offline.',
  'Reply contracts': 'Reply-reminder cadence and how long contracts stay tracked.',
  'Managed runtimes': 'Defaults applied to dashboard-spawned managed agents.',
  'Retention & rotation': 'Message/file retention and stale-record cleanup windows.',
  'Dashboard': 'Dashboard-only preferences.',
};
export const HELP_TAB = 'Help';
export function activeSettingsTab() {
  const tabs = [...SETTINGS_SCHEMA.map((g) => g.group), HELP_TAB];
  return tabs.includes(state.settingsTab) ? state.settingsTab : SETTINGS_SCHEMA[0].group;
}
export function renderSettings() {
  const host = byId('settings-form');
  if (!host) return;
  // Don't rebuild while the operator is editing a FIELD — the 15s poll re-renders settings and
  // would otherwise wipe an in-progress edit (deep-audit C1). Scope strictly to editable inputs:
  // the tab buttons live inside this same host, so guarding on any focused descendant also blocked
  // tab switches (a real click focuses the tab → early return → panel never switched). 2026-06-29 fix.
  const _ae = document.activeElement;
  if (_ae && host.contains(_ae) && _ae.matches && _ae.matches('input, select, textarea')) return;
  const s = state.settings || {};
  const active = activeSettingsTab();
  const tabBar = `<div class="settings-tabs" role="group" aria-label="Settings sections">`
    + SETTINGS_SCHEMA.map((g) => `<button type="button" class="settings-tab${g.group === active ? ' active' : ''}" data-settings-tab="${esc(g.group)}">${esc(SETTINGS_TAB_LABELS[g.group] || g.group)}</button>`).join('')
    + `<button type="button" class="settings-tab${active === HELP_TAB ? ' active' : ''}" data-settings-tab="${HELP_TAB}">${HELP_TAB}</button>`
    + `</div>`;
  const panels = SETTINGS_SCHEMA.map((grp) => `
    <section class="settings-panel${grp.group === active ? ' active' : ''}${grp.appearance ? ' settings-appearance' : ''}" data-settings-panel="${esc(grp.group)}">
      ${SETTINGS_TAB_DESC[grp.group] ? `<p class="settings-panel-desc">${esc(SETTINGS_TAB_DESC[grp.group])}</p>` : ''}
      ${grp.items.map((item) => settingsFieldHtml(item, s[item.key], s)).join('')}
    </section>`).join('');
  host.innerHTML = tabBar + panels;
  // Help tab shows the static help-band; schema tabs hide it. Save/Classic buttons hide on Help.
  const helpBand = byId('help-band');
  if (helpBand) helpBand.hidden = active !== HELP_TAB;
  const saveBtn = byId('settings-save');
  if (saveBtn) saveBtn.style.display = active === HELP_TAB ? 'none' : '';
}
export function readAppearanceInputs() {
  const val = (k) => byId(`set-${k}`)?.value;
  return {
    dashboard_theme: val('dashboard_theme'),
    dashboard_primary_color: val('dashboard_primary_color'),
    dashboard_secondary_color: val('dashboard_secondary_color'),
    dashboard_tertiary_color: val('dashboard_tertiary_color'),
    dashboard_title: val('dashboard_title'),
  };
}
export function previewAppearance() {
  const a = readAppearanceInputs();
  previewTheme({ theme: a.dashboard_theme, primary: a.dashboard_primary_color, secondary: a.dashboard_secondary_color, tertiary: a.dashboard_tertiary_color });
  refreshActiveTerminalTheme(); // live-preview the console accent as the operator edits the palette
  const title = String(a.dashboard_title || 'AIFY Comms').trim() || 'AIFY Comms';
  document.title = title;
  const brand = document.querySelector('.brand-copy strong');
  if (brand) brand.textContent = title;
  // Keep the hex labels next to the color pickers in sync.
  document.querySelectorAll('.field-control-color').forEach((wrap) => {
    const input = wrap.querySelector('input[type="color"]');
    const code = wrap.querySelector('.field-color-hex');
    if (input && code) code.textContent = input.value;
  });
}
export function terminalAccentColor() {
  try {
    const v = getComputedStyle(document.body).getPropertyValue('--accent').trim();
    if (/^#[0-9a-fA-F]{6}$/.test(v)) return v;
  } catch {}
  const preset = THEMES[String(document.body.dataset.theme || 'default')] || THEMES.default;
  return preset.accent || '#51c5b0';
}
export function terminalThemeFromDashboard() {
  const accent = terminalAccentColor();
  return {
    background: '#0b0e13',
    foreground: '#cdd6f4',
    cursor: accent,
    cursorAccent: '#0b0e13',
    selectionBackground: `${accent}55`, // ~33% alpha tint of the accent
  };
}
export function refreshActiveTerminalTheme() {
  const entry = state.activeXterm;
  if (!entry || !entry.term) return;
  const accent = terminalAccentColor();
  if (entry._themeAccent === accent) return;
  entry._themeAccent = accent;
  try { entry.term.options.theme = terminalThemeFromDashboard(); } catch {}
  try { entry.webgl?.clearTextureAtlas?.(); } catch {}
}

// The theme-preset tile click, moved out of app.js's delegated click handler in v0.5.4 — the FIRST
// extract-method this repo's reconstruction proof could express. It lives here because everything it
// touches already did: THEMES, byId and previewAppearance are this module's own.
//
// The body is byte-identical to the branch it left, dedented by two. app.js keeps the guard and the
// `return;`, so the handler's control flow is untouched — this is a relocation, not a redesign.
export function applyThemeChoice(themeChoice) {
  const key = themeChoice.dataset.themeChoice;
  const sel = byId('set-dashboard_theme');
  if (sel) sel.value = key;
  // Selecting a preset resets the custom color pickers to that preset's palette.
  const preset = THEMES[key] || THEMES.default;
  const setColor = (k, v) => { const el = byId(`set-${k}`); if (el) el.value = v; };
  setColor('dashboard_primary_color', preset.accent);
  setColor('dashboard_secondary_color', preset.secondary);
  setColor('dashboard_tertiary_color', preset.tertiary);
  document.querySelectorAll('#theme-preview-grid .theme-preview').forEach((tile) => {
    tile.classList.toggle('active', tile.dataset.themeChoice === key);
  });
  previewAppearance();
}

// The settings tab selector, moved out of app.js's delegated click handler in v0.5.4. It belongs here
// because `renderSettings` — the thing it exists to trigger — is this module's own.
export function selectSettingsTab(settingsTab) {
  state.settingsTab = settingsTab.dataset.settingsTab;
  try { localStorage.setItem('aifySettingsTab', state.settingsTab); } catch { /* ignore */ }
  renderSettings();
}
