// Every form control in the shell has a name that survives typing.
//
// A placeholder is not an accessible name. It is erased the moment the field has content, so a screen
// reader user who tabs into a half-typed field hears nothing, and one who is checking what they filled
// in hears nothing either. WCAG 3.3.2 and 2.4.6 both land on this; the practical version is simpler —
// the label must not vanish exactly when the user is using the field.
//
// Read off the LIVE page 2026-08-25 rather than the source: of 163 controls, four had no label element,
// no aria-label and no title, and were named by a placeholder alone —
//
//     chat-filter          "Search conversations + messages"
//     chat-new-channel     "New channel name"
//     chat-msg-search      "Search this chat"
//     chat-composer-body   "Select a conversation"
//
// The last is the worst of them: "Select a conversation" is a STATE HINT, not a name. It never changes
// (nothing in the dashboard writes that placeholder) and the textarea is not disabled, so the message
// box announced itself as an instruction and then, once typed into, as nothing at all.
//
// DERIVED FROM THE MARKUP, not a list of those four ids. A test that pinned the four would go green and
// stay green while a fifth arrived.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

// SCOPED TO index.html, DELIBERATELY, and this is the honest limit of it.
//
// The shell holds 28 controls; the live page has 163. The other 135 come out of template literals
// in the modules, and a regex cannot tell a named control from an unnamed one there: their ids are
// interpolated (`id="${id}"`) and so are the labels that name them (`<label for="${id}">`).
// Widening this scan to the modules reported TEN correctly-labelled controls as nameless -- six in
// settings-fields.mjs alone, every one of them carrying an interpolated label -- and acting on that
// would have added aria-labels that OVERRIDE the visible label and then drift from it. A gate that
// fires on correct markup is worse than no gate.
//
// The modules were checked instead on the RUNNING page, 2026-08-25: of 163 rendered controls, zero
// were unnamed and four were named by a placeholder alone -- all four static, all four in this file,
// all four fixed here. One module-rendered control is genuinely placeholder-only and is recorded in
// KNOWN_ISSUES.md rather than fixed blind: the codex console input in session-console.mjs.
const HTML = readFileSync(new URL('./index.html', import.meta.url), 'utf8');

/** Every control tag in the shell, as raw markup. */
function controlTags() {
  const tags = [];
  for (const m of HTML.matchAll(/<(input|textarea|select)\b([^>]*)>/gi)) {
    tags.push({ tag: m[1].toLowerCase(), attrs: m[2], raw: m[0], at: m.index });
  }
  return tags;
}

const attr = (attrs, name) => {
  const m = attrs.match(new RegExp(`${name}="([^"]*)"`, 'i'));
  return m ? m[1] : null;
};

/**
 * Whether a control tag sits INSIDE a <label>...</label>.
 *
 * Implicit labelling -- `<label><span>Agent ID</span><input ...></label>` -- is valid and gives a real
 * accessible name, and this file's first version could not see it. It reported four correctly-labelled
 * controls as nameless (global-filter, env-spawn-agent-id, env-spawn-workspace, env-spawn-prompt), all
 * of them wrapped this way. Acting on that would have added redundant aria-labels to correct markup,
 * which is worse than the defect: an aria-label OVERRIDES the visible label, so the two can then drift.
 */
function insideLabel(text, offset) {
  const open = text.lastIndexOf('<label', offset);
  if (open === -1) return false;
  const close = text.indexOf('</label>', open);
  return close !== -1 && close > offset;
}

/** ids named by a <label for="..."> somewhere in the shell. */
function labelledIds() {
  return new Set([...HTML.matchAll(/<label[^>]*\bfor="([^"]+)"/gi)].map((m) => m[1]));
}

test('the scan finds the shell controls', () => {
  // The control. An empty tag list agrees with any assertion below, which is the shape of every wrong
  // zero this review has produced.
  const tags = controlTags();
  // 28 measured 2026-08-25 in index.html.
  assert.ok(tags.length > 20, `only ${tags.length} control tags parsed; the scan is broken`);
  assert.ok(tags.some((t) => t.attrs.includes('chat-composer-body')), 'the scan missed a known control');
});

test('no control is named by a placeholder alone', () => {
  const labelled = labelledIds();
  const nameless = controlTags().filter((t) => {
    if (/type="hidden"/i.test(t.attrs)) return false;
    const id = attr(t.attrs, 'id');
    if (id && labelled.has(id)) return false;
    if (insideLabel(HTML, t.at)) return false;   // implicit label, a real name
    if (attr(t.attrs, 'aria-label') || attr(t.attrs, 'aria-labelledby') || attr(t.attrs, 'title')) return false;
    return Boolean(attr(t.attrs, 'placeholder'));
  }).map((t) => `${t.tag}#${attr(t.attrs, 'id') || '(no id)'} placeholder="${attr(t.attrs, 'placeholder')}"`);

  assert.deepEqual(
    nameless, [],
    'these controls are named only by a placeholder, which is erased as soon as the field has content: '
    + nameless.join('; '),
  );
});

test('no control has no name at all', () => {
  // The stricter half. A control with neither a label, an aria-label nor a placeholder announces only
  // its type — "edit text" — which is worse than a vanishing name.
  const labelled = labelledIds();
  const anonymous = controlTags().filter((t) => {
    if (/type="(hidden|submit|button|checkbox|radio)"/i.test(t.attrs)) return false;
    const id = attr(t.attrs, 'id');
    if (id && labelled.has(id)) return false;
    if (insideLabel(HTML, t.at)) return false;   // implicit label, a real name
    return !(attr(t.attrs, 'aria-label') || attr(t.attrs, 'aria-labelledby')
             || attr(t.attrs, 'title') || attr(t.attrs, 'placeholder'));
  }).map((t) => `${t.tag}#${attr(t.attrs, 'id') || '(no id)'}`);

  assert.deepEqual(anonymous, [], 'these controls announce only their type: ' + anonymous.join(', '));
});

test('the message box is named for what it is, not for what to do first', () => {
  // Named explicitly, because it is the one whose placeholder is a state hint rather than a label. A
  // general rule that quietly stopped covering the case that produced it would still pass above.
  const composer = controlTags().find((t) => attr(t.attrs, 'id') === 'chat-composer-body');
  assert.ok(composer, 'the composer is gone');
  assert.equal(attr(composer.attrs, 'aria-label'), 'Message');
  assert.equal(
    attr(composer.attrs, 'placeholder'), 'Select a conversation',
    'the hint was removed along with the fix; it is useful, it just is not a name',
  );
});
