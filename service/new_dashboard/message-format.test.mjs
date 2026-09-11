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
