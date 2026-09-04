// A control that shows only a glyph, or no label at all, must SAY what it does.
//
// B2's design pass, and the half of it that is measurable rather than a matter of taste. A button
// reading `↑` is announced by a screen reader as "up arrow, button", and a row checkbox with no name
// is announced as "checkbox" in a list of forty. `title` is a last-resort accessible name — some
// assistive tech ignores it and it never appears on touch — so these get an `aria-label` and the
// tooltip stays as the sighted hint.
//
// FOUR GLYPH BUTTONS WERE FOUND when this was first run, and THREE OF THEM WERE AN HOUR OLD: the
// console find bar's previous/next/close controls, shipped in the same session as this gate. That is
// the argument for deriving the population rather than auditing once — the audit was right on the day
// and the defect arrived the same afternoon.
//
// THE POPULATION IS DERIVED FROM THE MARKUP, never listed. This repo has already had a hand-kept
// accessibility list go stale exactly this way: `keyboard-shortcuts.mjs` enumerated the `role=button`
// spans it covered, a third shipped without anyone comparing the lists, and the fix was to derive it.

import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const HERE = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SKIP_DIRS = new Set(['vendor', 'fixtures', 'node_modules']);

function sourceFiles(dir = HERE, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(entry)) sourceFiles(full, out);
      continue;
    }
    if (entry.includes('.test.')) continue;
    if (/\.(mjs|js|html)$/.test(entry)) out.push(full);
  }
  return out;
}

/**
 * Markup with COMMENTS REMOVED.
 *
 * The first census run here reported seven bare form controls and TWO OF THEM WERE PROSE — a
 * `<select>` and an `<input type="color">` written inside explanatory comments about them. A gate
 * that reads commentary as markup fails on a sentence somebody wrote, which is the least explicable
 * red there is.
 */
export function withoutComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

/**
 * Buttons whose label is a literal glyph and nothing else.
 *
 * A button is ICON-ONLY when, after removing nested tags, its content carries no letter or digit AND
 * no `${…}` hole — the hole is what makes a label a runtime value this scan cannot see. Most of this
 * dashboard's buttons render `${esc(label)}`, so excluding them is what keeps the gate about icons.
 */
export function iconOnlyButtons(text, file) {
  const found = [];
  for (const match of withoutComments(text).matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)) {
    const [, attrs, inner] = match;
    const withoutTags = inner.replace(/<[^>]*>/g, '');
    if (withoutTags.includes('${')) continue;              // labelled at runtime
    if (/[0-9A-Za-z]/.test(withoutTags)) continue;         // has a real word
    if (!withoutTags.trim()) continue;                     // empty is a different defect
    found.push({ file, glyph: withoutTags.trim(), named: /aria-label|aria-labelledby/.test(attrs) });
  }
  return found;
}

/**
 * Form controls with no accessible name at all.
 *
 * THREE WAYS TO BE LABELLED, and missing one makes the number wrong by a lot. The first version of
 * this census checked `aria-label` and `<label for=…>` only, and reported 41 of 58 controls
 * unlabelled. THIRTY-FOUR of those were WRAPPED in a `<label>`, which is a perfectly good implicit
 * label — acting on that number would have meant "fixing" 34 correct controls. The real figure was
 * five.
 *
 * A PLACEHOLDER IS NOT A LABEL and is deliberately not counted: it vanishes the moment the operator
 * types, so the one moment they might need reminding what the field is, is the one moment it is gone.
 */
export function unlabelledControls(text, file) {
  const src = withoutComments(text);
  const labelledFor = new Set([...src.matchAll(/<label[^>]*\bfor="([^"]+)"/g)].map((m) => m[1]));
  const wrapped = new Set();
  for (const block of src.matchAll(/<label\b[^>]*>([\s\S]*?)<\/label>/g)) {
    const innerAt = block.index + block[0].indexOf(block[1]);
    for (const inner of block[1].matchAll(/<(input|select|textarea)\b/g)) {
      wrapped.add(innerAt + inner.index);
    }
  }
  const out = [];
  for (const m of src.matchAll(/<(input|select|textarea)\b([^>]*)>/g)) {
    const [, tag, attrs] = m;
    if (/type="(hidden|submit|button)"/.test(attrs)) continue;
    if (/aria-label|aria-labelledby/.test(attrs)) continue;
    const id = /\bid="([^"]+)"/.exec(attrs);
    if (id && labelledFor.has(id[1])) continue;
    if (wrapped.has(m.index)) continue;
    out.push({ file, tag, id: id ? id[1] : '(none)' });
  }
  return out;
}

const FILES = sourceFiles();
const GLYPH_BUTTONS = FILES.flatMap((f) => iconOnlyButtons(readFileSync(f, 'utf8'), f));
const BARE_CONTROLS = FILES.flatMap((f) => unlabelledControls(readFileSync(f, 'utf8'), f));

const shortName = (path) => path.split(/[\\/]/).pop();

test('POSITIVE CONTROL: the scans find what is unmistakably there', () => {
  // A scan that matched nothing would make every assertion below vacuous — an empty set passes an
  // "every" check silently, which is this repo's most-repeated false green.
  const button = iconOnlyButtons('<button class="x" title="t">✕</button>', 'probe');
  assert.equal(button.length, 1);
  assert.equal(button[0].named, false);
  assert.equal(unlabelledControls('<input type="text">', 'probe').length, 1);
  assert.ok(GLYPH_BUTTONS.length > 0, 'the real scan found no glyph buttons at all, so it reads nothing');
  assert.ok(FILES.length > 40, `the file walk found only ${FILES.length} files`);
});

test('NEGATIVE CONTROL: a control labelled any of the three ways is not reported', () => {
  // Each of these was a real error in an earlier version of one of these scans.
  assert.deepEqual(iconOnlyButtons('<button>${esc(label)}</button>', 'p'), [], 'labelled at runtime');
  assert.deepEqual(iconOnlyButtons('<button>Refresh</button>', 'p'), [], 'has a real word');
  assert.equal(unlabelledControls('<label>Name <input></label>', 'p').length, 0, 'WRAPPED in a label');
  assert.equal(unlabelledControls('<label for="a">N</label><input id="a">', 'p').length, 0, 'label[for]');
  assert.equal(unlabelledControls('<input aria-label="N">', 'p').length, 0, 'aria-label');
  assert.equal(unlabelledControls('// a comment mentioning <select>', 'p').length, 0, 'prose is not markup');
  // And a NAMED glyph button is still FOUND, just not faulted — the scan must not stop seeing it.
  const named = iconOnlyButtons('<button aria-label="Close">✕</button>', 'p');
  assert.equal(named.length, 1);
  assert.equal(named[0].named, true);
});

test('a PLACEHOLDER is not accepted as a label', () => {
  // Its own test, because this is the exclusion someone will be tempted to relax: every bare input in
  // this dashboard has a placeholder, so counting them would empty the gate in one edit.
  assert.equal(unlabelledControls('<input type="text" placeholder="Search runs">', 'p').length, 1,
    'a placeholder was accepted as a label — it disappears exactly when it is needed');
});

test('every icon-only button carries an accessible name', () => {
  const unnamed = GLYPH_BUTTONS.filter((b) => !b.named)
    .map((b) => `${shortName(b.file)}: ${JSON.stringify(b.glyph)}`);
  assert.deepEqual(unnamed, [],
    'these buttons show a glyph and nothing else, so assistive tech announces the glyph. Give each an '
    + 'aria-label saying what it does; keep the title as the sighted tooltip.');
});

test('every form control carries an accessible name', () => {
  const bare = BARE_CONTROLS.map((c) => `${shortName(c.file)}: <${c.tag} id=${c.id}>`);
  assert.deepEqual(bare, [],
    'these controls announce only their type to a screen reader. Give each an aria-label, or wrap it '
    + 'in a <label>; a placeholder does not count.');
});
