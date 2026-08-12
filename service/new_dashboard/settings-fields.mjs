// Settings-field markup: one field of the Settings page, and the theme tiles one field type embeds.
//
// FIRST JS EXTRACTION of the v0.5.4 decomposition, and its job is as much to establish the proof as to
// move 49 lines. See docs/JS_DECOMPOSITION_PROOF_PACKET.md.
//
// WHY THESE TWO. `app.js` cannot be imported by a test at all — importing it executes module-scope
// browser code and dies on `ReferenceError: location is not defined` — so nothing in it has ever been
// unit-tested. These two functions take data and return an HTML string: no DOM, no module-scope mutable
// state, and no reference from an inline `on*=` handler attribute (55 of app.js's functions ARE bound
// that way and resolve against the global scope, so moving one would break the page silently).
//
// ONE EXPORT, NOT TWO. `themePreviewTilesHtml` is called only by `settingsFieldHtml` and stays private;
// it is tested through the public root rather than exported to make testing convenient. An export exists
// because a named production consumer needs it.
//
// `esc` is the HTML escaper from util.js and every interpolation of caller data goes through it. The one
// place that does NOT is `attrs` in the callers' `data-*` jump targets, which is caller-authored markup
// and never user input — the tests below pin the escaping of the fields that ARE user-controlled.

import { esc } from './util.js';
import { THEMES, paletteFromSettings } from './theme.js';

function themePreviewTilesHtml(selectedKey) {
  const selected = THEMES[selectedKey] ? selectedKey : 'default';
  return `<div class="theme-preview-grid" id="theme-preview-grid">${Object.entries(THEMES).map(([key, t]) => `
    <button type="button" class="theme-preview${key === selected ? ' active' : ''}" data-theme-choice="${esc(key)}" title="Use ${esc(t.label)} color scheme">
      <b>${esc(t.label)}</b>
      <span class="theme-preview-swatches"><span style="background:${esc(t.accent)}"></span><span style="background:${esc(t.secondary)}"></span><span style="background:${esc(t.tertiary)}"></span></span>
    </button>`).join('')}</div>`;
}

export function settingsFieldHtml(item, value, settings = {}) {
  const id = `set-${item.key}`;
  const hint = item.hint ? `<span class="field-hint">${esc(item.hint)}</span>` : '';
  // Associate the label with its input (for/id) so screen readers announce the field name.
  const labelBlock = `<label class="field-label" for="${id}">${esc(item.label)}${hint}</label>`;
  const bounds = `${item.min != null ? ` min="${item.min}"` : ''}${item.max != null ? ` max="${item.max}"` : ''}`;

  if (item.type === 'toggle') {
    return `<div class="settings-field"><label class="field-label" for="${id}">${esc(item.label)}${hint}</label>`
      + `<div class="field-control"><label class="switch"><input type="checkbox" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="toggle"${value === true ? ' checked' : ''}><span class="switch-slider"></span></label></div></div>`;
  }
  if (item.type === 'theme') {
    const key = THEMES[value] ? value : 'default';
    const opts = Object.entries(THEMES).map(([k, t]) => `<option value="${esc(k)}"${k === key ? ' selected' : ''}>${esc(t.label)}</option>`).join('');
    return `<div class="settings-field settings-field-wide">${labelBlock}`
      + `<div class="field-control"><select id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="theme">${opts}</select></div>`
      + `<div class="settings-field-extra">${themePreviewTilesHtml(key)}</div></div>`;
  }
  if (item.type === 'color') {
    const preset = paletteFromSettings(settings, settings.dashboard_theme);
    const fallback = item.key === 'dashboard_secondary_color' ? preset.secondary : item.key === 'dashboard_tertiary_color' ? preset.tertiary : preset.accent;
    const hex = /^#[0-9a-fA-F]{6}$/.test(String(value || '')) ? value : fallback;
    return `<div class="settings-field">${labelBlock}<div class="field-control field-control-color"><input type="color" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="color" value="${esc(hex)}"><code class="field-color-hex">${esc(hex)}</code></div></div>`;
  }
  let control;
  if (item.type === 'select') {
    const opts = (item.options || []).map((o) => {
      const label = (item.optionLabels && item.optionLabels[o] != null) ? item.optionLabels[o] : (o === '' ? '(default)' : o);
      return `<option value="${esc(o)}"${String(value ?? '') === String(o) ? ' selected' : ''}>${esc(label)}</option>`;
    }).join('');
    control = `<select id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="select">${opts}</select>`;
  } else if (item.type === 'number') {
    control = `<input type="number" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="number" value="${esc(value ?? '')}"${bounds}>`;
  } else if (item.type === 'csv') {
    const text = Array.isArray(value) ? value.join(', ') : (value ?? '');
    control = `<input type="text" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="csv" value="${esc(text)}">`;
  } else {
    control = `<input type="text" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="text" value="${esc(value ?? '')}">`;
  }
  return `<div class="settings-field">${labelBlock}<div class="field-control">${control}</div></div>`;
}
