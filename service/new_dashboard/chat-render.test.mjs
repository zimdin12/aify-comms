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

import {
  deliveryToastFor,
  messageHtml,
  railItemHtml,
  renderAnalyticsPanelHtml,
  subjectIsEchoOfBody,
} from './chat-render.mjs';

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

// ── MOVED FROM `chat.test.mjs` in v0.5.4 ─────────────────────────────────────────────────────
// These already covered the three builders below; they moved with the code rather than being
// rewritten. The subject-echo group is long because every one of its cases is a real message shape
// the API produces — an exact echo, an 80-char derived slice, a deliberately short subject.
test("deliveryToastFor maps the send response to the truthful ladder", () => {
  assert.equal(deliveryToastFor({ runs: [{ steered: true }] }, "x").text, "Steered into x's active turn");
  assert.equal(deliveryToastFor({ runs: [{ status: "queued" }] }, "x").tone, "info");
  assert.equal(deliveryToastFor({ consoleDeliveries: [{}], runs: [] }, "x").text, "Delivered to x's console");
  assert.equal(deliveryToastFor({ runs: [{ status: "running" }] }, "x").text, "Woke x");
  assert.equal(deliveryToastFor({ runs: [], notStarted: [{}] }, "x").tone, "warn");
  assert.equal(deliveryToastFor({ ok: false, error: "offline" }, "x").tone, "error");
});


// ── subject echo (operator report 2026-07-27: "i see messages in duplicate manner") ──────────
//
// A message sent with an empty Subject field gets `subject = body.slice(0, 80)` (app.js
// chatSendMessage). Rendering that heading above the body printed the same words twice, which reads
// as the hub duplicating messages. Nothing is duplicated in storage — it is the derivation echoing.

test("subjectIsEchoOfBody: exact match is an echo (body under 80 chars)", () => {
  const body = "btw this is from another pc. answer me here";
  assert.equal(subjectIsEchoOfBody(body, body), true);
});

test("subjectIsEchoOfBody: the 80-char derived slice is an echo", () => {
  const body = "Hey look at this (I am trying to write and delete stuff in dashboard terminal.. but i cant.. weird)";
  assert.ok(body.length > 80, "fixture must exceed the 80-char derivation to be meaningful");
  assert.equal(subjectIsEchoOfBody(body.slice(0, 80), body), true);
});

test("subjectIsEchoOfBody: a genuinely typed subject is KEPT", () => {
  assert.equal(subjectIsEchoOfBody("Console garbage", "The draft is full of escape fragments"), false);
});

test("subjectIsEchoOfBody: degenerate inputs never hide a real subject", () => {
  // No subject → caller's own `m.subject ?` guard already handles it; report false, not true.
  assert.equal(subjectIsEchoOfBody("", "some body"), false);
  assert.equal(subjectIsEchoOfBody(null, "some body"), false);
  assert.equal(subjectIsEchoOfBody(undefined, "some body"), false);
  // A subject with NO body is the only thing worth rendering — must never be suppressed.
  assert.equal(subjectIsEchoOfBody("Only a subject", ""), false);
  assert.equal(subjectIsEchoOfBody("Only a subject", null), false);
});

test("subjectIsEchoOfBody: whitespace differences still count as an echo", () => {
  assert.equal(subjectIsEchoOfBody("  hello world  ", "hello world"), true);
});

test("subjectIsEchoOfBody: a subject the body merely CONTAINS is not an echo", () => {
  assert.equal(subjectIsEchoOfBody("deploy", "please deploy the hotfix"), false);
});

test("subjectIsEchoOfBody: a deliberately-typed SHORT subject is never hidden (self-review fix)", () => {
  // The first cut tested `body.startsWith(subject)` — any prefix — which hid real subjects the
  // operator had typed. Suppressing an echo is removing noise; suppressing a chosen subject is
  // losing signal, which is strictly worse. Only the two exact derivation shapes count.
  assert.equal(subjectIsEchoOfBody("Deploy", "Deploy the hotfix now"), false);
  assert.equal(subjectIsEchoOfBody("N7", "N7 is fixed"), false);
  assert.equal(subjectIsEchoOfBody("Console garbage", "Console garbage is escape fragments"), false);
});

test("subjectIsEchoOfBody: a subject one char short of the full body is NOT an echo", () => {
  // Boundary: only `=== body` or `=== body.slice(0,80)` are derivations. Anything else renders.
  const body = "abcdefghij";
  assert.equal(subjectIsEchoOfBody(body.slice(0, 9), body), false);
  assert.equal(subjectIsEchoOfBody(body, body), true);
});

test("subjectIsEchoOfBody: the 80-char boundary is exact", () => {
  const body = "x".repeat(200);
  assert.equal(subjectIsEchoOfBody(body.slice(0, 80), body), true, "the derivation itself");
  assert.equal(subjectIsEchoOfBody(body.slice(0, 79), body), false, "79 chars is not the derivation");
  assert.equal(subjectIsEchoOfBody(body.slice(0, 81), body), false, "81 chars is not the derivation");
});

test("messageHtml omits the subject heading when it echoes the body", () => {
  const body = "btw this is from another pc. answer me here";
  const html = messageHtml({ id: "m1", from: "dashboard", subject: body, body });
  assert.doesNotMatch(html, /chat-msg-subject/, "the echoed heading must not render");
  assert.match(html, /chat-msg-body/, "the body still renders");
});

test("messageHtml keeps a distinct subject heading", () => {
  const html = messageHtml({ id: "m2", from: "dashboard", subject: "Deploy done", body: "cb732c4 is live" });
  assert.match(html, /chat-msg-subject/);
  assert.match(html, /Deploy done/);
});

// ── send(): queueing is EXPLICIT, and replying clears the peer's unread badge ──────────────────
//
// Both from operator reports, 2026-07-27:
//   * "what does ordinary pressing enter do? it should steer / ordinary send, not queue. message was
//     queued" — the old `#chat-queue` checkbox was sticky and hidden inside the collapsed Options
//     disclosure, so one tick queued every later message silently. Removed; Queue is now the second
//     half of the split Send button and passes an explicit flag.
//   * "if i write to you then it should disappear" — the unread badge survived a reply.

// `send()` toasts, and toast() reaches for document.createElement. Stub a DOM just deep enough.

test("deliveryToastFor does NOT claim 'queued' when a queueIfBusy send actually went straight through", () => {
  // Operator report 2026-07-31: "these last 2 messages were sent using queue feature. but i already
  // see that my last one was delivered. must be a bug." It is not — queueIfBusy waits only if the
  // target is MID-TURN. Live evidence from the same minute: one queued send was claimed after 1s
  // (target idle), another after 7m24s (target working). The toast must reflect which happened
  // rather than always saying "Queued", or it would be reporting a wait that never occurred.
  const delivered = deliveryToastFor({ dispatchRuns: [{ status: 'claimed' }] }, 'peer');
  assert.doesNotMatch(delivered.text, /queued/i, 'an immediately-claimed run must not report as queued');
  assert.match(delivered.text, /Woke peer/);

  const queued = deliveryToastFor({ dispatchRuns: [{ status: 'queued' }] }, 'peer');
  assert.match(queued.text, /Queued behind peer's active work/);
});
// ── a conversation row names its own action ───────────────────────────────────────────────
// A button with no aria-label is named from its CONTENT, and this row contains another control: the
// favourite star, a `role="button"` span with its own label. Read off the LIVE page's a11y tree on
// 2026-08-26, every row announced as "Unfavorite <agent> available <agent> 1 coder · available ·
// <last message>" -- the star's verb first, for a button whose action is to open the conversation.
//
// `title` was already there and does not fix it: a title is a name of last resort, used only when
// there is no content. With content present it lands as the DESCRIPTION, which is what the tree
// showed.

test("a DM row is named for opening the chat, not for the star inside it", () => {
  const html = railItemHtml(item({ favorited: true }), "none", {}, false);
  assert.match(html, /aria-label="Chat with coder"/);
  // The star keeps its own label; the row simply stops borrowing it.
  assert.match(html, /aria-label="Unfavorite coder"/);
});

test("the unread count is IN the name, because it is why the row matters", () => {
  const html = railItemHtml(item({ unread: 3 }), "none");
  assert.match(html, /aria-label="Chat with coder, 3 unread"/);
});

test("a channel row says channel, since its action is not a DM", () => {
  const html = railItemHtml(item({ kind: "channel", key: "ch:status", id: "status" }), "none");
  assert.match(html, /aria-label="Open channel status"/);
});

test("the name is escaped like every other attribute here", () => {
  // Agent ids come from registration, which is a bridge talking to the API -- not a trusted field.
  // This is the same argument the id-escaping test above makes, applied to the attribute this change
  // ADDS. A new attribute fed by the same untrusted string needs its own assertion, not an
  // assumption that the neighbouring one covers it.
  const html = railItemHtml(item({ id: 'x" onmouseover="steal()' }), "none");
  assert.doesNotMatch(html, /aria-label="[^"]*" onmouseover="steal\(\)/);
});

test("title stays as well -- it is the hover affordance, not the name", () => {
  // Keeping both is deliberate: the tooltip a sighted operator hovers and the name a screen reader
  // announces are different jobs. Removing `title` to "avoid duplication" would cost the first.
  const html = railItemHtml(item(), "none");
  assert.match(html, /title="coder"/);
});
