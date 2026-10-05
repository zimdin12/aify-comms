import assert from "node:assert/strict";
import { once } from "node:events";
import net from "node:net";
import { test } from "node:test";
import { isPortFree, somethingAnswers } from "../hermes-endpoint.js";

// Loopback only. A wildcard listener would prompt for a firewall rule on Windows.
async function listenLoopback(t) {
  const server = net.createServer((socket) => socket.destroy());
  t.after(() => new Promise((resolve) => server.close(resolve)));
  server.listen({ port: 0, host: "127.0.0.1" });
  await once(server, "listening");
  return server;
}

// Catches a connect probe that always reports silence, or never reaches the listener.
test("somethingAnswers sees a real loopback listener", async (t) => {
  const server = await listenLoopback(t);
  assert.equal(await somethingAnswers(server.address().port), true);
});

// Catches treating a refused connection as a listener, which would reject free ports.
test("somethingAnswers reports silence after the loopback listener closes", async (t) => {
  const server = await listenLoopback(t);
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  assert.equal(await somethingAnswers(port), false);
});

// Windows can bind beside a wildcard listener. Inject the answer to exercise that
// composition without opening a wildcard listener or depending on Windows bind semantics.
// Catches the old bind-only probe, a missing negation, and lost port/host propagation.
test("isPortFree requires silence even when it can bind", async (t) => {
  const server = await listenLoopback(t);
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  const host = "127.0.0.1";
  const calls = [];
  const answers = (answered) => async (...args) => { calls.push(args); return answered; };
  assert.equal(await isPortFree(port, host, { answers: answers(false) }), true);
  assert.equal(await isPortFree(port, host, { answers: answers(true) }), false);
  assert.deepEqual(calls, [[port, host], [port, host]]);
});
