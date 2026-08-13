#!/usr/bin/env node
// Verifies comms_console_tail / comms_console_input call the right endpoints
// with the right args. httpCall is injected so no real server is needed.
import assert from "node:assert/strict";

// IS_REMOTE is derived from a server URL env var at import time; set one so the
// handlers take the remote path.
process.env.AIFY_SERVER_URL = process.env.AIFY_SERVER_URL || "http://127.0.0.1:8800";
process.env.AIFY_AGENT_ID = "manager-bot";

const {
  commsConsoleTailHandler,
  commsConsoleInputHandler,
  CONSOLE_INPUT_TOOL_DESCRIPTION,
  COMMS_SEND_TOOL_DESCRIPTION,
} = await import("../server.js");

// `comms_interrupt` moved to the dispatch group in v0.5.4. It is exercised here rather than in
// `dispatch-tools.test.js` because what it asserts is console behaviour — the Ctrl+C byte and the
// endpoint it reaches — which is this file's subject. The tool's registration and schema are proven
// next to its four siblings; only the handler is imported here.
const { commsInterruptHandler } = await import("../dispatch-tools.mjs");

assert.match(CONSOLE_INPUT_TOOL_DESCRIPTION, /recovery-only/i);
assert.match(CONSOLE_INPUT_TOOL_DESCRIPTION, /read the console first/i);
assert.match(CONSOLE_INPUT_TOOL_DESCRIPTION, /do not inject normal work messages/i);

// --- comms_interrupt: target the agent's live console with terminal-native Ctrl+C ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { ok: true, live: true, terminalId: "term_live", controlId: "ctl_interrupt" };
  };
  const res = await commsInterruptHandler(
    { agentId: "busy-agent", from: "manager-bot" },
    { httpCall: fakeHttp },
  );
  assert.deepEqual(calls, [{
    method: "POST",
    endpoint: "/agents/busy-agent/console/input",
    body: { text: "\u0003", enter: false, from: "manager-bot" },
  }]);
  assert.match(res.content[0].text, /Interrupted busy-agent/);
}

assert.match(COMMS_SEND_TOOL_DESCRIPTION, /omit requireReply/i,
  "normal type defaults should not require a reply override");
assert.match(COMMS_SEND_TOOL_DESCRIPTION, /set requireReply=true/i,
  "the exceptional opt-in must be explicit");
assert.match(COMMS_SEND_TOOL_DESCRIPTION, /set requireReply=false/i,
  "the intentional fire-and-forget override must be explicit");

// --- comms_console_tail: GET the console endpoint with capped lines ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint) => {
    calls.push({ method, endpoint });
    return { ok: true, live: true, terminalId: "term_x", status: "running", lines: 3, output: "a\nb\nc" };
  };
  const res = await commsConsoleTailHandler({ agentId: "stuck-agent", lines: 3 }, { httpCall: fakeHttp });
  assert.equal(calls.length, 1, "tail should make exactly one call");
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].endpoint, "/agents/stuck-agent/console?lines=3");
  assert.match(res.content[0].text, /last 3 lines/);
  assert.match(res.content[0].text, /a\nb\nc/);
}

// --- comms_console_tail: clamps lines to the 1..200 range ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint) => {
    calls.push({ method, endpoint });
    return { ok: true, live: true, terminalId: "t", status: "running", lines: 200, output: "x" };
  };
  await commsConsoleTailHandler({ agentId: "a", lines: 9999 }, { httpCall: fakeHttp });
  assert.equal(calls[0].endpoint, "/agents/a/console?lines=200", "lines must be clamped to 200");
}

// --- comms_console_tail: live:false surfaces the server message ---
{
  const fakeHttp = async () => ({ ok: true, live: false, message: "a has no live console (it lazy-starts on a message)." });
  const res = await commsConsoleTailHandler({ agentId: "a" }, { httpCall: fakeHttp });
  assert.equal(res.isError, undefined, "live:false is not an error");
  assert.match(res.content[0].text, /no live console/);
}

// --- comms_console_input: POST the input endpoint with text/enter/from ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { ok: true, live: true, terminalId: "term_y", controlId: "ctl_1" };
  };
  const res = await commsConsoleInputHandler(
    { agentId: "stuck-agent", text: "/status", enter: true, from: "manager-bot" },
    { httpCall: fakeHttp }
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].endpoint, "/agents/stuck-agent/console/input");
  assert.deepEqual(calls[0].body, { text: "/status", enter: true, from: "manager-bot" });
  // C8 (2026-07-26): this asserted /Input sent to stuck-agent/, and "Input sent" is the exact
  // sentence an operator's sc-manager read as confirmation before burning ~15 minutes retrying a
  // lever that could not work. The write is only QUEUED; even a completed control proves nothing
  // beyond "bytes reached the PTY". Pin the honest wording AND the absence of the old claim, so a
  // future edit cannot quietly restore a success message this call cannot justify.
  assert.match(res.content[0].text, /Input QUEUED to stuck-agent/);
  assert.match(res.content[0].text, /NOT confirmation/);
  assert.doesNotMatch(res.content[0].text, /Input sent/,
    "must not claim the input was delivered — that is unknowable from here");
}

// --- comms_console_input: defaults enter=true and from=AIFY_AGENT_ID ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { ok: true, live: true, terminalId: "t", controlId: "c" };
  };
  await commsConsoleInputHandler({ agentId: "a" }, { httpCall: fakeHttp });
  assert.equal(calls[0].body.enter, true, "enter defaults true");
  assert.equal(calls[0].body.from, "manager-bot", "from defaults to AIFY_AGENT_ID");
  assert.equal(calls[0].body.text, "", "text defaults to empty string");
}

// --- comms_console_input: ok:false from server becomes an error result ---
{
  const fakeHttp = async () => ({ ok: false, live: false, message: "a has no live console; send a message to start it first." });
  const res = await commsConsoleInputHandler({ agentId: "a" }, { httpCall: fakeHttp });
  assert.equal(res.isError, true);
  assert.match(res.content[0].text, /no live console/);
}

// --- comms_console_tail: a DEAD worker's recording is served, and marked NOT LIVE (v0.2 WS-1) ---
// The 2026-08-07 incident: a managed hermes worker died 65s after spawn, the cause sat in
// terminal_sessions.output for 2.5h, and this handler threw it away because live was false.
const HERMES_FATAL_LINE =
  "[hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/ did not become ready within 60000ms: fetch failed";
{
  const fakeHttp = async () => ({
    ok: true,
    live: false,
    historical: true,
    terminalId: "term_1786109794427_0f32fd75",
    status: "stopped",
    stoppedAt: "2026-08-07T13:37:39Z",
    failureLine: HERMES_FATAL_LINE,
    lines: 2,
    output: [HERMES_FATAL_LINE, "[hermes-aify] FATAL: managed gateway host did not come up."].join("\n"),
  });
  const res = await commsConsoleTailHandler({ agentId: "sc-architect" }, { httpCall: fakeHttp });
  assert.ok(!res.isError, "a historical read is a successful answer, not an error");
  const text = res.content[0].text;
  assert.match(text, /NOT LIVE/, "must not be readable as the state of a running session");
  assert.match(text, /term_1786109794427_0f32fd75/);
  assert.match(text, /2026-08-07T13:37:39Z/);
  assert.ok(
    text.includes(`Cause: ${HERMES_FATAL_LINE}`),
    "the one-line cause must LEAD, so the answer is useful without reading the whole dump",
  );
  assert.match(text, /this worker is gone/i);
}

// --- comms_console_tail: an agent that never had a terminal keeps the pre-v0.2 answer ---
{
  const fakeHttp = async () => ({
    ok: true,
    live: false,
    historical: false,
    message: "never-ran has no live console (it lazy-starts on a message).",
  });
  const res = await commsConsoleTailHandler({ agentId: "never-ran" }, { httpCall: fakeHttp });
  assert.match(res.content[0].text, /lazy-starts/);
  assert.doesNotMatch(res.content[0].text, /NOT LIVE/);
}

// --- comms_console_tail: a historical read with nothing recorded says so, invents nothing ---
{
  const fakeHttp = async () => ({
    ok: true, live: false, historical: true,
    terminalId: "t1", status: "failed", stoppedAt: "", failureLine: "", lines: 0, output: "",
  });
  const res = await commsConsoleTailHandler({ agentId: "silent" }, { httpCall: fakeHttp });
  assert.match(res.content[0].text, /nothing was recorded/);
  assert.doesNotMatch(res.content[0].text, /Cause:/, "no cause line when no cause was recorded");
}

// --- comms_console_tail: a LIVE console is unchanged by the historical branch ---
{
  const fakeHttp = async () => ({
    ok: true, live: true, historical: false, terminalId: "term_live", status: "attached",
    lines: 1, output: "> waiting for input",
  });
  const res = await commsConsoleTailHandler({ agentId: "alive" }, { httpCall: fakeHttp });
  assert.match(res.content[0].text, /Console of alive/);
  assert.doesNotMatch(res.content[0].text, /NOT LIVE/);
}

// --- the tool description must point agents at it for diagnosis ---
{
  const fs = await import("node:fs");
  const source = fs.readFileSync(new URL("../server.js", import.meta.url), "utf8");
  const idx = source.indexOf('"comms_console_tail"');
  assert.ok(idx > 0, "comms_console_tail registration must exist");
  const registration = source.slice(idx, idx + 900);
  assert.match(registration, /DEAD worker/,
    "an agent will not reach for this on a dead worker unless the description says it works");
  assert.match(registration, /LAST RECORDED/);
}

console.log("console-tools.test.js: all assertions passed");

// --- formatOutboundActivity: the field that retires the false "silent lane" claim ---
// Audit finding 1. Every other field on the health surface answers about inbound traffic or
// registration liveness; during the 2026-08-10 outage all of them were true while a reply sat
// undelivered, and a manager reported the lane dead three times on that evidence.
{
  // v0.5.4: moved to tool-response-format.mjs with the other pure response formatters. Importing
  // from server.js used to be the ONLY way to reach it — server.js is the bin entry point, so this
  // test paid the cost of importing the whole bridge to check one string.
  const { formatOutboundActivity } = await import("../tool-response-format.mjs");

  assert.match(
    formatOutboundActivity({ outbound: { lastSentAt: "2026-08-10T16:02:58Z" } }),
    /OUTBOUND.*sent 2026-08-10T16:02:58Z/,
    "a sent message is production and must be shown as such",
  );

  assert.match(
    formatOutboundActivity({ outbound: { lastCompletedRunAt: "2026-08-10T16:03:00Z" } }),
    /completed a run 2026-08-10T16:03:00Z/,
  );

  const both = formatOutboundActivity({
    outbound: { lastSentAt: "2026-08-10T16:02:58Z", lastCompletedRunAt: "2026-08-10T16:03:00Z" },
  });
  assert.match(both, /sent .*; completed a run /, "both facts when both exist");

  // The honesty case: a pre-fix service sends no `outbound`, and rendering that as "never sent
  // anything" would manufacture exactly the confident-but-wrong claim this finding is about.
  for (const missing of [{}, undefined, null]) {
    const out = formatOutboundActivity(missing);
    assert.match(out, /unknown/, "absence must read as unknown");
    assert.match(out, /pre-v0\.3\.1/, "and must name the reason we cannot answer");
    assert.doesNotMatch(out, /never/i, "must not assert the agent has produced nothing");
  }

  // AUDIT 4/4 F2: the two absences are NOT the same absence, and collapsing them reopened a
  // smaller copy of the finding above. A current service answering `outbound: {}` for a fresh
  // agent HAS answered — reporting that as "the service did not report" is false and blames the
  // wrong component.
  const answeredEmpty = formatOutboundActivity({ outbound: {} });
  assert.match(answeredEmpty, /none recorded/, "a known-empty answer is a fact, not a gap");
  assert.match(answeredEmpty, /the service answered/);
  assert.doesNotMatch(
    answeredEmpty,
    /did not report/,
    "the service DID report — saying otherwise sends the operator to debug the wrong layer",
  );

  // `_agent_record_to_dict` always emits the key, so key presence is the discriminator. If that
  // ever stops being true this assertion is the thing that notices.
  assert.notEqual(
    formatOutboundActivity({ outbound: {} }),
    formatOutboundActivity({}),
    "known-empty and not-reported must never render identically",
  );
}
