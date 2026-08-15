// Tests that CALL `chat-render.mjs` — the doctor-predicates.js standard, applied to the dashboard.
//
// `chat.test.mjs` already drove three of these six through `chat.js`, and those tests moved with the
// code rather than being duplicated. What is here is the two that NOTHING exercised: `railItemHtml`,
// which was module-private and so unreachable from a test at all, and `renderAnalyticsPanelHtml`,
// which was exported and simply never called.
//
// EVERY ASSERTION IS ABOUT ESCAPING OR ABOUT A ZERO. Those are the two ways an HTML builder fed by
// other agents' text goes wrong: a name or subject that closes an attribute and continues as markup,
// or a missing number rendered as a blank where the reader will read it as "none".

import assert from "node:assert/strict";
import test from "node:test";

import { railItemHtml, renderAnalyticsPanelHtml } from "./chat-render.mjs";

const item = (over = {}) => ({
  key: "dm:coder",
  kind: "dm",
  id: "coder",
  label: "coder",
  status: "online",
  unread: 0,
  ...over,
});

test("railItemHtml marks the selected row and only that row", () => {
  const selected = railItemHtml(item(), "dm:coder");
  const other = railItemHtml(item(), "dm:tester");
  // The class is `active`, not `selected` — asserted from the code rather than from what the name
  // suggested. My first draft guessed and went red, which is the characterization test working.
  assert.match(selected, /class="chat-rail-item active/);
  assert.doesNotMatch(other, /class="chat-rail-item active/);
});

test("railItemHtml escapes an agent id that would otherwise close the attribute", () => {
  // Agent ids come from registration, which is a bridge talking to the API — not a trusted field.
  const html = railItemHtml(item({ id: 'x" onmouseover="steal()', label: "x" }), "none");
  assert.doesNotMatch(html, /onmouseover="steal\(\)/, "the injected handler must not survive as markup");
});

test("railItemHtml renders the agent ID, never the label", () => {
  // Worth pinning because `label` is on the item and looks like the display field. The row shows
  // `id`, so a label carrying markup cannot reach the DOM through this builder at all.
  const html = railItemHtml(item({ id: "coder", label: "<img src=x onerror=boom>" }), "none");
  assert.doesNotMatch(html, /<img /);
  assert.doesNotMatch(html, /onerror/);
  assert.match(html, /chat-rail-name clip">coder</);
});

test("railItemHtml shows an unread count only when there is one", () => {
  assert.doesNotMatch(railItemHtml(item({ unread: 0 }), "none"), /chat-unread/);
  assert.match(railItemHtml(item({ unread: 3 }), "none"), /class="chat-unread">3</);
});

test("railItemHtml renders a channel row without a presence dot", () => {
  // A channel has no status of its own; borrowing the agent dot would assert presence for a thing
  // that cannot be present.
  const html = railItemHtml(item({ kind: "channel", key: "ch:dev", id: "dev", label: "dev" }), "none");
  assert.doesNotMatch(html, /status-dot/);
  assert.match(html, /chat-rail-hash">#</, "it gets a hash instead");
});

test("renderAnalyticsPanelHtml says unavailable rather than rendering zeros", () => {
  // The distinction the panel exists to keep: "we could not measure this agent" and "this agent did
  // nothing" look identical once both render as 0h 0m.
  for (const payload of [null, undefined, { ok: false }]) {
    assert.match(renderAnalyticsPanelHtml("coder", payload), /Analytics unavailable/);
  }
});

test("renderAnalyticsPanelHtml escapes the agent id in the unavailable message", () => {
  const html = renderAnalyticsPanelHtml('<script>alert(1)</script>', { ok: false });
  assert.doesNotMatch(html, /<script>/);
});

test("renderAnalyticsPanelHtml formats working minutes as hours and minutes", () => {
  const html = renderAnalyticsPanelHtml("coder", { workingMinutes: 125 });
  assert.match(html, /2h 5m/);
});

test("renderAnalyticsPanelHtml renders a missing median reply as 0m, not blank", () => {
  const html = renderAnalyticsPanelHtml("coder", { workingMinutes: 0 });
  assert.match(html, /0m/);
});

test("renderAnalyticsPanelHtml switches the median reply to hours past 60 minutes", () => {
  assert.match(renderAnalyticsPanelHtml("c", { medianReplyMinutes7d: 90 }), /1h 30m/);
  assert.match(renderAnalyticsPanelHtml("c", { medianReplyMinutes7d: 45 }), /45m/);
});

test("renderAnalyticsPanelHtml caps the peer list at five", () => {
  // The field is `peer`, not `agentId` — another assumption the code corrected.
  const byPeer = Array.from({ length: 9 }, (_, i) => ({ peer: `peer${i}`, count: 9 - i }));
  const html = renderAnalyticsPanelHtml("coder", { byPeer });
  assert.match(html, /peer0/);
  assert.doesNotMatch(html, /peer5/, "a rail panel showing nine peers is a scrolling list, not a summary");
});

test("renderAnalyticsPanelHtml escapes a peer id", () => {
  const html = renderAnalyticsPanelHtml("coder", { byPeer: [{ peer: '"><b>x</b>', count: 1 }] });
  assert.doesNotMatch(html, /<b>x<\/b>/);
});

test("renderAnalyticsPanelHtml tolerates byPeer arriving as a non-array", () => {
  // The API returns {} for an agent with no history, and `.slice` on that would throw INSIDE a
  // render — which blanks the panel rather than showing an error.
  for (const byPeer of [undefined, null, {}, "nope"]) {
    assert.doesNotThrow(() => renderAnalyticsPanelHtml("coder", { byPeer }));
  }
});
