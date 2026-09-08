// Report the dashboard's REAL settings schema, option domains and rendered bindings, as JSON.
//
// WHY THIS EXISTS RATHER THAN A REGEX. Its Python caller used to extract the control list with a
// regular expression, and review demonstrated seven source changes the whole gate stayed green
// through: a commented-out row still counted, a double-quoted row was invisible, a duplicated key
// read as one control, `min: 90.5` read as 90, `min: 45 * 3` read as 45 while the browser renders
// 135, and the renderer could be rewired to display a different setting's value entirely.
//
// Every one of those is a question about what JavaScript DOES, so the answer has to come from
// JavaScript running it. This module imports the panel and evaluates it: comments, quoting,
// expressions and duplicates are then handled by the parser that actually ships.
//
// THE BINDING IS MEASURED BY DIFFERENCE, not by looking for a sentinel. A sentinel string is not
// representable in a checkbox, a select or a number input, so "is my value in the HTML" answers only
// for text-like fields -- 20 of 35 here. Instead each key is changed ON ITS OWN and the whole panel
// re-rendered: a field that is not wired to its own setting produces IDENTICAL HTML when that
// setting changes. That is type-agnostic and it catches the exact rewiring review demonstrated,
// where `retention_days` was drawn from `rotation_enabled`.
//
// NO BROWSER, NO NETWORK, NO APP. The only stub is the DOM calls `renderSettings` makes, so the
// HTML it produces is the real one.
//
// Run: node service/tests/settings_schema_probe.mjs

import { SETTINGS_SCHEMA, EFFORT_OPTS, PI_EFFORT_OPTS, renderSettings } from
  "../new_dashboard/settings-panel.mjs";
import { state } from "../new_dashboard/state.mjs";
import { THEMES } from "../new_dashboard/theme.js";

//: REAL THEME NAMES. Two invented ones select no tile, so the panel renders identically for both and
//: the difference test reports a false "not bound" -- which it did, for `dashboard_theme` alone,
//: before this was here. A probe that cannot vary a field cannot judge it.
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

/** Two distinct values for a control, chosen so BOTH are representable in its own widget. */
function pairFor(control) {
  if (control.type === "toggle") return [true, false];
  if (control.type === "number") return [11, 22];
  if (control.type === "csv") return [["alpha"], ["beta"]];
  if (Array.isArray(control.options) && control.options.length >= 2) {
    return [control.options[0], control.options[1]];
  }
  if (control.type === "theme") return [THEME_NAMES[0], THEME_NAMES[1]];
  if (control.type === "color") return ["#111111", "#222222"];
  return ["ALPHA_VALUE", "BETA_VALUE"];
}

function render(settings) {
  state.settings = settings;
  host.innerHTML = "";
  renderSettings();
  return host.innerHTML;
}

let renderError = null;
const baseline = {};
for (const control of controls) baseline[control.key] = pairFor(control)[0];

let baseHtml = "";
try {
  baseHtml = render({ ...baseline });
} catch (error) {
  renderError = String(error && error.message ? error.message : error);
}

// A field wired to its own setting must redraw when that setting alone changes.
const respondsToItsOwnValue = {};
if (!renderError) {
  for (const control of controls) {
    const [, other] = pairFor(control);
    const changed = render({ ...baseline, [control.key]: other });
    respondsToItsOwnValue[control.key] = changed !== baseHtml;
  }
}

process.stdout.write(JSON.stringify({
  controls,
  optionDomains: { EFFORT_OPTS, PI_EFFORT_OPTS },
  respondsToItsOwnValue,
  renderError,
  renderedLength: baseHtml.length,
}));
