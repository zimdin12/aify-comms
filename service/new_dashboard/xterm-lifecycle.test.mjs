// Real tests for the xterm teardown, extracted from app.js in v0.5.4.
//
// THE INDEPENDENT try BLOCKS ARE THE DESIGN, and a single shared try would look equivalent while being
// wrong. A console can be disposed after its container has left the DOM, or after xterm has already torn
// itself down, so ANY step can throw. Each is wrapped separately so a failure in the first does not skip
// the rest — and above all does not skip `state.activeXterm = null`, which is what lets the next console
// mount. A stale entry there makes the next mount believe an xterm is already live.
//
// So every test below throws from one step and asserts the OTHERS still ran. That is the only way to tell
// four independent try blocks from one big one.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { awaitTerminalSize, disposeActiveXterm } from "./xterm-lifecycle.mjs";

function entry({ observerThrows = false, removeThrows = false, disposeThrows = false, container = true } = {}) {
  const calls = { disconnect: 0, remove: 0, dispose: 0 };
  const wheelHandler = () => {};
  return {
    calls,
    wheelHandler,
    resizeObserver: {
      disconnect() {
        calls.disconnect += 1;
        if (observerThrows) throw new Error("observer already disconnected");
      },
    },
    container: container ? {
      removeEventListener(type, fn) {
        calls.remove += 1;
        calls.removedType = type;
        calls.removedFn = fn;
        if (removeThrows) throw new Error("node detached");
      },
    } : null,
    term: {
      dispose() {
        calls.dispose += 1;
        if (disposeThrows) throw new Error("already disposed");
      },
    },
  };
}

test("a normal teardown disconnects, unhooks the wheel, disposes, and clears the slot", () => {
  const e = entry();
  state.activeXterm = e;
  disposeActiveXterm();

  assert.equal(e.calls.disconnect, 1);
  assert.equal(e.calls.remove, 1);
  assert.equal(e.calls.removedType, "wheel", "the wheel listener is the one added at mount");
  assert.equal(e.calls.removedFn, e.wheelHandler, "the SAME handler reference must be removed");
  assert.equal(e.calls.dispose, 1);
  assert.equal(state.activeXterm, null, "the slot must be cleared or the next mount is blocked");
});

test("with nothing mounted it does nothing and does not throw", () => {
  // Called from paths that run whether or not a console is open.
  state.activeXterm = null;
  disposeActiveXterm();
  assert.equal(state.activeXterm, null);

  state.activeXterm = undefined;
  disposeActiveXterm();
  assert.equal(state.activeXterm, undefined, "an absent entry returns EARLY, before the slot is nulled");
});

test("a throwing ResizeObserver does not prevent dispose or the clear", () => {
  const e = entry({ observerThrows: true });
  state.activeXterm = e;
  disposeActiveXterm();
  assert.equal(e.calls.dispose, 1, "the later steps must still run");
  assert.equal(state.activeXterm, null);
});

test("a detached container does not prevent dispose or the clear", () => {
  const e = entry({ removeThrows: true });
  state.activeXterm = e;
  disposeActiveXterm();
  assert.equal(e.calls.dispose, 1);
  assert.equal(state.activeXterm, null);
});

test("A THROWING dispose STILL CLEARS THE SLOT — the leak this guards against", () => {
  // The worst case: xterm itself fails. If the slot were left set, every later mount would short-circuit
  // on a dead instance and the console would stay blank with nothing in the log.
  const e = entry({ disposeThrows: true });
  state.activeXterm = e;
  disposeActiveXterm();
  assert.equal(state.activeXterm, null);
});

test("all three steps failing at once still clears the slot", () => {
  const e = entry({ observerThrows: true, removeThrows: true, disposeThrows: true });
  state.activeXterm = e;
  disposeActiveXterm();
  assert.equal(e.calls.disconnect, 1);
  assert.equal(e.calls.remove, 1);
  assert.equal(e.calls.dispose, 1, "every step is attempted regardless of the ones before it");
  assert.equal(state.activeXterm, null);
});

test("a missing observer, handler or container is skipped rather than crashing", () => {
  // Optional chaining and the `wheelHandler && container` guard. A console torn down before it finished
  // mounting has some of these unset.
  state.activeXterm = { term: { dispose() {} } };
  disposeActiveXterm();
  assert.equal(state.activeXterm, null);

  const e = entry({ container: false });
  state.activeXterm = e;
  disposeActiveXterm();
  assert.equal(e.calls.remove, 0, "no container means no listener to remove");
  assert.equal(e.calls.dispose, 1);
  assert.equal(state.activeXterm, null);
});

// --- awaitTerminalSize ---------------------------------------------------------------------------
//
// It closes the loop between a local xterm resize and the server-side PTY actually adopting it. Without
// the wait, output is repainted against dimensions the PTY has not applied yet and wraps wrongly — which
// reads as a corrupted console rather than a timing bug.

test("awaitTerminalSize polls the terminal endpoint with the id ENCODED", async () => {
  const calls = [];
  const had = "fetch" in globalThis;
  const prev = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    // `api` reads response.TEXT and JSON.parses it — it never calls .json(). A stub that only implements
    // json() returns `{}` here, and the wait then polls 30 times and throws, which is how this test
    // first failed.
    return {
      ok: true, status: 200, headers: { get: () => "application/json" },
      text: async () => JSON.stringify({ terminal: { cols: 80, rows: 24 } }),
    };
  };
  try {
    await awaitTerminalSize("term/1 2", 80, 24);
    assert.ok(calls.length >= 1, "it must actually read the terminal back");
    assert.match(calls[0], /\/terminals\/term%2F1(%20|\+)2$/, "the id is encoded into the path");
  } finally {
    if (had) globalThis.fetch = prev; else delete globalThis.fetch;
  }
});

test("it reads the `terminal` field, not the envelope", async () => {
  // `(await api(...)).terminal`. Handing the envelope to the size comparison would never match the
  // requested cols/rows, so the wait would run to its timeout on every single resize.
  const had = "fetch" in globalThis;
  const prev = globalThis.fetch;
  let served = 0;
  globalThis.fetch = async () => {
    served += 1;
    return {
      ok: true, status: 200, headers: { get: () => "application/json" },
      text: async () => JSON.stringify({ terminal: { cols: 120, rows: 40 } }),
    };
  };
  try {
    await awaitTerminalSize("t1", 120, 40);
    assert.equal(served, 1, "a matching size must settle on the first read");
  } finally {
    if (had) globalThis.fetch = prev; else delete globalThis.fetch;
  }
});
