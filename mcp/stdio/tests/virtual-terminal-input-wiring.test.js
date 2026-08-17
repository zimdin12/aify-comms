// The module-scope wiring between the console input buffer and the dispatcher.
//
// Sixteenth cluster off the V8-coverage census: `virtual-terminals.mjs`'s `dispatch` and `onError` - the two
// closures handed to `createVirtualTerminalInputManager` at module scope - plus `virtual-terminal-input.js`'s
// `clear`, all with a zero call count. `virtual-terminal-input.test.js` drives the manager with its own stubs,
// which is why the REAL wiring had never been exercised: the manager is well covered, the two functions
// connecting it to the bridge were not.
//
// WHAT THIS PROTECTS. Operator keystrokes reach a managed pi agent one byte at a time through
// terminal_controls, are buffered per terminal until a newline, and are then dispatched. The dashboard console
// is a hard requirement for managed agents, and the failure mode here is specific: if a dispatch rejection
// escaped `append`, the HTTP handler that fed those bytes would fail, and the console would stop accepting
// input at the first bad line rather than reporting it and carrying on.
//
// NO PI SESSION IS ACQUIRED. `dispatchVirtualTerminalLine` reads the bridge's agent state FIRST and throws for
// an unknown agent before it reaches `acquirePiSession`, so an unregistered id drives the whole
// buffer -> dispatch -> onError path without a runtime. That is also the realistic failure: a console line
// arriving for an agent this bridge no longer holds.
//
// ONE MUTATION SURVIVES: passing `""` as the line BODY from the module-scope dispatch closure. That seam only
// carries the body as far as a dispatcher which reads its agent state first and throws before the body is used,
// so an unregistered agent cannot observe it. Reaching further would mean registering state and acquiring a
// real pi session. The manager's own (agentId, line) pass-through is covered with stubs in
// virtual-terminal-input.test.js; what is untestable here is specifically the closure's second argument.

import assert from "node:assert/strict";
import test from "node:test";

import { VIRTUAL_TERMINAL_INPUT } from "../virtual-terminals.mjs";

const UNKNOWN_AGENT = "no-such-agent-virtual-terminal-wiring";
const TERMINAL = "term_wiring_1";

// Returns the console.error lines the run produced, and rethrows nothing itself — several tests here are about
// what is LOGGED rather than what is thrown.
async function captureErrors(run) {
  const original = console.error;
  const lines = [];
  console.error = (...args) => { lines.push(args.map(String).join(" ")); };
  try {
    await run();
  } finally {
    console.error = original;
  }
  return lines;
}

// The manager is module-scope state shared with the live bridge, so every test leaves it empty. `clear()` is
// itself one of the functions under test here.
test.afterEach(() => { VIRTUAL_TERMINAL_INPUT.clear(); });

test("a completed line reaches the dispatcher, and its failure is LOGGED rather than thrown", async () => {
  const lines = await captureErrors(async () => {
    // Must not reject: the HTTP handler feeding these bytes would otherwise fail, and the console would stop
    // accepting input at the first bad line.
    await assert.doesNotReject(() =>
      VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, "status please\n"));
  });
  const logged = lines.join("\n");

  assert.equal(lines.length, 1, "the dispatch failure was silent");
  assert.match(logged, /virtual-terminal dispatch failed/);
  // The id must be in the PREFIX the handler builds from its own context...
  assert.match(logged, new RegExp(`failed for "${UNKNOWN_AGENT}"`), "the log does not say WHICH agent");
  // ...and again inside the dispatcher's own message, which is what proves the agent id was FORWARDED and not
  // merely logged. The id appears twice for that reason; asserting only its presence anywhere passes even when
  // the dispatcher was handed the wrong one.
  assert.match(logged, new RegExp(`No bridge state for agent "${UNKNOWN_AGENT}"`),
    "the dispatcher was called with a different agent than the buffer holds");
  assert.match(logged, /status please/, "the log does not say which line failed");
});

test("re-binding a terminal to a NEW agent redirects the line already half-typed into it", async () => {
  // A terminal can change hands - a bridge rotation re-registers the agent that owns it. Each append refreshes
  // the entry's agent, so the completed line goes to whoever owns the terminal NOW. Keeping the first agent
  // would deliver an operator's command to a session they are no longer looking at.
  const OTHER_AGENT = "second-agent-virtual-terminal-wiring";
  const lines = await captureErrors(async () => {
    await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, "half a comm");
    await VIRTUAL_TERMINAL_INPUT.append(OTHER_AGENT, TERMINAL, "and\n");
  });

  assert.equal(lines.length, 1);
  assert.match(lines[0], new RegExp(`failed for "${OTHER_AGENT}"`), "the line went to the previous owner");
  assert.match(lines[0], /half a command/, "the two chunks were not joined into one line");
});

test("input for a terminal with NO id is dropped, not merged into a shared buffer", async () => {
  // Without the id guard every id-less write would land in one buffer keyed `undefined`, so two terminals'
  // keystrokes would interleave into a single line and be dispatched as one command.
  const lines = await captureErrors(async () => {
    await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, "", "orphan line\n");
    await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, null, "another\n");
    await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, undefined, "third\n");
  });

  assert.deepEqual(lines, [], "input with no terminal id was dispatched");
  assert.deepEqual(VIRTUAL_TERMINAL_INPUT.snapshot(), {}, "an id-less buffer was created");
});

test("the logged line is TRUNCATED and quoted", async () => {
  // A console paste can be enormous, and it is operator-typed text: unbounded and unquoted, it would spill
  // into the bridge's log as newlines and control characters. 80 chars, JSON-quoted.
  const lines = await captureErrors(() =>
    VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, `${"A".repeat(500)}\n`));

  const logged = lines.join("\n");
  assert.equal(lines.length, 1);
  const quoted = /line=("(?:[^"\\]|\\.)*")/.exec(logged);
  assert.ok(quoted, `the line was not quoted: ${logged.slice(0, 200)}`);
  assert.equal(JSON.parse(quoted[1]).length, 80, "the logged line was not truncated to 80 characters");
});

test("a line containing quotes and newlines cannot break the log record", async () => {
  const lines = await captureErrors(() =>
    VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, 'say "hi"\ttabbed\n'));

  const logged = lines.join("\n");
  assert.equal(logged.split("\n").length, 1, "the operator's text added a line to the log");
  const quoted = /line=("(?:[^"\\]|\\.)*")/.exec(logged);
  assert.ok(quoted, "the quoting broke on an embedded double quote");
  assert.equal(JSON.parse(quoted[1]), 'say "hi"\ttabbed');
});

test("an INCOMPLETE line is buffered and dispatched to nobody", async () => {
  // The whole point of the buffer: keystrokes arrive one byte at a time, and a half-typed command must not be
  // sent to the agent.
  const lines = await captureErrors(async () => {
    for (const ch of "half a comm") {
      await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, ch);
    }
  });

  assert.deepEqual(lines, [], "an unterminated line was dispatched");
  const snapshot = VIRTUAL_TERMINAL_INPUT.snapshot();
  assert.equal(snapshot[TERMINAL]?.buffer, "half a comm", "the keystrokes were not retained");
  assert.equal(snapshot[TERMINAL]?.agentId, UNKNOWN_AGENT);
});

test("clear() empties every terminal's buffer", async () => {
  // Used on teardown and when a bridge stops owning its terminals. Without it, a superseded bridge's buffered
  // half-line would be waiting to be completed by the NEXT operator to type into that terminal.
  await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, "term_a", "partial a");
  await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, "term_b", "partial b");
  assert.deepEqual(Object.keys(VIRTUAL_TERMINAL_INPUT.snapshot()).sort(), ["term_a", "term_b"]);

  VIRTUAL_TERMINAL_INPUT.clear();
  assert.deepEqual(VIRTUAL_TERMINAL_INPUT.snapshot(), {}, "buffers survived clear()");
});

test("remove() drops one terminal and leaves the others", async () => {
  await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, "term_a", "partial a");
  await VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, "term_b", "partial b");
  VIRTUAL_TERMINAL_INPUT.remove("term_a");
  assert.deepEqual(Object.keys(VIRTUAL_TERMINAL_INPUT.snapshot()), ["term_b"]);
});

test("several complete lines in one chunk are each dispatched, in order", async () => {
  // A paste arrives as one write. Each line is its own turn, and the order is the order the operator typed.
  // All three newline forms count as a terminator, because the source is a real terminal.
  const lines = await captureErrors(() =>
    VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, "first\nsecond\r\nthird\r"));

  assert.equal(lines.length, 3, "not every line in the paste was dispatched");
  assert.ok(lines[0].includes("first") && lines[1].includes("second") && lines[2].includes("third"),
    `lines arrived out of order: ${lines.map((l) => l.slice(-40)).join(" | ")}`);

  // Draining empties the BUFFER; it does not forget the terminal. The entry survives to receive the next
  // keystroke, which is why `remove`/`clear` exist as separate operations.
  const snapshot = VIRTUAL_TERMINAL_INPUT.snapshot();
  assert.equal(snapshot[TERMINAL]?.buffer, "", "a dispatched line was left in the buffer");
  assert.equal(snapshot[TERMINAL]?.dispatching, false, "the terminal was left marked as dispatching");
});

test("a trailing partial line survives the lines dispatched ahead of it", async () => {
  // The realistic paste: complete commands followed by a half-typed one. The partial must stay buffered, not
  // be sent as if the operator had pressed enter.
  const lines = await captureErrors(() =>
    VIRTUAL_TERMINAL_INPUT.append(UNKNOWN_AGENT, TERMINAL, "done\nstill typ"));

  assert.equal(lines.length, 1, "the partial line was dispatched too");
  assert.match(lines[0], /done/);
  assert.equal(VIRTUAL_TERMINAL_INPUT.snapshot()[TERMINAL]?.buffer, "still typ");
});
