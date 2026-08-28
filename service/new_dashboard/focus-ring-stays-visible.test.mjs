// The keyboard focus ring has to be visible on every palette the operator can choose.
//
// It was painted `2px solid var(--accent)`. `--accent` is a SURFACE token -- `applyTheme` writes
// whatever hex the colour picker accepts -- so the ring inherited the same defect the text colours
// had: judged against the root default, decided by the operator's choice. An accent near the panel
// colour measures 1.00 against every opaque surface, which is not a dim ring, it is no ring. Keyboard
// focus becomes untrackable and the page cannot be navigated without a mouse.
//
// WCAG 1.4.11 asks 3.0 for a non-text indicator. `--accent-text` is derived by MEASURING against the
// real surfaces, so it clears that by construction rather than by luck.
//
// This test judges the token the CSS actually names, read out of styles.css -- not a token named
// here. A test that hardcoded `--accent-text` would keep passing after somebody repointed the rule.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { contrastRatio, readableAccentText, THEMES } from "./theme.js";

const CSS = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

/** Every opaque surface a ring can be drawn against, read from :root rather than retyped. */
const SURFACES = Object.fromEntries(
  [...CSS.matchAll(/^\s*(--(?:bg|panel|panel-2|panel-3|chat-surface)):\s*(#[0-9a-f]{6});/gim)]
    .map((m) => [m[1], m[2]]),
);

/** WCAG 1.4.11, non-text contrast. */
const MIN_RING = 3;

/** Accents worth judging: every preset, plus the extremes the colour picker permits. */
const ACCENTS = {
  ...Object.fromEntries(Object.entries(THEMES).map(([k, v]) => [k, v.accent])),
  "custom: the panel colour itself": "#15191b",
  "custom: black": "#000000",
  "custom: white": "#ffffff",
};

/** The colour the focus rules actually name, whatever it is today.
 *
 * EVERY rule, not the first. Reading one was how `.diagnostic-check:focus-visible` -- a third copy of
 * the same mistake -- stayed unnoticed through a hand grep: the eye stops at the rule it went looking
 * for. If the rules ever disagree, that is itself the finding, so this refuses rather than picks. */
function ringTokenFromCss() {
  const tokens = [...CSS.matchAll(/:focus(?:-visible)?[^{]*\{[^}]*outline:\s*2px solid var\((--[a-z-]+)\)/gi)]
    .map((m) => m[1]);
  assert.ok(tokens.length >= 2, `only ${tokens.length} focus outline rule(s) found — the scan has drifted`);
  const distinct = [...new Set(tokens)];
  assert.equal(distinct.length, 1, `focus rules disagree about the ring colour: ${distinct.join(", ")}`);
  return distinct[0];
}

/** How the runtime resolves that token for a given accent. */
function resolve(token, accent) {
  if (token === "--accent") return accent;
  if (token === "--accent-text") return readableAccentText(accent);
  assert.fail(`the focus ring now uses ${token}, which this test does not know how to resolve`);
}

test("the surfaces and accents were actually found", () => {
  // The control. Two empty maps make every loop below vacuous, and a green vacuous suite is the
  // failure this repo keeps producing.
  assert.equal(Object.keys(SURFACES).length, 5, `found surfaces: ${JSON.stringify(SURFACES)}`);
  assert.ok(Object.keys(ACCENTS).length >= 10);
});

test("the focus ring clears 3:1 on every surface, for every accent the picker allows", () => {
  const token = ringTokenFromCss();
  const failures = [];
  for (const [name, accent] of Object.entries(ACCENTS)) {
    const ring = resolve(token, accent);
    for (const [surface, hex] of Object.entries(SURFACES)) {
      const ratio = contrastRatio(ring, hex);
      if (ratio < MIN_RING) failures.push(`${name} on ${surface}: ${ratio.toFixed(2)}`);
    }
  }
  assert.deepEqual(failures, [], `the keyboard focus ring is invisible in these combinations`);
});

test("var(--accent) is why this test exists — it fails the same check", () => {
  // The negative control, and the regression it guards. If this ever stops failing, the accent token
  // has changed meaning and the rule above is no longer proving anything.
  const failures = [];
  for (const [name, accent] of Object.entries(ACCENTS)) {
    for (const hex of Object.values(SURFACES)) {
      if (contrastRatio(accent, hex) < MIN_RING) failures.push(name);
    }
  }
  assert.ok(failures.length > 0,
    "a raw accent now passes everywhere, so this test can no longer tell the two tokens apart");
});

test("no focus rule paints its outline in a raw surface token", () => {
  // The sweep, so a NEW focus rule cannot reintroduce this one rule at a time. `.status-why-trigger`
  // was a second copy of the same mistake and was found only by looking for all of them.
  const offenders = [...CSS.matchAll(/:focus(?:-visible)?[^{]*\{[^}]*outline:\s*[^;}]*var\((--[a-z-]+)\)/gi)]
    .map((m) => m[1])
    .filter((token) => token === "--accent" || token === "--secondary" || token === "--tertiary");
  assert.deepEqual(offenders, [], "a focus outline is painted in a runtime surface token");
});
