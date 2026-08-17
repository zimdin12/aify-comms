// The two things about the realtime socket that only a FRESH module instance can test: what arrives on the
// wire, and what happens before anything has been wired up.
//
// From the dashboard V8-coverage census: `sock.onmessage` plus the module-scope no-op defaults
// (`dashboardNotifier.handle`, `evaluateFlowGates`, `refreshSoon`, `resyncActiveConsole`,
// `scheduleRenderAll`). `realtime-socket.test.mjs` is thorough about connect/backoff/resume and about routing —
// it calls `applyRealtimeEvent` directly — but it never fires a socket MESSAGE, and it calls
// `initRealtimeSocket` in every test, so the pre-init defaults never run.
//
// WHY A SEPARATE FILE. The defaults only exist BEFORE `initRealtimeSocket` replaces them, and that state cannot
// be recreated once any test in a file has inited: the module-scope bindings are permanently overwritten.
// `node --test` gives each file its own process, so this file's first test gets the module as the browser gets
// it at boot.
//
// WHAT THE WIRE BOUNDARY IS FOR. `onmessage` is the only place a server frame becomes a dashboard action, and
// its `try/catch` swallows everything. That is correct — a malformed frame must not kill the socket, because a
// dead socket means a frozen console and stale statuses while the page looks healthy — but it also means a
// mistake there is completely silent. Nothing else in the suite crosses that boundary.
//
// TWO MUTATIONS SURVIVE, both the NOTIFIER default: replacing `{ handle() {} }` with `null` or with `{}`.
// `applyRealtimeEvent` calls it inside its own `try { ... } catch {}` — deliberately, so a notification can
// never break the dashboard's handling of the event — so the missing default is absorbed there. One defence
// written twice. The other three defaults are NOT absorbed and each is reached on purpose above:
// `refreshSoon` on the unknown-agent branch, `scheduleRenderAll` with an agent seeded into state, and
// `evaluateFlowGates` through a socket that opens before init.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  applyRealtimeEvent, connectRealtimeSocket, initRealtimeSocket,
} from "./realtime-socket.mjs";

const CONNECTING = 0;
const OPEN = 1;
const CLOSED = 3;

function installFakeWebSocket() {
  const built = [];
  class FakeWebSocket {
    static OPEN = OPEN;
    static CONNECTING = CONNECTING;
    static CLOSED = CLOSED;
    static CLOSING = 2;
    constructor(url) { this.url = url; this.readyState = CONNECTING; this.closed = 0; built.push(this); }
    close() { this.closed += 1; this.readyState = CLOSED; }
  }
  globalThis.WebSocket = FakeWebSocket;
  return built;
}

// ── before init: the no-op defaults ─────────────────────────────────────────
//
// This must be the FIRST test in the file. Once init runs, the defaults are gone for the whole process.

test("an event that arrives BEFORE init does not throw", () => {
  // `applyRealtimeEvent` is exported, so anything can call it, and the module holds no-op defaults for exactly
  // this window: `dashboardNotifier = { handle() {} }` and four `() => {}`s. Without them a frame arriving
  // before boot finished wiring would throw inside the socket's own handler — and that handler's catch would
  // swallow it, leaving a dashboard that silently never routes anything.
  //
  // EACH DEFAULT IS REACHED DELIBERATELY, not incidentally. `refreshSoon` is the unknown-agent branch;
  // `scheduleRenderAll` needs the agent to already be in state, which is why one is seeded here — without it the
  // event takes the refreshSoon path and the scheduleRenderAll default is never called at all.
  assert.doesNotThrow(() => applyRealtimeEvent("agent_status", { agentId: "nobody-here", status: "working" }),
    "the refreshSoon default was missing");

  state.agents = [{ id: "seeded-agent", status: "online" }];
  assert.doesNotThrow(() => applyRealtimeEvent("agent_status", { agentId: "seeded-agent", status: "working" }),
    "the scheduleRenderAll default was missing");
  assert.equal(state.agents[0].status, "working", "the patch-in-place path did not run");

  assert.doesNotThrow(() => applyRealtimeEvent("message_sent", { to: "dashboard", from: "coder" }));
  // And the shape that reaches the notifier default rather than any router.
  assert.doesNotThrow(() => applyRealtimeEvent("channel_message", { channel: "ops" }));
});

test("a socket that OPENS before init does not throw either", () => {
  // `onopen` calls `evaluateFlowGates()` directly — the one default that no `applyRealtimeEvent` path reaches.
  // A connect completing before boot finished wiring is a real order: `connectRealtimeSocket` is exported too.
  const built = installFakeWebSocket();
  setApiBase("http://127.0.0.2:1/api/v1");
  connectRealtimeSocket();
  assert.equal(built.length, 1, "no socket was constructed");
  assert.doesNotThrow(() => built[0].onopen(), "the evaluateFlowGates default was missing");
  assert.equal(state.realtimeConnected, true);
});

test("an UNKNOWN event before init is equally harmless", () => {
  assert.doesNotThrow(() => applyRealtimeEvent("something_new_the_server_added", {}));
  assert.doesNotThrow(() => applyRealtimeEvent(undefined, undefined));
});

// ── the wire boundary ───────────────────────────────────────────────────────

function wire() {
  const calls = { routed: [], evaluateFlowGates: 0, refreshSoon: 0, scheduleRenderAll: 0 };
  initRealtimeSocket({
    // The notifier is called FIRST inside applyRealtimeEvent, so it doubles as a record of what was routed.
    dashboardNotifier: { handle: (event, data) => calls.routed.push([event, data]) },
    evaluateFlowGates: () => { calls.evaluateFlowGates += 1; },
    refreshSoon: () => { calls.refreshSoon += 1; },
    resyncActiveConsole: async () => {},
    scheduleRenderAll: () => { calls.scheduleRenderAll += 1; },
  });
  const built = installFakeWebSocket();
  setApiBase("http://127.0.0.2:1/api/v1");
  connectRealtimeSocket();
  assert.equal(built.length, 1, "no socket was constructed");
  return { calls, sock: built[built.length - 1] };
}

test("a well-formed frame is parsed and routed", async () => {
  const { calls, sock } = wire();
  sock.onmessage({ data: JSON.stringify({ event: "agent_status", data: { agentId: "coder", status: "working" } }) });
  assert.deepEqual(calls.routed, [["agent_status", { agentId: "coder", status: "working" }]]);
});

test("MALFORMED JSON is swallowed — the socket survives it", async () => {
  // A truncated or non-JSON frame must not throw out of the handler. There is no caller above it: an escaping
  // error becomes an unhandled rejection and the socket is left in an unknown state, which is the silent
  // frozen-dashboard failure this module's other mechanisms exist to prevent.
  const { calls, sock } = wire();
  // UNPARSEABLE only. An empty or absent `event.data` is a different path — `data || '{}'` turns it into a
  // valid empty frame — and lumping the two together is how the first version of this test failed: it expected
  // nothing routed and `""` had legitimately routed an empty one.
  for (const data of ['{"event":', "not json at all", "<html>502</html>", "[unterminated"]) {
    assert.doesNotThrow(() => sock.onmessage({ data }), `threw on ${JSON.stringify(data)}`);
  }
  assert.deepEqual(calls.routed, [], "an unparseable frame was routed anyway");
  // The socket must SURVIVE it. Closing on a bad frame would hand every garbage byte a reconnect, with the
  // backoff climbing while the connection itself was fine.
  assert.equal(sock.closed, 0, "a malformed frame closed the socket");
  assert.notEqual(sock.readyState, CLOSED);

  // …and a good frame after the bad ones still works: the handler is not left in a broken state.
  sock.onmessage({ data: JSON.stringify({ event: "agent_status", data: { agentId: "a" } }) });
  assert.equal(calls.routed.length, 1, "the handler stopped working after a malformed frame");
});

test("an EMPTY frame is a valid empty frame, not a parse error", async () => {
  // `JSON.parse(event.data || '{}')`. A socket can deliver an empty payload — a keepalive, a truncated write —
  // and the `|| '{}'` makes that a routed no-op rather than a swallowed exception. Pinned because the two look
  // the same from outside and only one of them leaves the handler's catch untouched.
  const { calls, sock } = wire();
  for (const data of ["", null, undefined]) {
    assert.doesNotThrow(() => sock.onmessage({ data }));
  }
  assert.deepEqual(calls.routed, [[undefined, {}], [undefined, {}], [undefined, {}]],
    "an empty frame did not become an empty routed event");
});

test("a frame with NO data becomes an empty object, not undefined", async () => {
  // `payload.data || {}`. Routers read fields off it; undefined would throw inside the router and be swallowed
  // by the same catch, so the event would vanish with no trace.
  const { calls, sock } = wire();
  sock.onmessage({ data: JSON.stringify({ event: "agent_status" }) });
  assert.deepEqual(calls.routed, [["agent_status", {}]]);
});

test("a frame with no EVENT still routes, with whatever it carried", async () => {
  // Pinned as current behaviour: the handler does not filter on the event name — `applyRealtimeEvent` does.
  // Dropping it here instead would hide a server-side rename behind silence rather than surfacing it.
  const { calls, sock } = wire();
  sock.onmessage({ data: JSON.stringify({ data: { agentId: "coder" } }) });
  assert.deepEqual(calls.routed, [[undefined, { agentId: "coder" }]]);
});

test("a frame whose data is not an object is passed through as it arrived", async () => {
  // `payload.data || {}` only replaces falsy values, so an array or a string reaches the routers. Recorded
  // rather than asserted as desirable: nothing coerces it, and a router that indexes it would read undefined.
  const { calls, sock } = wire();
  sock.onmessage({ data: JSON.stringify({ event: "agent_status", data: "a string" }) });
  sock.onmessage({ data: JSON.stringify({ event: "agent_status", data: 0 }) });
  assert.deepEqual(calls.routed, [["agent_status", "a string"], ["agent_status", {}]],
    "0 is falsy so it becomes {}, a non-empty string is not");
});

test("the socket keeps its own state across messages", async () => {
  // The handler is installed once per socket, so a second frame must not need a reconnect.
  const { calls, sock } = wire();
  for (let i = 0; i < 5; i += 1) {
    sock.onmessage({ data: JSON.stringify({ event: "agent_status", data: { agentId: `a${i}` } }) });
  }
  assert.equal(calls.routed.length, 5);
  assert.equal(sock.closed, 0, "the socket was closed while handling ordinary frames");
});

test("state.realtimeConnected is untouched by message handling", async () => {
  // Connectivity is owned by onopen/onclose. If a message could flip it, one malformed frame would make the
  // dashboard report itself offline while the socket was fine.
  const { sock } = wire();
  state.realtimeConnected = true;
  sock.onmessage({ data: "garbage" });
  assert.equal(state.realtimeConnected, true, "a bad frame changed the connection state");
});
