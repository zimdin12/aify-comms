// Every virtual-terminal control gets an ACK — and `updateTerminalControl` is how.
//
// The export ratchet listed `virtual-terminals.mjs#updateTerminalControl` as named by no test. It is
// one line, and what it is one line OF is the thing that stops the dashboard retrying forever: the
// service marks a terminal control pending, the bridge acts on it, and this PATCH is the only signal
// that it was handled. Miss it on any branch and the reconcile path re-issues that control on every
// pass — the "infinite-retry" the handler's own comments name twice.
//
// SO THE INTERESTING TEST IS A CENSUS, not a single call: every action `handleVirtualTerminalControl`
// accepts must produce exactly one ack, including the two that do NO WORK. `resize` has no PTY
// dimensions to set and a stray `start` is meaningless for a terminal created through
// `/virtual-terminal/ensure` — both still have to answer, and answering is the whole of what they do.
//
// A REAL HTTP SERVER on 127.0.0.2, following `virtual-terminals.test.js`, which records why: the
// module reaches the network through an imported binding that cannot be monkey-patched, and
// `aify-service-endpoint.mjs` resolves its URL at module load, once per process. The env var is set
// BEFORE the import for that reason.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

const REQUESTS = [];

const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("{}");
  });
});

const PORT = await new Promise((resolve) => {
  SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port));
});

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;

// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a

// live wrapper environment exports it. Setting only the new name left the fake below unused.

process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
const { handleVirtualTerminalControl, updateTerminalControl } =
  await import("../virtual-terminals.mjs");

test.after(() => SERVER.close());

function reset() {
  REQUESTS.length = 0;
}

function acks() {
  return REQUESTS.filter((r) => r.method === "PATCH" && r.url.startsWith("/api/v1/terminals/controls/"));
}

// ── updateTerminalControl ───────────────────────────────────────────────────────────────────────

test("the ack PATCHes the control's own endpoint", async () => {
  reset();
  await updateTerminalControl("ctl-1", { status: "completed" });
  assert.equal(acks().length, 1);
  assert.equal(acks()[0].url, "/api/v1/terminals/controls/ctl-1");
});

test("the control id is URL-ENCODED into the path", async () => {
  // Control ids are generated server-side, but the id travels through a URL path and a `/` or `#` in
  // one would silently address a different resource — or a nonexistent one, which reads as a control
  // that was never acknowledged and is therefore retried forever.
  reset();
  await updateTerminalControl("ctl/../weird #1", { status: "completed" });
  assert.equal(acks()[0].url, "/api/v1/terminals/controls/ctl%2F..%2Fweird%20%231");
});

test("the body is forwarded as JSON", async () => {
  reset();
  await updateTerminalControl("ctl-1", { status: "completed", terminalStatus: "running" });
  assert.deepEqual(JSON.parse(acks()[0].body), { status: "completed", terminalStatus: "running" });
});

// ── the ack census ──────────────────────────────────────────────────────────────────────────────

const AGENT = "vt-agent";
const TERMINAL = "vterm_ack";

function control(action, extra = {}) {
  return { id: `ctl-${action}`, action, ...extra };
}

test("EVERY accepted action acks exactly once", async () => {
  // The census. A branch that acts and does not answer is invisible: the control stays pending, the
  // reconcile path re-issues it, and the work happens again on every pass.
  for (const action of ["input", "resize", "stop", "start"]) {
    reset();
    await handleVirtualTerminalControl(AGENT, TERMINAL, control(action, { body: "x" }));
    assert.equal(acks().length, 1, `${action} produced ${acks().length} acks`);
    assert.equal(acks()[0].url, `/api/v1/terminals/controls/ctl-${action}`);
  }
});

test("every ack reports the control COMPLETED", async () => {
  // `completed` is what takes it out of the pending set. Any other status leaves it claimable.
  for (const action of ["input", "resize", "stop", "start"]) {
    reset();
    await handleVirtualTerminalControl(AGENT, TERMINAL, control(action, { body: "x" }));
    assert.equal(JSON.parse(acks()[0].body).status, "completed", action);
  }
});

test("the acked TERMINAL STATUS distinguishes stop from everything else", async () => {
  // The second field is what the dashboard renders. A resize that reported `stopped` would grey out a
  // terminal the operator is still typing into; a stop that reported `running` would leave a dead one
  // looking live.
  const observed = {};
  for (const action of ["input", "resize", "stop", "start"]) {
    reset();
    await handleVirtualTerminalControl(AGENT, TERMINAL, control(action, { body: "x" }));
    observed[action] = JSON.parse(acks()[0].body).terminalStatus;
  }
  assert.deepEqual(observed, {
    input: "running", resize: "running", start: "running", stop: "stopped",
  });
});

test("the two NO-OP actions ack without doing anything else", async () => {
  // `resize` has no PTY dimensions to set, and a stray `start` is meaningless for a terminal created
  // through `/virtual-terminal/ensure`. Both comments say the ack exists to stop the retry — so the
  // ack must be the ONLY request either makes.
  for (const action of ["resize", "start"]) {
    reset();
    await handleVirtualTerminalControl(AGENT, TERMINAL, control(action));
    assert.equal(REQUESTS.length, 1, `${action} made ${REQUESTS.length} requests`);
  }
});

test("an UNSUPPORTED action THROWS and acks nothing", async () => {
  // The one branch that deliberately does not answer. An unknown action is a bridge/service version
  // skew, and acking it as completed would tell the service the bridge handled something it did not
  // understand — a control silently dropped rather than retried after an upgrade.
  reset();
  await assert.rejects(
    () => handleVirtualTerminalControl(AGENT, TERMINAL, control("teleport")),
    /Unsupported virtual-terminal control action: teleport/,
  );
  assert.equal(acks().length, 0);
});

test("a BLANK action is unsupported too", async () => {
  reset();
  await assert.rejects(
    () => handleVirtualTerminalControl(AGENT, TERMINAL, { id: "ctl-blank" }),
    /Unsupported virtual-terminal control action/,
  );
  assert.equal(acks().length, 0);
});

test("an input control forwards its body to the terminal's input BUFFER", async () => {
  // The ack is not the work: `input` appends to the buffer the RPC session drains on the next
  // newline. An ack with no append is a keystroke the operator saw accepted and the agent never
  // received.
  //
  // No CR in the body, deliberately: a complete line would drain immediately into a pi session this
  // test has not created, and the manager swallows that failure through `onError`. Keeping the
  // keystroke incomplete is what makes the append itself observable — and it is also the real shape,
  // since these arrive one byte at a time.
  const { VIRTUAL_TERMINAL_INPUT } = await import("../virtual-terminals.mjs");
  VIRTUAL_TERMINAL_INPUT.remove(TERMINAL);
  reset();
  await handleVirtualTerminalControl(AGENT, TERMINAL, control("input", { body: "hel" }));
  const entry = VIRTUAL_TERMINAL_INPUT.snapshot()[TERMINAL];
  assert.ok(entry, "no buffer entry was created for the terminal");
  assert.equal(entry.buffer, "hel");
  assert.equal(entry.agentId, AGENT, "the buffer must remember which agent to dispatch to");
  VIRTUAL_TERMINAL_INPUT.remove(TERMINAL);
});

test("a stop control REMOVES the terminal's input buffer", async () => {
  // Otherwise a half-typed line outlives the terminal it was typed into and is delivered to whatever
  // reuses the id.
  const { VIRTUAL_TERMINAL_INPUT } = await import("../virtual-terminals.mjs");
  reset();
  await handleVirtualTerminalControl(AGENT, TERMINAL, control("input", { body: "half" }));
  assert.ok(VIRTUAL_TERMINAL_INPUT.snapshot()[TERMINAL], "precondition: a buffer exists");
  await handleVirtualTerminalControl(AGENT, TERMINAL, control("stop"));
  assert.equal(VIRTUAL_TERMINAL_INPUT.snapshot()[TERMINAL], undefined);
});
