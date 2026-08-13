// Real tests for the terminal theming the Settings appearance controls drive.
//
// These replace regex assertions in `app.test.mjs` that matched the SOURCE TEXT of
// `terminalThemeFromDashboard` and `refreshActiveTerminalTheme`. Those could only prove the lines had been
// written; the poll-safety gate below is a behaviour a regex can assert the existence of and never the
// correctness of. Both functions became testable the moment they left app.js — that is most of the point
// of moving them.
//
// SEALING. `getComputedStyle` and `document` do not exist in Node and are installed per test, then removed,
// so nothing here can pass by accident on a host that provides them. `state` is a shared singleton, so the
// fields used are rebuilt each time.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  refreshActiveTerminalTheme,
  terminalAccentColor,
  terminalThemeFromDashboard,
} from "./settings-panel.mjs";

function withDom({ accentVar = "", theme = "default" }, run) {
  const hadDoc = "document" in globalThis;
  const hadGcs = "getComputedStyle" in globalThis;
  globalThis.document = { body: { dataset: { theme } } };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => accentVar });
  try {
    return run();
  } finally {
    if (!hadDoc) delete globalThis.document;
    if (!hadGcs) delete globalThis.getComputedStyle;
  }
}

test("the accent comes from the live CSS variable when it is a real hex colour", () => {
  assert.equal(withDom({ accentVar: "  #ff8800  " }, terminalAccentColor), "#ff8800",
    "the value is trimmed — CSS custom properties carry their leading space");
});

test("a junk or missing CSS variable falls back to the theme preset, never to junk", () => {
  // The guard is a strict 6-digit hex test. Before it, a half-written variable could reach xterm as a
  // colour and throw inside the terminal rather than here.
  for (const junk of ["", "   ", "not-a-colour", "#fff", "#gggggg", "rgb(1,2,3)"]) {
    const got = withDom({ accentVar: junk }, terminalAccentColor);
    assert.match(got, /^#[0-9a-fA-F]{6}$/, `"${junk}" must fall back to a valid colour, got ${got}`);
  }
});

test("an unknown theme name still yields a colour", () => {
  const got = withDom({ accentVar: "", theme: "no-such-theme" }, terminalAccentColor);
  assert.match(got, /^#[0-9a-fA-F]{6}$/);
});

test("the terminal theme is DERIVED from the accent and stays dark for TUI legibility", () => {
  const theme = withDom({ accentVar: "#51c5b0" }, terminalThemeFromDashboard);
  assert.equal(theme.cursor, "#51c5b0", "the cursor takes the dashboard accent");
  assert.equal(theme.selectionBackground, "#51c5b055", "selection is the accent at ~33% alpha");
  assert.equal(theme.background, "#0b0e13", "background stays dark regardless of accent — TUIs assume it");
  assert.equal(theme.cursorAccent, "#0b0e13");
});

test("refreshActiveTerminalTheme NO-OPS when the accent has not changed", () => {
  // THE POLL-SAFETY GATE, and the reason this file exists. It runs on the ~15s refresh; without the guard
  // an unconditional atlas clear would flicker an open console every tick. The old assertion for this was
  // a regex matching the literal `if (entry._themeAccent === accent) return;`.
  let cleared = 0;
  const entry = { term: { options: {} }, webgl: { clearTextureAtlas: () => { cleared += 1; } } };
  state.activeXterm = entry;

  withDom({ accentVar: "#51c5b0" }, refreshActiveTerminalTheme);
  assert.equal(cleared, 1, "the first call must apply the theme and clear the atlas");
  const applied = entry.term.options.theme;

  withDom({ accentVar: "#51c5b0" }, refreshActiveTerminalTheme);
  assert.equal(cleared, 1, "an unchanged accent must not clear the atlas again");
  assert.equal(entry.term.options.theme, applied, "…nor re-assign the theme object");

  withDom({ accentVar: "#ff0000" }, refreshActiveTerminalTheme);
  assert.equal(cleared, 2, "a CHANGED accent must clear the atlas");
  assert.equal(entry.term.options.theme.cursor, "#ff0000");
});

test("refreshActiveTerminalTheme survives a terminal with no WebGL addon", () => {
  // The webgl addon is optional — xterm falls back to canvas. The optional chaining is load-bearing.
  state.activeXterm = { term: { options: {} } };
  withDom({ accentVar: "#123456" }, refreshActiveTerminalTheme);
  assert.equal(state.activeXterm.term.options.theme.cursor, "#123456");
});

test("refreshActiveTerminalTheme is a no-op when no console is open", () => {
  for (const empty of [null, undefined, {}, { term: null }]) {
    state.activeXterm = empty;
    withDom({ accentVar: "#123456" }, refreshActiveTerminalTheme);
  }
});

// ---------------------------------------------------------------------------------------------------
// The settings schema and the settings render, tested by CALLING them.
//
// These replace source-text assertions in `service/tests/test_new_dashboard_session_mode_switch.py`,
// which grepped app.js for "SETTINGS_SCHEMA", "key: 'manual_session_mode'", "function renderSettings()"
// and "SETTINGS_SCHEMA.map". Every one of those broke when the code moved here, though nothing about the
// behaviour changed — and none of them could ever have failed on a schema that rendered wrongly.

import { HELP_TAB, SETTINGS_SCHEMA, SETTINGS_TAB_LABELS, renderSettings } from "./settings-panel.mjs";
import { esc } from "./util.js";

const allItems = () => SETTINGS_SCHEMA.flatMap((g) => g.items);

test("the schema still exposes manual_session_mode", () => {
  // It is the resident<->managed switch chip toggle. It was the ONLY setting once, and is now one knob
  // among many; losing it silently would remove the operator's only control over those chips.
  const item = allItems().find((i) => i.key === "manual_session_mode");
  assert.ok(item, "manual_session_mode must be present in the schema");
  assert.equal(item.type, "toggle");
});

test("every schema group has a tab label and every item has a key and type", () => {
  for (const group of SETTINGS_SCHEMA) {
    assert.ok(SETTINGS_TAB_LABELS[group.group], `group "${group.group}" has no tab label`);
    assert.ok(Array.isArray(group.items) && group.items.length, `group "${group.group}" has no items`);
    for (const item of group.items) {
      assert.match(item.key, /^\w+$/, `item in "${group.group}" has a bad key: ${item.key}`);
      assert.ok(item.type, `item ${item.key} has no type`);
    }
  }
  const keys = allItems().map((i) => i.key);
  assert.equal(new Set(keys).size, keys.length, "setting keys must be unique across groups");
});

function withSettingsDom({ activeElement = null, host = {} } = {}, run) {
  const hadDoc = "document" in globalThis;
  const els = { "settings-form": { innerHTML: "", contains: () => true, ...host } };
  globalThis.document = {
    activeElement,
    body: { dataset: {} },
    getElementById: (id) => els[id] || null,
  };
  try {
    run(els);
  } finally {
    if (!hadDoc) delete globalThis.document;
  }
}

test("renderSettings builds one tab per schema group, plus Help, and marks the active one", () => {
  state.settings = {};
  state.settingsTab = SETTINGS_SCHEMA[0].group;
  withSettingsDom({}, (els) => {
    renderSettings();
    const html = els["settings-form"].innerHTML;
    for (const group of SETTINGS_SCHEMA) {
      // ESCAPED, not raw: a group named "Status & lifecycle" reaches the attribute as "Status &amp;
      // lifecycle". Asserting the raw name failed here — the test was wrong, the render was right, and an
      // attribute that carried an unescaped `&` would be the actual bug worth catching.
      const attr = esc(group.group);
      assert.ok(html.includes(`data-settings-tab="${attr}"`), `missing tab for ${group.group}`);
      assert.ok(html.includes(`data-settings-panel="${attr}"`), `missing panel for ${group.group}`);
    }
    assert.ok(html.includes(`data-settings-tab="${HELP_TAB}"`), "the Help tab must be rendered");
    assert.ok(html.includes(`settings-tab active`), "the active tab must be marked");
  });
});

test("renderSettings does NOT rebuild while an input is focused, but DOES while a tab is", () => {
  // A real bug, fixed 2026-06-29. The 15s poll re-renders settings; rebuilding while the operator is
  // typing wipes the edit. The first guard checked for ANY focused descendant — but the tab buttons live
  // inside the same host, so clicking a tab focused it, returned early, and the panel never switched.
  // The guard is therefore scoped to editable controls. No regex over source text can distinguish those
  // two cases; this is exactly the behaviour the old assertions could not reach.
  state.settings = {};
  const editable = { matches: (sel) => sel.includes("input") };
  withSettingsDom({ activeElement: editable }, (els) => {
    els["settings-form"].innerHTML = "UNTOUCHED";
    renderSettings();
    assert.equal(els["settings-form"].innerHTML, "UNTOUCHED", "an in-progress field edit must survive");
  });

  const tabButton = { matches: () => false };
  withSettingsDom({ activeElement: tabButton }, (els) => {
    els["settings-form"].innerHTML = "UNTOUCHED";
    renderSettings();
    assert.notEqual(els["settings-form"].innerHTML, "UNTOUCHED", "a focused TAB must not block the switch");
  });
});

test("renderSettings is a no-op when the settings host is absent", () => {
  // It runs on the poll from every page, not only the Settings page.
  const hadDoc = "document" in globalThis;
  globalThis.document = { activeElement: null, body: { dataset: {} }, getElementById: () => null };
  try {
    renderSettings();
  } finally {
    if (!hadDoc) delete globalThis.document;
  }
});
