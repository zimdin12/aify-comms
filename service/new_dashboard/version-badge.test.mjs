// Real tests for the build-version badge, extracted from app.js in v0.5.4.
//
// This badge is how an operator answers "is the dashboard I am looking at the code I just deployed?" —
// the question CLAUDE.md says every deploy path in this repo fails silently on. So the failure mode that
// matters is not a crash: it is the badge showing something REASSURING when it knows nothing. The catch
// blanks it and says so in the tooltip rather than leaving the last good value on screen.
//
// It fetches `/version` from the service ROOT, not through `api()`, because that endpoint is not under
// the `/api/v1` prefix — a badge built from `apiBase` would ask for `/api/v1/version` and always fail.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { loadVersionBadge, serviceBuildShort } from "./version-badge.mjs";

let HANDLER = (_req, res) => { res.writeHead(200); res.end("{}"); };
const SEEN = [];
const SERVER = http.createServer((req, res) => {
  req.on("data", () => {});
  req.on("end", () => { SEEN.push(req.url); HANDLER(req, res); });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
const ORIGIN = `http://127.0.0.2:${PORT}`;
// base and origin differ here on purpose: it is the only way to prove the badge uses the ORIGIN.
setApiBase(`${ORIGIN}/api/v1`, ORIGIN);

test.after(() => SERVER.close());

function serve(payload, status = 200) {
  SEEN.length = 0;
  HANDLER = (_req, res) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(payload));
  };
}

function badgeEl() {
  const classes = new Set();
  return {
    textContent: "PREVIOUS", title: "PREVIOUS",
    classList: {
      toggle: (c, on) => { if (on) classes.add(c); else classes.delete(c); },
      contains: (c) => classes.has(c),
    },
  };
}

async function withBadge(el) {
  const had = "document" in globalThis;
  assert.equal(had, false, "document leaked into the test environment — the seal is broken");
  globalThis.document = { getElementById: (id) => (id === "version-badge" ? el : null) };
  try {
    await loadVersionBadge();
  } finally {
    delete globalThis.document;
  }
  return el;
}

test("it asks the service ROOT, not the /api/v1 prefix", async () => {
  serve({ sha_short: "abc1234", branch: "main" });
  await withBadge(badgeEl());
  assert.equal(SEEN[0], "/version", "built from apiOrigin; via apiBase this would be /api/v1/version");
});

test("up to date shows the sha alone and is not marked behind", async () => {
  serve({ sha_short: "abc1234", branch: "main", update: { behind_by: 0 } });
  const el = await withBadge(badgeEl());
  assert.equal(el.textContent, "abc1234");
  assert.equal(el.classList.contains("behind"), false);
  assert.match(el.title, /up to date/);
  assert.match(el.title, /main/, "the branch belongs in the tooltip");
});

test("being behind is shown ON the badge, not only in the tooltip", async () => {
  // A tooltip nobody hovers is not a signal. The count is the whole point of the badge.
  serve({ sha_short: "abc1234", branch: "main", update: { behind_by: 3 } });
  const el = await withBadge(badgeEl());
  assert.equal(el.textContent, "abc1234 · 3 behind");
  assert.equal(el.classList.contains("behind"), true);
  assert.match(el.title, /3 commits behind/);
});

test("one commit behind is not '1 commits'", async () => {
  serve({ sha_short: "abc1234", branch: "main", update: { behind_by: 1 } });
  const el = await withBadge(badgeEl());
  assert.match(el.title, /1 commit behind/);
  assert.ok(!el.title.includes("1 commits"), "the plural is conditional for a reason");
});

test("a missing update block reads as up to date, never as NaN", async () => {
  // An older service omits the block entirely. The behaviour is right and worth pinning.
  //
  // BUT NOT FOR THE REASON THE CODE SUGGESTS. I first wrote that `Number(v?.update?.behind_by || 0)`'s
  // `|| 0` is what prevents "abc1234 · NaN behind", then mutated it away and the suite stayed green:
  // `Number(undefined)` is NaN and `NaN > 0` is FALSE, so the up-to-date branch is taken either way. The
  // guard is belt-and-braces, not load-bearing, and this test passes because of the comparison rather
  // than because of it. Said plainly so nobody later "restores" a guard believing this test proves it.
  serve({ sha_short: "abc1234", branch: "main" });
  const el = await withBadge(badgeEl());
  assert.equal(el.textContent, "abc1234");
  assert.ok(!el.textContent.includes("NaN"));
});

test("the full sha is used when no short one is given, and '?' when neither is", async () => {
  serve({ sha: "abcdef1234567890", branch: "main" });
  assert.equal((await withBadge(badgeEl())).textContent, "abcdef1234567890");

  serve({ branch: "main" });
  assert.equal((await withBadge(badgeEl())).textContent, "?", "an unknown build must say so");
});

test("a sha containing markup is escaped", async () => {
  // The value comes off the wire. It reaches textContent here, but the escaping is what keeps that true
  // if the badge ever becomes innerHTML.
  serve({ sha_short: "<img src=x>", branch: "main" });
  const el = await withBadge(badgeEl());
  assert.ok(!el.textContent.includes("<img src=x"));
});

test("AN UNREACHABLE SERVICE BLANKS THE BADGE — it must not keep showing the last known build", async () => {
  // THE FAILURE THAT MATTERS. Leaving a stale sha on screen answers "which code is running?" with a
  // confident wrong answer, which is precisely what `aify-comms doctor` exists to stop elsewhere.
  serve({ detail: "down" }, 503);
  const el = await withBadge(badgeEl());
  assert.equal(el.textContent, "", "the previous value must be cleared");
  assert.equal(el.title, "Build version unavailable");
});

test("a non-JSON response is treated as unavailable rather than throwing", async () => {
  SEEN.length = 0;
  HANDLER = (_req, res) => { res.writeHead(200, { "content-type": "text/html" }); res.end("<html>nope"); };
  const el = await withBadge(badgeEl());
  assert.equal(el.textContent, "");
});

test("no badge element on the page is a no-op, not a crash", async () => {
  // The badge lives in the header, but this runs from the shared startup path.
  serve({ sha_short: "abc1234" });
  const had = "document" in globalThis;
  globalThis.document = { getElementById: () => null };
  try {
    await loadVersionBadge();
  } finally {
    if (!had) delete globalThis.document;
  }
  assert.deepEqual(SEEN, [], "it must not even ask when there is nowhere to show the answer");
});

// ---- the service build this module remembers, and who reads it ----------------------------------
//
// `serviceBuildShort()` exists so the environments panel can name a bridge running a DIFFERENT build
// than the service. That comparison is only sound while the empty case stays empty: a `/version`
// that has not answered yet is NO EVIDENCE, and a reader treating it as a mismatch would warn on
// every dashboard load until the first poll completed.
//
// It comes from the 2026-08-28 incident where the operator restarted aify-env twice for an empty
// AGENT column that only a bridge relaunch could fill: the bridge was two commits behind the code
// that sends the label, and nothing on any screen said so.

test("the remembered build is EMPTY before /version has answered", async () => {
  // Ordering caveat, stated because it is load-bearing: this file's earlier cases already fetched, so
  // the empty case is asserted through a FAILED fetch rather than by module freshness. A failure must
  // not leave a stale build behind for the comparison to use.
  serve({}, 500);
  const badge = badgeEl();
  globalThis.document = { getElementById: () => badge };
  try {
    await loadVersionBadge();
  } finally {
    delete globalThis.document;
  }
  assert.equal(serviceBuildShort(), "", "a failed /version left a build behind for readers to compare");
});

test("a successful fetch is remembered, and it is the SHORT sha", async () => {
  serve({ sha: "450455054285b1729757571c88ce14055a1ae579", sha_short: "45045505", branch: "main" });
  const badge = badgeEl();
  globalThis.document = { getElementById: () => badge };
  try {
    await loadVersionBadge();
  } finally {
    delete globalThis.document;
  }
  assert.equal(
    serviceBuildShort(), "45045505",
    "the long sha would never equal a bridge's short one, so every environment would read as stale",
  );
});
