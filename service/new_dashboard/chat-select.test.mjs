#!/usr/bin/env node
// Tests for `chat-select.mjs`: which conversations the rail shows, in what order, and which
// messages belong to each.
//
// MOVED HERE FROM `chat.test.mjs` in v0.5.4 rather than rewritten, when the builders left `chat.js`.
// Every block below is the one that already covered these functions — a rewrite would have quietly
// changed what is asserted while looking like a relocation.

import assert from "node:assert/strict";
import test from "node:test";

import { chatConversationItems, dmMessages, sortChronological } from "./chat-select.mjs";

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

test("a starting agent sorts above dead ones, not below them", () => {
  // `starting` is a LIVE status -- status.js gives it dotKind 'working' and keeps it out of
  // NON_LIVE_AGENT_STATUSES -- but the sort rank was a hand-written map that never listed it, so it
  // fell through to the `unknown` fallback and ranked BELOW offline and stopped. A booting agent sank
  // beneath dead ones in the rail that exists to surface who is doing something.
  const items = chatConversationItems({
    agents: [
      { id: "gone", status: "offline" },
      { id: "halted", status: "stopped" },
      { id: "booting", status: "starting" },
    ],
    messages: [],
    chat: { identity: "dashboard", channels: [], sortMode: "status" },
  });
  const order = items.filter((i) => i.kind !== "channel").map((i) => i.id);
  assert.ok(
    order.indexOf("booting") < order.indexOf("gone"),
    `starting sorted below offline: ${order.join(" < ")}`,
  );
  assert.ok(
    order.indexOf("booting") < order.indexOf("halted"),
    `starting sorted below stopped: ${order.join(" < ")}`,
  );
});

test("every live status outranks every dead one, whoever edits the vocabulary next", () => {
  // The rank is DERIVED now, which is what the note at the top of this file always claimed. A status
  // added to status.js and forgotten here lands above the dead group instead of beneath it, so the
  // next `starting` cannot repeat.
  const live = ["working", "blocked", "online", "available", "starting"];
  const dead = ["offline", "stopped", "misconfigured"];
  const items = chatConversationItems({
    agents: [...dead, ...live].map((s, i) => ({ id: `a${i}-${s}`, status: s })),
    messages: [],
    chat: { identity: "dashboard", channels: [], sortMode: "status" },
  });
  const order = items.filter((i) => i.kind !== "channel").map((i) => i.id);
  const worstLive = Math.max(...live.map((s) => order.findIndex((id) => id.endsWith(s))));
  const bestDead = Math.min(...dead.map((s) => order.findIndex((id) => id.endsWith(s))));
  assert.ok(worstLive < bestDead, `a dead agent outranked a live one: ${order.join(" < ")}`);
});
