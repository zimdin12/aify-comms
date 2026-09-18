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
// Every declaration was byte-identical to the one that stood in app.js, with `export ` added. Since
// 2026-09-19 the schema, tab labels and effort lists differ, declared as edits in
// extraction-proof.test.mjs: the settings come from the service now (see adoptSettingsSchema below). Leading comments stayed behind in
// app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its comments
// could not round-trip through the proof.


import { settingsFieldHtml } from './settings-fields.mjs';
import { state } from './state.mjs';
import { THEMES, normalizedHexColor, previewTheme } from './theme.js';
import { byId } from './ui.js';
import { esc } from './util.js';

export const EFFORT_OPTS = Object.freeze([]); // empty: choices come from GET /settings/schema; kept for the proof
export const PI_EFFORT_OPTS = Object.freeze([]); // empty: choices come from GET /settings/schema; kept for the proof
export const SETTINGS_SCHEMA = []; // filled from GET /settings/schema by adoptSettingsSchema()
export const SETTINGS_TAB_LABELS = {
  'Replies & messages': 'Replies', 'Agent liveness': 'Liveness', 'Managed workers': 'Workers',
  'Files & retention': 'Files', 'Appearance': 'Appearance', 'Advanced': 'Advanced',
};
export const SETTINGS_TAB_DESC = {
  'Replies & messages': 'When agents are reminded to reply, and what happens when they do not.',
  'Agent liveness': 'How much silence before an agent or a machine reads offline.',
  'Managed workers': 'What new dashboard-spawned workers start with. Saving changes only new workers; use the button to update existing ones.',
  'Files & retention': "Shared file size, and how long a removed agent's run history is kept.",
  'Appearance': 'Theme, accent colours, and the dashboard title.',
  'Advanced': 'Internal timings and legacy switches. The defaults suit almost every setup.',
};
export const HELP_TAB = 'Help';
export function activeSettingsTab() {
  const tabs = [...SETTINGS_SCHEMA.map((g) => g.group), HELP_TAB];
  return tabs.includes(state.settingsTab) ? state.settingsTab : (SETTINGS_SCHEMA[0]?.group || HELP_TAB);
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
  if (!SETTINGS_SCHEMA.length) { host.innerHTML = '<p class="settings-panel-desc">Loading settings…</p>'; return; }
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
      ${grp.group === 'Managed workers' ? '<button type="button" class="btn" id="settings-apply-defaults">Apply model and effort to existing workers</button>' : ''}
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
    // `normalizedHexColor` is the one place that decides what a usable hex colour is. This hand-rolled
    // the same regex, as did settings-fields.mjs -- three implementations of one question, which agree
    // until somebody widens one of them.
    const v = normalizedHexColor(getComputedStyle(document.body).getPropertyValue('--accent'), '');
    if (v) return v;
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

// THE PANEL IS DRAWN FROM THE SERVICE'S DECLARATIONS (service/api_core/settings_spec.py), served by
// GET /settings/schema, so a setting's type, bounds, label and help exist in one place. Until
// 2026-09-19 this module hand-listed them, and the list and the service disagreed about bounds.
const WIDGET = { bool: 'toggle', int: 'number', model: 'text', text: 'text', color: 'color', runtimes: 'csv' };
const APPLIES_NOTE = { 'next worker start': 'Takes effect when a worker next starts.', 'next rotation': '' };

export function settingsItemFromDeclaration(d) {
  const type = d.kind === 'choice' ? (d.key === 'dashboard_theme' ? 'theme' : 'select') : (WIDGET[d.kind] || 'text');
  const hint = [d.help, APPLIES_NOTE[d.applies] || ''].filter(Boolean).join(' ');
  const item = { key: d.key, label: d.unit ? `${d.label} (${d.unit})` : d.label, type };
  if (hint) item.hint = hint;
  if (d.min != null) item.min = d.min;
  if (d.max != null) item.max = d.max;
  if (type === 'select') {
    item.options = d.choices;
    item.optionLabels = { '': 'default' };
  }
  return item;
}

export function adoptSettingsSchema(served) {
  const declarations = Array.isArray(served?.settings) ? served.settings : [];
  const groups = (Array.isArray(served?.groups) ? served.groups : []).map((group) => ({
    group,
    appearance: group === 'Appearance',
    items: declarations.filter((d) => d.group === group).map(settingsItemFromDeclaration),
  })).filter((g) => g.items.length);
  SETTINGS_SCHEMA.splice(0, SETTINGS_SCHEMA.length, ...groups);
  return SETTINGS_SCHEMA;
}
