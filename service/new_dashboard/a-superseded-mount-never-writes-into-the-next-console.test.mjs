// A console mount that loses the pane mid-fetch must not write into the console that replaced it.
//
// R9-M1, external review 2026-09-06. `mountXtermForTerminal` takes a `_mountGen` token and checks it
// ONCE, before opening the terminal, to survive the font await. Three more awaits follow -- a double
// rAF, the snapshot GET, and a 700ms resize settle with a second GET -- and the token is never
// consulted again. Every write after them goes through `state.activeXterm`, which a newer mount has
// already reassigned, so the superseded continuation writes into the NEXT console's entry.
//
// TWO CONSEQUENCES, both silent:
//
//   `lastSeq` — the previous terminal's sequence is almost always HIGHER than the new console's, and
//   `realtime-socket.mjs` drops every frame at or below `lastSeq`. The new console paints its
//   snapshot and then never moves again. It looks like a dead agent.
//
//   `ownsPty` — a stale `managed` verdict landing on a RESIDENT console makes the dashboard resize
//   the operator's own terminal. `xterm-mount.mjs` calls that "the exact harm this guard prevents"
//   in the comment beside the mode check, and this path routes around it.
//
// THIS DRIVES THE REAL FUNCTION. A test that asserted "every write is guarded" by reading the source
// would pass on a comment, and would say nothing about the ORDER the awaits resolve in -- which is
// the whole defect.
//
// WHICH GUARD IS LOAD-BEARING, measured rather than assumed. The fix adds a bail straight after the
// snapshot GET and an identity check at each remaining write. Removing ONLY a per-write check does
// not redden anything, because the bail has already returned; both tests fail only when the bail
// goes too. So the bail is the guard and the per-write checks are defence in depth. They are kept
// deliberately -- a future edit that moves work above the bail would otherwise be unguarded again --
// but nobody should read their presence as covered behaviour.

import assert from "node:assert/strict";
import test from "node:test";

import { mountXtermForTerminal } from "./xterm-mount.mjs";
import { state } from "./state.mjs";

const LF = String.fromCharCode(10);

/** Everything `mountXtermForTerminal` touches on a terminal, and nothing it does not. */
function fakeTerm() {
  const written = [];
  return {
    cols: 80, rows: 24, written,
    buffer: { active: { length: 0, cursorY: 0, getLine: () => null } },
    parser: { registerCsiHandler: () => {} },
    unicode: { activeVersion: "6" },
    textarea: { addEventListener() {}, focus() {} },
    element: { addEventListener() {} },
    loadAddon() {}, open() {}, focus() {}, dispose() {}, reset() {},
    write(chunk) { written.push(String(chunk)); },
    paste() {}, getSelection: () => "", hasSelection: () => false,
    attachCustomKeyEventHandler() {},
    onData: () => ({ dispose() {} }),
    onResize: () => ({ dispose() {} }),
  };
}

function node() {
  const el = {
    className: "", textContent: "", innerHTML: "", style: {}, dataset: {},
    isConnected: true, offsetParent: {}, clientWidth: 800, clientHeight: 600,
    children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, removeAttribute() {}, appendChild() {}, remove() {},
    addEventListener() {}, removeEventListener() {},
    getBoundingClientRect: () => ({ width: 800, height: 600 }),
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => null,
  };
  return el;
}

/**
 * Install a browser enough for the mount to run, plus a snapshot fetch we control the timing of.
 *
 * SEALED: `location`, `window`, `document`, `fetch` and `requestAnimationFrame` are all installed
 * here and removed afterwards, so nothing passes because the host supplied it.
 */
function withBrowser(run) {
  const saved = {};
  for (const k of ["location", "window", "document", "fetch", "requestAnimationFrame", "ResizeObserver", "localStorage"]) {
    saved[k] = { had: k in globalThis, value: globalThis[k] };
  }
  const savedXterm = state.activeXterm;
  const savedAgents = state.agents;
  const savedSessions = state.sessions;
  // THE TWO TERMINALS MUST DIFFER IN MODE, or `ownsPty` is false for both and the assertion below
  // passes whatever the code does. `t-old` is managed (owned, resized); `t-new` is a resident
  // mirror of the operator's real terminal, which must never be resized.
  state.sessions = [];
  state.agents = [
    { id: "a-old", sessionMode: "managed", terminalId: "t-old" },
    { id: "a-new", sessionMode: "resident", terminalId: "t-new" },
  ];

  let releaseSnapshot;
  const snapshotGate = new Promise((resolve) => { releaseSnapshot = resolve; });
  let snapshotSeq = 9999;

  globalThis.location = { search: "", protocol: "http:", hostname: "127.0.0.1", origin: "http://127.0.0.1:8801", href: "http://127.0.0.1:8801/" };
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  globalThis.window = {
    Terminal: function () { return fakeTerm(); },
    FitAddon: { FitAddon: class { fit() {} proposeDimensions() { return { cols: 80, rows: 24 }; } dispose() {} } },
    WebLinksAddon: { WebLinksAddon: class { dispose() {} } },
    WebglAddon: { WebglAddon: class { onContextLoss() { return { dispose() {} }; } dispose() {} } },
    Unicode: { Unicode11Addon: class { dispose() {} } },
    ResizeObserver: globalThis.ResizeObserver,
    addEventListener() {}, removeEventListener() {},
  };
  globalThis.window.FitAddon.FitAddon.prototype.activate = () => {};
  globalThis.document = {
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    createElement: () => node(), body: node(), addEventListener() {}, removeEventListener() {},
    activeElement: null,
  };
  const gets = [];
  // WHAT THE PTY LAST ACKNOWLEDGED. The managed path nudges the size and then WAITS for the terminal
  // to report it back, so a fake that always answers 80x24 never satisfies the nudge -- the wait
  // runs its thirty attempts, throws, and the mount's own `catch` swallows it. The second snapshot
  // fetch then never happens, silently, which is precisely how that request went untested.
  let acknowledged = { cols: 80, rows: 24 };
  globalThis.fetch = async (url, options = {}) => {
    gets.push(String(url));
    if (String(url).includes("/resize")) {
      try {
        const body = JSON.parse(String(options.body || "{}"));
        acknowledged = { cols: Number(body.cols), rows: Number(body.rows) };
      } catch { /* leave the last acknowledgement in place */ }
      return { ok: true, status: 200, text: async () => '{"ok":true}' };
    }
    if (String(url).includes("/size")) {
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify({ terminal: { id: "t", ...acknowledged } }),
      };
    }
    // The FIRST terminal's snapshot is held open so the test can switch consoles mid-flight, which
    // is the race. Everything else answers at once.
    if (String(url).includes("t-old")) await snapshotGate;
    return {
      ok: true, status: 200,
      text: async () => JSON.stringify({
        ok: true,
        terminal: { id: "t", snapshot: "hello", output: "hello", outputSeq: snapshotSeq, cols: 80, rows: 24, status: "running" },
      }),
    };
  };

  const restore = () => {
    state.activeXterm = savedXterm;
    state.agents = savedAgents;
    state.sessions = savedSessions;
    for (const [k, s] of Object.entries(saved)) {
      if (s.had) globalThis[k] = s.value; else delete globalThis[k];
    }
  };
  return Promise.resolve()
    .then(() => run({
      releaseSnapshot: () => releaseSnapshot(),
      setSnapshotSeq: (n) => { snapshotSeq = n; },
      gets,
    }))
    .finally(restore);
}

const settle = () => new Promise((r) => setTimeout(r, 30));

test("POSITIVE CONTROL: an undisturbed mount does seed its own entry", async () => {
  // Without this, every assertion below ("the new entry was not written to") would also pass against
  // a mount that silently does nothing at all.
  await withBrowser(async ({ releaseSnapshot }) => {
    const container = node();
    const mounting = mountXtermForTerminal("t-new", "a-new", container, {}, { resyncActiveConsole: async () => {} });
    releaseSnapshot();
    await mounting;
    await settle();
    assert.equal(state.activeXterm?.terminalId, "t-new");
    assert.equal(state.activeXterm.lastSeq, 9999, "the mount never seeded its own lastSeq, so this file proves nothing");
  });
});

test("A SUPERSEDED MOUNT DOES NOT WRITE lastSeq INTO THE CONSOLE THAT REPLACED IT", async () => {
  await withBrowser(async ({ releaseSnapshot, setSnapshotSeq }) => {
    const first = node();
    // Held at its snapshot GET.
    const stale = mountXtermForTerminal("t-old", "a-old", first, {}, { resyncActiveConsole: async () => {} });
    await settle();

    // The operator switches consoles. This mount completes and publishes its own entry.
    setSnapshotSeq(12);
    const second = node();
    await mountXtermForTerminal("t-new", "a-new", second, {}, { resyncActiveConsole: async () => {} });
    await settle();
    const entry = state.activeXterm;
    assert.equal(entry?.terminalId, "t-new", "the second mount did not take the pane; the race is not set up");
    assert.equal(entry.lastSeq, 12);

    // Now the first mount's snapshot arrives. It must find itself superseded and write nothing.
    setSnapshotSeq(9999);
    releaseSnapshot();
    await stale;
    await settle();

    assert.equal(state.activeXterm, entry, "the superseded mount replaced the live entry outright");
    assert.equal(
      state.activeXterm.lastSeq, 12,
      "the superseded mount wrote the OLD terminal's sequence into the new console. "
      + "realtime-socket.mjs drops every frame at or below lastSeq, so that console would paint its "
      + "snapshot and never move again.",
    );
  });
});

test("A SUPERSEDED MOUNT DOES NOT WRITE ownsPty INTO THE CONSOLE THAT REPLACED IT", async () => {
  // The sharper half: `ownsPty` decides whether the dashboard RESIZES the terminal. A stale verdict
  // landing on a resident console SIGWINCHes the operator's own terminal.
  await withBrowser(async ({ releaseSnapshot }) => {
    const stale = mountXtermForTerminal("t-old", "a-old", node(), {}, { resyncActiveConsole: async () => {} });
    await settle();
    await mountXtermForTerminal("t-new", "a-new", node(), {}, { resyncActiveConsole: async () => {} });
    await settle();

    const entry = state.activeXterm;
    assert.equal(entry.terminalId, "t-new");
    assert.equal(entry.ownsPty, false, 'a resident console must not be owned; the fixture is wrong');
    releaseSnapshot();
    await stale;
    await settle();

    assert.equal(state.activeXterm, entry, "the superseded mount replaced the live entry");
    assert.equal(
      state.activeXterm.ownsPty, false,
      "the superseded mount decided ownsPty for the console that replaced it",
    );
  });
});

test("the mount asks for the CONSOLE PROJECTION, like the resync it shares a payload with", async () => {
  // The mount and the resync read the same three things off this response -- the snapshot, the
  // rendered size, and the sequence -- and one live response is 147,250 bytes, of which 93,430 is a
  // raw tail the snapshot replaces and 46,516 an event page neither reads.
  //
  // ASSERTED WITH ITS OWN NEGATIVE: a URL merely CONTAINING the word would still pass if the width
  // were dropped, and a console fetched at the wrong width comes back garbled -- which is the defect
  // the snapshot exists to prevent. So both halves of the question are pinned.
  await withBrowser(async ({ releaseSnapshot, gets }) => {
    const container = node();
    const mounting = mountXtermForTerminal("t-new", "a-new", container, {}, { resyncActiveConsole: async () => {} });
    releaseSnapshot();
    await mounting;
    await settle();
    const snapshotGets = gets.filter((url) => url.includes("/terminals/"));
    assert.ok(snapshotGets.length > 0, "the mount fetched nothing, so this asserts nothing");
    for (const url of snapshotGets) {
      assert.match(url, /view=console/, `the mount fetched the whole terminal: ${url}`);
      assert.match(url, /cols=\d+/, `the mount dropped the width it must render at: ${url}`);
    }
  });
});

test("A MANAGED mount asks for the projection on the SECOND snapshot fetch too", async () => {
  // THE THIRD REQUEST IS ONLY REACHED WHEN THE MOUNT OWNS THE PTY. `ownsPty` is true only for a
  // POSITIVELY managed agent, and every other test in this file mounts a resident one -- so the
  // post-resize `fresh` fetch was never executed by any test, and review removed `view=console`
  // from it with all four still green. Three request sites, two of them pinned, is not pinned.
  //
  // The resize legs are answered so `forceTerminalRepaint` completes: the size poll returns exactly
  // what was asked for, which is what `waitForTerminalSize` waits on.
  await withBrowser(async ({ releaseSnapshot, gets }) => {
    const savedAgents = state.agents;
    state.agents = [{ id: "a-managed", sessionMode: "managed", terminalId: "t-managed" }];
    try {
      const container = node();
      const mounting = mountXtermForTerminal("t-managed", "a-managed", container, {}, {
        resyncActiveConsole: async () => {},
      });
      releaseSnapshot();
      await mounting;
      await settle();

      const snapshotGets = gets.filter((url) => url.includes("/terminals/") && url.includes("cols="));
      // TWO, NOT ONE: the initial mount and the post-repaint refetch. Asserting only "every GET
      // carries it" would pass on a run where the second one never happened at all, which is
      // exactly the hole this test exists to close.
      assert.ok(snapshotGets.length >= 2,
        `the managed path must issue the post-resize refetch; it issued ${snapshotGets.length}: `
        + `${JSON.stringify(snapshotGets)}`);
      for (const url of snapshotGets) {
        assert.match(url, /view=console/, `a snapshot fetch asked for the whole terminal: ${url}`);
      }
      // AND THE SIZE POLL IS THE LIGHT ONE, on the same path, for the same reason.
      const sizeGets = gets.filter((url) => /\/terminals\/[^?]+\/size/.test(url));
      assert.ok(sizeGets.length > 0, "the resize wait never polled, so the repaint leg did not run");
    } finally {
      state.agents = savedAgents;
    }
  });
});
