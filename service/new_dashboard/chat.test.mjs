#!/usr/bin/env node
// Tests for the pure chat builders (chat.js): dmMessages, chatConversationItems sorting/
// filtering, and the delivery-toast ladder. The rail/timeline DOM rendering is exercised
// live in the browser.
//
// Run: node --test service/new_dashboard/chat.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { dmMessages, chatConversationItems, deliveryToastFor } from "./chat.js";

test("dmMessages keeps only messages to/from the peer", () => {
  const msgs = [
    { id: "1", from: "alice", to: "dashboard" },
    { id: "2", from: "dashboard", to: "alice" },
    { id: "3", from: "bob", to: "dashboard" },
    { id: "4", from: "carol", targetAgentId: "alice" },
  ];
  const got = dmMessages(msgs, "alice").map((m) => m.id);
  assert.deepEqual(got, ["1", "2", "4"]);
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

test("deliveryToastFor maps the send response to the truthful ladder", () => {
  assert.equal(deliveryToastFor({ runs: [{ steered: true }] }, "x").text, "Steered into x's active turn");
  assert.equal(deliveryToastFor({ runs: [{ status: "queued" }] }, "x").tone, "info");
  assert.equal(deliveryToastFor({ consoleDeliveries: [{}], runs: [] }, "x").text, "Delivered to x's console");
  assert.equal(deliveryToastFor({ runs: [{ status: "running" }] }, "x").text, "Woke x");
  assert.equal(deliveryToastFor({ runs: [], notStarted: [{}] }, "x").tone, "warn");
  assert.equal(deliveryToastFor({ ok: false, error: "offline" }, "x").tone, "error");
});

