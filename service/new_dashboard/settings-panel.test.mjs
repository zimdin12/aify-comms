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
import { THEMES } from "./theme.js";
import {
  applyThemeChoice,
  refreshActiveTerminalTheme,
  selectSettingsTab,
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

import { HELP_TAB, SETTINGS_SCHEMA, adoptSettingsSchema, renderSettings, settingsItemFromDeclaration } from "./settings-panel.mjs";
import { esc } from "./util.js";
import { _settingsSig, renderSection } from "./render-memo.mjs";

// THE SHAPE `GET /settings/schema` SERVES (service/api_core/settings_spec.py `describe()`), one of each
// kind. The real declarations are checked end to end by the Python gates, which feed the served schema
// through this module; these tests pin the MAPPING from a declaration to a control.
const SERVED = {
  groups: ["Replies & messages", "Agent liveness", "Appearance", "Advanced", "Empty group"],
  settings: [
    { key: "reply_contracts_enabled", default: true, kind: "bool", group: "Replies & messages", label: "Remind agents", help: "", unit: "", min: null, max: null, choices: [], applies: "now" },
    { key: "agent_liveness_seconds", default: 90, kind: "int", group: "Agent liveness", label: "Offline after", help: "Three missed beats.", unit: "s", min: 30, max: 600, choices: [], applies: "now" },
    { key: "dashboard_theme", default: "default", kind: "choice", group: "Appearance", label: "Colour scheme", help: "", unit: "", min: null, max: null, choices: ["default", "ember"], applies: "now" },
    { key: "managed_claude_effort", default: "high", kind: "choice", group: "Advanced", label: "Effort", help: "", unit: "", min: null, max: null, choices: ["low", "high"], applies: "next worker start" },
    { key: "managed_via_wrapper", default: ["codex"], kind: "runtimes", group: "Advanced", label: "Wrapped", help: "", unit: "", min: null, max: null, choices: ["codex", "hermes"], applies: "next worker start" },
  ],
};
const allItems = () => SETTINGS_SCHEMA.flatMap((g) => g.items);
const item = (key) => allItems().find((i) => i.key === key);

test("a declaration becomes the control its kind promises, with its unit, bounds and help", () => {
  adoptSettingsSchema(SERVED);
  assert.equal(item("reply_contracts_enabled").type, "toggle");
  assert.deepEqual(
    [item("agent_liveness_seconds").type, item("agent_liveness_seconds").label, item("agent_liveness_seconds").min, item("agent_liveness_seconds").max],
    ["number", "Offline after (s)", 30, 600],
  );
  assert.equal(item("agent_liveness_seconds").hint, "Three missed beats.");
  assert.equal(item("dashboard_theme").type, "theme", "the theme keeps its preview tiles");
  assert.deepEqual([item("managed_claude_effort").type, item("managed_claude_effort").options], ["select", ["low", "high"]]);
  assert.match(item("managed_claude_effort").hint, /next starts/, "a setting that waits for a restart must say so");
  assert.equal(item("managed_via_wrapper").type, "csv");
});

test("groups keep the served order, and a group with nothing shown is not a tab", () => {
  adoptSettingsSchema(SERVED);
  assert.deepEqual(SETTINGS_SCHEMA.map((g) => g.group), ["Replies & messages", "Agent liveness", "Appearance", "Advanced"]);
  assert.equal(SETTINGS_SCHEMA.find((g) => g.group === "Appearance").appearance, true);
});

test("adopting a schema replaces the previous one rather than appending to it", () => {
  adoptSettingsSchema(SERVED);
  adoptSettingsSchema(SERVED);
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

test("SETTINGS PAINTS ITS TABS WHEN A LATE SCHEMA ARRIVES, though no setting changed (0.7.1 C2)", () => {
  // /settings/schema failed at boot and /settings did not, so the page read "Loading settings…". The
  // retry adopted the schema, but the section's memo keyed on the settings VALUES alone, which the
  // retry refetched unchanged, so the repaint was skipped and the page stayed on "Loading" for good.
  // Driven through the memo and signature app.js's renderAll uses.
  adoptSettingsSchema({ groups: [], settings: [] });
  state.settings = { dashboard_theme: "default" };
  withSettingsDom({}, (els) => {
    renderSection("settings", _settingsSig(), renderSettings);
    assert.match(els["settings-form"].innerHTML, /Loading settings/, "CONTROL: no schema yet");
    adoptSettingsSchema(SERVED);
    state.settings = { dashboard_theme: "default" };
    renderSection("settings", _settingsSig(), renderSettings);
    assert.match(els["settings-form"].innerHTML, /data-settings-tab=/, "the adopted schema was never painted");
  });
});

test("renderSettings says it is loading until the schema has arrived", () => {
  adoptSettingsSchema({ groups: [], settings: [] });
  state.settings = {};
  withSettingsDom({}, (els) => {
    renderSettings();
    assert.match(els["settings-form"].innerHTML, /Loading settings/);
  });
});

test("renderSettings builds one tab per schema group, plus Help, and marks the active one", () => {
  adoptSettingsSchema(SERVED);
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

// --- applyThemeChoice --------------------------------------------------------------------------
//
// The theme-preset tile click. It was 13 lines inside app.js's 631-line delegated click handler, which no
// test can reach — app.js is imported by nothing. Moving it here is what makes the behaviour assertable,
// and the behaviour is worth asserting: it writes the operator's palette. If the preset lookup silently
// fell back, every tile would apply the SAME colours and the fallback would look like a working feature.

/** Build a DOM stub recording every value written, with `n` preset tiles. */
function themeDom(tileKeys = []) {
  const values = {};
  const els = {};
  const mk = (id) => (els[id] ??= { id, value: "", get _v() { return values[id]; } });
  const tiles = tileKeys.map((k) => ({
    dataset: { themeChoice: k },
    active: null,
    classList: { toggle(_cls, on) { this._owner.active = on; } },
  }));
  for (const t of tiles) t.classList._owner = t;

  return {
    values,
    tiles,
    doc: {
      title: "",
      // `previewAppearance` runs for real at the end of the body, and it reaches through `previewTheme`
      // into `applyTheme`, which writes CSS custom properties on body and documentElement. Stubbing those
      // rather than mocking `previewAppearance` keeps this an assertion about what a click ACTUALLY does.
      body: { dataset: {}, style: { setProperty() {}, removeProperty() {} } },
      documentElement: { dataset: {}, style: { setProperty() {}, removeProperty() {} } },
      getElementById: (id) => {
        const el = mk(id);
        return { ...el, set value(v) { values[id] = v; }, get value() { return values[id] ?? ""; } };
      },
      querySelector: () => null,
      querySelectorAll: (sel) => (sel.includes("theme-preview") ? tiles : []),
    },
  };
}

function withThemeDom(dom, run) {
  const had = "document" in globalThis;
  const hadGcs = "getComputedStyle" in globalThis;
  const prevDoc = globalThis.document;
  const prevGcs = globalThis.getComputedStyle;
  globalThis.document = dom.doc;
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  try {
    return run();
  } finally {
    if (had) globalThis.document = prevDoc; else delete globalThis.document;
    if (hadGcs) globalThis.getComputedStyle = prevGcs; else delete globalThis.getComputedStyle;
  }
}

test("applyThemeChoice writes the chosen preset's palette into the three colour inputs", () => {
  const key = Object.keys(THEMES).find((k) => k !== "default") ?? "default";
  const preset = THEMES[key];
  const dom = themeDom([key]);
  withThemeDom(dom, () => applyThemeChoice({ dataset: { themeChoice: key } }));

  assert.equal(dom.values["set-dashboard_theme"], key, "the select follows the tile");
  assert.equal(dom.values["set-dashboard_primary_color"], preset.accent);
  assert.equal(dom.values["set-dashboard_secondary_color"], preset.secondary);
  assert.equal(dom.values["set-dashboard_tertiary_color"], preset.tertiary);
});

test("AN UNKNOWN PRESET FALLS BACK TO `default` rather than writing undefined", () => {
  // `THEMES[key] || THEMES.default`. Without the fallback the three setColor calls would write undefined
  // into the colour inputs — which reads as "no colour chosen" rather than as an error, so a stale or
  // renamed preset key would degrade silently.
  const dom = themeDom(["no-such-theme"]);
  withThemeDom(dom, () => applyThemeChoice({ dataset: { themeChoice: "no-such-theme" } }));
  assert.equal(dom.values["set-dashboard_primary_color"], THEMES.default.accent);
  assert.notEqual(dom.values["set-dashboard_primary_color"], undefined);
});

test("exactly the matching tile is marked active, and the others are cleared", () => {
  // `toggle('active', tile.dataset.themeChoice === key)` — the toggle is passed an explicit boolean, so
  // non-matching tiles are actively CLEARED. A one-argument toggle would flip them instead, leaving two
  // tiles lit after a second click.
  const keys = Object.keys(THEMES).slice(0, 3);
  const chosen = keys[keys.length - 1];
  const dom = themeDom(keys);
  withThemeDom(dom, () => applyThemeChoice({ dataset: { themeChoice: chosen } }));
  assert.deepEqual(dom.tiles.map((t) => t.active), keys.map((k) => k === chosen));
});

test("it works with no tiles rendered at all", () => {
  // The settings panel may not be open. `querySelectorAll(...).forEach` over an empty list must not throw,
  // or clicking a preset from a non-settings page would break the handler for every branch after it.
  const dom = themeDom([]);
  assert.doesNotThrow(() => withThemeDom(dom, () => applyThemeChoice({ dataset: { themeChoice: "default" } })));
});

// --- selectSettingsTab -------------------------------------------------------------------------

/** Run with a storage stub that can be made to refuse, restoring `state.settingsTab`. */
function withSettingsTab({ refuse = false } = {}, run) {
  const saved = state.settingsTab;
  const hadLs = "localStorage" in globalThis;
  const prev = globalThis.localStorage;
  const store = new Map();
  globalThis.localStorage = {
    setItem: (k, v) => { if (refuse) throw new Error("private mode"); store.set(k, v); },
    getItem: (k) => store.get(k) ?? null,
  };
  const savedSettings = state.settings;
  state.settings = state.settings ?? {};
  try {
    // `selectSettingsTab` ends in a real `renderSettings()`, which needs a form to write into. Running it
    // for real is the point: the state assignment and the storage write both happen BEFORE it, so a test
    // that stubbed it out could not tell a working click from one that never re-renders.
    return withSettingsDom({}, () => run(store));
  } finally {
    state.settingsTab = saved;
    state.settings = savedSettings;
    if (hadLs) globalThis.localStorage = prev; else delete globalThis.localStorage;
  }
}

test("selectSettingsTab records the tab in state AND persists it", () => {
  // Persisting is what makes the settings page reopen where the operator left it. Dropping it is
  // invisible for the whole session.
  withSettingsTab({}, (store) => {
    selectSettingsTab({ dataset: { settingsTab: "appearance" } });
    assert.equal(state.settingsTab, "appearance");
    assert.equal(store.get("aifySettingsTab"), "appearance");
  });
});

test("A REFUSING STORAGE STILL SWITCHES THE TAB", () => {
  // `try { localStorage.setItem(...) } catch { /* ignore */ }` — in private mode the write throws. The
  // state assignment happens BEFORE it, and `renderSettings()` after, so an unguarded write would leave
  // the tab recorded but never rendered: a click that does nothing visible.
  withSettingsTab({ refuse: true }, () => {
    assert.doesNotThrow(() => selectSettingsTab({ dataset: { settingsTab: "general" } }));
    assert.equal(state.settingsTab, "general", "the switch survives the storage failure");
  });
});

test("settingsItemFromDeclaration maps each declared kind to the control that can edit it", () => {
  const item = (d) => settingsItemFromDeclaration({ help: "", applies: "", ...d });
  assert.equal(item({ key: "a", kind: "bool", label: "A" }).type, "toggle");
  assert.equal(item({ key: "dashboard_theme", kind: "choice", label: "T", choices: ["default"] }).type, "theme");
  const effort = item({ key: "e", kind: "choice", label: "E", choices: ["", "high"] });
  assert.deepEqual([effort.type, effort.options, effort.optionLabels], ["select", ["", "high"], { "": "default" }]);
  const minutes = item({ key: "m", kind: "int", label: "Idle", unit: "minutes", min: 0, max: 60 });
  assert.deepEqual([minutes.type, minutes.label, minutes.min, minutes.max], ["number", "Idle (minutes)", 0, 60]);
  assert.equal(item({ key: "r", kind: "runtimes", label: "R" }).type, "csv");
  assert.equal(item({ key: "c", kind: "color", label: "C" }).type, "color");
  assert.equal(item({ key: "w", kind: "int", label: "W", applies: "next worker start" }).hint,
    "Takes effect when a worker next starts.");
});

// --- switching tabs keeps unsaved edits (v0.7 C1) -------------------------------------------------

/** A settings form that already holds its rendered tabs and panels, the way a real page does. */
function renderedSettingsHost(groups) {
  const node = (dataset) => {
    const classes = new Set();
    return {
      dataset,
      classList: {
        toggle: (name, on) => { if (on) classes.add(name); else classes.delete(name); },
        contains: (name) => classes.has(name),
      },
    };
  };
  const tabs = [...groups, HELP_TAB].map((group) => node({ settingsTab: group }));
  const panels = groups.map((group) => node({ settingsPanel: group }));
  return {
    tabs,
    panels,
    innerHTML: "RENDERED PANELS, WITH THE OPERATOR'S UNSAVED VALUES IN THEIR INPUTS",
    contains: () => true,
    querySelector: (sel) => (sel.includes("data-settings-panel") ? panels[0] || null : null),
    querySelectorAll: (sel) => (sel.includes("data-settings-panel") ? panels : sel.includes("data-settings-tab") ? tabs : []),
  };
}

test("SWITCHING SETTINGS TABS KEEPS AN UNSAVED EDIT ON ANOTHER TAB", () => {
  // Every panel stays in the DOM so Save collects every field whatever tab is showing. A tab click
  // that REBUILDS the panels from `state.settings` throws away what the operator typed on the tab
  // they just left, and the Save that follows sends nothing for it with no sign anything was lost.
  adoptSettingsSchema(SERVED);
  const groups = SETTINGS_SCHEMA.map((g) => g.group);
  const host = renderedSettingsHost(groups);
  withSettingsTab({}, () => withSettingsDom({ host }, (els) => {
    const form = els["settings-form"];
    selectSettingsTab({ dataset: { settingsTab: groups[1] } });
    assert.equal(form.innerHTML, "RENDERED PANELS, WITH THE OPERATOR'S UNSAVED VALUES IN THEIR INPUTS",
      "a tab switch must not rebuild the panels, which would discard what was typed into them");
    assert.deepEqual(form.panels.map((p) => p.classList.contains("active")), groups.map((g) => g === groups[1]),
      "only the chosen panel is shown");
    assert.deepEqual(form.tabs.map((t) => t.classList.contains("active")), [...groups, HELP_TAB].map((g) => g === groups[1]),
      "only the chosen tab is marked");
  }));
});
