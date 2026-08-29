#!/usr/bin/env node
// Tests for `createChatController` — the half of the chat surface that needs a document.
//
// The pure builders left in v0.5.4: the conversation list to `chat-select.mjs`, the HTML to
// `chat-render.mjs`, and their tests went with them. What is here drives the controller through a
// fake DOM, which is exactly why it could not live beside them.
//
// Run: node --test service/new_dashboard/chat.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatController } from "./chat.js";
// `messageHtml` from its OWNER, not re-exported through `chat.js` — a re-export is what makes a
// stale import look valid. It is here because one controller test asserts the ACTION LABELS the
// timeline renders for a read DM, which is a claim about the two together: the controller decides
// the identity, the builder decides the wording, and the bug being guarded was the pair disagreeing.
import { messageHtml } from "./chat-render.mjs";


function withStubDocument(fn) {
  const previous = globalThis.document;
  // Exactly what ui.js toast() touches: className, setAttribute, textContent, classList,
  // addEventListener, remove, plus host.children / firstElementChild for the stack cap.
  const node = () => ({
    className: "", textContent: "", innerHTML: "", style: {}, dataset: {},
    children: [], firstElementChild: null, isConnected: true,
    setAttribute() {}, removeAttribute() {},
    appendChild() {}, remove() {}, addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  });
  globalThis.document = {
    createElement: node, body: node(), getElementById: () => null,
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
  };
  const prevRaf = globalThis.requestAnimationFrame;
  if (typeof prevRaf !== "function") globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      globalThis.document = previous;
      globalThis.requestAnimationFrame = prevRaf;
    });
}

function sendHarness({ selected = "dm:peer" } = {}) {
  const sent = [];
  const readCalls = [];
  const els = {
    "chat-composer-body": { value: "hello there" },
    "chat-subject": { value: "" },
    "chat-expects-reply": { checked: false },
    "chat-timeline": { innerHTML: "", scrollTop: 0, scrollHeight: 0, clientHeight: 0 },
    "chat-rail-list": { innerHTML: "" },
    "chat-conv-actions": { innerHTML: "" },
    "chat-conv-title": { textContent: "" },
    "chat-composer": { hidden: false },
    "chat-msg-search": { hidden: true, value: "" },
  };
  const state = {
    agents: [{ id: "peer", status: "online" }],
    messages: [],
    channels: [],
    runs: [],
    chat: { selected, identity: "dashboard", drafts: {}, replyTo: null, analytics: {}, view: "dm" },
  };
  const controller = createChatController({
    state,
    byId: (id) => els[id] || null,
    sendMessage: async (payload) => { sent.push(payload); return { ok: true }; },
    refresh: async () => {},
    loadConversation: async () => {},
    markConversationRead: async (agentId, opts) => { readCalls.push({ agentId, opts }); },
    persistDrafts: () => {},
  });
  return { controller, sent, readCalls, els, state };
}


test("read DM actions are labelled as actions, not contradictory message state", () => {
  const message = {
    id: "m-read", from: "alice", to: "dashboard", type: "response",
    subject: "ack", body: "nothing owed", read: true, dispatchRequested: true,
  };
  const html = messageHtml(message, "dashboard");
  assert.match(html, />read<\/span>/, "the badge reports current read state");
  assert.match(html, />Write reply<\/button>/, "reply is clearly an optional compose action");
  assert.match(html, />Mark unread<\/button>/, "read toggle says what clicking it will do");
  assert.doesNotMatch(html, />Reply<\/button>/, "a bare Reply label looked like a reply obligation");
  assert.doesNotMatch(html, />Unread<\/button>/, "a bare Unread action contradicted the read badge");
  const allHtml = messageHtml(message, "all");
  assert.doesNotMatch(allHtml, /Write reply|Mark unread|Unsend/, "the all identity is read-only");
});

test("Viewing as all renders a read-only conversation surface", () => {
  const classList = { add() {}, remove() {}, toggle() {} };
  const element = () => ({ innerHTML: "", textContent: "", hidden: false, value: "", dataset: {}, classList, querySelector: () => null, addEventListener() {}, scrollHeight: 0, scrollTop: 0, clientHeight: 0 });
  const ids = ["chat-rail-list", "chat-conv-title", "chat-timeline", "chat-msg-search", "chat-scroll-bottom", "chat-conv-actions", "chat-composer", "chat-identity", "chat-new-channel-form"];
  const els = Object.fromEntries(ids.map((id) => [id, element()]));
  const state = {
    loaded: true,
    agents: [{ id: "alice", status: "online", favorited: true }, { id: "bob", status: "online" }],
    messages: [{ id: "m1", from: "alice", to: "bob", body: "handoff", timestamp: 1 }],
    chat: { identity: "all", selected: "dm:alice", view: "console", analytics: {}, pulse: {}, channels: [{ name: "ops", members: ["alice"], memberCount: 1 }], channelMessages: {}, drafts: {} },
  };
  let consoleMounts = 0;
  const previousDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const controller = createChatController({ state, byId: (id) => els[id] || null, mountChatConsole: () => { consoleMounts += 1; } });
    controller.render();
    assert.equal(consoleMounts, 0, "all-view must not mount an input-capable console");
    assert.doesNotMatch(els["chat-conv-actions"].innerHTML, /data-chat-view|data-mark-conv-read/);
    assert.doesNotMatch(els["chat-rail-list"].innerHTML, /data-fav-toggle/);
    assert.equal(els["chat-new-channel-form"].hidden, true);

    state.chat.selected = "channel:ops";
    controller.renderConversation();
    assert.doesNotMatch(els["chat-conv-actions"].innerHTML, /data-chat-channel-action|data-channel-add-member|data-channel-remove-member/);
  } finally {
    globalThis.document = previousDocument;
  }
});

test("send() without arguments never queues — Enter and Send are ordinary sends", () => withStubDocument(async () => {
    const h = sendHarness();
    await h.controller.send();
    assert.equal(h.sent.length, 1);
    assert.equal(h.sent[0].queueIfBusy, false, "a bare send must not queue");
}));

test("send({queue:true}) queues — the Queue half of the split button", () => withStubDocument(async () => {
    const h = sendHarness();
    await h.controller.send({ queue: true });
    assert.equal(h.sent.length, 1);
    assert.equal(h.sent[0].queueIfBusy, true);
}));

test("send() marks the peer's messages read — answering IS reading", () => withStubDocument(async () => {
    const h = sendHarness();
    await h.controller.send();
    assert.deepEqual(h.readCalls, [{ agentId: "peer", opts: { quiet: true } }],
      "must clear the unread badge quietly (the send already toasts)");
}));

test("send() to a CHANNEL does not touch DM read state", () => withStubDocument(async () => {
    const h = sendHarness({ selected: "channel:ops" });
    await h.controller.send();
    assert.equal(h.sent.length, 1);
    assert.deepEqual(h.readCalls, [], "channel read state is per-membership, a different contract");
}));

test("a failing mark-read never loses the sent message", () => withStubDocument(async () => {
    const h = sendHarness();
    h.controller.__proto__; // no-op, keeps shape explicit
    const controller = createChatController({
      state: h.state,
      byId: (id) => h.els[id] || null,
      sendMessage: async (payload) => { h.sent.push(payload); return { ok: true }; },
      refresh: async () => {},
      loadConversation: async () => {},
      markConversationRead: async () => { throw new Error("read endpoint down"); },
      persistDrafts: () => {},
    });
    h.els["chat-composer-body"].value = "hello there";
    await controller.send(); // must not throw
    assert.equal(h.sent.length, 1, "the message was still sent");
}));

// ── open / close / pulse / analytics ────────────────────────────────────────
//
// The v0.6 Phase 3 census found these five never called by any test. They are the conversation
// lifecycle: which chat is open, what happens when it closes, and the two async loaders behind the
// landing view. Both loaders carry a stale-async guard, which is the interesting part — a guard that
// is never exercised is a guard nobody knows is there, and both were written after a real incident.

function pulseHarness({ selected = "", analytics = { agent: "", data: null }, loadPulse, loadAgentAnalytics } = {}) {
  const el = () => ({
    innerHTML: "", textContent: "", hidden: false, value: "", dataset: {},
    scrollHeight: 0, scrollTop: 0, clientHeight: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, addEventListener() {},
  });
  const ids = [
    "chat-rail-list", "chat-conv-title", "chat-timeline", "chat-msg-search", "chat-scroll-bottom",
    "chat-conv-actions", "chat-composer", "chat-identity", "chat-new-channel-form", "chat-composer-body",
  ];
  const els = Object.fromEntries(ids.map((id) => [id, el()]));
  const state = {
    loaded: true,
    agents: [{ id: "alice", status: "online" }, { id: "bob", status: "online" }],
    messages: [],
    channels: [],
    chat: {
      identity: "dashboard", selected, view: "messenger", drafts: {}, replyTo: null,
      analytics, channels: [], channelMessages: {}, msgFilter: "",
      pulse: { window: "24h", data: null, loading: false, lastMs: 0 },
    },
  };
  const loaded = [];
  const controller = createChatController({
    state,
    byId: (id) => els[id] || null,
    sendMessage: async () => ({ ok: true }),
    refresh: async () => {},
    loadConversation: async (name) => { loaded.push(name); },
    loadPulse,
    loadAgentAnalytics,
    persistDrafts: () => {},
  });
  return { controller, state, els, loaded };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

test("open() clears the per-conversation message filter when the conversation changes", () => withStubDocument(async () => {
  const h = pulseHarness({ selected: "dm:alice" });
  h.state.chat.msgFilter = "deploy";
  await h.controller.open("dm:bob");
  assert.equal(h.state.chat.msgFilter, "", "a search typed in one conversation must not follow you into the next");
  assert.equal(h.state.chat.selected, "dm:bob");
}));

test("open() re-selecting the SAME conversation keeps the filter", () => withStubDocument(async () => {
  const h = pulseHarness({ selected: "dm:alice" });
  h.state.chat.msgFilter = "deploy";
  await h.controller.open("dm:alice");
  assert.equal(h.state.chat.msgFilter, "deploy", "re-opening what is already open is not a change");
}));

test("open() follows analytics to a different agent, with the rail prefix stripped", () => withStubDocument(async () => {
  // The bug this encodes: rail keys are `dm:<id>` and openAnalytics wants the RAW id. Passing the
  // prefixed key through loaded an empty, all-zero analytics panel that looked like real data.
  const asked = [];
  const h = pulseHarness({
    selected: "dm:alice",
    analytics: { agent: "alice", data: { ok: true } },
    loadAgentAnalytics: async (id) => { asked.push(id); return { ok: true, id }; },
  });
  await h.controller.open("dm:bob");
  assert.deepEqual(asked, ["bob"], "the raw agent id, never the dm: key");
  assert.equal(h.state.chat.analytics.agent, "bob");
}));

test("open() on the agent already under analytics falls back to messages", () => withStubDocument(async () => {
  // This is the "Back to chat" button: it opens dm:<the agent you are viewing>, and must LEAVE
  // analytics rather than reloading it.
  const asked = [];
  const h = pulseHarness({
    selected: "dm:alice",
    analytics: { agent: "alice", data: { ok: true } },
    loadAgentAnalytics: async (id) => { asked.push(id); return { ok: true }; },
  });
  await h.controller.open("dm:alice");
  assert.deepEqual(asked, [], "re-opening the same agent must not reload analytics");
  assert.equal(h.state.chat.analytics.agent, "", "it must leave the analytics view");
}));

test("open() on a channel loads that channel's conversation", () => withStubDocument(async () => {
  const h = pulseHarness();
  await h.controller.open("channel:ops");
  assert.deepEqual(h.loaded, ["ops"], "the channel name, without the key prefix");
}));

test("close() drops the cached pulse so returning refetches it", () => withStubDocument(async () => {
  const h = pulseHarness({ selected: "dm:alice" });
  h.state.chat.pulse.data = { ok: true, stale: true };
  h.controller.close();
  assert.equal(h.state.chat.selected, "");
  assert.equal(h.state.chat.pulse.data, null, "a stale pulse must not be what greets you on return");
}));

test("loadFleetPulse patches agent statuses from the payload so rail and pulse agree", () => withStubDocument(async () => {
  // 2026-07-02 screenshot incident: the pulse board carried freshly-derived statuses while the rail
  // rendered an older /agents poll, so one frame showed a green dot beside "working now".
  const h = pulseHarness({
    loadPulse: async () => ({ ok: true, agents: [{ id: "alice", status: "working" }] }),
  });
  h.controller.refreshPulse(true);
  await settle();
  assert.equal(h.state.agents.find((a) => a.id === "alice").status, "working", "the shared roster must be patched");
  assert.equal(h.state.agents.find((a) => a.id === "bob").status, "online", "an agent the pulse did not mention is untouched");
}));

test("loadFleetPulse discards a payload whose window is no longer selected", () => withStubDocument(async () => {
  // The stale-async guard. Switching window mid-flight must not paint the previous window's numbers.
  let release;
  const gate = new Promise((r) => { release = r; });
  const h = pulseHarness({ loadPulse: async () => { await gate; return { ok: true, window: "24h" }; } });
  h.controller.refreshPulse(true);
  await settle();
  h.state.chat.pulse.window = "7d";
  release();
  await settle();
  await settle();
  assert.equal(h.state.chat.pulse.data, null, "the 24h payload must not land on a 7d view");
}));

test("loadFleetPulse records a failure rather than leaving the old numbers up", () => withStubDocument(async () => {
  const h = pulseHarness({ loadPulse: async () => { throw new Error("network"); } });
  h.controller.refreshPulse(true);
  await settle();
  await settle();
  assert.deepEqual(h.state.chat.pulse.data, { ok: false }, "a failed fetch is a state, not silence");
}));

test("loadFleetPulse throttles unforced refetches", () => withStubDocument(async () => {
  let calls = 0;
  const h = pulseHarness({ loadPulse: async () => { calls += 1; return { ok: true }; } });
  h.controller.refreshPulse(true);
  await settle();
  assert.equal(calls, 1);
  h.controller.refreshPulse();
  await settle();
  assert.equal(calls, 1, "a poll tick moments later must not refetch");
  h.controller.refreshPulse(true);
  await settle();
  assert.equal(calls, 2, "force is what overrides the throttle");
}));

test("openAnalytics discards a payload for an agent you have already left", () => withStubDocument(async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const h = pulseHarness({
    loadAgentAnalytics: async (id) => { await gate; return { ok: true, id }; },
  });
  const inflight = h.controller.openAnalytics("alice");
  h.state.chat.analytics.agent = "bob"; // operator clicked away mid-flight
  release();
  await inflight;
  assert.equal(h.state.chat.analytics.data, null, "alice's numbers must not appear under bob's name");
}));

test("openAnalytics records a failed load instead of an empty panel", () => withStubDocument(async () => {
  const h = pulseHarness({ loadAgentAnalytics: async () => { throw new Error("boom"); } });
  await h.controller.openAnalytics("alice");
  assert.deepEqual(h.state.chat.analytics.data, { ok: false });
}));

// ---- the rail says what it is built from ----------------------------------------------------------
//
// MEASURED ON THE OPERATOR'S DATABASE, 2026-08-29: 3,189 messages addressed to `dashboard`, 1,792 of
// them unread, and 29 unread inside the 80-row page the rail is built from. Every per-conversation
// badge counts unread rows in THAT page, so a badge could under-report by roughly sixty to one while
// looking authoritative -- and the response had been carrying `showing` and `total` all along, dropped
// by the transport one function from where they were needed.

function railWith(messageCounts) {
  // THROUGH THE STUB DOCUMENT. `render()` reads `document.activeElement` to avoid stealing focus, so
  // without it these fail on the DOM rather than on the assertion.
  return withStubDocument(() => {
    const harness = sendHarness();
    // `render()` reaches into the composer for the draft field, which `sendHarness` does not build --
    // it exists for send(), not for a full render. Filled in here rather than widened there, so the
    // send tests keep their small fixture.
    const stub = { innerHTML: "", textContent: "", value: "", hidden: false, dataset: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      setAttribute() {}, addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      scrollHeight: 0, scrollTop: 0, clientHeight: 0 };
    for (const id of ["chat-composer", "chat-scroll-bottom", "chat-identity", "chat-new-channel-form",
      "chat-conv-title", "chat-msg-search", "chat-timeline", "chat-conv-actions", "chat-rail-list"]) {
      harness.els[id] = { ...stub };
    }
    harness.state.messageCounts = messageCounts;
    harness.controller.render();
    return harness.els["chat-rail-list"].innerHTML;
  });
}

//: Everything OUTSIDE an attribute value -- i.e. what a reader actually sees on the page. The hint
//: is meant to be one glyph; asserting on raw HTML cannot tell a tooltip from a paragraph, because
//: both contain the sentence.
const visibleText = (html) => String(html).replace(/"[^"]*"/g, '""');

test("A PARTIAL RAIL SAYS SO, and says the badges are partial too", async () => {
  const html = await railWith({ showing: 80, truncated: true });
  assert.match(html, /Showing the 80 most recent messages/);
  assert.match(html, /older ones are not loaded/i);
  assert.match(html, /unread badges count only these/i,
    "a badge counted from a page, beside a note that does not say so, still reads as authoritative");
  assert.match(html, /Direct messages/, "the note must not replace the rail it annotates");
  // NO FLEET TOTAL IN THE SENTENCE. The rendered list is the recent feed; 3,189 belongs to one
  // agent's inbox, and putting it here would conflate two populations behind a correct-looking number.
  assert.doesNotMatch(html, /3,189|of \d+ messages/);
});

test("the caveat is a HINT, not a paragraph across the rail", async () => {
  // The operator's complaint, pinned. The sentence was never wrong; it occupied a row of the list it
  // annotates, on every partial render. It now lives on a hover/focus affordance.
  const html = await railWith({ showing: 80, truncated: true });
  assert.match(html, /class="chat-rail-why"/, "the hint affordance is gone");
  assert.doesNotMatch(html, /chat-rail-note subtle/, "the paragraph is back");
  assert.doesNotMatch(visibleText(html), /most recent messages/,
    "the caveat is rendered as visible text again rather than as a hint");
});

test("the hint is reachable by keyboard and named for a screen reader", async () => {
  // A `?` that only answers a mouse is a caveat keyboard users never receive, and a lone "?" is not
  // an accessible name -- which is why the same sentence carries both roles.
  const html = await railWith({ showing: 80, truncated: true });
  assert.match(html, /<button type="button" class="chat-rail-why"/, "the hint must be focusable");
  assert.match(html, /aria-label="Showing the 80 most recent messages/, "the hint has no name");
  assert.match(html, /title="Showing the 80 most recent messages/, "the hint has no hover text");
});

test("a complete rail says nothing", async () => {
  // The control. A note on every render is one nobody reads, and it would be false here.
  const html = await railWith({ showing: 12, truncated: false });
  assert.doesNotMatch(html, /most recent messages/);
});

test("before the first load there is no note rather than a wrong one", async () => {
  // Zeroes are the pre-load state. "showing the 0 most recent of 0" is worse than silence.
  assert.doesNotMatch(await railWith({ showing: 0, truncated: false }), /most recent messages/);
  assert.doesNotMatch(await railWith({ showing: 0, truncated: true }), /most recent messages/,
    "truncated with nothing loaded is the pre-load state, not a page");
  assert.doesNotMatch(await railWith(undefined), /most recent messages/);
});

test("the note names the count it can prove and no other", async () => {
  // `showing` is the rows on screen. There is no fleet total in this sentence because the endpoint
  // that supplies the list does not report one, and borrowing another endpoint's total is the
  // conflation this note was rewritten to avoid.
  const html = await railWith({ showing: 41, truncated: true });
  assert.match(html, /Showing the 41 most recent messages/);
});
