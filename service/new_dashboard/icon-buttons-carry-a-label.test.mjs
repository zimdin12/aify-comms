// A control whose only content is a glyph must say what it does, in a form a screen reader can read.
//
// THE DEFECT. `chat.js` built the reply banner's cancel control as a bare glyph button with no
// `aria-label` and no `title`. A screen reader announces it as its character or as nothing at all, so
// the only way to learn what it does is to press it -- and what it does is discard the reply you were
// composing. Sighted users get the same deal on hover: no tooltip, because there was no title either.
//
// MEASURED 2026-08-26 across the 78 non-test dashboard sources: 164 `<button>` elements, of which
// exactly ONE was a literal glyph with no label. This is not a sweeping accessibility claim about the
// dashboard -- it is one rule, checked everywhere, with one violation found and fixed.
//
// WHY A DERIVED SCAN RATHER THAN A TEST FOR THAT BUTTON. Pinning `data-chat-reply-clear` would prove
// one string exists and would say nothing about the next glyph button somebody writes. The repo's own
// rule is to derive allowed values rather than list them; the derived form here is "every button, no
// exceptions", which is why this file scans instead of asserting.
//
// WHAT IS DELIBERATELY NOT FLAGGED: a button whose text is a template expression (`${...}`). Its label
// is decided at runtime and this scan cannot read it, so calling it unlabelled would be a guess. That
// makes this gate deliberately incomplete in one direction -- it can miss, it cannot cry wolf.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const DIR = fileURLToPath(new URL('.', import.meta.url));
const BUTTON = /<button\b[^>]*>([\s\S]*?)<\/button>/gi;

/** Every non-test dashboard source that can contain markup. */
function sources() {
  const out = [];
  for (const name of readdirSync(DIR)) {
    if (name === 'fixtures' || name === 'node_modules') continue;
    if (!/\.(mjs|js|html)$/.test(name)) continue;
    if (/\.test\.(mjs|js)$/.test(name)) continue;
    const path = join(DIR, name);
    if (statSync(path).isFile()) out.push([name, readFileSync(path, 'utf8')]);
  }
  return out;
}

/**
 * Buttons carrying no readable name. PURE over the text so the tests below can feed it a constructed
 * case -- a scan that can only be run against the real tree cannot be shown to say no.
 */
export function unlabelledButtons(text, file = '?') {
  const found = [];
  for (const match of text.matchAll(BUTTON)) {
    const whole = match[0];
    const head = whole.slice(0, whole.indexOf('>') + 1);
    const inner = match[1].replace(/<[^>]+>/g, '');
    if (inner.includes('${')) continue;                       // named at runtime; unreadable here
    if (/\b(aria-label|title)\s*=/i.test(head)) continue;      // named outright
    if (/[A-Za-z]{2,}/.test(inner)) continue;                  // its own text is the name
    found.push({ file, head, inner: inner.trim() });
  }
  return found;
}

test('the scan finds buttons at all', () => {
  // The control. Zero buttons satisfies "zero unlabelled buttons" while proving nothing, and that is
  // the exact shape of failure this repo keeps paying for.
  const total = sources().reduce((n, [, text]) => n + [...text.matchAll(BUTTON)].length, 0);
  assert.ok(total > 100, `only ${total} <button> elements found across the dashboard; the scan is broken`);
});

test('the scan can say a button IS unlabelled', () => {
  // The negative control. A predicate that never fires would pass the real assertion forever.
  const flagged = unlabelledButtons('<button class="ghost" type="button" data-x>✕</button>');
  assert.equal(flagged.length, 1, 'a bare glyph button was not flagged');
});

test('the scan does not flag a button that names itself three ways', () => {
  assert.deepEqual(unlabelledButtons('<button>Cancel reply</button>'), [], 'own text');
  assert.deepEqual(unlabelledButtons('<button aria-label="Cancel reply">✕</button>'), [], 'aria-label');
  assert.deepEqual(unlabelledButtons('<button title="Cancel reply">✕</button>'), [], 'title');
  assert.deepEqual(unlabelledButtons('<button>${label}</button>'), [], 'named at runtime');
});

test('every glyph-only button in the dashboard says what it does', () => {
  const flagged = sources().flatMap(([name, text]) => unlabelledButtons(text, name));
  assert.deepEqual(
    flagged.map((f) => `${f.file}: ${f.head}`),
    [],
    'these buttons show a glyph and announce nothing; a screen reader user cannot tell what they do, ' +
      'and a mouse user gets no tooltip either. Add aria-label AND title.',
  );
});
