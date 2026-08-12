// Unit tests that IMPORT AND CALL the extracted settings-field renderer.
//
// This is the first test in the repo that executes a line of logic which used to live in `app.js`. That
// file cannot be imported at all — `import('./app.js')` throws `ReferenceError: location is not defined`
// because module-scope browser code runs on import — so its four existing tests read its SOURCE TEXT and
// assert on patterns. A source test cannot fail on wrong logic, only on changed text: it would pass
// unchanged if `settingsFieldHtml` returned the empty string for every toggle.
//
// So the bar here is representative + degenerate + escaping, per the proof packet:
//   representative — each field type produces the control it claims to
//   degenerate     — null/undefined/missing options, the class source tests structurally cannot see
//   escaping       — untrusted text in a label, key or value cannot break out of the attribute or element

import assert from "node:assert/strict";
import { test } from "node:test";

import { settingsFieldHtml } from "./settings-fields.mjs";

test("a toggle renders a checkbox bound to its setting key", () => {
  const html = settingsFieldHtml({ key: "notify", label: "Notify", type: "toggle" }, true);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /data-setting-key="notify"/);
  assert.match(html, /data-setting-type="toggle"/);
  assert.match(html, / checked/, "value true must render the box checked");
  const off = settingsFieldHtml({ key: "notify", label: "Notify", type: "toggle" }, false);
  assert.doesNotMatch(off, / checked/, "value false must not render checked");
});

test("a select renders one option per choice and marks the current value", () => {
  const html = settingsFieldHtml(
    { key: "mode", label: "Mode", type: "select", options: ["", "fast", "slow"] },
    "slow",
  );
  assert.equal((html.match(/<option /g) || []).length, 3);
  assert.match(html, /<option value="slow" selected>/);
  assert.match(html, />\(default\)</, "the empty option is labelled (default)");
});

test("a csv field joins an array and survives a bare string", () => {
  const fromArray = settingsFieldHtml({ key: "roots", label: "Roots", type: "csv" }, ["a", "b"]);
  assert.match(fromArray, /value="a, b"/);
  const fromString = settingsFieldHtml({ key: "roots", label: "Roots", type: "csv" }, "a, b");
  assert.match(fromString, /value="a, b"/);
});

test("a number field carries its bounds only when they are given", () => {
  const bounded = settingsFieldHtml({ key: "n", label: "N", type: "number", min: 1, max: 9 }, 5);
  assert.match(bounded, / min="1"/);
  assert.match(bounded, / max="9"/);
  const unbounded = settingsFieldHtml({ key: "n", label: "N", type: "number" }, 5);
  assert.doesNotMatch(unbounded, / min=/);
  assert.doesNotMatch(unbounded, / max=/);
});

test("a color field falls back to the palette when the value is not a hex triplet", () => {
  const good = settingsFieldHtml({ key: "dashboard_accent_color", label: "Accent", type: "color" }, "#aabbcc");
  assert.match(good, /value="#aabbcc"/);
  const bad = settingsFieldHtml({ key: "dashboard_accent_color", label: "Accent", type: "color" }, "red");
  assert.doesNotMatch(bad, /value="red"/, "a non-hex value must not reach the color input");
  assert.match(bad, /value="#[0-9a-fA-F]{6}"/, "it must fall back to a real hex colour");
});

test("a theme field embeds the preview tiles and marks the active one", () => {
  const html = settingsFieldHtml({ key: "dashboard_theme", label: "Theme", type: "theme" }, "default");
  assert.match(html, /theme-preview-grid/, "the private tile renderer must be reached through this root");
  assert.match(html, /data-theme-choice="default"/);
  assert.match(html, /class="theme-preview active"/, "the selected theme's tile is active");
});

test("an unknown theme value degrades to default rather than rendering an empty grid", () => {
  const html = settingsFieldHtml({ key: "dashboard_theme", label: "Theme", type: "theme" }, "nope");
  assert.match(html, /data-theme-choice="default"/);
  assert.match(html, /class="theme-preview active"/);
  assert.doesNotMatch(html, /data-theme-choice="nope"/);
});

test("DEGENERATE: null and undefined values render an empty control, not the words null or undefined", () => {
  for (const value of [null, undefined]) {
    const html = settingsFieldHtml({ key: "k", label: "L", type: "text" }, value);
    assert.match(html, /value=""/, `value ${String(value)} must render empty`);
    assert.doesNotMatch(html, /value="(null|undefined)"/);
  }
});

test("DEGENERATE: a select with no options list renders a select with no options", () => {
  const html = settingsFieldHtml({ key: "k", label: "L", type: "select" }, "");
  assert.match(html, /<select /);
  assert.equal((html.match(/<option /g) || []).length, 0);
});

test("DEGENERATE: an unknown field type falls through to a text input", () => {
  const html = settingsFieldHtml({ key: "k", label: "L", type: "not-a-type" }, "v");
  assert.match(html, /type="text"/);
  assert.match(html, /value="v"/);
});

test("DEGENERATE: a missing hint omits the hint span entirely", () => {
  const withHint = settingsFieldHtml({ key: "k", label: "L", type: "text", hint: "why" }, "");
  assert.match(withHint, /class="field-hint">why</);
  const without = settingsFieldHtml({ key: "k", label: "L", type: "text" }, "");
  assert.doesNotMatch(without, /field-hint/);
});

test("ESCAPING: an untrusted label cannot inject markup", () => {
  const html = settingsFieldHtml({ key: "k", label: '<img src=x onerror="boom()">', type: "text" }, "");
  assert.doesNotMatch(html, /<img /, "the label must not reach the page as a tag");
  assert.match(html, /&lt;img/);
});

test("ESCAPING: an untrusted value cannot break out of the value attribute", () => {
  const html = settingsFieldHtml({ key: "k", label: "L", type: "text" }, '" onfocus="boom()');
  // My first version of this asserted the SUBSTRING "onfocus=" was absent, and it failed against correct
  // code: the text appears inside the escaped value as `value="&quot; onfocus=&quot;boom()"`, which is
  // inert. Asserting the absence of a substring conflates "cannot execute" with "cannot appear". What
  // actually matters is that the quote is escaped, so the attribute never closes early.
  assert.match(html, /value="&quot; onfocus=&quot;boom\(\)"/);
  assert.doesNotMatch(html, /value="" onfocus=/, "the value attribute must not be closed by its content");
});

test("PINNED DEFECT: item.key is NOT escaped into the id/for attributes", () => {
  // FOUND BY THIS TEST, on the first executable assertion ever run against a line of app.js.
  //
  // `settingsFieldHtml` builds `const id = `set-${item.key}`` and interpolates it into `for="${id}"` and
  // `id="${id}"` with NO esc(), while the neighbouring `data-setting-key="${esc(item.key)}"` IS escaped.
  // So a key containing a quote closes the attribute and injects arbitrary attributes into the label and
  // the input.
  //
  // NOT EXPLOITABLE TODAY, and that is why this pins rather than fixes: every `item.key` comes from
  // `SETTINGS_SCHEMA`, a hardcoded const array of developer-authored literals in app.js. There is no path
  // from user or agent input to a setting key. It is latent, and it becomes real the day the schema grows
  // a key from a runtime source.
  //
  // v0.5.x is structural-only with an empty behaviour changelog, so fixing it here would be exactly the
  // "while I'm in there" change the series forbids. Pinned as CURRENT behaviour, reported for its own
  // behaviour tag — the same treatment `validate_name`'s trailing-newline acceptance got.
  const html = settingsFieldHtml({ key: '" data-evil="1', label: "L", type: "text" }, "");
  assert.match(html, /data-evil="1"/, "unescaped id injection is the CURRENT behaviour being pinned");
  assert.match(html, /data-setting-key="&quot; data-evil=&quot;1"/, "the data attribute IS escaped");
});

test("ESCAPING: a hint is escaped as well as the label", () => {
  const html = settingsFieldHtml({ key: "k", label: "L", type: "text", hint: "<b>bold</b>" }, "");
  assert.doesNotMatch(html, /<b>bold<\/b>/);
  assert.match(html, /&lt;b&gt;/);
});
