// A `role="button"` span has NO native key handling, so every one needs an explicit Enter/Space path.
//
// WHY THIS IS DERIVED AND NOT A LIST. `keyboard-shortcuts.mjs` handled two of them --
// `[data-status-why]` and `[data-fav-toggle]` -- and its own header comment enumerated exactly those
// two. A third, the triage tiles at `summary-tiles.mjs` (`[data-diag-jump]`), shipped as
// `role="button" tabindex="0"` with a click handler and no key handler at all. Nothing compared the
// two lists, because one of them was a sentence.
//
// That is this repo's own rule arriving as a bug: "Derive allowed values, never list them. A list you
// must remember to update is a defect with a delay on it." So the population comes from the MARKUP --
// every element rendered with `role="button"` -- and the assertion is that the keyboard module names
// each one. A fourth cannot arrive unnoticed.
//
// WHAT IT COSTS TO GET WRONG, since it is invisible on screen: a keyboard-only operator tabs to the
// tile, the focus ring appears because `tabindex="0"` is honoured, the screen reader announces a
// button -- and pressing Enter does nothing. Everything looks right.
//
// SCOPE: this checks that the selector is NAMED in the keyboard module, not that the resulting
// behaviour matches the click path. Those are different questions and this is the one that was wrong.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** Source files that RENDER markup: this directory's modules, excluding tests. */
function sourceFiles() {
  return fs.readdirSync(HERE)
    .filter((name) => (name.endsWith(".mjs") || name.endsWith(".js")) && !name.includes(".test."))
    .sort();
}

/**
 * Strip `//` and block comments.
 *
 * NOT OPTIONAL HERE. The fix that prompted this test added a comment to `keyboard-shortcuts.mjs`
 * containing the words `role=button`, and this repo has already shipped a scanner that read its own
 * documentation and reported a field named `X`. A scanner that cannot tell markup from prose about
 * markup will confirm whatever the prose says.
 */
function withoutComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => line.replace(/(^|[^:])\/\/.*$/, "$1"))
    .join("\n");
}

/** Every rendered `role="button"` element, as {file, line, dataAttrs, focusable}. */
function roleButtonSites() {
  const sites = [];
  for (const name of sourceFiles()) {
    const source = withoutComments(fs.readFileSync(path.join(HERE, name), "utf8"));
    source.split("\n").forEach((line, index) => {
      if (!/role="button"/.test(line)) return;
      const dataAttrs = [...line.matchAll(/\bdata-([a-z][a-z0-9-]*)\s*=/g)].map((m) => `data-${m[1]}`);
      sites.push({
        file: name,
        line: index + 1,
        dataAttrs,
        focusable: /tabindex\s*=\s*"0"/.test(line),
      });
    });
  }
  return sites;
}

const KEYBOARD = withoutComments(
  fs.readFileSync(path.join(HERE, "keyboard-shortcuts.mjs"), "utf8"),
);

function keyboardNames(selector) {
  return KEYBOARD.includes(`[${selector}]`);
}

// -- controls on the instrument -------------------------------------------------------------------

test("the scan finds the role=button sites that are definitely there", () => {
  const sites = roleButtonSites();
  assert.ok(sites.length >= 3, `found ${sites.length} role=button sites -- the scan is broken, not the markup`);
  const files = new Set(sites.map((s) => s.file));
  assert.ok(files.has("summary-tiles.mjs"), "the scan missed the triage tiles, which is the site that was broken");
  assert.ok(files.has("status.js"), "the scan missed the status chip");
});

test("prose about role=button is not mistaken for markup", () => {
  // NEGATIVE CONTROL on the parser. `keyboard-shortcuts.mjs` now says `role=button` in a comment.
  const stripped = withoutComments('// a role="button" span\nconst x = 1;\n/* role="button" */');
  assert.ok(!stripped.includes('role="button"'), "comments survived stripping");
});

test("a selector the keyboard module does not name is reported as unhandled", () => {
  // NEGATIVE CONTROL on the lookup. A checker that answered true for anything proves nothing below.
  assert.equal(keyboardNames("data-no-such-control"), false);
});

test("and one it does name is reported as handled", () => {
  // POSITIVE CONTROL, paired with the above.
  assert.equal(keyboardNames("data-status-why"), true);
});

// -- the gate --------------------------------------------------------------------------------------

test("every focusable role=button element has an Enter/Space path", () => {
  const unhandled = roleButtonSites()
    .filter((site) => site.focusable)
    .filter((site) => site.dataAttrs.length > 0)
    .filter((site) => !site.dataAttrs.some(keyboardNames))
    .map((site) => `${site.file}:${site.line} (${site.dataAttrs.join(", ")})`);
  assert.deepEqual(
    unhandled, [],
    "these render as buttons and take focus, but pressing Enter does nothing:\n  "
    + unhandled.join("\n  ")
    + "\nAdd a branch to keyboard-shortcuts.mjs. Nothing on screen looks wrong when this is missing.",
  );
});

test("a focusable role=button element carries something to dispatch on", () => {
  // The gate above skips a site with no `data-` attribute, so this says out loud that none exists --
  // otherwise "no data attribute" would be a silent way to leave the gate.
  const anonymous = roleButtonSites()
    .filter((site) => site.focusable && site.dataAttrs.length === 0)
    .map((site) => `${site.file}:${site.line}`);
  assert.deepEqual(anonymous, [],
    `these are focusable buttons with no data-* hook, so the gate above cannot judge them: ${anonymous}`);
});
