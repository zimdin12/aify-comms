// A bridge fetch that sends headers never follows a redirect.
//
// `fetch` re-sends custom headers on a redirect, so a 302 hands an `X-API-Key` to whatever origin it
// names. The v0.7 review reproduced exactly that against `agent-for-handle.mjs`, a new file that did
// not copy `redirect: "manual"` from the modules beside it. Every other keyed call already had it, and
// nothing said it was a rule. This makes it one: a call's options that mention `headers` must also say
// `redirect: "manual"`. It reads the call's own lines, so a helper that builds its options elsewhere
// is out of its sight; the positive control below is what says it is still looking at real calls.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CALL = /\b(?:fetch|fetchImpl)\(/;

/** Each call site's text: the call line, and following lines up to the one that closes the call. */
function callSites(source) {
  const lines = source.split("\n");
  const sites = [];
  lines.forEach((line, i) => {
    if (!CALL.test(line) || /^\s*(\/\/|\*)/.test(line)) return;
    let end = i;
    while (end < i + 10 && end < lines.length - 1 && !/\)\s*[;,]?\s*$/.test(lines[end])) end += 1;
    sites.push({ line: i + 1, text: lines.slice(i, end + 1).join("\n") });
  });
  return sites;
}

const followsRedirectWithHeaders = (site) => /\bheaders\b/.test(site.text) && !/redirect:\s*["']manual["']/.test(site.text);

function productFiles() {
  return readdirSync(STDIO).filter((f) => /\.(m?js)$/.test(f)).map((f) => path.join(STDIO, f));
}

test("no bridge fetch that sends headers follows a redirect", () => {
  const offenders = [];
  let keyed = 0;
  for (const file of productFiles()) {
    for (const site of callSites(readFileSync(file, "utf8"))) {
      if (/\bheaders\b/.test(site.text)) keyed += 1;
      if (followsRedirectWithHeaders(site)) offenders.push(`${path.basename(file)}:${site.line}`);
    }
  }
  // POSITIVE CONTROL: the known keyed calls (agent-for-handle, three in usage-collector, the
  // artifact and inbox tools, the heartbeats) are seen, or this scan is measuring nothing.
  assert.ok(keyed >= 8, `the scan saw only ${keyed} fetches that send headers`);
  assert.deepEqual(offenders, [], "these fetches send headers and would follow a redirect with them");
});

test("NEGATIVE CONTROL: the scan flags a keyed fetch that follows redirects, and passes one that does not", () => {
  const bad = 'const r = await fetch(url, {\n  headers: { "X-API-Key": k },\n});\n';
  const good = 'const r = await fetch(url, {\n  headers: { "X-API-Key": k },\n  redirect: "manual",\n});\n';
  assert.equal(callSites(bad).filter(followsRedirectWithHeaders).length, 1);
  assert.equal(callSites(good).filter(followsRedirectWithHeaders).length, 0);
});
