import DOMPurify from './vendor/purify.mjs';
import { marked } from './vendor/marked.mjs';
import { esc } from './util.js';

// Formatting is presentation only. Never write the result back into a message.
// A positive allowlist also prevents delegated dashboard actions via data-* attributes.
const tags = ['p','br','hr','h1','h2','h3','h4','h5','h6','strong','b','em','i','s','del','blockquote','ul','ol','li','pre','code','a','table','thead','tbody','tr','th','td','details','summary'];
export function richMessageHtml(value) {
  const raw = String(value ?? '');
  if (!DOMPurify.isSupported || raw.length > 200000) return esc(raw);
  try {
    return DOMPurify.sanitize(marked.parse(raw, { async: false, gfm: true, breaks: true }), {
      ALLOWED_TAGS: tags,
      ALLOWED_ATTR: ['href', 'title', 'open'],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      ALLOWED_URI_REGEXP: /^(?:https?:\/\/|mailto:)/i,
      FORBID_TAGS: ['script','style','iframe','svg','math','template'],
    });
  } catch { return esc(raw); }
}
