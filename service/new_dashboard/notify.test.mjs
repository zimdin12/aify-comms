import assert from "node:assert/strict";
import { test } from "node:test";
import {
  COALESCE_WINDOW_MS,
  OPERATOR_RECIPIENT,
  STORAGE_KEY,
  buildNotification,
  coalesceKey,
  createNotifier,
  isForOperator,
  readEnabled,
  requestPermission,
  writeEnabled,
} from "./notify.mjs";

// A minimal stand-in for the browser Notification constructor that records what was raised.
function fakeNotificationApi(permission = "granted") {
  const raised = [];
  function FakeNotification(title, options) {
    raised.push({ title, ...options });
  }
  FakeNotification.permission = permission;
  FakeNotification.requestPermission = async () => permission;
  FakeNotification.raised = raised;
  return FakeNotification;
}

function notifierWith(overrides = {}) {
  const api = overrides.notificationApi || fakeNotificationApi();
  let clock = 1_000_000;
  const n = createNotifier({
    notificationApi: api,
    isEnabled: () => true,
    isFocused: () => false,
    now: () => clock,
    ...overrides,
  });
  return { n, api, tick: (ms) => { clock += ms; }, at: () => clock };
}

// ── who a message is for ─────────────────────────────────────────────────────────────
test("a message addressed to the operator qualifies", () => {
  assert.equal(isForOperator("message_sent", { to: [OPERATOR_RECIPIENT] }), true);
  assert.equal(isForOperator("message_sent", { to: "dashboard" }), true);
  assert.equal(isForOperator("message_sent", { to: ["DASHBOARD"] }), true, "case-insensitive");
});

test("fleet chatter between agents does NOT qualify", () => {
  // The hazard this feature is shaped around: 3,883 messages in 14 days, almost all agent-to-agent.
  assert.equal(isForOperator("message_sent", { to: ["sc-coder"] }), false);
  assert.equal(isForOperator("message_sent", { to: ["sc-coder", "sc-architect"] }), false);
  assert.equal(isForOperator("message_sent", {}), false);
  assert.equal(isForOperator("message_sent", { to: null }), false);
});

test("a message to the operator AMONG others still qualifies", () => {
  assert.equal(isForOperator("message_sent", { to: ["sc-coder", "dashboard"] }), true);
});

test("non-message socket events never qualify", () => {
  for (const e of ["message_read_state", "message_deleted", "shared", "terminal_output", ""]) {
    assert.equal(isForOperator(e, { to: ["dashboard"] }), false, e);
  }
});

// ── channel messages: membership-gated, FAIL CLOSED ──────────────────────────────────
// Blocking review finding: the first cut returned true for EVERY channel_message. The dashboard
// can SEE every channel, not only the ones it joined, so that notified on channels the operator
// never subscribed to — the volume failure this feature exists to avoid.
test("a channel the operator is a member of qualifies", () => {
  const member = (c) => c === "sand-castle";
  assert.equal(
    isForOperator("channel_message", { channel: "sand-castle", from: "sc-manager" }, { isChannelSubscribed: member }),
    true,
  );
});

test("a channel the operator is NOT a member of does not qualify", () => {
  const member = (c) => c === "sand-castle";
  assert.equal(
    isForOperator("channel_message", { channel: "echoes", from: "ef-manager" }, { isChannelSubscribed: member }),
    false,
  );
});

test("unknown membership FAILS CLOSED", () => {
  // The channel list loads asynchronously; notifying-when-unsure would fire on everything for the
  // first seconds after every reload. Staying quiet costs at most a missed popup for a message
  // that is still sitting in the dashboard.
  assert.equal(isForOperator("channel_message", { channel: "sand-castle" }), false, "no resolver = closed");
  assert.equal(
    isForOperator("channel_message", { channel: "sand-castle" }, { isChannelSubscribed: () => undefined }),
    false,
  );
});

test("a membership lookup that throws fails closed rather than breaking the socket", () => {
  const boom = () => { throw new Error("state not ready"); };
  assert.equal(isForOperator("channel_message", { channel: "x" }, { isChannelSubscribed: boom }), false);
});

test("a channel_message with no channel name never qualifies", () => {
  assert.equal(isForOperator("channel_message", {}, { isChannelSubscribed: () => true }), false);
});

test("the notifier honours membership end to end", () => {
  const { n, api } = notifierWith({ isChannelSubscribed: (c) => c === "sand-castle" });
  assert.equal(n.handle("channel_message", { channel: "echoes", from: "ef-manager", body: "x" }), "not-for-operator");
  assert.equal(api.raised.length, 0);
  assert.equal(n.handle("channel_message", { channel: "sand-castle", from: "sc-manager", body: "y" }), "fired");
  assert.equal(api.raised.length, 1);
});

test("the notifier defaults to closed for channels when no resolver is injected", () => {
  const { n, api } = notifierWith();
  assert.equal(n.handle("channel_message", { channel: "sand-castle", from: "a", body: "b" }), "not-for-operator");
  assert.equal(api.raised.length, 0);
});

// ── the suppression rules, each independently ────────────────────────────────────────
test("off by default — nothing fires when disabled", () => {
  const { n, api } = notifierWith({ isEnabled: () => false });
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "a" }), "disabled");
  assert.equal(api.raised.length, 0);
});

test("a focused tab suppresses — you are already looking at it", () => {
  const { n, api } = notifierWith({ isFocused: () => true });
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "a" }), "focused");
  assert.equal(api.raised.length, 0);
});

test("without permission nothing is raised", () => {
  const api = fakeNotificationApi("default");
  const { n } = notifierWith({ notificationApi: api });
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "a" }), "no-permission");
  assert.equal(api.raised.length, 0);
});

test("a browser without the API degrades quietly", () => {
  const { n } = notifierWith({ notificationApi: null });
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "a" }), "unsupported");
});

test("fleet chatter is rejected even when everything else is permissive", () => {
  const { n, api } = notifierWith();
  assert.equal(n.handle("message_sent", { to: ["sc-coder"], from: "sc-manager" }), "not-for-operator");
  assert.equal(api.raised.length, 0);
});

// ── the happy path ───────────────────────────────────────────────────────────────────
test("an operator-addressed message on an unfocused tab fires exactly one notification", () => {
  const { n, api } = notifierWith();
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "sc-manager", subject: "gate PASS" }), "fired");
  assert.equal(api.raised.length, 1);
  assert.equal(api.raised[0].title, "sc-manager → you");
  assert.equal(api.raised[0].body, "gate PASS");
});

test("a channel message renders with its channel", () => {
  const { n, api } = notifierWith({ isChannelSubscribed: () => true });
  n.handle("channel_message", { channel: "sand-castle", from: "sc-manager", body: "lane picked" });
  assert.equal(api.raised[0].title, "#sand-castle — sc-manager");
  assert.equal(api.raised[0].body, "lane picked");
});

// ── coalescing: the burst behaviour that decides whether this stays switched on ───────
test("a burst on the same subject raises ONE notification", () => {
  const { n, api, tick } = notifierWith();
  const msg = { to: ["dashboard"], from: "sc-manager", subject: "#178 take it now" };
  assert.equal(n.handle("message_sent", msg), "fired");
  tick(30_000);
  assert.equal(n.handle("message_sent", msg), "coalesced");
  tick(30_000);
  assert.equal(n.handle("message_sent", msg), "coalesced");
  assert.equal(api.raised.length, 1, "a 2-4min/message ping-pong must not become N popups");
});

test("the same subject after the window fires again", () => {
  const { n, api, tick } = notifierWith();
  const msg = { to: ["dashboard"], from: "sc-manager", subject: "#178" };
  n.handle("message_sent", msg);
  tick(COALESCE_WINDOW_MS + 1);
  assert.equal(n.handle("message_sent", msg), "fired");
  assert.equal(api.raised.length, 2);
});

test("different subjects are not collapsed into each other", () => {
  const { n, api } = notifierWith();
  n.handle("message_sent", { to: ["dashboard"], from: "sc-manager", subject: "one" });
  n.handle("message_sent", { to: ["dashboard"], from: "sc-manager", subject: "two" });
  n.handle("message_sent", { to: ["dashboard"], from: "sc-coder", subject: "one" });
  assert.equal(api.raised.length, 3, "sender AND subject distinguish");
});

test("coalescing keys on sender+subject, NOT message id", () => {
  // Keying on id would coalesce nothing — every message has a unique one — which is exactly the
  // burst this feature must survive.
  const a = coalesceKey("message_sent", { id: "m1", from: "x", subject: "s" });
  const b = coalesceKey("message_sent", { id: "m2", from: "x", subject: "s" });
  assert.equal(a, b);
});

test("the coalesce map does not grow without bound", () => {
  const { n, tick } = notifierWith();
  for (let i = 0; i < 200; i += 1) {
    n.handle("message_sent", { to: ["dashboard"], from: `a${i}`, subject: `s${i}` });
    tick(1000);
  }
  // No direct handle on the map; assert indirectly that a long-idle key is re-fireable and that
  // nothing threw across 200 distinct keys.
  tick(COALESCE_WINDOW_MS * 20);
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "a0", subject: "s0" }), "fired");
});

// ── content safety ───────────────────────────────────────────────────────────────────
test("long bodies are truncated so a notification cannot be a wall of text", () => {
  const { n, api } = notifierWith();
  n.handle("message_sent", { to: ["dashboard"], from: "a", subject: "x".repeat(5000) });
  assert.ok(api.raised[0].body.length <= 180);
});

test("missing fields degrade to something readable, never undefined", () => {
  const { n, api } = notifierWith();
  n.handle("message_sent", { to: ["dashboard"] });
  assert.equal(api.raised[0].title, "agent → you");
  assert.equal(api.raised[0].body, "(no subject)");
});

test("a throwing Notification constructor does not break the socket handler", () => {
  function Boom() { throw new Error("blocked by browser"); }
  Boom.permission = "granted";
  const { n } = notifierWith({ notificationApi: Boom });
  assert.equal(n.handle("message_sent", { to: ["dashboard"], from: "a" }), "failed");
});

// ── permission + persistence ─────────────────────────────────────────────────────────
test("permission is not re-requested once granted or denied", async () => {
  let asked = 0;
  const api = fakeNotificationApi("granted");
  api.requestPermission = async () => { asked += 1; return "granted"; };
  assert.equal(await requestPermission(api), "granted");
  assert.equal(asked, 0, "already granted — must not prompt again");

  const denied = fakeNotificationApi("denied");
  denied.requestPermission = async () => { asked += 1; return "granted"; };
  assert.equal(await requestPermission(denied), "denied");
  assert.equal(asked, 0, "denied is the browser's answer; re-prompting is what gets sites blocked");
});

test("requestPermission asks when undecided, and survives a throw", async () => {
  const api = fakeNotificationApi("default");
  assert.equal(await requestPermission(api), "default");
  const boom = fakeNotificationApi("default");
  boom.requestPermission = async () => { throw new Error("nope"); };
  assert.equal(await requestPermission(boom), "denied");
  assert.equal(await requestPermission(null), "unsupported");
});

test("the toggle is off by default and persists", () => {
  const store = new Map();
  const storage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
  };
  assert.equal(readEnabled(storage), false, "off by default");
  writeEnabled(storage, true);
  assert.equal(store.get(STORAGE_KEY), "1");
  assert.equal(readEnabled(storage), true);
  writeEnabled(storage, false);
  assert.equal(readEnabled(storage), false);
});

test("a storage-less or throwing context never breaks the dashboard", () => {
  const hostile = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
  };
  assert.equal(readEnabled(hostile), false);
  assert.equal(writeEnabled(hostile, true), false);
  assert.equal(readEnabled(null), false);
  assert.equal(writeEnabled(null, true), false);
});
