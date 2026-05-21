import assert from "assert";

process.env.AIFY_PI_COMMAND = process.execPath;

const { PiSession, formatPiEventAsTerminalFrame } = await import("../pi-session.js");

// ── Pure formatter ─────────────────────────────────────────────────────────

// ANSI escape sequences (CSI ... letter, plus reset, bold, dim — full
// vt100 SGR set used by the synthesizer). Strip for content assertions so
// tests pin semantics, not exact color codes.
const ANSI_RE = /\x1b\[[0-9;]*[A-Za-z]/g;
function noColor(s) { return String(s || "").replace(ANSI_RE, ""); }

// Bare 'ready' frame is empty — PiSession's _emitReadyBanner replaces it
// with a richer banner that depends on model/effort/session context.
assert.equal(formatPiEventAsTerminalFrame({ type: "ready" }), "");
assert.match(noColor(formatPiEventAsTerminalFrame({ type: "agent_start" })), /turn started/);
assert.match(noColor(formatPiEventAsTerminalFrame({ type: "agent_end" })), /turn ended/);
{
  const usedEnd = formatPiEventAsTerminalFrame({ type: "agent_end", usage: { input_tokens: 1200, output_tokens: 340 } });
  assert.match(noColor(usedEnd), /in=1200/);
  assert.match(noColor(usedEnd), /out=340/);
}
assert.match(
  noColor(formatPiEventAsTerminalFrame({ type: "error", error: "auth required" })),
  /error.*auth required/,
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
{
  const startFrame = noColor(formatPiEventAsTerminalFrame({
    type: "tool_execution_start",
    tool: { name: "bash", input: { command: "ls -la" } },
  }));
  assert.match(startFrame, /→ bash/);
  assert.match(startFrame, /"command":"ls -la"/);
}
{
  const endOk = noColor(formatPiEventAsTerminalFrame({
    type: "tool_execution_end",
    tool: { name: "bash", result: "total 0" },
    success: true,
  }));
  assert.match(endOk, /✓ bash/);
  assert.match(endOk, /total 0/);
}
{
  const endFail = noColor(formatPiEventAsTerminalFrame({
    type: "tool_execution_end",
    tool: { name: "bash" },
    success: false,
    error: "permission denied",
  }));
  assert.match(endFail, /✗ bash/);
  assert.match(endFail, /permission denied/);
}
{
  const promptFrame = noColor(formatPiEventAsTerminalFrame({
    type: "RpcExtensionUIRequest",
    request: { kind: "confirm", question: "Proceed?", options: ["yes", "no"] },
  }));
  assert.match(promptFrame, /\? confirm/);
  assert.match(promptFrame, /Proceed\?/);
  assert.match(promptFrame, /yes \| no/);
}
{
  const usageFrame = noColor(formatPiEventAsTerminalFrame({
    type: "usage",
    usage: { input_tokens: 250, output_tokens: 80 },
  }));
  assert.match(usageFrame, /in=250/);
  assert.match(usageFrame, /out=80/);
}
assert.equal(formatPiEventAsTerminalFrame({ type: "response", id: "x" }), "");
assert.equal(formatPiEventAsTerminalFrame({ type: "unknown_event" }), "");

// Long input gets briefed
const longInput = { command: "x".repeat(500) };
const briefFrame = formatPiEventAsTerminalFrame({
  type: "tool_execution_start",
  tool: { name: "bash", input: longInput },
});
const briefFrameNoColor = noColor(briefFrame);
assert.ok(briefFrameNoColor.length < 350, `expected brief tool input, got ${briefFrameNoColor.length} chars`);
assert.ok(briefFrameNoColor.includes("…"), `expected ellipsis in trimmed frame, got ${briefFrameNoColor.slice(-30)}`);

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
