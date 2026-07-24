#!/usr/bin/env node
// Tests for the pure chat builders (chat.js): dmMessages, chatConversationItems sorting/
// filtering, and the delivery-toast ladder. The rail/timeline DOM rendering is exercised
// live in the browser.
//
// Run: node --test service/new_dashboard/chat.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatController, dmMessages, chatConversationItems, deliveryToastFor, messageHtml, sortChronological } from "./chat.js";

test("sortChronological orders oldest→newest so the newest sits at the bottom (2026-07-06)", () => {
  // /messages/recent returns DESCENDING (newest first). The timeline must show ascending.
  const recentOrder = [
    { id: "new", timestamp: 3000 },
    { id: "mid", timestamp: 2000 },
    { id: "old", timestamp: 1000 },
  ];
  const got = sortChronological(recentOrder).map((m) => m.id);
  assert.deepEqual(got, ["old", "mid", "new"], "newest must end up last (rendered at the bottom)");
});

test("sortChronological is pure (does not mutate the input) and tolerates field-name/absent timestamps", () => {
  const input = [
    { id: "b", createdAt: 2000 },
    { id: "a", time: 1000 },
    { id: "z" }, // no timestamp → treated as 0, sorts first, never throws
  ];
  const snapshot = input.map((m) => m.id);
  const got = sortChronological(input).map((m) => m.id);
  assert.deepEqual(got, ["z", "a", "b"]);
  assert.deepEqual(input.map((m) => m.id), snapshot, "input array order must be untouched");
  assert.deepEqual(sortChronological(null), []);
  assert.deepEqual(sortChronological(undefined), []);
});

test("dmMessages keeps only messages between the viewing identity and peer", () => {
  const msgs = [
    { id: "1", from: "alice", to: "dashboard" },
    { id: "2", from: "dashboard", to: "alice" },
    { id: "3", from: "bob", to: "dashboard" },
    { id: "4", from: "carol", targetAgentId: "alice" },
  ];
  const got = dmMessages(msgs, "alice").map((m) => m.id);
  assert.deepEqual(got, ["1", "2"]);
  assert.deepEqual(dmMessages(msgs, "alice", "all").map((m) => m.id), ["1", "2", "4"]);
});

test("chatConversationItems does not use third-party traffic for a dashboard DM", () => {
  const items = chatConversationItems({
    agents: [{ id: "alice", status: "online" }, { id: "carol", status: "online" }],
    messages: [
      { id: "mine", from: "alice", to: "dashboard", body: "for dashboard", timestamp: 1 },
      { id: "other", from: "carol", to: "alice", body: "private handoff", timestamp: 2 },
    ],
    chat: { identity: "dashboard", channels: [] },
  });
  const alice = items.find((item) => item.id === "alice");
  assert.equal(alice.msgCount, 1);
  assert.equal(alice.preview, "for dashboard");
});

test("dmMessages excludes channel-broadcast rows (bughunt 2026-07-03)", () => {
  const msgs = [
    { id: "1", from: "alice", to: "dashboard" },
    { id: "c", from: "alice", to: null, source: "channel", channel: "sand-castle" },
    { id: "c2", from: "alice", channel: "status" },
  ];
  // A channel post from the same peer must NOT render in the DM timeline (it would
  // get DM-only Reply/Mark-read/Unsend controls that misfire).
  assert.deepEqual(dmMessages(msgs, "alice").map((m) => m.id), ["1"]);
});

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

test("chatConversationItems pins favorites, then sorts by activity (default)", () => {
  const state = {
    agents: [
      { id: "quiet", status: "online", favorited: false },
      { id: "fav", status: "online", favorited: true },
      { id: "unread", status: "online" },
      { id: "dashboard" }, // excluded
    ],
    messages: [
      { id: "m1", from: "unread", to: "dashboard", read: false },
      { id: "m2", from: "fav", to: "dashboard", read: true, timestamp: "2026-06-16T10:00:00Z" },
    ],
    chat: { identity: "dashboard", channels: [{ name: "sand-castle", memberCount: 6, unreadCount: 0 }] },
  };
  const items = chatConversationItems(state);
  assert.equal(items[0].id, "fav", "favorites pin to the top");
  assert.ok(items.find((i) => i.kind === "channel" && i.id === "sand-castle"), "channels included");
  assert.equal(items.find((i) => i.id === "dashboard"), undefined, "dashboard excluded from rail");
});

test("chatConversationItems honors sortMode=unread and open-only/working-up toggles", () => {
  const base = {
    agents: [
      { id: "a-busy", status: "working" },
      { id: "z-unread", status: "idle" },
      { id: "m-quiet", status: "online" },
    ],
    messages: [
      { id: "u1", from: "z-unread", to: "dashboard", read: false },
      { id: "q1", from: "m-quiet", to: "dashboard", read: true },
    ],
  };
  const unreadSort = chatConversationItems({ ...base, chat: { identity: "dashboard", sortMode: "unread", channels: [] } });
  assert.equal(unreadSort[0].id, "z-unread", "unread sort floats the unread DM");
  // open-only drops the DM with no loaded messages (a-busy)
  const openOnly = chatConversationItems({ ...base, chat: { identity: "dashboard", openOnly: true, channels: [] } });
  assert.ok(!openOnly.find((i) => i.id === "a-busy"), "open-only hides message-less DMs");
  // working-up floats the working agent to the top
  const workingUp = chatConversationItems({ ...base, chat: { identity: "dashboard", workingUp: true, channels: [] } });
  assert.equal(workingUp[0].id, "a-busy", "working-up floats the working agent");
});

test("chatConversationItems honors the search filter and liveOnly", () => {
  const state = {
    agents: [
      { id: "sc-coder", status: "online" },
      { id: "ef-tester", status: "offline" },
    ],
    messages: [],
    chat: { identity: "dashboard", filter: "coder", liveOnly: false, channels: [] },
  };
  const filtered = chatConversationItems(state);
  assert.deepEqual(filtered.map((i) => i.id), ["sc-coder"]);

  const live = chatConversationItems({ ...state, chat: { ...state.chat, filter: "", liveOnly: true } });
  assert.deepEqual(live.map((i) => i.id), ["sc-coder"], "offline agent hidden under liveOnly");
});

test("chatConversationItems honors scope, unreadOnly, and the status filter set", () => {
  const base = {
    agents: [
      { id: "w", status: "working" },
      { id: "off", status: "offline" },
      { id: "fav", status: "online", favorited: true },
    ],
    messages: [{ id: "u1", from: "w", to: "dashboard", read: false }],
    chat: { identity: "dashboard", channels: [{ name: "room", memberCount: 3 }] },
  };
  // scope=dm hides channels; scope=channel hides DMs; scope=favorites keeps only favorited
  const dmOnly = chatConversationItems({ ...base, chat: { ...base.chat, scope: "dm" } });
  assert.ok(!dmOnly.find((i) => i.kind === "channel"), "scope=dm hides channels");
  const chOnly = chatConversationItems({ ...base, chat: { ...base.chat, scope: "channel" } });
  assert.deepEqual(chOnly.map((i) => i.id), ["room"], "scope=channel keeps only channels");
  const favOnly = chatConversationItems({ ...base, chat: { ...base.chat, scope: "favorites" } });
  assert.deepEqual(favOnly.map((i) => i.id), ["fav"], "scope=favorites keeps only favorited");
  // unreadOnly keeps only conversations with unread > 0 (the 'w' DM has an unread message)
  const unread = chatConversationItems({ ...base, chat: { ...base.chat, unreadOnly: true } });
  assert.deepEqual(unread.map((i) => i.id), ["w"], "unreadOnly keeps only unread conversations");
  // statusFilter keeps matching statuses (+ channels/unread/favorited always pass the status gate)
  const statusF = chatConversationItems({ ...base, chat: { ...base.chat, statusFilter: new Set(["offline"]) } });
  assert.ok(statusF.find((i) => i.id === "off"), "status filter keeps matching-status DMs");
  assert.ok(!statusF.find((i) => i.id === "w" && false), "non-matching plain DM excluded unless unread/fav");
});

test("deliveryToastFor maps the send response to the truthful ladder", () => {
  assert.equal(deliveryToastFor({ runs: [{ steered: true }] }, "x").text, "Steered into x's active turn");
  assert.equal(deliveryToastFor({ runs: [{ status: "queued" }] }, "x").tone, "info");
  assert.equal(deliveryToastFor({ consoleDeliveries: [{}], runs: [] }, "x").text, "Delivered to x's console");
  assert.equal(deliveryToastFor({ runs: [{ status: "running" }] }, "x").text, "Woke x");
  assert.equal(deliveryToastFor({ runs: [], notStarted: [{}] }, "x").tone, "warn");
  assert.equal(deliveryToastFor({ ok: false, error: "offline" }, "x").tone, "error");
});

