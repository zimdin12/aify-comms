// The realtime socket, tested by driving a fake WebSocket through the states that actually break it.
//
// Everything here was unreachable while it lived in app.js, and the failures it guards are all of the
// same kind: the socket is silently not connected, so the dashboard shows a frozen console and stale
// statuses while looking perfectly healthy. Three separate mechanisms exist for that — the CONNECTING
// watchdog, the backoff, and the resume nudge — and none of them had a test.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  applyRealtimeEvent,
  connectRealtimeSocket,
  initRealtimeSocket,
  nudgeRealtimeSocketOnResume,
  wireRealtimeResumeReconnect,
} from "./realtime-socket.mjs";

const OPEN = 1;
const CONNECTING = 0;
const CLOSING = 2;
const CLOSED = 3;

/** Records every socket constructed, so a test can drive onopen/onclose/onmessage by hand. */
function installFakeWebSocket() {
  const built = [];
  class FakeWebSocket {
    static OPEN = OPEN;
    static CONNECTING = CONNECTING;
    static CLOSING = CLOSING;
    static CLOSED = CLOSED;
    constructor(url) {
      this.url = url;
      this.readyState = CONNECTING;
      this.closed = 0;
      built.push(this);
    }
    close() { this.closed += 1; this.readyState = CLOSED; }
  }
  globalThis.WebSocket = FakeWebSocket;
  return built;
}

/** Seed the module with recording dependencies and a clean socket state. */
function harness({ deps = {} } = {}) {
  const calls = { evaluateFlowGates: 0, refreshSoon: 0, resyncActiveConsole: 0, scheduleRenderAll: 0, notified: [] };
  initRealtimeSocket({
    dashboardNotifier: { handle: (event, data) => calls.notified.push([event, data]) },
    evaluateFlowGates: () => { calls.evaluateFlowGates += 1; },
    refreshSoon: () => { calls.refreshSoon += 1; },
    resyncActiveConsole: async () => { calls.resyncActiveConsole += 1; },
    scheduleRenderAll: () => { calls.scheduleRenderAll += 1; },
    ...deps,
  });
  return calls;
}

function withFakes(run) {
  const saved = {
    WebSocket: globalThis.WebSocket,
    setTimeout: globalThis.setTimeout,
    document: globalThis.document,
    window: globalThis.window,
  };
  const timers = [];
  globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  const built = installFakeWebSocket();
  setApiBase("http://fake.invalid/api/v1", "http://fake.invalid");
  try {
    return run({ built, timers });
  } finally {
    Object.assign(globalThis, saved);
    if (!saved.window) delete globalThis.window;
    if (!saved.document) delete globalThis.document;
  }
}

test("INIT REFUSES A PARTIAL BAG rather than defaulting to a no-op", () => {
  // Every dependency here is a side effect the operator sees. A missing `refreshSoon` would leave the
  // socket connected and the page never updating — the exact symptom the socket exists to prevent, and
  // indistinguishable from a network problem. A silent default would make that a supported state.
  const full = {
    dashboardNotifier: { handle() {} },
    evaluateFlowGates() {},
    refreshSoon() {},
    resyncActiveConsole: async () => {},
    scheduleRenderAll() {},
  };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initRealtimeSocket(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.throws(() => initRealtimeSocket(null), TypeError);
  assert.doesNotThrow(() => initRealtimeSocket(full));
});

test("connect builds a ws:// URL from the http api origin", () => {
  withFakes(({ built }) => {
    harness();
    connectRealtimeSocket();
    assert.equal(built.length, 1);
    assert.equal(built[0].url, "ws://fake.invalid/ws");
  });
});

test("a second connect while OPEN or CONNECTING does NOT build another socket", () => {
  // Without the guard, every resume signal would stack sockets and the old ones would keep delivering
  // events into the same handlers.
  withFakes(({ built }) => {
    harness();
    connectRealtimeSocket();
    built[0].readyState = CONNECTING;
    connectRealtimeSocket();
    built[0].readyState = OPEN;
    connectRealtimeSocket();
    assert.equal(built.length, 1, "one live socket, not three");
  });
});

test("THE HALF-OPEN WATCHDOG FORCE-CLOSES A SOCKET STUCK IN CONNECTING", () => {
  // After a laptop sleep or a radio handoff a socket sits in CONNECTING forever: neither onopen nor
  // onclose ever fires, so the CONNECTING guard above wedges reconnect PERMANENTLY. The watchdog is the
  // only thing that breaks that deadlock.
  withFakes(({ built, timers }) => {
    harness();
    connectRealtimeSocket();
    const sock = built[0];
    const watchdog = timers.find((t) => t.ms === 8000);
    assert.ok(watchdog, "a connect must arm the 8s watchdog");
    sock.readyState = CONNECTING;
    watchdog.fn();
    assert.equal(sock.closed, 1, "a still-CONNECTING socket must be force-closed so onclose can retry");
  });
});

test("the watchdog leaves a socket that DID connect alone", () => {
  withFakes(({ built, timers }) => {
    harness();
    connectRealtimeSocket();
    built[0].readyState = OPEN;
    timers.find((t) => t.ms === 8000).fn();
    assert.equal(built[0].closed, 0, "closing a healthy socket would drop a working connection");
  });
});

test("the watchdog is PER SOCKET — a successor is not left unwatched", () => {
  // A shared global timer id could be cleared by a different socket's onclose during a resume overlap.
  withFakes(({ built, timers }) => {
    harness();
    connectRealtimeSocket();
    const first = built[0];
    first.readyState = CLOSED;
    first.onclose();
    connectRealtimeSocket();
    const second = built[1];
    assert.ok(second && second !== first, "onclose must schedule a fresh connect");
    const watchdogs = timers.filter((t) => t.ms === 8000);
    assert.equal(watchdogs.length, 2, "each socket arms its own watchdog");
    second.readyState = CONNECTING;
    watchdogs[1].fn();
    assert.equal(second.closed, 1, "the successor's watchdog must still fire");
  });
});

test("BACKOFF GROWS AND IS CAPPED, so a restarting service is not hammered by every open tab", () => {
  // The single-worker service restarts on every deploy. A flat retry from every tab piles load on
  // exactly when it is weakest.
  withFakes(({ built, timers }) => {
    harness();
    const delays = [];
    for (let i = 0; i < 9; i += 1) {
      connectRealtimeSocket();
      const sock = built[built.length - 1];
      sock.readyState = CLOSED;
      timers.length = 0;
      sock.onclose();
      delays.push(timers.filter((t) => t.ms !== 8000).map((t) => t.ms).pop());
    }
    assert.ok(delays[0] < delays[1], `backoff must grow: ${delays.slice(0, 3)}`);
    assert.ok(delays.every((d) => d <= 30000), `capped at 30s, saw ${Math.max(...delays)}`);
    assert.equal(delays.at(-1), 30000, "the cap must actually be reached, not approached");
  });
});

test("A HEALTHY OPEN RESETS THE BACKOFF — otherwise one bad night makes every later retry 30s", () => {
  withFakes(({ built, timers }) => {
    harness();
    for (let i = 0; i < 6; i += 1) {
      connectRealtimeSocket();
      const s = built[built.length - 1];
      s.readyState = CLOSED;
      s.onclose();
    }
    connectRealtimeSocket();
    const good = built[built.length - 1];
    good.readyState = OPEN;
    good.onopen();
    good.readyState = CLOSED;
    timers.length = 0;
    good.onclose();
    const delay = timers.filter((t) => t.ms !== 8000).map((t) => t.ms).pop();
    assert.equal(delay, 3000, "after a healthy open the next retry is fast again, not the 30s cap");
  });
});

test("a reconnect (not a first connect) resyncs the mounted console and pulls fresh data", () => {
  // A dropped-then-reconnected socket missed every terminal_output frame in the gap. An IDLE agent
  // emits no new frame to trip the sequence-gap resync, so the console shows stale canvas and typed
  // keys echo into a frame the tab never repaints.
  withFakes(({ built }) => {
    const calls = harness();
    state.realtimeConnected = false;
    state.activeXterm = { term: { write() {} } };
    connectRealtimeSocket();
    const first = built[0];
    first.readyState = CLOSED;
    first.onclose();                 // one failed attempt, so this is a RECONNECT
    connectRealtimeSocket();
    state.realtimeConnected = false;
    built[built.length - 1].onopen();
    assert.equal(calls.resyncActiveConsole, 1, "the mounted console must repaint from the authoritative buffer");
    assert.equal(calls.refreshSoon, 1);
    assert.equal(state.realtimeConnected, true);
  });
});

test("a FIRST connect does not resync — there is nothing missed to catch up on", () => {
  withFakes(({ built }) => {
    const calls = harness();
    state.realtimeConnected = false;
    state.activeXterm = { term: { write() {} } };
    connectRealtimeSocket();
    built[0].onopen();
    assert.equal(calls.resyncActiveConsole, 0);
    assert.equal(calls.refreshSoon, 0, "a first open must not trigger a redundant refetch");
  });
});

test("the resume nudge reconnects a CLOSED socket immediately, short-circuiting the backoff", () => {
  // A woken tab often has a CLOSED socket with a 30s backoff timer still pending. The operator stares
  // at a stale console for the remainder of it.
  withFakes(({ built }) => {
    harness();
    connectRealtimeSocket();
    built[0].readyState = CLOSED;
    nudgeRealtimeSocketOnResume();
    assert.equal(built.length, 2, "a CLOSED socket on resume must reconnect now");
  });
});

test("the resume nudge leaves OPEN and CONNECTING sockets alone", () => {
  // Aborting a healthy slow connect just churns; a genuinely stuck one is the watchdog's job.
  for (const readyState of [OPEN, CONNECTING]) {
    withFakes(({ built }) => {
      harness();
      connectRealtimeSocket();
      built[0].readyState = readyState;
      nudgeRealtimeSocketOnResume();
      assert.equal(built.length, 1, `readyState ${readyState} must not be reconnected`);
    });
  }
});

test("resume wiring subscribes to all four signals and ignores a hidden visibilitychange", () => {
  withFakes(({ built }) => {
    harness();
    const handlers = [];
    globalThis.document = { visibilityState: "hidden", addEventListener: (ev, fn) => handlers.push([ev, fn]) };
    globalThis.window = { addEventListener: (ev, fn) => handlers.push([ev, fn]) };
    wireRealtimeResumeReconnect();
    assert.deepEqual(handlers.map(([ev]) => ev).sort(), ["focus", "online", "pageshow", "visibilitychange"]);
    connectRealtimeSocket();
    built[0].readyState = CLOSED;
    const onVisibility = handlers.find(([ev]) => ev === "visibilitychange")[1];
    onVisibility({ type: "visibilitychange" });
    assert.equal(built.length, 1, "a tab going HIDDEN must not trigger a reconnect");
    globalThis.document.visibilityState = "visible";
    onVisibility({ type: "visibilitychange" });
    assert.equal(built.length, 2, "…and becoming visible must");
  });
});

// --- applyRealtimeEvent -------------------------------------------------------------------------

function seedState() {
  Object.assign(state, {
    agents: [{ id: "a1", status: "online", statusNote: null }],
    terminalOwners: new Map(),
    activeXterm: null,
  });
}

test("the notifier is called BEFORE routing, and its throw cannot break handling", () => {
  const calls = harness({ deps: { dashboardNotifier: { handle() { throw new Error("notifier blew up"); } } } });
  seedState();
  assert.doesNotThrow(() => applyRealtimeEvent("terminal_started", { terminalId: "t1", agentId: "a1" }));
  assert.equal(state.terminalOwners.get("t1"), "a1", "routing must still have happened");
  assert.equal(calls.refreshSoon, 1);
});

test("agent_status patches in place instead of triggering the ten-endpoint refetch", () => {
  // The dashboard's biggest poll-load reduction. Falling back to refreshSoon for a KNOWN agent would
  // silently undo it and nothing would look wrong.
  const calls = harness();
  seedState();
  applyRealtimeEvent("agent_status", { agentId: "a1", status: "working", statusNote: "on it" });
  assert.equal(state.agents[0].status, "working");
  assert.equal(state.agents[0].statusRaw, "working");
  assert.equal(state.agents[0].statusNote, "on it");
  assert.equal(calls.scheduleRenderAll, 1);
  assert.equal(calls.refreshSoon, 0, "a known agent must NOT cause a full refetch");
});

test("agent_status for an UNKNOWN agent falls back to a refetch", () => {
  const calls = harness();
  seedState();
  applyRealtimeEvent("agent_status", { agentId: "not-loaded-yet", status: "working" });
  assert.equal(calls.refreshSoon, 1, "a registration we have not loaded must pull the roster");
  assert.equal(calls.scheduleRenderAll, 0);
});

test("terminal_output for ANOTHER agent's terminal is dropped", () => {
  // Owner mismatch means a stale frame from a terminal that has been re-adopted. Painting it writes
  // one agent's output into another agent's console.
  const calls = harness();
  seedState();
  state.terminalOwners.set("t1", "a1");
  let written = "";
  state.activeXterm = { terminalId: "t1", lastSeq: -1, container: { offsetParent: {} }, term: { write: (s) => { written += s; } } };
  applyRealtimeEvent("terminal_output", { terminalId: "t1", agentId: "OTHER", output: "hello" });
  assert.equal(written, "", "a frame whose agent is not the owner must not be painted");
  assert.equal(calls.refreshSoon, 0);
});

test("terminal_output paints, tracks seq, and never triggers a refetch", () => {
  // terminal_output streams every 1-4s. A refetch per frame made the status chip flap and burned the
  // whole ten-endpoint poll every second.
  const calls = harness();
  seedState();
  let written = "";
  state.activeXterm = { terminalId: "t1", lastSeq: -1, container: { offsetParent: {} }, term: { write: (s) => { written += s; } } };
  applyRealtimeEvent("terminal_output", { terminalId: "t1", agentId: "a1", output: "hi", seq: 4 });
  assert.equal(written, "hi");
  assert.equal(state.activeXterm.lastSeq, 4);
  assert.equal(calls.refreshSoon, 0, "live bytes must never cost a data refetch");
});

test("a REPLAYED frame is dropped and a GAP resyncs instead of painting out of order", () => {
  const calls = harness();
  seedState();
  let written = "";
  state.activeXterm = { terminalId: "t1", lastSeq: 4, container: { offsetParent: {} }, term: { write: (s) => { written += s; } } };
  applyRealtimeEvent("terminal_output", { terminalId: "t1", agentId: "a1", output: "old", seq: 3 });
  assert.equal(written, "", "an already-painted seq must be dropped");
  applyRealtimeEvent("terminal_output", { terminalId: "t1", agentId: "a1", output: "future", seq: 9 });
  assert.equal(written, "", "a gap must resync, not paint out-of-order bytes");
  assert.equal(calls.resyncActiveConsole, 1);
});

test("a hidden console pane is not painted into", () => {
  // The xterm stays mounted but offscreen when the operator switches pages; writing to it burns CPU
  // and grows invisible scrollback.
  harness();
  seedState();
  let written = "";
  state.activeXterm = { terminalId: "t1", lastSeq: -1, container: { offsetParent: null }, term: { write: (s) => { written += s; } } };
  applyRealtimeEvent("terminal_output", { terminalId: "t1", agentId: "a1", output: "hi", seq: 1 });
  assert.equal(written, "");
});

test("the coarse event list still refetches", () => {
  const listed = ["message_sent", "dispatch_queued", "dispatch_claimed", "dispatch_updated",
    "dispatch_control_requested", "dispatch_control_updated", "contract_reminders_sent",
    "settings_updated", "session_control_requested", "session_deleted", "agent_registered"];
  for (const event of listed) {
    const calls = harness();
    seedState();
    applyRealtimeEvent(event, {});
    assert.equal(calls.refreshSoon, 1, `${event} must pull fresh data`);
  }
});

test("an event nobody classified refetches rather than vanishing", () => {
  // REVERSED DELIBERATELY, and the old assertion here was "an unknown event must not cost a
  // refetch". That was a real concern -- refresh() fires a ten-request bundle -- but it was paid
  // for by silence: measured 2026-08-25, the service broadcasts 49 event names and 35 of them fell
  // off the end of applyRealtimeEvent with no branch and no log, including channel_message,
  // terminal_stopped, message_deleted, conversation_cleared, file_shared and all three
  // spawn_request_*. A chat message could sit a full poll behind because nobody had decided it
  // should not.
  //
  // What makes the reversal affordable is already in app.js: refreshSoon debounces 250ms, and a
  // second guard runs at most one bundle at a time and queues exactly one more. So a burst of
  // events costs one refetch, not one per event.
  //
  // The rate evidence is one sample and is stated as such: 4 events in 60s over a live websocket
  // with the fleet idle. There is no busy-fleet measurement, which is why environment_heartbeat --
  // the only event observed at any rate -- stays ignored rather than being swept in with the rest.
  const calls = harness();
  seedState();
  applyRealtimeEvent("something_nobody_handles", {});
  assert.equal(calls.refreshSoon, 1, "an unclassified event was silently dropped again");
});

test("the measured-noisy event is still not refetching", () => {
  const calls = harness();
  seedState();
  applyRealtimeEvent("environment_heartbeat", {});
  assert.equal(calls.refreshSoon, 0, "the highest-frequency event now costs a refetch each time");
});

test("the page-resume rationale travelled WITH the code it explains", async () => {
  // The extraction plan restores these six lines into the reconstruction by declaration rather than by
  // reading them out of the module, which is the one place that mechanism cannot verify itself. This
  // asserts they are actually here, so they cannot be quietly dropped or reworded.
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./realtime-socket.mjs", import.meta.url), "utf8");
  for (const line of [
    "// Reconnect on page-resume (Hermes parity). When a backgrounded/slept tab wakes, its socket is",
    "// often CLOSED with a long backoff timer still pending (up to 30s away) — the operator stares at a",
    "// stale console. On any resume signal, if we're not OPEN, reconnect NOW (short-circuiting the",
    "// backoff). A stuck-CONNECTING socket is force-closed first so the CONNECTING guard can't block the",
    "// fresh connect. Throttled so a burst of resume events (focus+visibilitychange+online together)",
    "// fires one reconnect.",
  ]) {
    assert.ok(src.includes(line), `the extraction plan restores this line; it must exist here: ${line.slice(0, 48)}…`);
  }
});
