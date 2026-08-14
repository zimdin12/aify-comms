// The notification state and the permission handshake, tested by CALLING them.
//
// Every DECISION about whether to notify lives in notify.mjs and is tested there. What is here is the
// flag, the notifier's wiring into it, and the handshake — and the handshake has one constraint that
// is easy to lose and impossible to recover from: the browser permission prompt must come from a USER
// GESTURE. A page that asks on load gets the site denied permanently, and the only way back is the
// browser's own settings, which most operators will never find.
//
// So the test that matters most is the negative one: turning notifications OFF must never ask.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { dashboardNotifier, notificationsEnabled, toggleNotifications } from "./notifications.mjs";

function withNotifications({ permission = "granted", promptAnswer = null, stored = null, focused = false } = {}) {
  const saved = {
    Notification: globalThis.Notification,
    localStorage: globalThis.localStorage,
    document: globalThis.document,
    requestAnimationFrame: globalThis.requestAnimationFrame,
  };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  const asked = [];
  const written = [];
  // CONSTRUCTIBLE, not a plain object: the notifier does `new Notification(...)` on the path where it
  // actually fires, and a non-constructible stub turns that into a throw the module swallows — which
  // makes "fired" and "suppressed" look identical and every comparison below vacuous.
  const fired = [];
  globalThis.Notification = class FakeNotification {
    static permission = permission;
    static requestPermission = async () => { asked.push(permission); return promptAnswer ?? permission; };
    constructor(title, options) { fired.push([title, options]); this.close = () => {}; this.addEventListener = () => {}; }
  };
  const store = new Map(stored ? Object.entries(stored) : []);
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { written.push([k, v]); store.set(k, v); },
    removeItem: (k) => store.delete(k),
  };
  globalThis.document = {
    // HIDDEN by default. A visible dashboard suppresses every notification — the operator is already
    // looking at it — so a "visible" fake makes every handle() return "focused" and every comparison
    // below trivially equal. That is how the first version of this file passed vacuously.
    visibilityState: focused ? "visible" : "hidden",
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({ className: "", innerHTML: "", textContent: "", children: [], firstElementChild: null, setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], classList: { add() {}, remove() {}, toggle() {} }, style: {} }),
    body: { children: [], firstElementChild: null, appendChild() {}, classList: { add() {}, remove() {} }, style: { setProperty() {} } },
  };
  state.chat = { ...(state.chat || {}), channels: [] };
  return { asked, written, fired, restore: () => Object.assign(globalThis, saved) };
}

test("TURNING NOTIFICATIONS OFF NEVER ASKS FOR PERMISSION", async () => {
  // The one that cannot be recovered from. Asking outside a user gesture — and switching OFF is not a
  // request to be notified — gets the site permanently denied by the browser, with no way back except
  // the browser's own settings.
  const h = withNotifications({ permission: "default", promptAnswer: "granted" });
  try {
    const result = await toggleNotifications(false);
    assert.deepEqual(h.asked, [], "switching off must not touch the permission API");
    assert.equal(result, false);
    assert.equal(h.written.length, 1, "the choice must still be remembered across reloads");
  } finally { h.restore(); }
});

test("an ALREADY-GRANTED permission is not re-prompted", async () => {
  // Prompting again when the answer is already yes is a needless interruption, and browsers may not
  // show it at all — so a handshake that depended on the callback would never enable.
  const h = withNotifications({ permission: "granted" });
  try {
    const result = await toggleNotifications(true);
    assert.deepEqual(h.asked, [], "granted means granted");
    assert.equal(result, true);
    assert.equal(notificationsEnabled, true, "the live binding must reflect the new state");
  } finally { h.restore(); }
});

test("an UNDECIDED permission goes through the prompt, and enables when it comes back granted", async () => {
  const h = withNotifications({ permission: "default", promptAnswer: "granted" });
  try {
    const result = await toggleNotifications(true);
    assert.equal(h.asked.length, 1, "an undecided permission must actually be asked for");
    assert.equal(result, true);
  } finally { h.restore(); }
});

test("A DENIED PERMISSION LEAVES THE FLAG OFF and writes nothing", async () => {
  // Returning true here would leave the dashboard believing it can notify while the browser drops
  // every attempt — silent, and indistinguishable from having no messages.
  const h = withNotifications({ permission: "denied" });
  try {
    // The flag is MODULE state and survives between tests, so it is set explicitly rather than
    // assumed. My first version inherited `true` from the test above and failed on that, not on the
    // behaviour it names.
    await toggleNotifications(false);
    h.written.length = 0;
    const result = await toggleNotifications(true);
    assert.equal(result, false, "a denied prompt must not report success");
    assert.equal(notificationsEnabled, false);
    assert.deepEqual(h.written, [], "…and must not persist an enabled state it does not have");
  } finally { h.restore(); }
});

test("a DISMISSED prompt is treated as not granted", async () => {
  // `default` is what a browser returns when the operator closes the prompt without choosing. It is
  // not `denied`, and a check written as `=== 'denied'` would take it as success.
  const h = withNotifications({ permission: "default", promptAnswer: "default" });
  try {
    await toggleNotifications(false);
    const result = await toggleNotifications(true);
    assert.equal(result, false);
    assert.equal(notificationsEnabled, false);
  } finally { h.restore(); }
});

test("the flag round-trips: on, then off", async () => {
  const h = withNotifications({ permission: "granted" });
  try {
    await toggleNotifications(true);
    assert.equal(notificationsEnabled, true);
    await toggleNotifications(false);
    assert.equal(notificationsEnabled, false, "the live binding must follow both ways");
  } finally { h.restore(); }
});

test("the notifier reads the CURRENT flag, not the one at construction", async () => {
  // `isEnabled` is a closure over the module variable for exactly this reason: capturing the value at
  // construction would freeze notifications in whatever state the page loaded with.
  //
  // Observable as the FIRST gate it fails. Disabled, `handle` stops at "disabled"; enabled, it gets
  // past that gate and stops at the next one. Which gate that is does not matter — that it got past
  // the first one at all is the proof the flag was re-read.
  const h = withNotifications({ permission: "granted" });
  try {
    await toggleNotifications(false);
    assert.equal(dashboardNotifier.handle("message_sent", { to: "dashboard", from: "coder" }), "disabled");
    await toggleNotifications(true);
    assert.notEqual(
      dashboardNotifier.handle("message_sent", { to: "dashboard", from: "coder" }),
      "disabled",
      "the same event must get past the enabled gate once the flag flipped",
    );
  } finally { h.restore(); }
});

test("THE NOTIFIER BINDS ITS BROWSER API AT CONSTRUCTION — which is why this file cannot test firing", () => {
  // Worth stating rather than leaving as a puzzle for whoever writes the next test here. `createNotifier`
  // resolves the Notification API when the module is EVALUATED, so a stub installed later is never seen
  // and every path reports "unsupported". In a browser that is correct and invisible; in a test it means
  // the fire/suppress decision belongs to notify.test.mjs, which constructs its own notifier with an
  // injected API.
  const h = withNotifications({ permission: "granted" });
  try {
    assert.equal(dashboardNotifier.handle("message_sent", { to: "dashboard", from: "coder" }), "unsupported");
  } finally { h.restore(); }
});

test("channel notifications are MEMBERSHIP-GATED through this module's own state", async () => {
  // The gate's logic — including that it fails CLOSED on unknown membership — is asserted in
  // notify.test.mjs, which can inject an API. What THIS module contributes is the wiring: the
  // membership answer must come from `state.chat.channels` and from the `members` array the chat UI
  // already maintains, not from a list of channels the dashboard can merely SEE. It can see all of
  // them, so a check that ignored membership would notify on traffic nobody subscribed to.
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./notifications.mjs", import.meta.url), "utf8");
  assert.match(src, /isChannelSubscribed:/, "the notifier must be given a membership check");
  assert.match(src, /state\.chat && state\.chat\.channels/, "…which reads the live channel list");
  assert.match(src, /Array\.isArray\(row\.members\) && row\.members\.includes\('dashboard'\)/,
    "…and answers from the members array, not from mere visibility");
});

test("a notifier throw cannot escape into the caller", async () => {
  // It is called fire-and-forget from the realtime path, ahead of the routing. An escaping error there
  // would stop the dashboard handling the event it was notified about.
  const h = withNotifications({ permission: "granted" });
  try {
    assert.doesNotThrow(() => dashboardNotifier.handle(undefined, undefined));
    assert.doesNotThrow(() => dashboardNotifier.handle("message_sent", null));
  } finally { h.restore(); }
});
