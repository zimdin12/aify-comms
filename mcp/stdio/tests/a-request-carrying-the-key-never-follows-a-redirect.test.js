#!/usr/bin/env node
// No request this bridge makes may follow a redirect.
//
// `fetch` FOLLOWS REDIRECTS BY DEFAULT AND RE-SENDS THE HEADERS. So a service answering 302 --
// compromised, misconfigured, or simply a proxy somebody put in front of it -- collects `X-API-Key`
// from a client that was authorised to send it to a completely different host. Review reproduced
// exactly that against real local receivers: the request went, the key arrived, and the 200 behind
// the redirect was accepted as the answer.
//
// THIS IS THE SECOND VERSION OF THIS GATE, and the first was defeated three ways in independently
// executed broken trees. Each defeat is now a test below.
//
//   1. IT COUNTED POLICY TEXT, NOT POLICY. `fetch(url, { ...options /* redirect: "manual" */ })`
//      passed with no executable policy at all, and so did
//      `{ ...options, redirect: "manual", ...{ redirect: "follow" } }`, where the later spread wins
//      and the request follows. Both leaked the key to the unrelated receiver.
//
//   2. ITS POPULATION KEYED ON HEADER SPELLING, case-sensitively. HTTP headers are case-INSENSITIVE,
//      so renaming `X-API-Key` to `x-api-key` throughout a file dropped it out of the judged set
//      entirely while it went on leaking.
//
//   3. IT WALKED ONE DIRECTORY. `adapters/`, `controllers/` and `scripts/` were never looked at.
//
// SO THE POPULATION IS NOW EVERY `fetch` IN THE BRIDGE, recursively, and header spelling has nothing
// to do with it. That is also the honest rule: a redirect is never a valid answer to any request this
// bridge makes, whether or not that particular call carries a credential today. A gate whose scope
// depends on a string somebody may rename is a gate with a rename-shaped hole in it.
//
// WHAT IS STILL NOT PROVEN HERE, and the reviewer is right to say so: this reads source, so it cannot
// prove CONSUMPTION. `tests/the-redirect-policy-is-enforced-at-runtime.test.js` is the other half --
// it stands two real receivers up and checks the key never reaches the second one.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const BRIDGE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKIP = new Set(["node_modules", "tests", "fixtures", ".git"]);

/** Every bridge source, RECURSIVELY. The first version of this walk saw one directory. */
export function bridgeSources(dir = BRIDGE, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) bridgeSources(full, out);
    else if (/\.(js|mjs)$/.test(entry.name)) {
      out.push({ name: path.relative(BRIDGE, full), text: readFileSync(full, "utf8") });
    }
  }
  return out;
}

/**
 * Source with COMMENTS blanked, so a policy written inside one is not counted as one.
 *
 * BLANKED RATHER THAN DELETED: offsets stay valid, so a match here can still be located in the
 * original, and newlines survive so line numbers do too.
 *
 * STRINGS SURVIVE, deliberately. The policy's own value IS a string literal -- blanking string bodies
 * turned `redirect: "manual"` into `redirect: "      "` and made every correct call read as a policy
 * that "is not a literal". My first version did exactly that, and its own positive control caught it.
 */
export function withoutComments(source) {
  const text = String(source ?? "");
  let out = "";
  let i = 0;
  const blank = (s) => s.replace(/[^\n]/g, " ");
  while (i < text.length) {
    const two = text.slice(i, i + 2);
    if (two === "//") {
      const end = text.indexOf("\n", i);
      const stop = end === -1 ? text.length : end;
      out += blank(text.slice(i, stop));
      i = stop;
    } else if (two === "/*") {
      const end = text.indexOf("*/", i + 2);
      const stop = end === -1 ? text.length : end + 2;
      out += blank(text.slice(i, stop));
      i = stop;
    } else {
      out += text[i];
      i += 1;
    }
  }
  return out;
}

/** The argument text of each `fetch(...)` call, brace- and paren-balanced. */
export function fetchCallArguments(source) {
  const clean = withoutComments(source);
  const calls = [];
  const pattern = /\bfetch\s*\(/g;
  let match;
  while ((match = pattern.exec(clean)) !== null) {
    let depth = 1;
    let i = pattern.lastIndex;
    while (i < clean.length && depth > 0) {
      const ch = clean[i];
      if (ch === "(" || ch === "{" || ch === "[") depth += 1;
      else if (ch === ")" || ch === "}" || ch === "]") depth -= 1;
      i += 1;
    }
    // The ORIGINAL text for the same span: comments are gone from the analysis, but the reader of a
    // failure wants to see what is actually written.
    calls.push({ at: match.index, args: clean.slice(pattern.lastIndex, i - 1) });
  }
  return calls;
}

/**
 * Why this call may follow a redirect, or "" when it cannot.
 *
 * REJECTS OVERRIDING FORMS, not just absent ones. A spread AFTER the policy can replace it -- the
 * reviewer's `{ ...options, redirect: "manual", ...{ redirect: "follow" } }` is a real leak that
 * reads as compliant -- and two `redirect:` keys mean the last one wins, which is not something a
 * reader should have to compute.
 */
export function redirectPolicyProblem(args) {
  // STRIPPED HERE TOO, so the predicate is correct however it is called. The gate's own
  // `fetchCallArguments` already removes them; a caller passing raw text -- a test, or a
  // future reader -- must get the same answer.
  const text = withoutComments(String(args ?? ""));
  const keys = [...text.matchAll(/\bredirect\s*:/g)];
  if (keys.length === 0) return "no redirect policy";
  if (keys.length > 1) return `${keys.length} redirect keys, so the last one silently wins`;
  const value = /\bredirect\s*:\s*["'](\w+)["']/.exec(text);
  if (!value) return "a redirect policy that is not a literal, so it cannot be read here";
  if (value[1] !== "manual" && value[1] !== "error") return `redirect: "${value[1]}" follows`;
  const after = text.slice(keys[0].index);
  if (/\.\.\./.test(after)) return "a spread AFTER the policy, which can replace it";
  return "";
}

// ── the walk ────────────────────────────────────────────────────────────────────────────────────

test("POSITIVE CONTROL: the walk reaches subdirectories and finds the real calls", () => {
  // The first version of this gate walked ONE directory and reported green while `adapters/`,
  // `controllers/` and `scripts/` were never opened.
  const sources = bridgeSources();
  const dirs = new Set(sources.map((f) => path.dirname(f.name)));
  assert.ok(dirs.size > 1, `the walk saw only ${[...dirs]} -- it is not recursive`);
  const calls = sources.reduce((n, f) => n + fetchCallArguments(f.text).length, 0);
  assert.ok(calls >= 20, `only ${calls} fetch calls found; the scan is looking in the wrong place`);
});

// ── the three defeats, each as a test ───────────────────────────────────────────────────────────

test("DEFEAT 1: a policy in a COMMENT is not a policy", () => {
  assert.notEqual(redirectPolicyProblem('url, { ...options /* redirect: "manual" */ }'), "");
  assert.notEqual(redirectPolicyProblem('url, { ...options } // redirect: "manual"'), "");
});

test("DEFEAT 1b: a policy a later spread can REPLACE is not a policy", () => {
  assert.notEqual(
    redirectPolicyProblem('url, { ...options, redirect: "manual", ...{ redirect: "follow" } }'), "");
  assert.notEqual(redirectPolicyProblem('url, { redirect: "manual", ...extra }'), "");
  // ...while a spread BEFORE it is fine: the explicit key wins.
  assert.equal(redirectPolicyProblem('url, { ...options, redirect: "manual" }'), "");
});

test("an explicit follow, a duplicate key, and a non-literal are all refused", () => {
  assert.match(redirectPolicyProblem('url, { redirect: "follow" }'), /follows/);
  assert.match(redirectPolicyProblem('url, { redirect: "manual", redirect: "follow" }'), /silently wins/);
  assert.match(redirectPolicyProblem("url, { redirect: policy }"), /not a literal/);
  assert.match(redirectPolicyProblem("url, { headers }"), /no redirect policy/);
});

test("POSITIVE CONTROL: a correct call passes", () => {
  // Without this, a predicate that refused everything would satisfy every assertion above and make
  // the walk below unsatisfiable rather than meaningful.
  assert.equal(redirectPolicyProblem('url, { headers, redirect: "manual", signal }'), "");
  assert.equal(redirectPolicyProblem("url, { redirect: 'error' }"), "");
});

test("DEFEAT 2: the population does not depend on how a header is spelled", () => {
  // HTTP headers are case-insensitive, so `x-api-key` is the same header -- and keying the judged set
  // on `X-API-Key` let a file leave the population by being renamed. Every fetch is judged now.
  const sources = bridgeSources();
  const mentioningTheHeader = sources.filter((f) => /x-api-key/i.test(f.text)).length;
  const withFetches = sources.filter((f) => fetchCallArguments(f.text).length > 0).length;
  assert.ok(withFetches > mentioningTheHeader,
    "the judged set is no larger than the header-mentioning set, so spelling still decides scope");
});

test("NO REQUEST THIS BRIDGE MAKES MAY FOLLOW A REDIRECT", () => {
  const offenders = [];
  for (const file of bridgeSources()) {
    for (const call of fetchCallArguments(file.text)) {
      const problem = redirectPolicyProblem(call.args);
      if (problem) {
        const line = file.text.slice(0, call.at).split("\n").length;
        offenders.push(`${file.name}:${line} — ${problem}`);
      }
    }
  }
  assert.deepEqual(offenders, [],
    "these calls would follow a 302 to another host, re-sending their headers to whatever it points "
    + "at. A redirect is never a valid answer to a request this bridge makes.");
});
