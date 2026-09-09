// `env-code-currency` — is the aify-env serving this host running the code on its disk?
//
// THE CHECK EXISTS BECAUSE THIS VERSION WAS BUILT ON A HOST IN THAT STATE, and nothing said so. The
// renderer the operator asked for sat in files the running daemon had never loaded, while
// `tier-version` read green: it compares VERSIONS, and a stale daemon reports the same version as a
// current one. These pin the verdicts that would have caught it.

import assert from "node:assert/strict";
import test from "node:test";

import { checkEnvCodeCurrency, envCodeCurrencyVerdict } from "../env-code-currency-check.mjs";

const verdictFor = envCodeCurrencyVerdict;

test("a daemon whose loaded build differs from its disk is STALE, and both hashes are named", () => {
  const verdict = verdictFor({ build: "3b2bf8f9", codeOnDisk: "1c36464c" });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "stale");
  // NAMES BOTH. "aify-env is out of date" sends a reader nowhere; the pair is what makes the row
  // checkable by somebody who did not run the check.
  assert.match(verdict.detail, /3b2bf8f9/);
  assert.match(verdict.detail, /1c36464c/);
  // AND SAYS WHOSE CALL IT IS. Restarting reaps the predecessor's managed workers.
  assert.match(verdict.fix, /operator/i);
});

test("a daemon running its own disk is CURRENT", () => {
  const verdict = verdictFor({ build: "1c36464c", codeOnDisk: "1c36464c" });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "current");
});

test("NO EVIDENCE IS NOT A PASS: a silent aify-env reads unknown, never ok", () => {
  for (const nothing of [null, undefined, "", 0]) {
    const verdict = verdictFor(nothing);
    assert.equal(verdict.ok, false, `${JSON.stringify(nothing)} was treated as an answer`);
    assert.equal(verdict.code, "unknown");
  }
});

test("half the pair is UNKNOWN, not agreement — the shape an older aify-env sends", () => {
  // AN aify-env THAT PREDATES `codeOnDisk` SENDS ONE AND NOT THE OTHER. Reading a missing half as
  // "they match" would report green on exactly the hosts most likely to be behind, which is the
  // false green this row exists to prevent.
  for (const half of [{ build: "3b2bf8f9" }, { codeOnDisk: "1c36464c" }, {}]) {
    const verdict = verdictFor(half);
    assert.equal(verdict.ok, false, `${JSON.stringify(half)} was read as current`);
    assert.equal(verdict.code, "unknown");
  }
});

test("the comparison is on the VALUES, not on their presence", () => {
  // The control for the check above: two halves that are both present and equal must be `current`,
  // so "unknown" is being decided by emptiness rather than by the check having given up.
  assert.equal(verdictFor({ build: "a", codeOnDisk: "a" }).code, "current");
  assert.equal(verdictFor({ build: "a", codeOnDisk: "b" }).code, "stale");
});

test("surrounding whitespace does not make a matching pair look stale", () => {
  // A hash that arrives padded is the same hash. Without the trim this row would fire on a healthy
  // host, and a check that cries wolf is one somebody switches off before the day it matters.
  assert.equal(verdictFor({ build: " 1c36464c ", codeOnDisk: "1c36464c" }).code, "current");
});

test("with no aify-env to ask, the row SKIPS rather than passing or failing", async () => {
  const skipped = [];
  const added = [];
  await checkEnvCodeCurrency({
    add: (...args) => added.push(args),
    skip: (id, why) => skipped.push([id, why]),
    endpoint: "",
    fetchJson: async () => {
      throw new Error("nothing should be fetched when there is no endpoint");
    },
  });
  assert.equal(added.length, 0, "a row was added for a host with no environment tier");
  assert.equal(skipped.length, 1);
  assert.equal(skipped[0][0], "env-code-currency");
});

test("the gather asks the endpoint it was given, and reports what came back", async () => {
  const asked = [];
  const added = [];
  await checkEnvCodeCurrency({
    add: (...args) => added.push(args),
    skip: () => assert.fail("skipped a host that has an environment tier"),
    endpoint: "http://127.0.0.1:8802",
    fetchJson: async (url) => {
      asked.push(url);
      return { build: "aaa", codeOnDisk: "bbb" };
    },
  });
  assert.deepEqual(asked, ["http://127.0.0.1:8802/health"]);
  assert.equal(added.length, 1);
  const [id, ok, code] = added[0];
  assert.equal(id, "env-code-currency");
  assert.equal(ok, false);
  assert.equal(code, "stale");
});
