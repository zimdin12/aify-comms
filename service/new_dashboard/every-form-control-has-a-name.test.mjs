// A form control must say what it is, to something that cannot see it.
//
// THIS FILE USED TO CARRY A SECOND BUTTON GATE AND THAT HALF IS RETIRED. It duplicated
// `icon-buttons-carry-a-label.test.mjs`, which has derived the same "every button, no exceptions"
// population since 2026-08-26 and carries the incident that produced it — and the two DISAGREED, one
// accepting `title` as a name and the other refusing it. Two derived gates over one population with
// contradictory rules is worse than either: a glyph button with a tooltip went red in one and green
// in the other, and the cheapest way out of that is to relax the strict one, at which point it is a
// silent duplicate. The stricter rule moved into that file, next to the history that justifies it,
// and what remains here is the half nothing else covers.
//
// FIVE CONTROLS HAD NO NAME AT ALL when this first ran: three per-row checkboxes announced as
// "checkbox" in lists of dozens, a channel member select, and the codex thread input, which had only
// a PLACEHOLDER. A placeholder is not a label — it disappears the moment the operator types, so the
// one moment they might need reminding what the field is, is the one moment it is gone.
//
// TWO OF MY OWN INSTRUMENTS WERE WRONG BEFORE THIS SETTLED, both in the direction that causes damage,
// and the tests below pin what corrected them:
//   * Checking only `aria-label` and `<label for=…>` reported 41 of 58 controls unlabelled.
//     THIRTY-FOUR were WRAPPED in a `<label>`, which is a perfectly good implicit label — acting on
//     that number meant "fixing" 34 correct controls. The real figure was five.
//   * It then said seven, and TWO WERE PROSE: a `<select>` and an `<input type="color">` written
//     inside comments ABOUT them. A gate that fails on a sentence somebody wrote is the least
//     explicable red there is.

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

/** Markup with comments removed, so commentary about a control is not read as one. */
export function withoutComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

/**
 * Form controls with no accessible name at all.
 *
 * THREE WAYS TO BE LABELLED, and missing one makes the number wrong by a factor of six.
 *
 * WRAPPED CONTROLS ARE MASKED, NOT OFFSET-MATCHED. The first version located them by computing
 * `block.index + block[0].indexOf(block[1])`, which finds the wrong place whenever a label's inner
 * text also occurs inside its own opening tag (`<label class="x">x</label>`) — a correctly wrapped
 * control would then be reported as bare. Blanking each label block to spaces of the same length
 * removes the arithmetic entirely: whatever is still visible afterwards is genuinely not wrapped.
 */
export function unlabelledControls(text, file) {
  const src = withoutComments(text);
  const labelledFor = new Set([...src.matchAll(/<label[^>]*\bfor="([^"]+)"/g)].map((m) => m[1]));
  const masked = src.replace(/<label\b[^>]*>[\s\S]*?<\/label>/g, (block) => ' '.repeat(block.length));

  const out = [];
  for (const m of masked.matchAll(/<(input|select|textarea)\b([^>]*)>/g)) {
    const [, tag, attrs] = m;
    if (/type="(hidden|submit|button)"/.test(attrs)) continue;
    // NON-EMPTY. `aria-label=""` gives no name and suppresses the content fallback, so accepting it
    // would leave the gate unable to fail on the likeliest regression.
    if (/\baria-label(?:ledby)?\s*=\s*"[^"]+"/.test(attrs)) continue;
    const id = /\bid="([^"]+)"/.exec(attrs);
    if (id && labelledFor.has(id[1])) continue;
    out.push({ file, tag, id: id ? id[1] : '(none)' });
  }
  return out;
}

const FILES = sourceFiles();
const BARE = FILES.flatMap((f) => unlabelledControls(readFileSync(f, 'utf8'), f));

test('POSITIVE CONTROL: the scan reaches real files and can say YES', () => {
  // An empty corpus passes an "every" check silently — this repo's most-repeated false green.
  assert.ok(FILES.length > 40, `the file walk found only ${FILES.length} files`);
  assert.equal(unlabelledControls('<input type="text">', 'probe').length, 1);
});

test('NEGATIVE CONTROL: all three ways of labelling are accepted, and prose is not markup', () => {
  // Each of these was a real error in an earlier version of this scan.
  assert.equal(unlabelledControls('<label>Name <input></label>', 'p').length, 0, 'WRAPPED in a label');
  assert.equal(unlabelledControls('<label for="a">N</label><input id="a">', 'p').length, 0, 'label[for]');
  assert.equal(unlabelledControls('<input aria-label="N">', 'p').length, 0, 'aria-label');
  assert.equal(unlabelledControls('// a comment mentioning <select>', 'p').length, 0, 'prose');
  assert.equal(unlabelledControls('/* <input type="color"> */', 'p').length, 0, 'block-comment prose');
});

test('a WRAPPED control is found even when the label repeats its text in an attribute', () => {
  // The offset bug: `indexOf(inner)` found the occurrence inside the opening tag, so the control was
  // located at the wrong position and a correctly labelled input was reported as bare.
  assert.equal(unlabelledControls('<label class="name">name <input></label>', 'p').length, 0,
    'a label whose inner text also appears in its own tag mislocated the control');
});

test('a PLACEHOLDER is not accepted as a label', () => {
  // Its own test, because this is the exclusion someone will be tempted to relax: nearly every bare
  // input in this dashboard has a placeholder, so counting them would empty the gate in one edit.
  assert.equal(unlabelledControls('<input type="text" placeholder="Search runs">', 'p').length, 1,
    'a placeholder was accepted as a label — it disappears exactly when it is needed');
});

test('an EMPTY aria-label is not a name', () => {
  assert.equal(unlabelledControls('<input aria-label="">', 'p').length, 1,
    'an empty aria-label supplies no name AND suppresses the content fallback');
});

test('every form control carries an accessible name', () => {
  const bare = BARE.map((c) => `${c.file.split(/[\\/]/).pop()}: <${c.tag} id=${c.id}>`);
  assert.deepEqual(bare, [],
    'these controls announce only their type to a screen reader. Give each an aria-label, or wrap it '
    + 'in a <label>; a placeholder does not count.');
});
