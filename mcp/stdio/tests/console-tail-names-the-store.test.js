// A recovered console tail says it was recovered, because a reconstruction is not the raw stream.
//
// THE FAILURE THIS COMPLETES. On 2026-08-26 the operator asked why sc-claude had died, and
// `comms_console_tail` answered "(nothing was recorded)" for the agent beside it. Two stores hold a
// terminal's output -- the accumulated `terminal_sessions.output` column and the `terminal_events`
// rows -- and the endpoint read only the first. sc-architect's column held exactly its own
// `[terminal exited]` marker, eighteen characters, while 14,773 characters of its last screen sat in
// the events of the same terminal.
//
// The service half now falls back to the events. This is the other half: the tool must SAY when it
// did, because "the column held nothing but an exit marker and this came from the event log" changes
// how much a reader should trust what follows -- events are capped per terminal, so a recovered tail
// can be shorter than the stream was.
//
// SAID ONLY WHEN IT IS THE SURPRISING CASE. The column is the normal source and remarking on it every
// time would train the reader to skip the line that matters. This is the same reason
// `terminal-attach-notice.js` stays silent on an unknown pty flag rather than warning by default.

import assert from "node:assert/strict";
import test from "node:test";

process.env.AIFY_SERVER_URL = process.env.AIFY_SERVER_URL || "http://127.0.0.2:1";
process.env.CLAUDE_MCP_SERVER_URL = process.env.AIFY_SERVER_URL;
process.env.AIFY_AGENT_ID = process.env.AIFY_AGENT_ID || "test-reader";

const { commsConsoleTailHandler } = await import("../console-tools.mjs");

/** Drives the handler against a canned endpoint response. No server, no network. */
async function tailWith(response) {
  const result = await commsConsoleTailHandler(
    { agentId: "sc-architect", lines: 40 },
    { httpCall: async () => response },
  );
  return result.content[0].text;
}

const DEAD = {
  live: false,
  historical: true,
  terminalId: "term_1787708943323_1fdebf65",
  status: "stopped",
  stoppedAt: "2026-08-26T02:07:43Z",
  failureLine: "",
};

test("a tail recovered from the events says so", async () => {
  const text = await tailWith({
    ...DEAD,
    recordedFrom: "events",
    output: 'Search Files("build_terrain_heat_source")',
  });
  assert.match(text, /recovered from the terminal's recorded events/i, "the reader is not told this is a reconstruction");
  assert.match(text, /exit marker/i, "the reason the column was skipped is not stated");
  assert.match(text, /build_terrain_heat_source/, "the recovered output itself went missing");
});

test("an ordinary tail from the output column says nothing extra", async () => {
  // The control on the control: if this line appeared always, it would carry no information and the
  // assertion above would pass for a tool that had learned nothing.
  const text = await tailWith({
    ...DEAD,
    recordedFrom: "output",
    output: "for h in UnifiedFluid; do git show a8cb16bb:sim/fields/$h.h; done",
  });
  assert.doesNotMatch(text, /recovered from/i);
  assert.match(text, /UnifiedFluid/, "the ordinary path lost its output");
});

test("an older service that sends no recordedFrom is treated as ordinary", async () => {
  // The bridge and the service deploy separately, so a bridge carrying this change will meet a
  // service that predates it. Absent must read as "the column answered", never as a recovery.
  const text = await tailWith({ ...DEAD, output: "some recorded output" });
  assert.doesNotMatch(text, /recovered from/i);
  assert.match(text, /some recorded output/);
});

test("the NOT LIVE framing survives on every path", async () => {
  // The property this tool already had and must not lose: a recording must never read as the state
  // of a running session.
  for (const recordedFrom of ["events", "output", undefined]) {
    const text = await tailWith({ ...DEAD, recordedFrom, output: "x" });
    assert.match(text, /NOT LIVE/, `the history warning vanished for recordedFrom=${recordedFrom}`);
    assert.match(text, /term_1787708943323_1fdebf65/, "the terminal id vanished");
  }
});

test("a genuinely empty recording still says nothing was recorded", async () => {
  // When both stores are silent the honest answer is unchanged. Recovering from events must not
  // manufacture an empty "recovered" claim.
  const text = await tailWith({ ...DEAD, recordedFrom: "", output: "" });
  assert.match(text, /\(nothing was recorded\)/);
  assert.doesNotMatch(text, /recovered from/i);
});
