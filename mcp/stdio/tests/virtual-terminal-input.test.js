import assert from "assert";

const { createVirtualTerminalInputManager } = await import("../virtual-terminal-input.js");

function makeRecorder() {
  const calls = [];
  return {
    calls,
    dispatch: async (agentId, line) => {
      calls.push({ agentId, line });
    },
  };
}

// CR submits a line, LF submits a line, CRLF submits exactly one line.
{
  const rec = makeRecorder();
  const mgr = createVirtualTerminalInputManager({ dispatch: rec.dispatch });
  await mgr.append("agent-a", "term1", "hello\r");
  await mgr.append("agent-a", "term1", "world\n");
  await mgr.append("agent-a", "term1", "third\r\n");
  assert.deepEqual(
    rec.calls.map((c) => c.line),
    ["hello", "world", "third"],
    `expected three line submissions, got ${JSON.stringify(rec.calls)}`,
  );
}

// Partial line stays buffered until terminator arrives.
{
  const rec = makeRecorder();
  const mgr = createVirtualTerminalInputManager({ dispatch: rec.dispatch });
  await mgr.append("agent-b", "term1", "ls ");
  await mgr.append("agent-b", "term1", "-la");
  assert.equal(rec.calls.length, 0, "partial line should not dispatch");
  assert.equal(mgr.snapshot().term1?.buffer, "ls -la");
  await mgr.append("agent-b", "term1", "\r");
  assert.deepEqual(rec.calls.map((c) => c.line), ["ls -la"]);
  assert.equal(mgr.snapshot().term1?.buffer, "");
}

// Multiple lines in a single chunk all flush in order.
{
  const rec = makeRecorder();
  const mgr = createVirtualTerminalInputManager({ dispatch: rec.dispatch });
  await mgr.append("agent-c", "term1", "one\r\ntwo\nthree\r");
  assert.deepEqual(rec.calls.map((c) => c.line), ["one", "two", "three"]);
}

// Buffer cap drops the oldest bytes when input outruns the operator's Enter.
{
  const rec = makeRecorder();
  const mgr = createVirtualTerminalInputManager({ dispatch: rec.dispatch, maxBufferChars: 8 });
  await mgr.append("agent-d", "term1", "ABCDEFGHIJKL");
  // First 4 chars (ABCD) should have been trimmed; the live buffer should be the tail.
  assert.equal(mgr.snapshot().term1?.buffer.length, 8);
  assert.ok(mgr.snapshot().term1.buffer.endsWith("EFGHIJKL"));
  // A submit terminator now should flush the surviving tail. The trim runs
  // against the post-append buffer length (tail + \r = 9 chars), so the
  // leading 'E' falls off and what flushes is the next-to-last 7 chars.
  await mgr.append("agent-d", "term1", "\r");
  assert.deepEqual(rec.calls.map((c) => c.line), ["FGHIJKL"]);
}

// Empty lines (bare Enter) are NOT dispatched.
{
  const rec = makeRecorder();
  const mgr = createVirtualTerminalInputManager({ dispatch: rec.dispatch });
  await mgr.append("agent-e", "term1", "\r");
  await mgr.append("agent-e", "term1", "\n");
  await mgr.append("agent-e", "term1", "\r\n");
  assert.equal(rec.calls.length, 0, `empty lines should be ignored, got ${JSON.stringify(rec.calls)}`);
}

// Dispatch errors are reported via onError but do not crash the queue —
// subsequent lines still flush.
{
  const errors = [];
  let calls = 0;
  const mgr = createVirtualTerminalInputManager({
    dispatch: async (agentId, line) => {
      calls++;
      if (calls === 1) throw new Error(`fail ${line}`);
    },
    onError: (error, ctx) => errors.push({ message: error.message, line: ctx.line }),
  });
  await mgr.append("agent-f", "term1", "one\r");
  await mgr.append("agent-f", "term1", "two\r");
  assert.equal(calls, 2);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].line, "one");
}

// remove(terminalId) drops the buffer entirely.
{
  const rec = makeRecorder();
  const mgr = createVirtualTerminalInputManager({ dispatch: rec.dispatch });
  await mgr.append("agent-g", "term1", "type");
  assert.ok(mgr.snapshot().term1);
  mgr.remove("term1");
  assert.equal(mgr.snapshot().term1, undefined);
}

// Concurrent appends serialize through the dispatching gate.
{
  const order = [];
  const mgr = createVirtualTerminalInputManager({
    dispatch: async (_agentId, line) => {
      order.push(`begin:${line}`);
      await new Promise((r) => setTimeout(r, 10));
      order.push(`end:${line}`);
    },
  });
  await Promise.all([
    mgr.append("agent-h", "term1", "alpha\r"),
    mgr.append("agent-h", "term1", "beta\r"),
  ]);
  assert.deepEqual(order, ["begin:alpha", "end:alpha", "begin:beta", "end:beta"], `expected serialized dispatch, got ${JSON.stringify(order)}`);
}

console.log("virtual-terminal-input.test.js: all assertions passed");
