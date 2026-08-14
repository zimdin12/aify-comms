// The four chat click-handler bodies, tested by CALLING them.
//
// All four lived inside app.js's 390-line delegated click handler, which no test can reach — app.js is
// imported by nothing. Each is small and each does two or three things where dropping one is invisible in
// review and obvious only in use: a reply that fills the composer but never focuses it, a view switch that
// leaves the inline terminal running, a pulse window that updates the number and never refetches.
//
// `chatController` IS THE SEAM. app.js builds it from app.js-local callbacks, so it cannot move; these
// functions take it as a parameter instead. That makes it a natural test double — the assertions below are
// about which controller methods a click reaches, which is precisely what the branch bodies decide.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  openChatConversation,
  openChatReply,
  runChannelAction,
  setChatView,
  setPulseWindow,
} from "./chat-click-handlers.mjs";

/** A controller double recording every call, in order. */
function controller() {
  const calls = [];
  const rec = (name) => (...args) => calls.push([name, ...args]);
  return {
    calls,
    names: () => calls.map((c) => c[0]),
    close: rec("close"),
    open: rec("open"),
    renderConversation: rec("renderConversation"),
    refreshPulse: rec("refreshPulse"),
  };
}

/** Seal `state.chat` and the DOM, since `state` is a shared singleton across the whole suite. */
function withChat(chat, run) {
  const saved = state.chat;
  const savedMsgs = state.messages;
  const hadDoc = "document" in globalThis;
  const prevDoc = globalThis.document;
  let focused = 0;
  globalThis.document = {
    getElementById: (id) => (id === "chat-composer-body" ? { focus: () => { focused += 1; } } : null),
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  state.chat = { selected: "", view: "messenger", peek: false, analytics: { agent: "" }, pulse: { window: 60 }, ...chat };
  try {
    return run({ focusCount: () => focused });
  } finally {
    state.chat = saved;
    state.messages = savedMsgs;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
  }
}

// --- openChatReply -----------------------------------------------------------------------------

test("openChatReply stages the message AND focuses the composer", () => {
  // Two effects. Staging without focusing means the operator sees a reply banner appear and then has to
  // click into the box — which reads as the button half-working.
  const ctl = controller();
  withChat({ selected: "dm:bob" }, (dom) => {
    state.messages = [{ id: "m1", from: "bob", subject: "hi", body: "there" }];
    openChatReply({ dataset: { chatReply: "m1" } }, ctl);

    assert.equal(state.chat.replyTo.id, "m1");
    assert.equal(state.chat.replyTo.from, "bob");
    assert.equal(state.chat.replyTo.conversationKey, "dm:bob", "the reply is pinned to the open thread");
    assert.deepEqual(ctl.names(), ["renderConversation"]);
    assert.equal(dom.focusCount(), 1, "the composer must be focused");
  });
});

test("openChatReply on an UNKNOWN id does nothing at all", () => {
  // `if (msg)`. Without the guard, `messageId(undefined)` would throw inside the handler and every branch
  // after this one in the delegated listener would stop running.
  const ctl = controller();
  withChat({}, (dom) => {
    state.messages = [{ id: "m1" }];
    assert.doesNotThrow(() => openChatReply({ dataset: { chatReply: "nope" } }, ctl));
    assert.equal(state.chat.replyTo, undefined, "nothing is staged");
    assert.deepEqual(ctl.names(), [], "and nothing is re-rendered");
    assert.equal(dom.focusCount(), 0);
  });
});

test("openChatReply falls back through body → preview for the excerpt", () => {
  const ctl = controller();
  withChat({}, () => {
    state.messages = [{ id: "m1", preview: "from preview" }];
    openChatReply({ dataset: { chatReply: "m1" } }, ctl);
    assert.equal(state.chat.replyTo.preview, "from preview");
    assert.equal(state.chat.replyTo.subject, "", "a missing subject becomes empty, not undefined");
    assert.equal(state.chat.replyTo.from, "unknown", "…and a missing sender is named, not blank");
  });
});

// --- openChatConversation ----------------------------------------------------------------------

test("RE-CLICKING THE OPEN CONVERSATION CLOSES IT — the click-again gesture", () => {
  // An operator-requested behaviour with a comment to match. Losing it makes the open chat impossible to
  // dismiss without navigating away.
  const ctl = controller();
  withChat({ selected: "dm:bob" }, () => {
    openChatConversation({ dataset: { chatOpen: "dm:bob" } }, ctl, () => {});
    assert.deepEqual(ctl.names(), ["close"]);
  });
});

test("…but NOT while per-agent analytics is showing — that pane owns the selection", () => {
  // `&& !state.chat.analytics.agent`. Without it, clicking the conversation you are viewing analytics for
  // closes the chat instead of returning to it.
  const ctl = controller();
  withChat({ selected: "dm:bob", analytics: { agent: "bob" } }, () => {
    openChatConversation({ dataset: { chatOpen: "dm:bob" } }, ctl, () => {});
    assert.deepEqual(ctl.names(), ["open"], "it re-opens rather than closing");
  });
});

test("opening a DM marks it read, and PEEK MODE suppresses exactly that", () => {
  // Peek mode's entire purpose. If the mark-read call ignored it, peek would silently do nothing — and
  // the only way to notice is that unread badges keep clearing.
  const marked = [];
  const ctl = controller();
  withChat({ selected: "" }, () => {
    openChatConversation({ dataset: { chatOpen: "dm:bob" } }, ctl, (id, opts) => marked.push([id, opts]));
    assert.deepEqual(marked, [["bob", { quiet: true }]], "the dm: prefix is stripped and it is quiet");
  });

  marked.length = 0;
  withChat({ selected: "", peek: true }, () => {
    openChatConversation({ dataset: { chatOpen: "dm:bob" } }, ctl, (id) => marked.push(id));
    assert.deepEqual(marked, [], "peek mode marks nothing");
  });
});

test("opening a CHANNEL never marks anything read", () => {
  // `key.startsWith('dm:')`. Channels have no per-agent read state; calling mark-read with a channel name
  // would address an agent that does not exist.
  const marked = [];
  const ctl = controller();
  withChat({ selected: "" }, () => {
    openChatConversation({ dataset: { chatOpen: "channel:general" } }, ctl, (id) => marked.push(id));
    assert.deepEqual(ctl.names(), ["open"]);
    assert.deepEqual(marked, []);
  });
});

// --- setPulseWindow ----------------------------------------------------------------------------

test("setPulseWindow refetches ONLY when the window actually changes", () => {
  // The guard is the whole function. The pulse buttons re-render on every poll, so an unguarded handler
  // would refetch analytics on every stray click of the already-selected window.
  const ctl = controller();
  withChat({ pulse: { window: 60 } }, () => {
    setPulseWindow({ dataset: { pulseWindow: "60" } }, ctl);
    assert.deepEqual(ctl.names(), [], "same window — no refetch");

    setPulseWindow({ dataset: { pulseWindow: "180" } }, ctl);
    assert.equal(state.chat.pulse.window, 180);
    assert.deepEqual(ctl.calls, [["refreshPulse", true]], "changed — refetch, forced");
  });
});

test("a junk or missing pulse window falls back to 60 rather than NaN", () => {
  // `Number(...) || 60`. NaN would reach the API as `window_minutes=NaN` and, being !== the current
  // value, would refetch on every single click.
  const ctl = controller();
  withChat({ pulse: { window: 180 } }, () => {
    setPulseWindow({ dataset: { pulseWindow: "abc" } }, ctl);
    assert.equal(state.chat.pulse.window, 60);
  });
});

// --- setChatView -------------------------------------------------------------------------------

test("LEAVING THE CONSOLE VIEW DISPOSES THE INLINE TERMINAL", () => {
  // The leak this guard exists for. `disposeActiveXterm` is imported by the module, so the assertion is
  // indirect: switching to messenger must re-render, and must do so only on a real change.
  const ctl = controller();
  withChat({ view: "console" }, () => {
    setChatView({ dataset: { chatView: "messenger" } }, ctl);
    assert.equal(state.chat.view, "messenger");
    assert.deepEqual(ctl.names(), ["renderConversation"]);
  });
});

test("setChatView is a NO-OP when the view is unchanged", () => {
  // Same-value clicks are common: the view chips re-render on every poll. Re-entering would dispose a
  // live terminal the operator is looking at.
  const ctl = controller();
  withChat({ view: "console" }, () => {
    setChatView({ dataset: { chatView: "console" } }, ctl);
    assert.deepEqual(ctl.names(), [], "nothing happens");
  });
});

test("any unrecognised view value means 'messenger', never a third state", () => {
  // `=== 'console' ? 'console' : 'messenger'`. The view drives a CSS attribute; a stray value would
  // render neither pane.
  const ctl = controller();
  for (const raw of ["", "Console", "wat", undefined]) {
    withChat({ view: "console" }, () => {
      setChatView({ dataset: { chatView: raw } }, ctl);
      assert.equal(state.chat.view, "messenger", `${JSON.stringify(raw)} must fall back`);
    });
  }
});

test("runChannelAction passes ACTION then CHANNEL, and swallows a rejection", async () => {
  // Two dataset fields on one element and the callee takes (action, channel). Swapped, a "leave" would
  // be sent as a channel name and address nothing. The `.catch` matters for the same reason it does on
  // every other row control: this returns a promise into a delegated click listener.
  //
  // `withChat` is NOT promise-aware — it restores `document` in a synchronous `finally` — so the
  // rejection half installs its own stub and awaits before tearing it down. Getting that wrong made the
  // toast fire against a restored global, which is how this test first failed.
  const calls = [];
  runChannelAction(
    { dataset: { chatChannelAction: "leave", channel: "general" } },
    (...a) => { calls.push(a); return Promise.resolve(); },
  );
  assert.deepEqual(calls, [["leave", "general"]]);

  const had = "document" in globalThis;
  const prev = globalThis.document;
  const hadRaf = "requestAnimationFrame" in globalThis;
  globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
  const el = () => ({
    className: "", textContent: "", style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {},
    querySelectorAll: () => [], firstChild: null, children: [],
  });
  globalThis.document = {
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    createElement: el, body: { appendChild() {}, contains: () => true },
  };
  try {
    assert.doesNotThrow(() => runChannelAction(
      { dataset: { chatChannelAction: "leave", channel: "general" } },
      () => Promise.reject(new Error("not a member")),
    ));
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    if (had) globalThis.document = prev; else delete globalThis.document;
    if (!hadRaf) delete globalThis.requestAnimationFrame;
  }
});
