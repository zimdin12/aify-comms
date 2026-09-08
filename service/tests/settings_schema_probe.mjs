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
// FIDELITY IS A THIRD OBLIGATION, separate from identity and responsiveness. A renderer changed to
// display `value + 1` moved the right field by the right amount under the right key and still showed
// the wrong number; a renderer with a hardcoded `min` carried bounds the schema never declared. So a
// second arm renders the SHIPPED DEFAULTS -- handed in as JSON by the caller, because only Python
// knows them -- and reports what each field DISPLAYS and what bounds it EMITS.
//
// Run: node service/tests/settings_schema_probe.mjs '{"retention_days": 90, ...}'

import { SETTINGS_SCHEMA, EFFORT_OPTS, PI_EFFORT_OPTS, renderSettings } from
  "../new_dashboard/settings-panel.mjs";
import { state } from "../new_dashboard/state.mjs";
import { THEMES, paletteFromSettings } from "../new_dashboard/theme.js";

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

// ── every arm, as RAW HTML, for a real parser on the other side ──────────────────────────────
//
// NOTHING IS EXTRACTED HERE ANY MORE. Three rounds of review defeated pattern-based extraction in
// turn -- an HTML comment, then CDATA, then `<script type="text/plain">` -- and each fix was another
// forbidden marker. Python's `html.parser` has the container semantics that ends the class: the
// contents of a script, a style or a comment are never start tags.
//
// EVERY ARM IS EMITTED, not just the baseline. Review comment-wrapped the fields ONLY when
// `retention_days` held its shipped default, so a baseline-only grammar check passed while the arm
// the defaults comparison was drawn from was entirely comments.
let renderError = null;
const baseline = {};
controls.forEach((control, index) => { baseline[control.key] = pairFor(control, index)[0]; });

const arms = {};
try {
  arms.baseline = render({ ...baseline });
  if (process.argv[2]) arms.defaults = render(JSON.parse(process.argv[2]));
  controls.forEach((control, index) => {
    const [, other] = pairFor(control, index);
    arms[`changed:${control.key}`] = render({ ...baseline, [control.key]: other });
  });
} catch (error) {
  renderError = String(error && error.message ? error.message : error);
}

const supplied = {};
controls.forEach((control, index) => { supplied[control.key] = pairFor(control, index)[0]; });

const themeKey = process.argv[2] ? (JSON.parse(process.argv[2]).dashboard_theme || "default") : "default";

process.stdout.write(JSON.stringify({
  controls,
  optionDomains: { EFFORT_OPTS, PI_EFFORT_OPTS },
  supplied,
  arms,
  inheritedPalette: paletteFromSettings({}, themeKey),
  themeKey,
  renderError,
}));
