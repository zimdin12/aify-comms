import test from 'node:test';
import assert from 'node:assert/strict';
import { richMessageHtml } from './message-format.mjs';

// Node has no DOMPurify-supported document. Browser sanitization is exercised by
// fixtures/messenger-browser.mjs; this directly pins the fail-safe public export.
test('richMessageHtml escapes markup when no sanitizer DOM is available', () => {
  assert.equal(richMessageHtml('<script>"x" & \'y\'</script>'), '&lt;script&gt;&quot;x&quot; &amp; &#39;y&#39;&lt;/script&gt;');
  assert.equal(richMessageHtml('**literal**\nnext'), '**literal**\nnext');
});

test('richMessageHtml accepts nullish and non-string message values', () => {
  assert.equal(richMessageHtml(null), '');
  assert.equal(richMessageHtml(undefined), '');
  assert.equal(richMessageHtml(42), '42');
});

// ── The policy itself, because the function cannot be asked about it here ────────────────────────
//
// In Node `DOMPurify.isSupported` is false, so every assertion about `richMessageHtml` above
// exercises the ESCAPE FALLBACK — the branch that never consults the policy. Real sanitization lives
// in `fixtures/messenger-browser.mjs`, which NO suite runs. So the config is what this file can
// hold, and a loosened config is the regression that would otherwise reach the operator's dashboard
// with every gate green.

import { RICH_MESSAGE_LIMIT, RICH_MESSAGE_POLICY, exceedsRichMessageLimit } from './message-format.mjs';

test('the sanitizer policy still refuses the attributes that would arm a dashboard action', () => {
  // Clicks are dispatched by ONE delegated handler keyed on data- attributes, so a message body
  // carrying one would be planting a control, not decorating itself.
  assert.equal(RICH_MESSAGE_POLICY.ALLOW_DATA_ATTR, false);
  assert.equal(RICH_MESSAGE_POLICY.ALLOW_ARIA_ATTR, false);
  // Only these three. Any event handler, `srcdoc`, `style`, or `target` appearing here is a hole.
  assert.deepEqual(RICH_MESSAGE_POLICY.ALLOWED_ATTR, ['href', 'title', 'open']);
});

test('the sanitizer policy still refuses every scheme but http, https and mailto', () => {
  const allowed = RICH_MESSAGE_POLICY.ALLOWED_URI_REGEXP;
  for (const href of ['https://example.test/x', 'http://example.test/x', 'mailto:a@b.test', 'HTTPS://E.TEST']) {
    assert.ok(allowed.test(href), `${href} should be renderable`);
  }
  // Drive the control by feeding it exactly what it exists to stop.
  for (const href of ['javascript:alert(1)', 'data:text/html;base64,PHN2Zz4=', 'vbscript:x', 'file:///etc/passwd', '  javascript:alert(1)']) {
    assert.equal(allowed.test(href), false, `${href} must not be renderable`);
  }
});

test('the sanitizer policy still forbids the element classes that carry script', () => {
  for (const tag of ['script', 'style', 'iframe', 'svg', 'math', 'template']) {
    assert.ok(RICH_MESSAGE_POLICY.FORBID_TAGS.includes(tag), `${tag} left the forbidden list`);
    assert.equal(RICH_MESSAGE_POLICY.ALLOWED_TAGS.includes(tag), false, `${tag} was added to the allowed list`);
  }
  // Positive control: the list is real and still permits ordinary formatting.
  for (const tag of ['p', 'strong', 'code', 'a', 'table']) assert.ok(RICH_MESSAGE_POLICY.ALLOWED_TAGS.includes(tag));
});

test('the oversize threshold is a real boundary, not a number nothing reads', () => {
  // THIS REPLACED A TEST THAT PASSED FOR THE WRONG REASON. It asserted that an oversized body came
  // back escaped -- which it does in Node whether or not the limit exists, because the no-DOM
  // fallback escapes everything. Deleting the limit left that test green, so it was measuring the
  // fallback and reporting on the guard. The predicate can be asked directly; the guard cannot,
  // here, and saying so is better than a green that means nothing.
  assert.equal(exceedsRichMessageLimit('a'.repeat(RICH_MESSAGE_LIMIT)), false);
  assert.equal(exceedsRichMessageLimit('a'.repeat(RICH_MESSAGE_LIMIT + 1)), true);
  assert.equal(exceedsRichMessageLimit(null), false);
});
