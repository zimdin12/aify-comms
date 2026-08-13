// How one run EVENT renders in the inspector.
//
// A run's event stream is the record of what actually happened to a dispatched turn, and its bodies are
// written by AGENTS — arbitrary text from another process, rendered into the dashboard's DOM as HTML. That
// is the whole reason these two functions escape everything they interpolate: an event body containing
// `<script>` or a broken tag is not a formatting problem, it is agent-authored markup executing in the
// operator's browser. `esc` on the body, on the type chip and on the timestamp title is load-bearing, and
// it is the property these tests exist to hold.
//
// A LONG BODY COLLAPSES INTO `<details>` rather than being truncated. Truncation loses the tail, which for
// an error body is usually the part that matters; a disclosure keeps all of it while a hundred events still
// fit on a screen.
//
// Pure: an event record in, an HTML string out. No DOM, no state.

import { esc, relTime } from './util.js';

export function renderEventBody(event) {
  const body = String(event?.body || '');
  if (!body) return '<p class="preview">No event body.</p>';
  if (body.length > 160) {
    return `<details><summary>Body</summary><p class="preview">${esc(body)}</p></details>`;
  }
  return `<p class="preview">${esc(body)}</p>`;
}

export function renderRunEvent(event) {
  const iso = event.createdAt || event.created_at || '';
  return `
    <article class="run-event">
      <div class="item-title">
        <time title="${esc(iso)}">${esc(relTime(iso) || 'now')}</time>
        <span class="event-chip">${esc(event.eventType || event.type || 'event')}</span>
      </div>
      ${renderEventBody(event)}
    </article>`;
}
