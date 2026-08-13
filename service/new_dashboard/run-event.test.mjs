// Rendering a run event — where the text being rendered was written by another agent.
//
// A run's event stream is the record of what happened to a dispatched turn, and its bodies come from
// agents: arbitrary text from another process, interpolated into the dashboard's DOM. Escaping is therefore
// not cosmetic here. An event body containing `<script>` is agent-authored markup running in the operator's
// browser, and the agent that wrote it may be one an operator is investigating precisely because it is
// behaving oddly.

import assert from "node:assert/strict";
import { test } from "node:test";

import { renderEventBody, renderRunEvent } from "./run-event.mjs";

test("A SCRIPT TAG IN AN EVENT BODY IS ESCAPED, not rendered", () => {
  // The one that matters. `<` must not survive as `<` anywhere in the output.
  const html = renderEventBody({ body: "<script>alert(1)</script>" });
  assert.doesNotMatch(html, /<script/i, "the tag must not survive into the DOM");
  assert.match(html, /&lt;script/i, "…it must appear as escaped text");
});

test("A LONG body is escaped TOO — the branch a big agent dump actually takes", () => {
  // The short-body test above cannot reach this: `<script>alert(1)</script>` is 25 characters, so it takes
  // the short branch and the `<details>` branch's own `esc()` goes unexercised. A mutation removing that
  // second escape survived until this test existed — and the long branch is precisely where a large
  // agent-authored dump goes, which is the payload most worth escaping.
  const html = renderEventBody({ body: `${"x".repeat(200)}<script>alert(1)</script>` });
  assert.match(html, /<details>/, "the fixture must really take the long branch");
  assert.doesNotMatch(html, /<script/i, "…and the tag must still not survive it");
  assert.match(html, /&lt;script/i);
});

test("every interpolated field is escaped, not just the body", () => {
  // The type chip and the timestamp title are interpolated too, and both come off the same record.
  const html = renderRunEvent({
    body: "<b>body</b>",
    eventType: '"><img src=x onerror=alert(1)>',
    createdAt: '"><script>x</script>',
  });
  assert.doesNotMatch(html, /<img|<script/i, "no injected tag may survive from any field");
  // The attribute-breaking quote must be neutralised, or the payload escapes its attribute.
  assert.doesNotMatch(html, /title="">/, "the timestamp title must not be closable by its own content");
});

test("a LONG body collapses into a disclosure instead of being truncated", () => {
  // Truncation loses the tail, and for an error body the tail is usually the part that explains it. A
  // `<details>` keeps all of it while a hundred events still fit on a screen.
  const long = "x".repeat(500);
  const html = renderEventBody({ body: long });
  assert.match(html, /<details>/, "a long body must be collapsible");
  assert.ok(html.includes(long), "…and must still contain the whole body, not a prefix of it");

  const short = renderEventBody({ body: "brief" });
  assert.doesNotMatch(short, /<details>/, "a short body must not cost the reader a click");
  assert.match(short, /brief/);
});

test("the long/short boundary is where the source says it is", () => {
  // 160 characters. Asserted from both sides so a change to the threshold is a decision rather than drift.
  assert.doesNotMatch(renderEventBody({ body: "y".repeat(160) }), /<details>/, "160 is still short");
  assert.match(renderEventBody({ body: "y".repeat(161) }), /<details>/, "161 collapses");
});

test("an empty or missing body says so rather than rendering a blank", () => {
  // An event with no body is normal — a state transition carries none. A blank `<p>` reads as a broken
  // renderer rather than an event that simply had nothing to say.
  for (const event of [{}, { body: "" }, { body: null }, undefined]) {
    const html = renderEventBody(event ?? {});
    assert.match(html, /No event body/, `${JSON.stringify(event)} must state that there is none`);
  }
});

test("both timestamp spellings are read, and a missing one still renders", () => {
  // `createdAt` from the newer routes, `created_at` from the older ones. A missed spelling would show every
  // event as "now", which is worse than showing no time at all because it is confidently wrong.
  const iso = "2020-01-01T00:00:00Z";
  assert.ok(renderRunEvent({ createdAt: iso }).includes(iso), "camelCase must reach the title");
  assert.ok(renderRunEvent({ created_at: iso }).includes(iso), "snake_case must too");
  assert.match(renderRunEvent({}), /now/, "an event with no timestamp renders as now rather than blank");
});

test("both event-type spellings are read, with a fallback", () => {
  assert.match(renderRunEvent({ eventType: "dispatch" }), /dispatch/);
  assert.match(renderRunEvent({ type: "dispatch" }), /dispatch/);
  assert.match(renderRunEvent({}), /event/, "an untyped event still gets a chip");
});

test("a junk record renders without throwing and without leaking placeholders", () => {
  // It is rendered per event in a list; one bad record must not blank the inspector.
  for (const event of [{}, { body: 42 }, { eventType: null }, { createdAt: 0 }]) {
    const html = renderRunEvent(event);
    assert.equal(typeof html, "string");
    assert.ok(html.includes("<article"), `${JSON.stringify(event)} must still produce an article`);
    assert.doesNotMatch(html, /undefined|NaN|\[object Object\]/, `leaked a placeholder: ${html.slice(0, 120)}`);
  }
});

test("CURRENT: an OBJECT body renders as the literal [object Object]", () => {
  // Found while writing the case above, pinned rather than fixed — changing it is a behaviour change and
  // this landed in a relocation slice. `String(event?.body)` on an object gives "[object Object]", which is
  // rendered to the operator as though it were the event's text.
  //
  // Whether it can happen depends on the API: every route observed today sends a string body. If one ever
  // sends a structured body, this is what the inspector will show — so the assertion exists to make that a
  // visible decision rather than a surprise, and to give a fix something to flip.
  const html = renderEventBody({ body: { detail: "structured" } });
  assert.match(html, /\[object Object\]/,
    "CURRENT behaviour: a non-string body is stringified rather than serialised or rejected");
  assert.doesNotMatch(html, /structured/, "…and its actual content is lost");
});
