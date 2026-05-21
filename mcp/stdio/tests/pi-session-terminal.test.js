import assert from "assert";

process.env.AIFY_PI_COMMAND = process.execPath;

const { PiSession, formatPiEventAsTerminalFrame } = await import("../pi-session.js");

// ── Pure formatter ─────────────────────────────────────────────────────────

assert.equal(formatPiEventAsTerminalFrame({ type: "ready" }), "[pi rpc ready]\r\n");
assert.equal(formatPiEventAsTerminalFrame({ type: "agent_start" }), "\r\n[turn started]\r\n");
assert.equal(formatPiEventAsTerminalFrame({ type: "agent_end" }), "\r\n[turn ended]\r\n");
assert.equal(
  formatPiEventAsTerminalFrame({ type: "error", error: "auth required" }),
  "\r\n[error] auth required\r\n",
);
assert.equal(
  formatPiEventAsTerminalFrame({
    type: "message_update",
    assistantMessageEvent: { type: "text_delta", delta: "Hello" },
  }),
  "Hello",
);
assert.equal(
  formatPiEventAsTerminalFrame({
    type: "message_update",
    assistantMessageEvent: { type: "text_end" },
  }),
  "\r\n",
);
assert.equal(
  formatPiEventAsTerminalFrame({
    type: "tool_execution_start",
    tool: { name: "bash", input: { command: "ls -la" } },
  }),
  '\r\n[tool] bash {"command":"ls -la"}\r\n',
);
assert.equal(
  formatPiEventAsTerminalFrame({
    type: "tool_execution_end",
    tool: { name: "bash", result: "total 0" },
    success: true,
  }),
  "[tool] bash → ok total 0\r\n",
);
assert.equal(
  formatPiEventAsTerminalFrame({
    type: "tool_execution_end",
    tool: { name: "bash" },
    success: false,
    error: "permission denied",
  }),
  "[tool] bash → ERROR permission denied\r\n",
);
assert.equal(
  formatPiEventAsTerminalFrame({
    type: "RpcExtensionUIRequest",
    request: { kind: "confirm", question: "Proceed?", options: ["yes", "no"] },
  }),
  "\r\n[prompt:confirm] Proceed? (yes | no)\r\n",
);
assert.equal(formatPiEventAsTerminalFrame({ type: "response", id: "x" }), "");
assert.equal(formatPiEventAsTerminalFrame({ type: "unknown_event" }), "");

// Long input gets briefed
const longInput = { command: "x".repeat(500) };
const briefFrame = formatPiEventAsTerminalFrame({
  type: "tool_execution_start",
  tool: { name: "bash", input: longInput },
});
assert.ok(briefFrame.length < 300, `expected brief tool input, got ${briefFrame.length} chars`);
assert.ok(briefFrame.endsWith("…\r\n") || briefFrame.endsWith("…\"}\r\n"), `expected ellipsis suffix, got ${briefFrame.slice(-10)}`);

// ── Sink + buffer mechanics ─────────────────────────────────────────────────

function makeSession() {
  return new PiSession({ agentId: "test-agent", agentInfo: {} });
}

// Buffer survives until sink attached, then drains in order.
{
  const session = makeSession();
  session._pushTerminalFrame("first ");
  session._pushTerminalFrame("second ");
  session._pushTerminalFrame("third");
  assert.equal(session.__terminalBufferForTests().length, 3);

  const received = [];
  session.attachTerminalSink(async (output, status) => {
    received.push({ output, status });
  });
  // wait for the microtask chain to drain
  await session._terminalFlushChain;
  assert.deepEqual(
    received.map((r) => r.output).join(""),
    "first second third",
    `expected ordered drain, got ${JSON.stringify(received)}`,
  );
  assert.equal(session.__terminalBufferForTests().length, 0);
}

// Frames pushed after attach flow straight through.
{
  const session = makeSession();
  const received = [];
  session.attachTerminalSink(async (output) => {
    received.push(output);
  });
  session._pushTerminalFrame("alpha ");
  session._pushTerminalFrame("beta");
  await session._terminalFlushChain;
  assert.deepEqual(received, ["alpha ", "beta"]);
}

// Sink failure is best-effort: the queue keeps draining.
{
  const session = makeSession();
  let calls = 0;
  session.attachTerminalSink(async () => {
    calls++;
    if (calls === 1) throw new Error("sink failed");
  });
  session._pushTerminalFrame("a");
  session._pushTerminalFrame("b");
  session._pushTerminalFrame("c");
  await session._terminalFlushChain;
  assert.equal(calls, 3, "expected all three frames attempted despite sink failure");
}

// Detach pauses delivery; reattach drains the rest.
{
  const session = makeSession();
  const received = [];
  session.attachTerminalSink(async (output) => {
    received.push(output);
  });
  session._pushTerminalFrame("one");
  await session._terminalFlushChain;
  session.detachTerminalSink();
  session._pushTerminalFrame("two");
  session._pushTerminalFrame("three");
  // detached: should remain buffered
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(received.length, 1, `expected only first frame delivered; got ${JSON.stringify(received)}`);

  session.attachTerminalSink(async (output) => {
    received.push(output);
  });
  await session._terminalFlushChain;
  assert.deepEqual(received, ["one", "two", "three"]);
}

// Buffer cap drops the oldest frames when the limit is exceeded.
{
  const session = makeSession();
  const big = "x".repeat(65536);
  session._pushTerminalFrame("OLDEST");
  session._pushTerminalFrame(big);
  session._pushTerminalFrame("NEWEST");
  // Total > 64KB; the OLDEST must have been evicted.
  const pending = session.__terminalBufferForTests();
  const joined = pending.map((f) => f.text).join("|");
  assert.ok(
    !joined.startsWith("OLDEST"),
    `expected oldest frame evicted, got buffer starting with ${joined.slice(0, 30)}`,
  );
  assert.ok(joined.endsWith("NEWEST"), `expected newest frame retained, got buffer ending with ${joined.slice(-30)}`);
}

// Status is delivered with the frame.
{
  const session = makeSession();
  const received = [];
  session.attachTerminalSink(async (output, status) => {
    received.push({ output, status });
  });
  session._pushTerminalFrame("[pi rpc ready]\r\n", "running");
  await session._terminalFlushChain;
  assert.deepEqual(received, [{ output: "[pi rpc ready]\r\n", status: "running" }]);
}

console.log("pi-session-terminal.test.js: all assertions passed");
