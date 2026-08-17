// The handlers a MOUNTED console installs — and the fit guard that keeps the renderer alive.
//
// From the dashboard V8-coverage census: `safeFit`, `canInput`, `onBlocked`, `onError`, `onWheel` and the
// `onResize` callback are all closures inside `mountXtermForTerminal`, and NOTHING in the suite has ever
// called one. `xterm-mount.test.mjs` says so in its own header — "this suite does NOT drive a real mount" —
// and covers only the refusals that happen before a terminal is constructed. So the console's input gate,
// its resize contract, its wheel focus gate and its error reporting have never executed under test, on the
// surface the project calls a hard requirement (a visible TUI in the dashboard).
//
// WHY A SEPARATE FILE. Driving a real mount needs a fake `window.Terminal`, a DOM, a ResizeObserver, rAF and
// `fetch` — and it CLAIMS `state.activeXterm`, which the existing file's refusal tests assert stays untouched.
// Keeping the two apart means neither has to reason about the other's global state.
//
// WHAT THE FAKES ARE FOR, one at a time:
//   * `fetch` is stubbed rather than pointed at a dead port because two of these contracts are about what the
//     SERVER said: a rejected input post must reach the operator inside the terminal, and it must not be able
//     to carry escape sequences there. An unreachable port can only produce one error message — the platform's
//     — and cannot produce a hostile one at all. The base URL is a name that resolves nowhere, so a request
//     that escaped the stub would fail loudly instead of reaching the operator's real service on :8800.
//   * `Date.now` is stubbed for the blocked-input throttle, because `consoleInputBlockedToastAt` is MODULE
//     state that outlives each test: with the real clock, whether a second toast appears would depend on how
//     long the preceding tests took. The stub starts far in the future so an earlier real-clock timestamp can
//     never read as "4 seconds ago".
//   * The DOM is installed ONCE for the file, not per test. `ui.js` caches its toast host and reuses it while
//     it is `isConnected`, so a per-test document would leave toasts appending into the previous test's host.
//
// NOT COVERED HERE, deliberately: `waitForSize`. It only exists on the managed `ownsPty` repaint path, which
// awaits `awaitTerminalSize` — up to 30 polls at 100ms before it throws. `forceTerminalRepaint` and
// `waitForTerminalSize` are both directly unit-tested in `terminal-input.test.mjs`; reaching the one-line
// wiring through a three-second poll would buy the wiring and pay for it in suite time.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import { mountXtermForTerminal } from "./xterm-mount.mjs";

// ── DOM ─────────────────────────────────────────────────────────────────────

function makeEl(tag = "div") {
  const el = {
    tagName: tag,
    className: "",
    textContent: "",
    innerHTML: "",
    style: {},
    dataset: {},
    isConnected: true,
    clientWidth: 800,
    clientHeight: 400,
    children: [],
    parentNode: null,
    listeners: new Map(),
    listenerOptions: new Map(),
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, removeAttribute() {},
    appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
    // A real remove() DETACHES. A no-op version hangs the suite: ui.js evicts old toasts with
    // `while (host.children.length >= 4) host.firstElementChild?.remove()`.
    remove() {
      const parent = el.parentNode;
      if (parent) {
        const at = parent.children.indexOf(el);
        if (at >= 0) parent.children.splice(at, 1);
      }
      el.parentNode = null;
      el.isConnected = false;
    },
    get firstElementChild() { return el.children[0] || null; },
    addEventListener(type, fn, options) {
      el.listenerOptions.set(fn, options);
      const bucket = el.listeners.get(type);
      if (bucket) bucket.push(fn); else el.listeners.set(type, [fn]);
    },
    removeEventListener(type, fn) {
      const bucket = el.listeners.get(type) || [];
      const at = bucket.indexOf(fn);
      if (at >= 0) bucket.splice(at, 1);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  return el;
}

function fire(el, type, event) {
  for (const fn of [...(el.listeners.get(type) || [])]) fn(event);
}

const created = [];
const documentBody = makeEl("body");
globalThis.document = {
  body: documentBody,
  activeElement: null,
  // No `fonts`: the font warm-up is skipped, which also skips the await it parks on. The supersession
  // check after it still runs — it is not inside the fonts branch.
  createElement: (tag) => { const el = makeEl(tag); created.push(el); return el; },
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
};

function toastsRaised(tone) {
  return created.filter((el) => String(el.className).includes(`toast-${tone}`)).length;
}

// ── xterm ───────────────────────────────────────────────────────────────────

let fitCalls = 0;

class FakeFitAddon {
  fit() { fitCalls += 1; }
  proposeDimensions() { return { cols: 80, rows: 24 }; }
}

class FakeTerminal {
  static last = null;

  constructor(options) {
    this.options = options;
    this.cols = 80;
    this.rows = 24;
    this.written = [];
    this.resets = 0;
    this.disposed = false;
    this.focused = false;
    // A full-screen TUI owns the ALTERNATE buffer; the wheel translation only applies there.
    this.buffer = { active: { type: "normal" } };
    this.textarea = makeEl("textarea");   // xterm's hidden input — the focus signal onWheel reads
    this.unicode = {};
    this.parser = { registerOscHandler: (code, cb) => { this.osc = { code, cb }; } };
    FakeTerminal.last = this;
  }

  loadAddon() {}
  open(el) { this.opened = el; }
  write(chunk) { this.written.push(String(chunk)); }
  reset() { this.resets += 1; }
  focus() { this.focused = true; }
  dispose() { this.disposed = true; }
  hasSelection() { return false; }
  getSelection() { return ""; }
  paste() {}
  onData(cb) { this.onDataHandler = cb; }
  onResize(cb) { this.onResizeHandler = cb; }
  attachCustomKeyEventHandler(cb) { this.keyHandler = cb; }
}

let observers = [];

class FakeResizeObserver {
  constructor(cb) { this.cb = cb; this.observing = []; this.disconnected = 0; observers.push(this); }
  observe(el) { this.observing.push(el); }
  disconnect() { this.disconnected += 1; }
}

// `new ResizeObserver(...)` is a BARE reference in the product (only the feature-detect reads
// `window.ResizeObserver`), so both have to exist.
globalThis.ResizeObserver = FakeResizeObserver;
globalThis.window = {
  Terminal: FakeTerminal,
  FitAddon: { FitAddon: FakeFitAddon },
  ResizeObserver: FakeResizeObserver,
  // Unicode11 / WebLinks / Webgl are deliberately absent: each is loaded inside its own try and none of
  // them owns a handler under test. Leaving them out keeps the fake surface to what the contracts need.
};

const frames = [];
globalThis.requestAnimationFrame = (cb) => { frames.push(cb); return frames.length; };
globalThis.cancelAnimationFrame = () => {};

/** Run every frame queued so far (a frame that queues another gets the next drain, as in a browser). */
function drainFrames() {
  const due = frames.splice(0, frames.length);
  for (const cb of due) cb(0);
}

// The mount awaits a DOUBLE rAF before its second fit, so a mount only completes if frames are drained
// while it is parked. One driver: drain on a short interval until the mount's promise settles.
async function settle(promise) {
  let done = false;
  const tracked = promise.then((v) => { done = true; return v; }, (e) => { done = true; throw e; });
  for (let i = 0; i < 200 && !done; i += 1) {
    drainFrames();
    await new Promise((r) => setTimeout(r, 1));
  }
  return tracked;
}

// ── fetch ───────────────────────────────────────────────────────────────────

const requests = [];
let responder = () => ({});

globalThis.fetch = async (url, options = {}) => {
  requests.push({ url: String(url), method: options.method || "GET", body: options.body });
  const answer = (await responder(String(url), options)) || {};
  return {
    ok: answer.ok !== false,
    status: answer.status || (answer.ok === false ? 500 : 200),
    statusText: answer.statusText || "OK",
    text: async () => (answer.body === undefined ? "{}" : answer.body),
  };
};

// A host that resolves nowhere. If a request ever escapes the stub it fails instead of reaching the
// operator's real service.
setApiBase("http://dashboard.invalid/api/v1");

function requestsTo(suffix) {
  return requests.filter((r) => r.url.endsWith(suffix));
}

// ── the mount under test ────────────────────────────────────────────────────

let terminalSeq = 0;

async function mount({ clientWidth = 800, clientHeight = 400, canInput = true } = {}) {
  const terminalId = `t${(terminalSeq += 1)}`;
  const container = makeEl("div");
  container.clientWidth = clientWidth;
  container.clientHeight = clientHeight;
  documentBody.appendChild(container);
  created.length = 0;
  requests.length = 0;
  observers = [];
  fitCalls = 0;
  responder = () => ({});
  await settle(mountXtermForTerminal(terminalId, "a1", container, { canInput },
    { resyncActiveConsole: async () => {} }));
  const term = FakeTerminal.last;
  assert.equal(term.opened, container, "the mount did not open a terminal on the container");
  return { terminalId, container, term };
}

test.afterEach(() => {
  // Leaving a claimed console behind would make the NEXT mount dispose it mid-test, and any pending
  // observer frame would then run against a stale entry.
  state.activeXterm = null;
  state.sessions = [];
  state.agents = [];
  frames.length = 0;
});

// ── safeFit: the renderer guard ─────────────────────────────────────────────

test("a pane with a measurable box IS fitted", async () => {
  const { container } = await mount();
  assert.ok(fitCalls >= 1, "a visible pane was never fitted, so the grid never matched the box");
  assert.equal(state.activeXterm.container, container, "the mount did not claim the console");
});

test("a ZERO-SIZED pane is never fitted — and the mount still completes", async () => {
  // fit() rebuilds the WebGL texture atlas; running it against a 0px box crashes the GL renderer, which
  // is why the guard measures the box rather than trusting that a mounted pane has one. A collapsing or
  // hidden sibling pane produces exactly this state mid-transition.
  const { container } = await mount({ clientWidth: 0, clientHeight: 0 });
  assert.equal(fitCalls, 0, "fit() ran on a zero-sized pane");
  // The mount must not ABORT on it either: the console still has to claim state and seed history, and
  // the ResizeObserver re-fits once the pane has a box.
  assert.equal(state.activeXterm.container, container, "a zero-sized pane aborted the whole mount");
  assert.equal(observers.length, 1, "no ResizeObserver was installed, so it could never re-fit");
});

test("a pane DETACHED after the mount is not fitted by its own observer", async () => {
  // The frame is scheduled from a layout burst; by the time it runs, a page switch may have removed the
  // pane. `container.isConnected` is the guard, and it is the same guard as safeFit's first line.
  const { container } = await mount();
  const before = fitCalls;
  container.isConnected = false;
  observers[0].cb();
  drainFrames();
  assert.equal(fitCalls, before, "a detached pane was fitted");
});

test("an observer frame that lands after the console moved on does nothing", async () => {
  // Stale-observer guard. A disposed terminal's queued frame must not touch the NEW entry — that is a
  // spurious resync and a visible flicker on the console the operator actually switched to.
  const { container } = await mount();
  const before = fitCalls;
  state.activeXterm = { container: makeEl("div"), term: FakeTerminal.last };  // a different console is live now
  observers[0].cb();
  drainFrames();
  assert.equal(fitCalls, before, "the stale observer re-fitted a console that is no longer active");
  assert.notEqual(state.activeXterm.container, container);
});

test("an observer BURST coalesces to one frame", async () => {
  // `roFrame` guards against stacking frames: without it a layout transition runs fit() once per
  // observer callback, which is the 0px-frame crash path this module documents.
  const { container } = await mount();
  const before = fitCalls;
  for (let i = 0; i < 5; i += 1) observers[0].cb();
  drainFrames();
  assert.equal(fitCalls, before + 1, "an observer burst ran fit() more than once");
  assert.equal(state.activeXterm.container, container);
});

// ── input: the gate, the throttle, the failure report ───────────────────────

test("typing reaches the PTY through the serialized poster", async () => {
  const { terminalId, term } = await mount();
  requests.length = 0;
  await term.onDataHandler("ls\r");
  const posts = requestsTo(`/terminals/${terminalId}/input`);
  assert.equal(posts.length, 1, "a keystroke did not reach the terminal input endpoint");
  assert.equal(posts[0].method, "POST");
  assert.deepEqual(JSON.parse(posts[0].body), { body: "ls\r", requestedBy: "dashboard" });
});

test("input is REFUSED when the console is not live, and the operator is told once per 4s", async () => {
  // Two contracts in one test on purpose: `consoleInputBlockedToastAt` is module state, so the throttle
  // can only be asserted deterministically under a controlled clock, and the same clock has to cover the
  // first toast as well.
  const realNow = Date.now;
  let clock = 2_000_000_000_000;   // far past any real timestamp an earlier test could have left behind
  Date.now = () => clock;
  try {
    const { terminalId, term } = await mount({ canInput: false });
    requests.length = 0;
    created.length = 0;

    for (const key of ["a", "b", "c"]) await term.onDataHandler(key);
    assert.deepEqual(requestsTo(`/terminals/${terminalId}/input`), [],
      "a console that is not accepting input still forwarded keystrokes to the PTY");
    assert.equal(toastsRaised("warn"), 1, "the operator was told three times for one burst");

    clock += 3_999;
    await term.onDataHandler("d");
    assert.equal(toastsRaised("warn"), 1, "the throttle window did not hold");

    clock += 2;   // 4001ms since the first
    await term.onDataHandler("e");
    assert.equal(toastsRaised("warn"), 2, "a later blocked keystroke never told the operator again");
    assert.deepEqual(requestsTo(`/terminals/${terminalId}/input`), [],
      "the gate leaked once the toast was throttled");
  } finally {
    Date.now = realNow;
  }
});

test("the gate reads the LIVE entry, so a console that goes dead mid-session stops accepting", async () => {
  // `canInput: () => !(state.activeXterm && state.activeXterm.canInput === false)` reads state on every
  // keystroke rather than closing over the mount-time value — that is what lets a session ending flip the
  // console without a remount.
  const { terminalId, term } = await mount({ canInput: true });
  requests.length = 0;
  await term.onDataHandler("x");
  assert.equal(requestsTo(`/terminals/${terminalId}/input`).length, 1);
  state.activeXterm.canInput = false;
  await term.onDataHandler("y");
  assert.equal(requestsTo(`/terminals/${terminalId}/input`).length, 1,
    "the gate used the mount-time value instead of the live one");
});

test("a REJECTED input post is reported inside the terminal, with escape sequences stripped", async () => {
  // The failure has to be visible where the operator is looking — a silent drop reads as a dead agent.
  // But the text comes from the SERVER, and the terminal is a byte sink: an error carrying escape
  // sequences would move the cursor, recolour the screen or clear it. The report keeps its own colouring
  // (2 escapes) and strips every escape out of the message.
  const { terminalId, term } = await mount();
  responder = () => ({ ok: false, status: 500, body: JSON.stringify({ error: "\x1b[2Jwiped\x1b[H\x1b[31m" }) });
  term.written.length = 0;
  await term.onDataHandler("q");
  const report = term.written.join("");
  assert.match(report, /\[input post failed: /, "a rejected keystroke was dropped silently");
  assert.match(report, /wiped/, "the server's message did not reach the operator");
  assert.equal((report.match(/\x1b/g) || []).length, 2,
    "the message carried escape sequences into the operator's terminal");
  assert.equal(requestsTo(`/terminals/${terminalId}/input`).length, 1);
});

// ── resize: debounce, dedupe, clamp ─────────────────────────────────────────

test("a grid change is posted once, after the debounce, at the clamped size", async () => {
  const { terminalId, term } = await mount();
  requests.length = 0;
  term.onResizeHandler({ cols: 100, rows: 30 });
  assert.deepEqual(requestsTo(`/terminals/${terminalId}/resize`), [],
    "the resize was posted synchronously — a fit burst would spam the PTY");
  await new Promise((r) => setTimeout(r, 160));
  const posts = requestsTo(`/terminals/${terminalId}/resize`);
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].body), { cols: 100, rows: 30, requestedBy: "dashboard" });
});

test("an UNCHANGED grid is not posted at all", async () => {
  // xterm fires onResize on every fit, including fits that changed nothing. Without the dedupe every
  // layout settle would SIGWINCH the PTY for no reason.
  const { terminalId, term } = await mount();
  requests.length = 0;
  term.onResizeHandler({ cols: 100, rows: 30 });
  await new Promise((r) => setTimeout(r, 160));
  term.onResizeHandler({ cols: 100, rows: 30 });
  await new Promise((r) => setTimeout(r, 160));
  assert.equal(requestsTo(`/terminals/${terminalId}/resize`).length, 1,
    "a no-op resize was posted to the PTY");
});

test("a burst of DIFFERENT sizes posts only the last one", async () => {
  const { terminalId, term } = await mount();
  requests.length = 0;
  term.onResizeHandler({ cols: 100, rows: 30 });
  term.onResizeHandler({ cols: 95, rows: 29 });
  term.onResizeHandler({ cols: 90, rows: 28 });
  await new Promise((r) => setTimeout(r, 160));
  const posts = requestsTo(`/terminals/${terminalId}/resize`);
  assert.equal(posts.length, 1, "each intermediate size reached the PTY");
  assert.deepEqual(JSON.parse(posts[0].body), { cols: 90, rows: 28, requestedBy: "dashboard" });
});

test("a collapsing pane is clamped to a usable grid, never to its real 2x1", async () => {
  // A mid-transition pane can fit to a couple of columns. Sending that to the PTY re-wraps every line
  // in the app's screen; the clamp is what keeps the shell's idea of its size usable.
  const { terminalId, term } = await mount();
  requests.length = 0;
  term.onResizeHandler({ cols: 2, rows: 1 });
  await new Promise((r) => setTimeout(r, 160));
  const posts = requestsTo(`/terminals/${terminalId}/resize`);
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].body), { cols: 20, rows: 5, requestedBy: "dashboard" });
});

// ── wheel: the focus gate ───────────────────────────────────────────────────

test("a HOVER-scroll over an unfocused console injects nothing", async () => {
  // The operator report this guard came from: scrolling the page with the pointer over a console typed up
  // to five synthetic arrows into that agent's live PTY, scattering their draft. `wheel` does not require
  // focus, so the handler has to require it.
  const { terminalId, container, term } = await mount();
  term.buffer.active.type = "alternate";
  document.activeElement = null;
  requests.length = 0;
  let prevented = 0;
  fire(container, "wheel", { deltaY: 120, preventDefault: () => { prevented += 1; } });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(requestsTo(`/terminals/${terminalId}/input`), [],
    "a hover-scroll injected keystrokes into a live PTY");
  assert.equal(prevented, 0, "the page was stopped from scrolling even though nothing was injected");
});

test("a wheel over a FOCUSED full-screen TUI scrolls it through the same serialized poster", async () => {
  // The other half of that fix: the wheel used to POST directly, opening a second unordered writer to the
  // same PTY, so wheel arrows and real keystrokes interleaved. It now goes through the input poster —
  // observable here as the same `/input` endpoint with the poster's own request shape.
  const { terminalId, container, term } = await mount();
  term.buffer.active.type = "alternate";
  document.activeElement = term.textarea;
  requests.length = 0;
  let prevented = 0;
  fire(container, "wheel", { deltaY: 120, preventDefault: () => { prevented += 1; } });
  await new Promise((r) => setTimeout(r, 5));
  const posts = requestsTo(`/terminals/${terminalId}/input`);
  assert.equal(posts.length, 1, "a focused wheel gesture did not reach the PTY");
  assert.deepEqual(JSON.parse(posts[0].body), { body: "\x1b[B\x1b[B\x1b[B", requestedBy: "dashboard" });
  assert.equal(prevented, 1, "the page scrolled as well as the terminal");
});

test("a wheel over the NORMAL buffer leaves the page — and the PTY — alone", async () => {
  // The normal buffer has real scrollback and xterm scrolls it natively; translating there would move the
  // cursor for nothing.
  const { terminalId, container, term } = await mount();
  term.buffer.active.type = "normal";
  document.activeElement = term.textarea;
  requests.length = 0;
  let prevented = 0;
  fire(container, "wheel", { deltaY: 120, preventDefault: () => { prevented += 1; } });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(requestsTo(`/terminals/${terminalId}/input`), []);
  assert.equal(prevented, 0, "native scrollback was suppressed");
});

test("a DEAD console's wheel is inert even when focused", async () => {
  const { terminalId, container, term } = await mount({ canInput: false });
  term.buffer.active.type = "alternate";
  document.activeElement = term.textarea;
  requests.length = 0;
  fire(container, "wheel", { deltaY: 120, preventDefault: () => {} });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(requestsTo(`/terminals/${terminalId}/input`), [],
    "a console that refuses typing accepted wheel-synthesised keystrokes");
});

test("the wheel listener is registered NON-passively, or preventDefault would be ignored", async () => {
  // `{ passive: false }` is not decoration: a passive listener's preventDefault() is ignored, so the page
  // would scroll at the same time as the TUI. And the handler stored on the entry has to be the SAME
  // function object that was registered, or dispose leaves it attached to a dead terminal.
  const { container } = await mount();
  const installed = container.listeners.get("wheel") || [];
  assert.equal(installed.length, 1, "no wheel listener was installed");
  assert.deepEqual(container.listenerOptions.get(installed[0]), { passive: false },
    "the wheel listener is passive, so its preventDefault() does nothing");
  assert.equal(state.activeXterm.wheelHandler, installed[0],
    "the handler kept for removal on dispose is not the one that was registered");
});
