import assert from "node:assert/strict";
import test from "node:test";
import { makeDefaultHandlePoster } from "../session-handle-heartbeat.js";
import { makeDefaultTurnBusyPoster } from "../turn-busy-heartbeat.js";

function withMockFetch(fn) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return { ok: true, status: 200, text: async () => "" };
  };
  return Promise.resolve()
    .then(() => fn(calls))
    .finally(() => {
      globalThis.fetch = original;
    });
}

test("session handle heartbeat poster includes API key when configured", async () => {
  await withMockFetch(async (calls) => {
    const post = makeDefaultHandlePoster("http://svc", "secret-key");
    await post("agent-1", "session-1");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://svc/api/v1/agents/agent-1/session-handle");
    assert.equal(calls[0].options.headers["X-API-Key"], "secret-key");
  });
});

test("turn busy heartbeat poster includes API key when configured", async () => {
  await withMockFetch(async (calls) => {
    const post = makeDefaultTurnBusyPoster("http://svc", "secret-key");
    await post("agent-1");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options.headers["X-API-Key"], "secret-key");
  });
});
