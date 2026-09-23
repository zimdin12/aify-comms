// The `external-keys` doctor row, CALLED (importing doctor.js runs the doctor).
//
// What it must catch: keys issued to other machines that restrict nothing -- set on a service with no
// API_KEY, or refused as malformed -- which the service only logs. What it must not do: go red on a
// host that simply issues none.

import assert from "node:assert/strict";
import { test } from "node:test";

import { checkExternalKeys } from "../external-keys-check.mjs";

async function verdict(health) {
  const recorded = [];
  await checkExternalKeys({ get: async (path) => (path === "/health" ? health : null), add: (...a) => recorded.push(a) });
  assert.equal(recorded.length, 1);
  const [id, ok, code] = recorded[0];
  assert.equal(id, "external-keys");
  return { ok, code, detail: recorded[0][3] };
}

test("keys set on a service with no API_KEY are red: they restrict nothing", async () => {
  const v = await verdict({ status: "healthy", externalKeys: { configured: 2, rejected: 0, enforced: false } });
  assert.deepEqual([v.ok, v.code], [false, "unenforced"]);
  assert.match(v.detail, /API_KEY is not/);
});

test("a refused entry is red, and says how many still work", async () => {
  const v = await verdict({ status: "healthy", externalKeys: { configured: 1, rejected: 1, enforced: true } });
  assert.deepEqual([v.ok, v.code], [false, "entries-refused"]);
  assert.match(v.detail, /1 key\(s\) work/);
});

test("CONTROL: enforced keys with nothing refused are green", async () => {
  const v = await verdict({ status: "healthy", externalKeys: { configured: 2, rejected: 0, enforced: true } });
  assert.deepEqual([v.ok, v.code], [true, "enforced"]);
});

test("CONTROL: a host that issues no external keys is green, not unknown", async () => {
  const v = await verdict({ status: "healthy" });
  assert.deepEqual([v.ok, v.code], [true, "none-configured"]);
});

test("no answer from the service is not a pass", async () => {
  const v = await verdict(null);
  assert.deepEqual([v.ok, v.code], [false, "unknown"]);
});
