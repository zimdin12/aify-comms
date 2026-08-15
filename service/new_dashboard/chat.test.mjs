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
