// The aify service endpoint: URL resolution, the failover latch, and the retry/timeout predicates.
//
// WHY THESE TESTS EXIST NOW AND NOT BEFORE. All of this lived inside `server.js`, the bin entry point,
// which nothing imports — so `httpCall` and the latch it advances were unreachable from a test. The
// bridge's hottest path had no direct coverage at all. Extracting it (v0.5.4 layer 0) is what makes the
// assertions below possible, and the reviewer required them as a condition of the extraction.
//
// THE LATCH IS THE THING TO GET RIGHT. `ACTIVE_SERVER_URL` records which of several configured servers
// last answered. It must advance ONLY on success: if a transient failure moved it, the bridge would
// pin itself to a server that just failed and keep preferring it.
//
// SINGLE INSTANCE IS A CORRECTNESS PROPERTY, not a style point. ESM module state is a per-process
// singleton, which is the whole reason this relocation preserves behaviour. Two module instances —
// reached through two different specifiers — would give the bridge and its callers separate latches
// that silently disagree about which server is live.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  RETRIABLE_POST_PATHS,
  activeServerUrl,
  coerceLoopbackToIPv4,
  isRetriableRequest,
  isTransientHttpError,
  uniqueServerUrls,
} from "../aify-service-endpoint.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STDIO = path.resolve(HERE, "..");

test("loopback is coerced to IPv4, and only in the host position", () => {
  // Not cosmetic: where `localhost` resolves to ::1 first, a service listening only on IPv4 is
  // unreachable through a name that looks correct in every log line.
  assert.equal(coerceLoopbackToIPv4("http://localhost:8800"), "http://127.0.0.1:8800");
  assert.equal(coerceLoopbackToIPv4("https://localhost/x"), "https://127.0.0.1/x");
  assert.equal(coerceLoopbackToIPv4("http://example.com/localhost"), "http://example.com/localhost",
    "a path segment named localhost must not be rewritten");
  assert.equal(coerceLoopbackToIPv4(""), "");
  assert.equal(coerceLoopbackToIPv4(null), "");
});

test("uniqueServerUrls dedupes while preserving order and dropping blanks", () => {
  // Order matters: the first entry is the preferred server, and the latch starts there.
  assert.deepEqual(
    uniqueServerUrls(["http://a", "", "http://b", "http://a", null, "http://b"]),
    ["http://a", "http://b"],
  );
  assert.deepEqual(uniqueServerUrls([]), []);
});

test("the latch starts at the first configured server", () => {
  // A read-only accessor, so this pins the initial state rather than a transition.
  assert.equal(typeof activeServerUrl(), "string");
});

test("retriable POST paths are an explicit allowlist, not a heuristic", () => {
  // A POST is not generally safe to retry. The set exists so retrying is opt-in per path; a heuristic
  // here would risk double-delivering work.
  assert.ok(RETRIABLE_POST_PATHS instanceof Set);
  assert.ok(RETRIABLE_POST_PATHS.size > 0, "an empty set would silently disable POST retries");
  for (const p of RETRIABLE_POST_PATHS) {
    assert.equal(typeof p, "string");
    assert.ok(p.startsWith("/"), `${p} should be a path`);
  }
});

test("GET is retriable; an arbitrary POST is not", () => {
  assert.equal(isRetriableRequest("GET", "/anything"), true);
  assert.equal(isRetriableRequest("POST", "/definitely-not-in-the-allowlist"), false);
  for (const p of RETRIABLE_POST_PATHS) {
    assert.equal(isRetriableRequest("POST", p), true, `${p} is allowlisted and must be retriable`);
  }
});

test("a TIMEOUT is transient but an HTTP error status is not", () => {
  // The distinction drives whether the bridge retries or surfaces the failure. Conflating them would
  // either hide a real 4xx behind retries or give up on a blip.
  const timeout = new Error("timed out");
  timeout.name = "TimeoutError";
  assert.equal(isTransientHttpError(timeout), true);

  const aborted = new Error("aborted");
  aborted.name = "AbortError";
  assert.equal(isTransientHttpError(aborted), true);

  const http404 = new Error("HTTP 404: nope");
  http404.status = 404;
  assert.equal(isTransientHttpError(http404), false, "a 404 is an answer, not a blip");

  assert.equal(isTransientHttpError(null), false);
});

test("production reaches this module through exactly ONE specifier", () => {
  // The reviewer's condition, and a real correctness property rather than tidiness: two specifiers
  // resolving to two module instances would give the bridge and its callers separate failover latches
  // that disagree about which server is live, with nothing to make the disagreement visible.
  const specifiers = new Set();
  for (const file of ["server.js"]) {
    const src = readFileSync(path.join(STDIO, file), "utf-8");
    for (const m of src.matchAll(/from\s+"([^"]*aify-service-endpoint[^"]*)"/g)) specifiers.add(m[1]);
  }
  assert.deepEqual([...specifiers], ["./aify-service-endpoint.mjs"],
    "every production import must use the same specifier so there is one module instance");
});

test("the latch is declared exactly once and written in exactly one place", () => {
  // Source-pinned because the property is about the MODULE, not about a call: a second `let` or a
  // second assignment would reintroduce the split-brain this extraction is meant to preserve against.
  const src = readFileSync(path.join(STDIO, "aify-service-endpoint.mjs"), "utf-8");
  const declarations = src.match(/^let ACTIVE_SERVER_URL\b/gm) || [];
  assert.equal(declarations.length, 1, "the latch must be declared once");
  // The declaration line begins with `let `, so this pattern matches ADVANCES only — which is the
  // property worth pinning. My first version expected 2 here by miscounting the declaration into it.
  const advances = src.match(/^\s+ACTIVE_SERVER_URL\s*=/gm) || [];
  assert.equal(advances.length, 1, "the latch must advance in exactly one place, inside httpCall");
  assert.match(src, /ACTIVE_SERVER_URL = baseUrl;/, "the advance records the URL that just succeeded");
});

test("server.js no longer declares any of the moved names", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["ACTIVE_SERVER_URL", "SERVER_URLS", "API_KEY", "HTTP_TIMEOUT_MS"]) {
    assert.equal(
      (src.match(new RegExp(`^(?:export )?(?:const|let) ${name}\\b`, "gm")) || []).length, 0,
      `${name} still declared in server.js — two owners`,
    );
  }
  assert.ok(!/^(?:export )?(?:async )?function httpCall\s*\(/m.test(src),
    "httpCall still defined in server.js");
});

test("no API key VALUE is embedded in the module", () => {
  // The binding is env-derived and named; a literal here would be a credential in the repo.
  const src = readFileSync(path.join(STDIO, "aify-service-endpoint.mjs"), "utf-8");
  assert.match(src, /API_KEY = process\.env\./, "the key must come from the environment");
  assert.ok(!/API_KEY\s*=\s*["'][^"']+["']/.test(src), "no literal key value may be assigned");
});

test("server.js imports nothing from this module that it does not use", () => {
  // The reviewer caught a dead `coerceLoopbackToIPv4` import in the layer-0 slice. My own dead-import
  // scan had run BEFORE the last function moved, so it was accurate when executed and stale by the time
  // I committed — the check passed, then the tree changed underneath it.
  //
  // A one-off scan cannot protect against that; a test re-runs on the final tree every time. Gating the
  // class rather than fixing the instance, which is this repo's rule for anything a process can
  // reproduce.
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  // ANCHORED to the block's own opening line. A bare `import \{[\s\S]*?\}` starts at the FIRST
  // import in the file and runs to the endpoint terminator, swallowing any block in between — which
  // is exactly what happened when a second leaf import was added above this one.
  const block = src.match(/^import \{[^}]*\} from "\.\/aify-service-endpoint\.mjs";$/m);
  assert.ok(block, "server.js must import from the endpoint leaf through the canonical specifier");
  const rest = src.replace(block[0], "");
  const imported = block[0]
    .split("\n")
    .slice(1, -1)
    .map((line) => line.trim().replace(/,$/, ""))
    .filter(Boolean);
  assert.ok(imported.length > 0, "the import block must not be empty");
  const dead = imported.filter((name) => !new RegExp(`(?<![\\w.])${name}(?![\\w])`).test(rest));
  assert.deepEqual(dead, [], "server.js imports these but never uses them");
});
