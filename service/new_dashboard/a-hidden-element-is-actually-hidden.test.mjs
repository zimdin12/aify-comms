// An element rendered with `hidden` must actually be hidden.
//
// THE DEFECT, shipped 2026-09-05 and caught the same day. The console find bar is rendered as
// `<div class="console-find" hidden>` and its rule sets `display: flex`. An AUTHOR-origin `display`
// beats the user-agent's `[hidden] { display: none }` regardless of specificity, so the bar was open
// on every live console from the first render: an empty search box, a blank counter and three
// buttons nobody asked for, permanently above the terminal. Ctrl+Shift+F appeared to do nothing but
// move the caret, and Escape and ✕ appeared to do nothing at all — the logic toggled `hidden`
// correctly and the paint ignored it. It also stole ~30px, so every console fitted one row short.
//
// THE REPO ALREADY KNEW. Six selectors in `styles.css` carry a `[hidden] { display: none }`
// companion, and one of them says why in a trailing comment: "the [hidden] attr must win over
// display:flex". Six people got it right and the seventh did not, which is the definition of a rule
// that should be a gate rather than a habit.
//
// DERIVED FROM THE STYLESHEET AND THE MARKUP TOGETHER. It fires only on the INTERSECTION — a class
// that some template renders with a bare `hidden` attribute, AND whose rule sets `display`. Either
// alone is fine and common: `.console-await-pill` is rendered `hidden` and sets no display, so its
// `hidden` works; hundreds of rules set `display` on elements nobody hides.

import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const HERE = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SKIP_DIRS = new Set(['vendor', 'fixtures', 'node_modules']);

function templateFiles(dir = HERE, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(entry)) templateFiles(full, out);
      continue;
    }
    if (entry.includes('.test.')) continue;
    if (/\.(mjs|js|html)$/.test(entry)) out.push(full);
  }
  return out;
}

/** Classes on an element that also carries a bare `hidden` attribute. */
export function classesRenderedHidden(markup) {
  const found = new Set();
  // A tag carrying `hidden` with no `=` after it: the boolean attribute, not `hidden="${…}"`.
  for (const tag of markup.matchAll(/<(\w+)\s[^>]*\bhidden(?![=\w-])[^>]*>/g)) {
    const cls = /\bclass="([^"$]*)"/.exec(tag[0]);
    if (!cls) continue;
    for (const name of cls[1].split(/\s+/).filter(Boolean)) found.add(name);
  }
  return found;
}

/** Classes whose own rule sets `display`, and those that also have a `[hidden]` companion. */
export function displayRules(css) {
  const sets = new Set();
  const guarded = new Set();
  for (const rule of css.matchAll(/^\s*\.([a-z0-9_-]+)(\[hidden\])?\s*\{([^}]*)\}/gim)) {
    const [, name, hiddenGuard, body] = rule;
    if (!/(^|[;{\s])display\s*:/.test(body)) continue;
    if (hiddenGuard) guarded.add(name);
    else sets.add(name);
  }
  return { sets, guarded };
}

const CSS = readFileSync(join(HERE, 'styles.css'), 'utf8');
const HIDDEN_CLASSES = new Set(templateFiles().flatMap((f) => [...classesRenderedHidden(readFileSync(f, 'utf8'))]));
const { sets, guarded } = displayRules(CSS);

test('POSITIVE CONTROL: both halves of the scan see what is unmistakably there', () => {
  // Either half returning nothing would make the intersection empty and the gate vacuous.
  assert.ok(HIDDEN_CLASSES.size > 5, `only ${HIDDEN_CLASSES.size} classes are rendered hidden`);
  assert.ok(sets.size > 20, `only ${sets.size} classes set display`);
  assert.ok(guarded.has('composer'), 'the scan missed the [hidden] companion the stylesheet documents');
  assert.ok(HIDDEN_CLASSES.has('console-find'), 'the scan missed a class rendered with a bare hidden attribute');
});

test('NEGATIVE CONTROL: the scan tells the three cases apart', () => {
  assert.deepEqual([...classesRenderedHidden('<div class="a" hidden>')], ['a']);
  // `hidden="${…}"` is a bound property, not the boolean attribute this gate is about.
  assert.deepEqual([...classesRenderedHidden('<div class="b" hidden="${x}">')], []);
  assert.deepEqual([...classesRenderedHidden('<div class="c">')], []);
  const probe = displayRules('.x { display: flex; }\n.y[hidden] { display: none; }\n.z { color: red; }');
  assert.deepEqual([...probe.sets], ['x'], 'a rule that sets no display must not be counted');
  assert.deepEqual([...probe.guarded], ['y']);
});

test('every class rendered with `hidden` that sets a display has a [hidden] companion', () => {
  const unguarded = [...HIDDEN_CLASSES].filter((name) => sets.has(name) && !guarded.has(name)).sort();
  assert.deepEqual(unguarded, [],
    'these classes are rendered with a bare `hidden` attribute AND set `display` in their own rule. '
    + 'An author-origin display beats the user-agent [hidden] rule, so the element is ALWAYS VISIBLE '
    + 'and every toggle of `.hidden` appears to do nothing. Add `.<class>[hidden] { display: none; }`, '
    + 'the way six other rules in this stylesheet already do.');
});
