#!/usr/bin/env node
// Unit tests for the hermes api_server capability probe + loud assertion.
// Probes the in-process fake-hermes-apiserver fixture; verifies specific
// failure reasons (daemon down / key mismatch / endpoint drift) and that
// assertApiServer throws a LOUD, explicit error when unavailable.
// Contract: docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import assert from "node:assert/strict";
import { test } from "node:test";
import { probeApiServer, assertApiServer } from "../hermes-version.js";
import { start } from "./fixtures/fake-hermes-apiserver.mjs";

async function withFixture(t, opts) {
  const fixture = await start(opts);
  t.after(() => fixture.close());
  return fixture;
}

test("probeApiServer reports available + version against the fixture", async (t) => {
  const { baseUrl, key } = await withFixture(t);
  const probe = await probeApiServer({ baseUrl, key });
  assert.equal(probe.available, true);
  // version surfaced from health (may be undefined if fixture omits it, but
  // the field must be present in the shape).
  assert.ok("version" in probe);
});

test("probeApiServer reports daemon-not-running on a closed port", async (t) => {
  // Port 1 is privileged + unused → ECONNREFUSED on localhost.
  const probe = await probeApiServer({ baseUrl: "http://127.0.0.1:1", key: "x" });
  assert.equal(probe.available, false);
  assert.match(probe.reason, /daemon not running|connection/i);
});

test("probeApiServer reports key-mismatch on an injected 401 client", async () => {
  const fakeClient = {
    health: async () => ({ ok: false, status: 401 }),
  };
  const probe = await probeApiServer({ baseUrl: "http://x", key: "bad", client: fakeClient });
  assert.equal(probe.available, false);
  assert.match(probe.reason, /key mismatch/i);
});

test("probeApiServer reports endpoint-missing / version drift on a 404 client", async () => {
  const fakeClient = {
    health: async () => ({ ok: false, status: 404 }),
  };
  const probe = await probeApiServer({ baseUrl: "http://x", key: "k", client: fakeClient });
  assert.equal(probe.available, false);
  assert.match(probe.reason, /endpoint missing|version drift/i);
});

test("probeApiServer never throws even if the client throws", async () => {
  const fakeClient = {
    health: async () => { throw new Error("boom"); },
  };
  const probe = await probeApiServer({ baseUrl: "http://x", key: "k", client: fakeClient });
  assert.equal(probe.available, false);
  assert.ok(probe.reason);
});

test("assertApiServer throws a LOUD error mentioning the reason when unavailable", () => {
  assert.throws(
    () => assertApiServer({ available: false, reason: "daemon not running" }),
    (err) => {
      assert.match(err.message, /FATAL/);
      assert.match(err.message, /daemon not running/);
      return true;
    },
  );
});

test("assertApiServer returns the version and does not throw when available", () => {
  const version = assertApiServer({ available: true, version: "x" });
  assert.equal(version, "x");
});
