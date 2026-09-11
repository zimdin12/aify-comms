import DOMPurify from './vendor/purify.mjs';
import { marked } from './vendor/marked.mjs';
import { esc } from './util.js';

// Formatting is presentation only. Never write the result back into a message.
//
// THIS IS THE ONLY PLACE THE DASHBOARD RENDERS UNTRUSTED TEXT AS HTML. Every other field in
// `chat-render.mjs` goes through `esc`; the message body does not, by design. Bodies are written by
// agents and by anyone who can reach the send API, so the policy below is the whole defence.
const tags = ['p','br','hr','h1','h2','h3','h4','h5','h6','strong','b','em','i','s','del','blockquote','ul','ol','li','pre','code','a','table','thead','tbody','tr','th','td','details','summary'];

/** Bodies past this are escaped rather than parsed; a megabyte of markdown is not worth rendering. */
export const RICH_MESSAGE_LIMIT = 200000;

/**
 * Is this body too long to hand to the parser?
 *
 * SPLIT OUT SO THE THRESHOLD IS CHECKABLE. Node has no DOM, so `richMessageHtml` escapes everything
 * here regardless — which means a test of the FUNCTION passes identically whether the limit is
 * enforced or deleted. It did, and the mutant survived. The threshold is a real decision, so it gets
 * a predicate a test can hold; that the guard still calls it is NOT provable in this suite, and the
 * browser fixture is where that would be shown.
 */
export function exceedsRichMessageLimit(value) {
  return String(value ?? '').length > RICH_MESSAGE_LIMIT;
}

/**
 * The sanitizer policy, named so a test can assert it.
 *
 * IT IS EXPORTED BECAUSE NOTHING ELSE COULD CHECK IT. DOMPurify needs a DOM, so in Node
 * `isSupported` is false and `richMessageHtml` returns escaped text without ever consulting this
 * object — which means a unit test of the FUNCTION exercises only the fallback and would stay green
 * while someone widened the policy to allow `data-*`, `onerror`, or a `javascript:` href. The
 * browser fixture that exercises real sanitization is not run by any suite. So the realistic
 * regression is a loosened config, and this is the shape a gate can hold.
 *
 * WHAT THAT GATE PROVES AND WHAT IT DOES NOT: that the policy we hand DOMPurify is still the strict
 * one. It does not prove DOMPurify enforces it — that is the library's job, it is vendored at a
 * pinned hash, and re-proving it here would need a DOM this suite does not have.
 *
 * `ALLOW_DATA_ATTR: false` is load-bearing rather than tidy. The dashboard dispatches clicks through
 * ONE delegated handler keyed on `data-` attributes (`data-msg-unsend`, `data-message-detail`), so a
 * body that could carry one would not be styling itself — it would be planting a dashboard action
 * under the operator's cursor.
 */
export const RICH_MESSAGE_POLICY = Object.freeze({
  ALLOWED_TAGS: tags,
  ALLOWED_ATTR: ['href', 'title', 'open'],
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
  ALLOWED_URI_REGEXP: /^(?:https?:\/\/|mailto:)/i,
  FORBID_TAGS: ['script','style','iframe','svg','math','template'],
});

export function richMessageHtml(value) {
  const raw = String(value ?? '');
  if (!DOMPurify.isSupported || exceedsRichMessageLimit(raw)) return esc(raw);
  try {
    return DOMPurify.sanitize(marked.parse(raw, { async: false, gfm: true, breaks: true }), RICH_MESSAGE_POLICY);
  } catch { return esc(raw); }
}
