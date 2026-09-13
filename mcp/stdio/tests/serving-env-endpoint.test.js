// Proves the doctor finds a `herdr-aify env` daemon the launcher's baked address does not name, and
// refuses every receipt that does not identify the process now answering on its port.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { readyReceipts, servingEnvEndpoint } from "../serving-env-endpoint.mjs";

const INSTALLED = "http://127.0.0.1:8802";
// The shape aify-env's `publishInstanceReady` wrote on the operator's host, 2026-09-13.
const LIVE = { invocation: "72caa80f", pid: 30068, envInstance: "189697d6", endpoint: "http://127.0.0.1:57978" };
const DEAD = { invocation: "51191402", pid: 4242, envInstance: "0badc0de", endpoint: "http://127.0.0.1:61001" };

/** A fake network: each endpoint answers with the body given, anything else is silent. Records what was asked. */
function network(answers) {
  const asked = [];
  return {
    asked,
    fetchHealth: async (endpoint) => { asked.push(endpoint); return answers[endpoint] ?? null; },
  };
}

test("an answering launcher endpoint wins, and no receipt is probed", async () => {
  const net = network({ [INSTALLED]: { pid: 1, instance: "x" }, [LIVE.endpoint]: { pid: LIVE.pid, instance: LIVE.envInstance } });
  const found = await servingEnvEndpoint({ installed: INSTALLED, receipts: [LIVE], fetchHealth: net.fetchHealth });
  assert.deepEqual(found, { endpoint: INSTALLED, answered: true, source: "installed" });
  assert.deepEqual(net.asked, [INSTALLED]);
});

test("THE OPERATOR'S HOST: 8802 silent, one live herdr-aify env -> that daemon", async () => {
  const net = network({ [LIVE.endpoint]: { pid: LIVE.pid, instance: LIVE.envInstance } });
  const found = await servingEnvEndpoint({ installed: INSTALLED, receipts: [DEAD, LIVE], fetchHealth: net.fetchHealth });
  assert.deepEqual(found, { endpoint: LIVE.endpoint, answered: true, source: "herdr-aify env" });
});

test("a receipt whose port answers with ANOTHER identity is not believed", async () => {
  // Invocations outlive their daemons and the OS reuses ports. Each identity half is removed alone.
  for (const health of [{ pid: 999, instance: LIVE.envInstance }, { pid: LIVE.pid, instance: "someone-else" }, { pid: LIVE.pid }]) {
    const net = network({ [LIVE.endpoint]: health });
    const found = await servingEnvEndpoint({ installed: INSTALLED, receipts: [LIVE], fetchHealth: net.fetchHealth });
    assert.deepEqual(found, { endpoint: INSTALLED, answered: false, source: "installed" }, JSON.stringify(health));
  }
});

test("nothing answering leaves the launcher's endpoint, unanswered, for the rows to report", async () => {
  const net = network({});
  const found = await servingEnvEndpoint({ installed: INSTALLED, receipts: [DEAD, LIVE], fetchHealth: net.fetchHealth });
  assert.deepEqual(found, { endpoint: INSTALLED, answered: false, source: "installed" });
});

test("two live dedicated daemons are not picked between", async () => {
  const other = { invocation: "b", pid: 7, envInstance: "i7", endpoint: "http://127.0.0.1:60000" };
  const net = network({
    [LIVE.endpoint]: { pid: LIVE.pid, instance: LIVE.envInstance },
    [other.endpoint]: { pid: other.pid, instance: other.envInstance },
  });
  const found = await servingEnvEndpoint({ installed: INSTALLED, receipts: [LIVE, other], fetchHealth: net.fetchHealth });
  assert.equal(found.answered, false);
  // Control: with one of them gone the same inputs resolve, so the refusal above is the ambiguity.
  const single = await servingEnvEndpoint({ installed: INSTALLED, receipts: [LIVE], fetchHealth: net.fetchHealth });
  assert.equal(single.endpoint, LIVE.endpoint);
});

test("receipts are read from disk, and only loopback, readable ones are candidates", () => {
  const root = mkdtempSync(join(tmpdir(), "aify-ready-"));
  try {
    const put = (id, body) => {
      mkdirSync(join(root, "invocations", id), { recursive: true });
      if (body !== undefined) writeFileSync(join(root, "invocations", id, "ready.json"), body);
    };
    put("live", JSON.stringify({ pid: LIVE.pid, envInstance: LIVE.envInstance, endpoint: LIVE.endpoint }));
    put("never-ready");
    put("garbled", "{not json");
    put("remote", JSON.stringify({ pid: 1, envInstance: "r", endpoint: "http://10.0.0.5:8802" }));
    assert.deepEqual(readyReceipts(root), [{ ...LIVE, invocation: "live" }]);
    assert.deepEqual(readyReceipts(join(root, "absent")), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
