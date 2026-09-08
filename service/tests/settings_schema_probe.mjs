// Report the dashboard's REAL settings schema, its RENDERED widgets and its bindings, as JSON.
//
// WHY THIS EXISTS RATHER THAN A REGEX OVER THE SOURCE. Its Python caller once extracted the control
// list with a regular expression, and review demonstrated seven source changes the whole gate stayed
// green through: a commented-out row still counted, a double-quoted row was invisible, a duplicated
// key read as one control, `min: 90.5` read as 90, `min: 45 * 3` read as 45 while the browser
// computes 135, and the renderer could be rewired to draw one setting from another. Every one of
// those is a question about what JavaScript DOES, so it is answered by running it.
//
// AND WHY THE BINDING IS READ PER FIELD RATHER THAN AS A WHOLE-PANEL DIFFERENCE. The first fix
// compared the entire rendered panel before and after changing one setting. Review broke it by
// SWAPPING two fields' value bindings: each key still changed the panel, so both received credit
// while each was displaying the other's value. A difference is only evidence when it belongs to the
// field that NAMES the setting -- so each field is extracted by its own `data-setting-key`, and a
// change anywhere else is reported as contamination rather than ignored.
//
// THE WIDGET IS ITS OWN AUTHORITY. `SETTINGS_SCHEMA` says a control is a number; the renderer decides
// whether it emits `<input type="number">`. Changing only the renderer left every schema-based check
// green, so the emitted tag and type are reported here and compared separately.
//
// NO BROWSER, NO NETWORK, NO APP. The only stub is the DOM calls `renderSettings` makes, so the HTML
// it produces is the real one.
//
// Run: node service/tests/settings_schema_probe.mjs

import { SETTINGS_SCHEMA, EFFORT_OPTS, PI_EFFORT_OPTS, renderSettings } from
  "../new_dashboard/settings-panel.mjs";
import { state } from "../new_dashboard/state.mjs";
import { THEMES } from "../new_dashboard/theme.js";

//: REAL THEME NAMES. Two invented ones select no tile, so the panel renders identically for both and
//: a difference test reports a false "not bound" -- which it did, for `dashboard_theme` alone.
const THEME_NAMES = Object.keys(THEMES);

/** Every control the schema declares, in order, with duplicates preserved so they can be counted. */
const controls = SETTINGS_SCHEMA.flatMap((group) => group.items.map((item) => ({
  key: item.key,
  label: item.label ?? null,
  type: item.type ?? null,
  min: Object.prototype.hasOwnProperty.call(item, "min") ? item.min : null,
  max: Object.prototype.hasOwnProperty.call(item, "max") ? item.max : null,
  options: Array.isArray(item.options) ? item.options : null,
})));

const host = { innerHTML: "", contains: () => false };
globalThis.document = {
  activeElement: null,
  getElementById: (id) => (id === "settings-form" ? host : null),
};

/**
 * The rendered state of the field that NAMES this key: its tag, its widget type, and what it shows.
 *
 * Returns null when no field carries the key, which is itself an answer.
 */
function fieldState(html, key) {
  const opening = new RegExp(
    `<(input|select)\\b[^>]*data-setting-key="${key}"[^>]*>`, "i",
  ).exec(html);
  if (!opening) return null;
  const tag = opening[1].toLowerCase();
  const attrs = opening[0];
  const attr = (name) => {
    const found = new RegExp(`\\b${name}="([^"]*)"`, "i").exec(attrs);
    return found ? found[1] : null;
  };
  const state = {
    tag,
    widgetType: tag === "select" ? "select" : attr("type"),
    declaredType: attr("data-setting-type"),
    value: attr("value"),
    checked: / checked(?=[\s>])/i.test(attrs),
    selected: null,
  };
  if (tag === "select") {
    const rest = html.slice(opening.index);
    const end = rest.indexOf("</select>");
    const body = end >= 0 ? rest.slice(0, end) : rest;
    const chosen = /<option[^>]*\bvalue="([^"]*)"[^>]*\bselected\b/i.exec(body);
    state.selected = chosen ? chosen[1] : null;
  }
  return state;
}

function shown(state) {
  if (!state) return null;
  return JSON.stringify([state.value, state.checked, state.selected]);
}

/** Two distinct values for a control, BOTH representable in its own widget. */
function pairFor(control, index) {
  if (control.type === "toggle") return [true, false];
  if (control.type === "number") return [1000 + index, 2000 + index];
  if (control.type === "csv") return [[`alpha${index}`], [`beta${index}`]];
  if (Array.isArray(control.options) && control.options.length >= 2) {
    return [control.options[0], control.options[1]];
  }
  if (control.type === "theme") return [THEME_NAMES[0], THEME_NAMES[1]];
  if (control.type === "color") return ["#111111", "#222222"];
  return [`ALPHA_${index}`, `BETA_${index}`];
}

function render(settings) {
  state.settings = settings;
  host.innerHTML = "";
  renderSettings();
  return host.innerHTML;
}

let renderError = null;
const baseline = {};
controls.forEach((control, index) => { baseline[control.key] = pairFor(control, index)[0]; });

let baseHtml = "";
try {
  baseHtml = render({ ...baseline });
} catch (error) {
  renderError = String(error && error.message ? error.message : error);
}

const widgets = {};
const respondsToItsOwnValue = {};
const contaminates = {};

if (!renderError) {
  const baseFields = {};
  for (const control of controls) {
    const found = fieldState(baseHtml, control.key);
    baseFields[control.key] = found;
    widgets[control.key] = found
      ? { tag: found.tag, widgetType: found.widgetType, declaredType: found.declaredType }
      : null;
  }

  controls.forEach((control, index) => {
    const [, other] = pairFor(control, index);
    const changedHtml = render({ ...baseline, [control.key]: other });
    // THE FIELD THAT NAMES THIS KEY must have changed...
    respondsToItsOwnValue[control.key] =
      shown(fieldState(changedHtml, control.key)) !== shown(baseFields[control.key]);
    // ...and NOTHING ELSE may have. A swapped binding moves another field instead of, or as well as,
    // this one, and a whole-panel comparison credits both.
    contaminates[control.key] = controls
      .map((other_) => other_.key)
      .filter((key) => key !== control.key
        && shown(fieldState(changedHtml, key)) !== shown(baseFields[key]));
  });
}

process.stdout.write(JSON.stringify({
  controls,
  optionDomains: { EFFORT_OPTS, PI_EFFORT_OPTS },
  widgets,
  respondsToItsOwnValue,
  contaminates,
  renderError,
  renderedLength: baseHtml.length,
}));
