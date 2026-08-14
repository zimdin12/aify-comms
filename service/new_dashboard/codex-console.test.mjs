// Real tests for the Codex console's socket bookkeeping and its output stream.
//
// None of this was reachable by a test while it lived in app.js. Two of the behaviours below are bounded
// resources — the scrollback cap and the connection map — and an unbounded one is the failure mode the cap
// was added for: the comment on it records that this DOM stream "had no bound → grew forever".
//
// SEALING. `document` does not exist in Node; a minimal fake is installed per test and removed afterwards.
// `codexConsoleConnections` is module state shared across tests, so each test clears it first.

import assert from "node:assert/strict";
import test from "node:test";

import {
  codexConsoleAppendLine,
  codexConsoleAppendText,
  codexConsoleClose,
  codexConsoleConnections,
} from "./codex-console.mjs";

function fakeContainer() {
  const children = [];
  return {
    children,
    scrollTop: 0,
    scrollHeight: 999,
    get childElementCount() { return children.length; },
    get firstChild() { return children[0]; },
    appendChild: (el) => { children.push(el); return el; },
    removeChild: (el) => { children.splice(children.indexOf(el), 1); return el; },
    querySelector: (sel) => {
      if (sel !== ".codex-line.delta:last-child") throw new Error(`unexpected selector ${sel}`);
      const last = children[children.length - 1];
      return last && last.className === "codex-line delta" ? last : null;
    },
  };
}

function withDom(run) {
  const had = "document" in globalThis;
  globalThis.document = { createElement: () => ({ className: "", textContent: "" }) };
  try {
    return run();
  } finally {
    if (!had) delete globalThis.document;
  }
}

test("closing a console shuts its socket and forgets it", () => {
  codexConsoleConnections.clear();
  let closed = 0;
  codexConsoleConnections.set("coder", { ws: { close: () => { closed += 1; } } });
  codexConsoleClose("coder");
  assert.equal(closed, 1);
  assert.equal(codexConsoleConnections.has("coder"), false,
    "a closed console must leave no entry behind — a stale one makes the next connect look already-open");
});

test("closing tolerates an unknown agent and a socket that throws", () => {
  codexConsoleConnections.clear();
  codexConsoleClose("nobody");                       // must not throw

  codexConsoleConnections.set("coder", { ws: { close: () => { throw new Error("already gone"); } } });
  codexConsoleClose("coder");
  assert.equal(codexConsoleConnections.has("coder"), false,
    "a socket that refuses to close must still be forgotten, or it leaks forever");

  codexConsoleConnections.set("noSocket", {});
  codexConsoleClose("noSocket");
  assert.equal(codexConsoleConnections.has("noSocket"), false);
});

test("appendLine writes a classed line and scrolls to the bottom", () => {
  withDom(() => {
    const c = fakeContainer();
    codexConsoleAppendLine(c, "hello", "err");
    assert.equal(c.children.length, 1);
    assert.equal(c.children[0].textContent, "hello");
    assert.equal(c.children[0].className, "codex-line err");
    assert.equal(c.scrollTop, c.scrollHeight, "the view must follow the output");

    codexConsoleAppendLine(c, "plain");
    assert.equal(c.children[1].className, "codex-line", "no class must not leave a trailing space");
  });
});

test("appendLine CAPS the scrollback at 2000 lines, dropping the oldest", () => {
  // The bound this exists for: before it, a long-running agent's console grew until the tab died.
  withDom(() => {
    const c = fakeContainer();
    for (let i = 0; i < 2100; i += 1) codexConsoleAppendLine(c, `line ${i}`);
    assert.equal(c.childElementCount, 2000, "the stream must stay bounded");
    assert.equal(c.children[c.children.length - 1].textContent, "line 2099", "the newest line is kept");
    assert.equal(c.children[0].textContent, "line 100", "…and the oldest are the ones dropped");
  });
});

test("appendText coalesces into the last delta line rather than making a line per chunk", () => {
  // Streamed output arrives in fragments. One div per fragment would blow the 2000-line cap in seconds
  // and break copy/paste of a single logical line.
  withDom(() => {
    const c = fakeContainer();
    codexConsoleAppendText(c, "Hel");
    codexConsoleAppendText(c, "lo ");
    codexConsoleAppendText(c, "world");
    assert.equal(c.children.length, 1, "three chunks must be one line");
    assert.equal(c.children[0].textContent, "Hello world");
    assert.equal(c.children[0].className, "codex-line delta");
  });
});

test("appendText starts a new delta line when the last line is not one", () => {
  withDom(() => {
    const c = fakeContainer();
    codexConsoleAppendLine(c, "a complete line", "info");
    codexConsoleAppendText(c, "streamed");
    assert.equal(c.children.length, 2, "streamed text must not be appended onto a finished line");
    assert.equal(c.children[1].textContent, "streamed");
  });
});

test("both append paths ignore a missing container", () => {
  // They are called from socket callbacks that can outlive the panel being closed.
  withDom(() => {
    codexConsoleAppendLine(null, "x");
    codexConsoleAppendText(undefined, "y");
  });
});

// ---------------------------------------------------------------------------------------------------
// Sending a turn, appended to this module in a later v0.5.4 slice.

import { codexConsoleSendTurn } from "./codex-console.mjs";

function wired({ readyState = 1, threadId = "thr-1" } = {}) {
  const sent = [];
  const container = fakeContainer();
  codexConsoleConnections.set("coder", {
    ws: { readyState, send: (s) => sent.push(JSON.parse(s)) },
    threadId,
    container,
  });
  return { sent, container };
}

test("a turn is sent as turn/start on the agent's thread, and echoed locally", () => {
  codexConsoleConnections.clear();
  const { sent, container } = wired();
  withDom(() => codexConsoleSendTurn("coder", "  do the thing  "));

  assert.equal(sent.length, 1);
  assert.equal(sent[0].method, "turn/start");
  assert.equal(sent[0].params.threadId, "thr-1", "a turn must be addressed to the live thread");
  assert.deepEqual(sent[0].params.input, [{ type: "text", text: "do the thing" }],
    "the text is trimmed before it goes out");
  assert.equal(sent[0].jsonrpc, "2.0");

  // The echo is what makes the operator's own input visible in the console they are watching.
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].textContent, "> do the thing");
  assert.equal(container.children[0].className, "codex-line user");
});

test("nothing is sent on a socket that is not OPEN", () => {
  // readyState 0/2/3 = CONNECTING/CLOSING/CLOSED. Writing to any of them throws or silently drops, and
  // the operator would see their input echoed as if it had been delivered.
  for (const readyState of [0, 2, 3]) {
    codexConsoleConnections.clear();
    const { sent, container } = wired({ readyState });
    withDom(() => codexConsoleSendTurn("coder", "hello"));
    assert.deepEqual(sent, [], `readyState ${readyState} must not be written to`);
    assert.equal(container.children.length, 0, "…and must not be echoed either");
  }
});

test("nothing is sent without a thread, an entry, or any text", () => {
  codexConsoleConnections.clear();
  const { sent: noThread } = wired({ threadId: "" });
  withDom(() => codexConsoleSendTurn("coder", "hello"));
  assert.deepEqual(noThread, [], "a console with no thread yet has nowhere to send");

  codexConsoleConnections.clear();
  withDom(() => codexConsoleSendTurn("nobody", "hello"));   // no entry at all — must not throw

  codexConsoleConnections.clear();
  const { sent: blank, container } = wired();
  for (const empty of ["", "   ", null, undefined]) {
    withDom(() => codexConsoleSendTurn("coder", empty));
  }
  assert.deepEqual(blank, [], "whitespace is not a turn");
  assert.equal(container.children.length, 0);
});

test("each turn carries a distinct request id", () => {
  // The id correlates the response. Reusing one would let a late reply resolve the wrong turn.
  codexConsoleConnections.clear();
  const { sent } = wired();
  withDom(() => {
    codexConsoleSendTurn("coder", "one");
    codexConsoleSendTurn("coder", "two");
  });
  assert.equal(sent.length, 2);
  assert.notEqual(sent[0].id, sent[1].id);
});
