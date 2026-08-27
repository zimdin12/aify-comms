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
// `esc` is the HTML escaper from util.js, and MOST interpolations of caller data go through it — but not
// all, and this comment claimed otherwise until the tests proved it wrong. `item.key` reaches `id="..."`
// and `for="..."` UNESCAPED via the `id` template, while the adjacent `data-setting-key` is escaped. So a
// setting key containing a quote injects attributes. It is not reachable today (keys come from the
// hardcoded `SETTINGS_SCHEMA`) and v0.5.x is structural-only, so `settings-fields.test.mjs` PINS it as
// current behaviour and it is reported for its own behaviour tag.
//
// The comment is corrected rather than deleted because "every interpolation is escaped" is exactly the kind
// of reassuring, unenforced sentence that makes the next reader stop checking.

import { esc } from './util.js';
import { THEMES, normalizedHexColor, paletteFromSettings } from './theme.js';

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
    // Through the helper, which also LOWERCASES. This kept the value exactly as stored, while
    // `<input type="color">` normalises its own value to lowercase -- so a setting saved as #AABBCC
    // showed `#AABBCC` in the code label beside a swatch driven by `#aabbcc`, and the theme applied
    // the lowercase one. One question, one answer.
    const hex = normalizedHexColor(value, fallback);
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
