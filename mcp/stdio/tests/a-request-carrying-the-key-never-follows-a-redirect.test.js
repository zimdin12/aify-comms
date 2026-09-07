#!/usr/bin/env node
// Every request that carries the service key must refuse redirects.
//
// `fetch` FOLLOWS REDIRECTS BY DEFAULT AND RE-SENDS THE HEADERS. So a service answering 302 --
// compromised, misconfigured, or simply a proxy somebody put in front of it -- collects `X-API-Key`
// from a client that was authorised to send it to a completely different host. Review reproduced
// that against a synthetic receiver: the request went, the key arrived, and the 200 behind the
// redirect was accepted as the answer.
//
// THIS GATE EXISTS BECAUSE I FIXED IT ONE FILE AT A TIME AND KEPT MISSING SIBLINGS. Three times in
// one session: the credential fallback went into `aify-http.mjs` while `server.js` and every channel
// sidecar read `API_KEY` from `aify-service-endpoint.mjs`; then the redirect fix went into the
// doctor's two probes while BOTH production http clients still followed them; then those two while
// five more files carried the key with their own `fetch`. Each fix was correct and each was partial,
// and a review round was spent discovering that every time.
//
// So the population is DERIVED rather than listed. Any file that mentions `X-API-Key` is judged, and
// every `fetch(` in it must set a redirect policy. A new caller written next month is covered without
// anyone remembering this rule -- which is the only kind of rule that survives.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const BRIDGE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Bridge sources, excluding tests and vendored code. */
function bridgeSources() {
  return readdirSync(BRIDGE, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(js|mjs)$/.test(entry.name))
    .map((entry) => ({ name: entry.name, text: readFileSync(path.join(BRIDGE, entry.name), "utf8") }));
}

//: A file that names the header is one that can send it.
const CARRIES_KEY = /X-API-Key/;
//: `redirect: "manual"` or `"error"` — either refuses to follow; only the DEFAULT is unsafe.
const SETS_POLICY = /redirect:\s*["'](manual|error)["']/;

test("POSITIVE CONTROL: the scan finds the files that carry the key", () => {
  // A walk that matched nothing would pass every assertion below while measuring nothing at all --
  // the exact false green this repo has paid for in three separate gates.
  const carriers = bridgeSources().filter((file) => CARRIES_KEY.test(file.text));
  assert.ok(carriers.length >= 5,
    `only ${carriers.length} files appear to carry the key; the scan is probably looking in the wrong place`);
  assert.ok(carriers.some((f) => f.name === "aify-service-endpoint.mjs"),
    "the module that owns the key was not found by the scan");
});

test("NEGATIVE CONTROL: the policy pattern can say a file lacks one", () => {
  assert.equal(SETS_POLICY.test('await fetch(url, { headers, signal })'), false);
  assert.equal(SETS_POLICY.test('await fetch(url, { redirect: "manual" })'), true);
  assert.equal(SETS_POLICY.test("await fetch(url, { redirect: 'error' })"), true);
  // `follow` is the default spelled out, and it is exactly what this gate refuses.
  assert.equal(SETS_POLICY.test('await fetch(url, { redirect: "follow" })'), false);
});

test("EVERY FILE THAT SENDS THE KEY REFUSES REDIRECTS", () => {
  const offenders = [];
  for (const file of bridgeSources()) {
    if (!CARRIES_KEY.test(file.text)) continue;
    const fetches = (file.text.match(/\bfetch\(/g) || []).length;
    if (fetches === 0) continue;   // it names the header but makes no request of its own
    const policies = (file.text.match(new RegExp(SETS_POLICY.source, "g")) || []).length;
    if (policies < fetches) {
      offenders.push(`${file.name}: ${fetches} fetch call(s), ${policies} with a redirect policy`);
    }
  }
  assert.deepEqual(offenders, [],
    "these files can send X-API-Key on a request that would follow a 302 to another host, handing "
    + "the key to whatever it points at");
});
