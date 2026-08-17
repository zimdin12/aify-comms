// The gateway RPC client's two operations: `request` and `close`.
//
// Sixth cluster off the V8-coverage census — both had a zero call count, which means no test had ever
// driven an actual request/response over this client. Eleven other files test the gateway's frames, its
// ports, its pool and its liveness; none of them sends one.
//
// EVERY HERMES RPC GOES THROUGH THIS. `session.active_list`, `session.list`, `prompt.submit`,
// `session.steer` — the delivery loop, the resume resolver and the turn detector all reach the gateway
// this way, so the correlation between a request and its reply IS hermes delivery. Getting it wrong
// resolves one call with another call's answer, which surfaces as an agent driven into the wrong session.
//
// THREE FAILURE MODES HERE HANG RATHER THAN ERROR, and each has its own guard:
//
//   * a socket that opens at TCP level and never completes the WS upgrade — the connect promise would
//     never settle, wedging the whole delivery loop (the 2026-06-02 hotfix; the agent looks dead, never
//     claims, never writes its ready marker);
//   * a request whose reply never arrives — the per-request timer names the METHOD so the log says which
//     call stalled;
//   * a gateway that drops the connection with requests in flight — `close` on the socket rejects every
//     pending promise, because a delivery loop awaiting a reply from a closed socket waits forever.
//
// A REAL `ws` SERVER, since `ws` is already a dependency and the client's whole subject is wire
// behaviour. `timeoutMs` is injected short so the timeout paths cost milliseconds.

import assert from "node:assert/strict";
import test from "node:test";
import { WebSocketServer } from "ws";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { spawn } from "node:child_process";

import { openGatewayWsClient } from "../hermes-gateway.mjs";

// TWO MUTATIONS OF THIS FUNCTION SURVIVE THIS FILE, and neither is a gap in it:
//
//   * dropping `msg.id !== undefined` from the reply guard changes nothing — `pending.has(undefined)` is
//     false for every map this client can build, since an id is always `frame.id ?? nextId++`. The guard is
//     a belt over a brace, not a second condition.
//   * not deleting an answered request from the pending map leaks the entry but cannot be observed through
//     the returned object: a duplicate reply resolves a settled promise and the close handler rejects one,
//     both no-ops. Proving it would mean reaching into a closure this module deliberately does not export.
//
// Recorded here rather than papered over with a test that asserts something adjacent.

// One server per test, so a handler cannot leak between them.
//
// TEARDOWN TERMINATES THE SOCKETS FIRST, and that is not tidiness. A server `close(cb)` waits on its
// live connections, and with one still attached the callback never fires — the await never settles, the
// loop drains, and node's runner then reports EVERY test in the file as `cancelledByParent` with zero
// passes and zero failures. That reads like a broken subject rather than a broken fixture, which cost a
// diagnosis here before the terminate went in.
async function withGateway(onMessage, run) {
  const wss = new WebSocketServer({ host: "127.0.0.2", port: 0 });
  await new Promise((resolve) => wss.once("listening", resolve));
  const url = `ws://127.0.0.2:${wss.address().port}`;
  const sockets = [];
  wss.on("connection", (socket) => {
    sockets.push(socket);
    socket.on("error", () => { /* a terminated peer is how several of these tests END */ });
    socket.on("message", (raw) => onMessage(socket, JSON.parse(String(raw))));
  });
  try {
    return await run(url, { sockets, wss });
  } finally {
    for (const socket of sockets) { try { socket.terminate(); } catch { /* ignore */ } }
    await new Promise((resolve) => wss.close(() => resolve()));
  }
}

const reply = (socket, id, body) => socket.send(JSON.stringify({ id, ...body }));

// ── the connect timeout ─────────────────────────────────────────────────────────────────────────

test("a socket that never completes the WS UPGRADE is rejected, not awaited forever", async () => {
  // The 2026-06-02 hotfix. A bare TCP listener accepts the connection and never speaks HTTP, so the
  // open/error promise never settles — and the delivery loop that awaited it never claims again.
  const accepted = [];
  const server = net.createServer((conn) => {
    accepted.push(conn);
    // RESUME, or this test lies. A paused node socket never reads, so it never sees the peer's FIN and
    // never emits 'end'/'close' — the teardown assertion below failed against a client that HAD torn the
    // connection down, and the false accusation landed on the product before the fixture.
    conn.resume();
    conn.on("error", () => { /* the client aborts this handshake on purpose */ });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
  const url = `ws://127.0.0.2:${server.address().port}`;
  try {
    await assert.rejects(
      () => openGatewayWsClient(url, { timeoutMs: 250 }),
      /connect timed out after 250ms/,
    );
    assert.equal(accepted.length, 1, "the client never reached the listener");
    // AND THE SOCKET IS GONE. Rejecting while leaving the half-open connection attached would trade the
    // hang for a leak — one dangling socket per delivery attempt against a sick gateway, which is exactly
    // when the retry loop attempts most often.
    await new Promise((resolve, reject) => {
      const bail = setTimeout(() => reject(new Error("the aborted socket was never closed")), 2000);
      accepted[0].on("close", () => { clearTimeout(bail); resolve(); });
    });
  } finally {
    for (const conn of accepted) conn.destroy();
    server.close();
  }
});

test("a refused connection rejects with the underlying error", async () => {
  // Distinct from the timeout: there is nothing listening, which `isGatewayConnectRefused` classifies
  // for the caller. It must arrive as an error rather than as a hang.
  await assert.rejects(() => openGatewayWsClient("ws://127.0.0.2:1", { timeoutMs: 2000 }));
});

// ── request / reply correlation ─────────────────────────────────────────────────────────────────

test("a request resolves with its own reply's RESULT", async () => {
  await withGateway((socket, msg) => reply(socket, msg.id, { result: { sessions: ["a"] } }), async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    try {
      assert.deepEqual(await client.request({ method: "session.list" }), { sessions: ["a"] });
    } finally {
      client.close();
    }
  });
});

test("the client ASSIGNS an id when the frame has none", async () => {
  // Correlation is by id, so an un-numbered frame would never be matched to its reply.
  const seen = [];
  await withGateway((socket, msg) => { seen.push(msg); reply(socket, msg.id, { result: 1 }); },
    async (url) => {
      const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
      try {
        await client.request({ method: "a" });
        await client.request({ method: "b" });
      } finally {
        client.close();
      }
    });
  assert.equal(seen.length, 2);
  assert.ok(Number.isFinite(seen[0].id), `no id assigned: ${JSON.stringify(seen[0])}`);
  assert.notEqual(seen[0].id, seen[1].id, "two requests shared an id");
});

test("a frame's OWN id is preserved", async () => {
  // The protocol builders number their own frames (`buildSessionListFrame({ id })`), and the resume
  // resolver counts on the reply matching the number it chose.
  const seen = [];
  await withGateway((socket, msg) => { seen.push(msg.id); reply(socket, msg.id, { result: 1 }); },
    async (url) => {
      const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
      try {
        await client.request({ id: 42, method: "session.list" });
      } finally {
        client.close();
      }
    });
  assert.deepEqual(seen, [42]);
});

test("replies arriving OUT OF ORDER each resolve their own request", async () => {
  // Two RPCs in flight is the normal case — the resume resolver sends `active_list` then `session.list`.
  // Resolving the first promise with the second answer is how an agent gets driven into another session.
  await withGateway((socket, msg) => {
    // Answer the SECOND request first.
    if (msg.method === "second") reply(socket, msg.id, { result: "second-answer" });
    else setTimeout(() => reply(socket, msg.id, { result: "first-answer" }), 60);
  }, async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    try {
      const first = client.request({ method: "first" });
      const second = client.request({ method: "second" });
      assert.equal(await second, "second-answer");
      assert.equal(await first, "first-answer");
    } finally {
      client.close();
    }
  });
});

test("a reply for an UNKNOWN id resolves nothing", async () => {
  // Inbound gateway events carry ids this client never issued. Matching loosely would resolve a pending
  // request with a delta frame.
  await withGateway((socket, msg) => {
    reply(socket, 999_999, { result: "not-yours" });
    setTimeout(() => reply(socket, msg.id, { result: "yours" }), 40);
  }, async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    try {
      assert.equal(await client.request({ method: "a" }), "yours");
    } finally {
      client.close();
    }
  });
});

test("an INBOUND EVENT with no id is ignored", async () => {
  // The TUI's transport owns those frames. This client only cares about RPC replies, and a crash here
  // would take the delivery loop down on an ordinary streaming delta.
  await withGateway((socket, msg) => {
    socket.send(JSON.stringify({ method: "agent.message.delta", params: { text: "hi" } }));
    reply(socket, msg.id, { result: "ok" });
  }, async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    try {
      assert.equal(await client.request({ method: "a" }), "ok");
    } finally {
      client.close();
    }
  });
});

test("a NON-JSON frame is ignored rather than throwing in the message handler", async () => {
  await withGateway((socket, msg) => {
    socket.send("not json at all");
    reply(socket, msg.id, { result: "ok" });
  }, async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    try {
      assert.equal(await client.request({ method: "a" }), "ok");
    } finally {
      client.close();
    }
  });
});

test("an ERROR reply REJECTS with the gateway's own error object", async () => {
  // The caller needs the code: 4007 "session not found" is what tells the resume path a key is dead,
  // and flattening it to a string loses that.
  await withGateway((socket, msg) => reply(socket, msg.id, { error: { code: 4007, message: "session not found" } }),
    async (url) => {
      const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
      try {
        await assert.rejects(() => client.request({ method: "session.resume" }), (err) => {
          assert.equal(err.code, 4007);
          assert.equal(err.message, "session not found");
          return true;
        });
      } finally {
        client.close();
      }
    });
});

test("a reply with NEITHER result nor error resolves with the whole message", async () => {
  // `msg.result ?? msg`. Gateway builds differ in shape, and a caller that reads a field off the reply
  // gets the envelope rather than undefined.
  await withGateway((socket, msg) => socket.send(JSON.stringify({ id: msg.id, sessions: [] })),
    async (url) => {
      const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
      try {
        const answer = await client.request({ method: "a" });
        assert.deepEqual(answer.sessions, []);
      } finally {
        client.close();
      }
    });
});

test("a result of NULL falls back to the ENVELOPE, not to null", async () => {
  // MEASURED, not assumed — I wrote this expecting the opposite. `msg.result ?? msg` treats a null result
  // as "no result", so a void ack resolves with the reply envelope rather than with null. Callers here all
  // read a field off the answer or ignore it, so nothing is currently wrong; it is pinned because the line
  // LOOKS like it distinguishes null from absent (`??` rather than `||`) and does not. A future caller that
  // tests `answer === null` for a void ack would be reading a truthy object.
  await withGateway((socket, msg) => reply(socket, msg.id, { result: null }), async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    try {
      const answer = await client.request({ method: "a" });
      assert.notEqual(answer, null, "a null result resolved as null");
      assert.ok(answer.id !== undefined, "the fallback was not the envelope");
    } finally {
      client.close();
    }
  });
});

// ── timeouts and teardown ───────────────────────────────────────────────────────────────────────

test("a reply that never comes REJECTS, and names the method", async () => {
  // The log has to say which call stalled. A gateway that accepts a frame and answers nothing is the
  // observed failure when a session is mid-attach.
  await withGateway(() => { /* never reply */ }, async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 200 });
    try {
      await assert.rejects(() => client.request({ method: "session.active_list" }),
        /hermes RPC session\.active_list timed out/);
    } finally {
      client.close();
    }
  });
});

test("a LATE reply after a timeout resolves nothing and throws nothing", async () => {
  // The pending entry is deleted when the timer fires, so the late frame finds no waiter. Leaving it
  // would resolve a promise that already rejected.
  await withGateway((socket, msg) => setTimeout(() => reply(socket, msg.id, { result: "late" }), 300),
    async (url) => {
      const client = await openGatewayWsClient(url, { timeoutMs: 150 });
      try {
        await assert.rejects(() => client.request({ method: "slow" }), /timed out/);
        await new Promise((resolve) => setTimeout(resolve, 300));
      } finally {
        client.close();
      }
    });
});

test("a CLOSED socket rejects every request still in flight", async () => {
  // The one that would otherwise hang forever: a delivery loop awaiting a reply from a gateway that has
  // gone away. Every pending promise is rejected on close, so the loop can retry.
  await withGateway((socket) => { socket.close(); }, async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 5000 });
    await assert.rejects(() => client.request({ method: "a" }), /WS closed/);
  });
});

test("a request AFTER close is refused immediately rather than queued", async () => {
  // Queueing it would produce a promise that can only ever time out — five seconds of a delivery loop
  // spent on a socket it already closed.
  await withGateway((socket, msg) => reply(socket, msg.id, { result: "ok" }), async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    await client.request({ method: "a" });
    client.close();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await assert.rejects(() => client.request({ method: "b" }), /not open/);
  });
});

test("an ANSWERED request does not pin the event loop for the rest of its timeout", async () => {
  // The reply path clears the per-request timer, and nothing inside the module can see that it did: the
  // late timer only rejects an already-settled promise, so every in-process assertion passes either way.
  // What it costs is a live 60s handle per answered RPC — a bridge that closes its gateway client and waits
  // to exit sits there instead. So this measures the ONE observable consequence: a child that finishes its
  // work exits promptly rather than at the timeout.
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "aify-gw-timer-"));
  const gatewayUrl = new URL("../hermes-gateway.mjs", import.meta.url).href;
  // The child lives OUTSIDE the package, so a bare `ws` specifier does not resolve from there — both
  // imports have to be absolute URLs. (`hermes-gateway.mjs`'s own dynamic `import("ws")` still resolves,
  // because that one is relative to the module, not to the child.)
  const wsUrl = import.meta.resolve("ws");
  const script = path.join(dir, "answered-then-exit.mjs");
  await fs.writeFile(script, [
    `import { WebSocketServer } from ${JSON.stringify(wsUrl)};`,
    `import { openGatewayWsClient } from ${JSON.stringify(gatewayUrl)};`,
    'const wss = new WebSocketServer({ host: "127.0.0.2", port: 0 });',
    'await new Promise((r) => wss.once("listening", r));',
    'wss.on("connection", (s) => s.on("message", (raw) => {',
    '  s.send(JSON.stringify({ id: JSON.parse(String(raw)).id, result: "ok" }));',
    "}));",
    // A timeout far longer than the parent's patience: if the answered request's timer survives, this
    // child cannot exit until it fires.
    "const client = await openGatewayWsClient(`ws://127.0.0.2:${wss.address().port}`, { timeoutMs: 20000 });",
    'await client.request({ method: "session.list" });',
    "client.close();",
    "for (const c of wss.clients) c.terminate();",
    "await new Promise((r) => wss.close(() => r()));",
  ].join("\n"), "utf8");

  const started = process.hrtime.bigint();
  const code = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], { stdio: "ignore" });
    const bail = setTimeout(() => { child.kill(); reject(new Error("child never exited")); }, 15000);
    child.on("error", reject);
    child.on("exit", (c) => { clearTimeout(bail); resolve(c); });
  });
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
  await fs.rm(dir, { recursive: true, force: true });

  assert.equal(code, 0, "the child failed rather than exiting cleanly");
  assert.ok(elapsedMs < 8000,
    `the child took ${Math.round(elapsedMs)}ms — an answered request left its timeout handle alive`);
});

test("close is idempotent and never throws", async () => {
  // Callers close it in a `finally` and some close it twice on an error path.
  await withGateway((socket, msg) => reply(socket, msg.id, { result: "ok" }), async (url) => {
    const client = await openGatewayWsClient(url, { timeoutMs: 2000 });
    client.close();
    client.close();
  });
});
