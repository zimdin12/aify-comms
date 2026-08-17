// The two REAL gates on a desktop notification: is the tab visible, and did the operator join that channel.
//
// From the dashboard V8-coverage census: `notifications.mjs`'s `isFocused` and `isChannelSubscribed` — the two
// closures it hands `createNotifier` — are never called by the suite. `notify.test.mjs` covers the decision
// logic with INJECTED gates, and `notifications.test.mjs` says outright why it cannot cover these: the notifier
// resolves the Notification API when the module is EVALUATED, so a stub installed afterwards is never seen and
// every path reports "unsupported". Its membership assertion therefore falls back to a source regex —
// `assert.match(src, /isChannelSubscribed:/)` — which proves a line was written, not that it answers correctly.
//
// THIS FILE INSTALLS THE BROWSER GLOBALS BEFORE THE DYNAMIC IMPORT, which is the only thing that was missing.
// The notifier then binds a constructible stub, `handle()` runs to completion, and the membership gate can be
// asserted by BEHAVIOUR instead of by grep.
//
// TWO MUTATIONS SURVIVE THIS FILE, neither a gap:
//
//   * Flipping `isChannelSubscribed = () => false` to `() => true` in `isForOperator`'s own signature. That
//     default is unreachable from here — `notifications.mjs` always supplies the real gate — and it is covered
//     where it belongs, in notify.test.mjs, which constructs notifiers without one.
//   * Dropping the `state.chat &&` guard from the membership closure. Without it a missing `state.chat` throws
//     inside the gate, and `isForOperator`'s own try/catch turns that into the same `false` — so the outcome is
//     identical. The two are one defence expressed twice, and the catch half IS caught (see the throwing
//     channel-list case below), which is what makes the pair tested rather than merely doubled.
//
// WHY THE MEMBERSHIP GATE MATTERS. It exists because of a review finding: the dashboard can SEE every channel,
// not only the ones it joined, so "any channel_message" notified on traffic nobody subscribed to. The fleet
// produced 3,883 messages in 14 days in two-agent bursts — a gate that fails open here does not merely annoy,
// it gets the whole feature switched off. It must also fail CLOSED while the channel list is still loading,
// because "unknown membership" is the normal state for the first seconds after every reload.

import assert from "node:assert/strict";
import test from "node:test";

// ── globals FIRST, then the import ──────────────────────────────────────────

const FIRED = [];
class NotificationStub {
  constructor(title, options) { FIRED.push({ title, ...options }); }
  static permission = "granted";
  static requestPermission() { return Promise.resolve("granted"); }
}

const STORE = new Map();
globalThis.Notification = NotificationStub;
globalThis.localStorage = {
  getItem: (k) => (STORE.has(k) ? STORE.get(k) : null),
  setItem: (k, v) => STORE.set(k, String(v)),
};
globalThis.document = { visibilityState: "hidden" };
globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };

const { dashboardNotifier, toggleNotifications } = await import("./notifications.mjs");
const { state } = await import("./state.mjs");

// The flag is module-scope and starts from localStorage; turn it on through the real handshake so every test
// below exercises the enabled path rather than short-circuiting at "disabled".
assert.equal(await toggleNotifications(true), true, "the permission handshake did not enable notifications");

function withChannels(channels, run) {
  const previous = state.chat;
  state.chat = { ...(previous || {}), channels };
  try {
    return run();
  } finally {
    state.chat = previous;
  }
}

function handle(event, data, { visibility = "hidden" } = {}) {
  FIRED.length = 0;
  globalThis.document.visibilityState = visibility;
  return dashboardNotifier.handle(event, data);
}

// EVERY PAYLOAD MUST BE UNIQUE, because the notifier's coalesce map is module-scope and lives for the whole
// file: two tests sending the same {from, subject} inside the 90s window make the SECOND one "coalesced". Four
// tests here failed that way before this helper existed — the coalescer was right and the fixtures collided.
let seq = 0;
const uniqueMessage = (extra = {}) => ({ to: "dashboard", from: `coder-${++seq}`, subject: `s-${seq}`, ...extra });
const uniqueChannel = (extra = {}) => ({ channel: "ops", from: `coder-${++seq}`, body: `b-${seq}`, ...extra });

// ── the sanity check that makes the rest meaningful ─────────────────────────

test("the notifier is WIRED here, not 'unsupported'", () => {
  // If this reports "unsupported" the API was bound before the stub existed and every assertion below would
  // be vacuous — which is exactly the state notifications.test.mjs documents. It is asserted first, loudly.
  const outcome = handle("message_sent", uniqueMessage({ from: "coder", subject: "hello" }));
  assert.notEqual(outcome, "unsupported",
    "the Notification stub was installed after the module bound its API — the rest of this file proves nothing");
  assert.equal(outcome, "fired");
  assert.equal(FIRED.length, 1, "no notification was constructed");
  assert.equal(FIRED[0].title, "coder → you");
});

// ── isFocused ───────────────────────────────────────────────────────────────

test("a VISIBLE dashboard suppresses the popup", () => {
  // The operator is already looking at it. This is `document.visibilityState === 'visible'` — the real closure,
  // not an injected one.
  assert.equal(handle("message_sent", uniqueMessage(), { visibility: "visible" }), "focused");
  assert.deepEqual(FIRED, []);
});

test("a HIDDEN dashboard lets it through", () => {
  assert.equal(handle("message_sent", uniqueMessage(), { visibility: "hidden" }), "fired");
});

test("any state that is not exactly 'visible' counts as not-focused", () => {
  // `prerender` and the empty string both occur; treating an unknown state as visible would silence
  // notifications for a tab the operator cannot see.
  for (const visibility of ["hidden", "prerender", "", "unloaded"]) {
    assert.equal(handle("message_sent", uniqueMessage(), { visibility }), "fired",
      `visibilityState=${JSON.stringify(visibility)} suppressed the notification`);
  }
});

test("with NO document at all, the gate does not suppress", () => {
  // `typeof document !== 'undefined' && ...`. Pinned as the current reading: no document means not-focused,
  // so notifications are allowed. In a browser the document always exists; this is the guard's other branch.
  const saved = globalThis.document;
  delete globalThis.document;
  try {
    FIRED.length = 0;
    assert.equal(dashboardNotifier.handle("message_sent", uniqueMessage()), "fired");
  } finally {
    globalThis.document = saved;
  }
});

// ── isChannelSubscribed ─────────────────────────────────────────────────────

test("a channel the operator JOINED notifies", () => {
  const outcome = withChannels(
    [{ name: "ops", members: ["dashboard", "coder"] }],
    () => handle("channel_message", uniqueChannel({ from: "coder" })),
  );
  assert.equal(outcome, "fired");
  assert.equal(FIRED[0].title, "#ops — coder");
});

test("a channel the operator can SEE but did not join does NOT notify", () => {
  // The review finding, now asserted by behaviour rather than by grepping for the property name. The dashboard
  // lists every channel; membership is the only thing that makes one the operator's.
  const outcome = withChannels(
    [{ name: "ops", members: ["coder", "tester"] }],
    () => handle("channel_message", uniqueChannel()),
  );
  assert.equal(outcome, "not-for-operator");
  assert.deepEqual(FIRED, []);
});

test("membership UNKNOWN fails closed — the channel list is still loading", () => {
  // The normal state for the first seconds after every reload. Notifying while unsure would fire on the whole
  // fleet's traffic exactly then, which is the volume failure the feature is shaped around.
  for (const channels of [[], undefined, null]) {
    const outcome = withChannels(channels, () => handle("channel_message", uniqueChannel()));
    assert.equal(outcome, "not-for-operator", `channels=${JSON.stringify(channels)} notified while unknown`);
  }
});

test("a channel row with no members array is not membership", () => {
  // Rows arrive from two shapes over time; a row that carries no `members` is not evidence of joining.
  for (const row of [{ name: "ops" }, { name: "ops", members: null }, { name: "ops", members: "dashboard" }]) {
    const outcome = withChannels([row], () => handle("channel_message", uniqueChannel()));
    assert.equal(outcome, "not-for-operator", `${JSON.stringify(row)} was treated as joined`);
  }
});

test("a DIFFERENT channel's membership does not grant this one", () => {
  const outcome = withChannels(
    [{ name: "other", members: ["dashboard"] }, { name: "ops", members: ["coder"] }],
    () => handle("channel_message", uniqueChannel()),
  );
  assert.equal(outcome, "not-for-operator", "membership of one channel notified for another");
});

test("the channel name is compared as a STRING on both sides", () => {
  // Names arrive from JSON and from the socket payload; a numeric-looking name must still match its row.
  const outcome = withChannels(
    [{ name: 123, members: ["dashboard"] }],
    () => handle("channel_message", uniqueChannel({ channel: "123" })),
  );
  assert.equal(outcome, "fired", "a numeric row name did not match its string channel");
});

test("state with NO chat section at all is not membership", () => {
  // The state before the chat page has ever loaded. `(state.chat && state.chat.channels) || []` guards it;
  // reading `state.chat.channels` directly would throw inside the gate, and the throw is then swallowed by
  // isForOperator's catch — so the operator would get silence with no error anywhere to explain it.
  const previous = state.chat;
  state.chat = undefined;
  try {
    assert.equal(handle("channel_message", uniqueChannel()), "not-for-operator");
  } finally {
    state.chat = previous;
  }
});

test("a channel list that THROWS fails closed rather than notifying", () => {
  // The gate is called inside isForOperator's try/catch, and that catch answers false. A malformed channels
  // value — anything without a working `find` — must therefore mean "not subscribed", never "notify anyway".
  const previous = state.chat;
  state.chat = { channels: { find() { throw new Error("malformed channel list"); } } };
  try {
    assert.equal(handle("channel_message", uniqueChannel()), "not-for-operator");
    assert.deepEqual(FIRED, []);
  } finally {
    state.chat = previous;
  }
});

test("a channel_message with no channel is not for the operator", () => {
  const outcome = withChannels([{ name: "ops", members: ["dashboard"] }],
    () => handle("channel_message", uniqueChannel({ channel: undefined })));
  assert.equal(outcome, "not-for-operator");
});

test("a message NOT addressed to the operator never reaches the gates", () => {
  assert.equal(handle("message_sent", uniqueMessage({ to: "coder" })), "not-for-operator");
  assert.deepEqual(FIRED, []);
});

test("a channel the operator joined still coalesces a burst", () => {
  // The gates and the coalescer compose: membership lets the first one through, and the second identical one
  // inside the window collapses into it. Two-agent ping-pong at 2-4 minutes is the measured cadence.
  const burst = uniqueChannel();
  const outcomes = withChannels([{ name: "ops", members: ["dashboard"] }], () => [
    handle("channel_message", burst),
    dashboardNotifier.handle("channel_message", burst),
  ]);
  assert.deepEqual(outcomes, ["fired", "coalesced"]);
});
