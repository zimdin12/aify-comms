#!/usr/bin/env node
// The credential must not reach the second host, PROVEN BY RUNNING THE CODE.
//
// Its sibling `a-request-carrying-the-key-never-follows-a-redirect.test.js` reads source. Review's
// standing objection to that gate is exactly right and this file is the answer to it: source text
// cannot prove CONSUMPTION. `redirect: "manual"` could be written on every call in the tree and
// still be handed to something that ignores it -- a wrapper that rebuilds the options object, a
// polyfill, a future `fetch` that spells the option differently. The only proof is a second host
// that says whether it received the key.
//
// SO THIS STANDS REAL SERVERS UP on the loopback and drives the ACTUAL production transports:
// `httpCall` from `aify-service-endpoint.mjs` (which carries `X-API-Key`) and the hermes API-server
// client (which carries `Authorization: Bearer` and `X-Hermes-Session-Key`). The redirector answers
// 302; the receiver records every header of every request that reaches it and is asked afterwards
// what it saw.
//
// WHAT UNDICI ALREADY DOES, MEASURED HERE 2026-09-08 ON NODE v22.20.0 rather than assumed, because
// it decides which of these tests would catch anything and I had the story wrong at first:
//
//   | header                 | cross-origin 302 | same-origin 302 |
//   |------------------------|------------------|-----------------|
//   | Authorization          | STRIPPED         | SURVIVES        |
//   | X-API-Key              | survives         | survives        |
//   | X-Hermes-Session-Key   | survives         | survives        |
//
// Two consequences, and both are why the fix is not narrower than it looks. `Authorization` being
// stripped cross-origin is real protection that nobody in this repo arranged, and it is the reason
// my first framing of the hermes calls as naked bearer leaks was too strong -- but it evaporates on
// a SAME-ORIGIN hop, which is the ordinary shape of a service redirecting `/v1/runs` to `/v2/runs`.
// And undici strips exactly one header name: `X-Hermes-Session-Key` is a session credential that
// `createRun` attaches, and it rides a cross-origin redirect untouched. The custom-header carriers
// were never protected at all.
//
// THE CONTROLS ARE THE LOAD-BEARING TESTS HERE, not formalities. "The receiver saw nothing" and
// "the receiver is broken" are the same observation, and a receiver that recorded nothing would make
// every assertion below pass while proving the opposite of what it claims. So a default-policy
// `fetch` goes through the same redirector first and MUST leak -- the danger reproduced, in this
// run, on this machine, with these servers -- and is asked again at the end, after everything else,
// to prove it was still recording throughout.
//
// LOOPBACK ONLY, EPHEMERAL PORTS, and no environment name that could reach the operator's fleet is
// left pointing anywhere real: ALL FOUR endpoint and key carriers are set to this test's own
// redirector for the duration of the import and restored after -- see `pointTheBridgeAt`, and the
// reason it is four names rather than the two I first wrote. An earlier session set `AIFY_SERVER_URL`
// to the LIVE service for a "hostile carriers" run and registered six agents into the operator's
// production registry.

import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE = path.join(HERE, "..");

//: Distinct per carrier, so a recorded header names WHICH credential leaked rather than "a secret".
const API_KEY_SECRET = "runtime-gate-x-api-key-secret";
const BEARER_SECRET = "runtime-gate-bearer-secret";

/** A server that records every request it receives and answers 200 with JSON. */
function receiver() {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ url: req.url, headers: { ...req.headers } });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, from: "the-second-host" }));
  });
  return { server, seen };
}

/**
 * A server that answers 302 to `target` for everything.
 *
 * CROSS-ORIGIN when `target` is another port, which is the compromised-proxy shape. Its same-origin
 * sibling below is a different threat and undici treats them differently, so both are exercised.
 */
function redirector(target) {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ url: req.url, headers: { ...req.headers } });
    res.writeHead(302, { Location: `${target}${req.url}` });
    res.end();
  });
  return { server, seen };
}

/**
 * One server that redirects to ITSELF on another path, and records what lands.
 *
 * This is where `Authorization` is genuinely exposed: undici keeps it across a same-origin hop, so a
 * service that moves an endpoint hands the token to the new path with no policy of ours involved.
 * A redirect is still not an API response, which is the whole rule.
 */
function sameOriginRedirector() {
  const seen = [];
  const server = http.createServer((req, res) => {
    if (!req.url.startsWith("/landed")) {
      res.writeHead(302, { Location: "/landed" });
      res.end();
      return;
    }
    seen.push({ url: req.url, headers: { ...req.headers } });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
  });
  return { server, seen };
}

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

/** Every header value any request to this receiver carried, flattened for a substring search. */
function everythingItSaw(seen) {
  return seen.flatMap((r) => Object.values(r.headers)).join(" | ");
}

/**
 * Run `body` with both servers up, then tear them down.
 *
 * The receiver's recording is handed to the body rather than read from a closure so a test cannot
 * accidentally assert against a list that was never populated because setup threw.
 */
async function withTwoHosts(body) {
  const second = receiver();
  const secondUrl = await listen(second.server);
  const first = redirector(secondUrl);
  const firstUrl = await listen(first.server);
  try {
    return await body({ firstUrl, secondUrl, saw: second.seen, redirected: first.seen });
  } finally {
    await close(first.server);
    await close(second.server);
  }
}

/**
 * Point the bridge at `url` with key `key`, and hand back the restore.
 *
 * SEALS EVERY CARRIER OF BOTH VALUES, not the one this test happens to think of. The endpoint is read
 * as `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` and the LEGACY name wins, so sealing only the aify
 * spelling is hermetic exactly where the other is unset -- every developer machine, and no wrapper
 * environment. A reviewer running this suite inside a live wrapper once got 94 failures nobody else
 * could see, because seventeen files sealed one name and every fake server sat unused while the
 * requests went to the operator's real service. `env-carrier-pairs-are-sealed-together.test.js`
 * caught this file doing the same thing.
 *
 * THE KEY PAIR IS SEALED TOO, though that gate does not scan for it: `apiKeyFrom` reads its two names
 * through `.map().find(Boolean)` rather than `A || B`, so the scanner's regex never sees the pair --
 * while `CLAUDE_MCP_API_KEY` takes precedence over `AIFY_API_KEY` exactly as the URL names do.
 */
function pointTheBridgeAt(url, key) {
  const names = {
    CLAUDE_MCP_SERVER_URL: url,
    AIFY_SERVER_URL: url,
    CLAUDE_MCP_API_KEY: key,
    AIFY_API_KEY: key,
  };
  const before = new Map(Object.keys(names).map((n) => [n, process.env[n]]));
  for (const [name, value] of Object.entries(names)) process.env[name] = value;
  return () => {
    for (const [name, value] of before) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  };
}

// ── the controls: without a policy, the credential really does travel ────────────────────────────

test("POSITIVE CONTROL: a default-policy fetch LEAKS the key to the second host", async () => {
  // Both servers work, the redirect is real, and the danger this gate exists for is reproduced here
  // rather than asserted from a comment. Without this, every "saw nothing" below could be a broken
  // server. `X-API-Key` is the carrier used because it is the one undici does NOT strip -- see the
  // table at the top; the same assertion written with `Authorization` would fail for the wrong
  // reason and read as a passing gate.
  await withTwoHosts(async ({ firstUrl, saw, redirected }) => {
    const res = await fetch(`${firstUrl}/api/v1/agents`, {
      headers: { "X-API-Key": API_KEY_SECRET },
    });
    assert.equal(res.status, 200, "the default fetch did not follow the redirect at all");
    assert.equal(redirected.length, 1, "the redirector was not asked");
    assert.equal(saw.length, 1, "the second host was never reached, so it cannot report a leak");
    assert.equal(saw[0].headers["x-api-key"], API_KEY_SECRET,
      "the second host did not receive the key, so this control proves nothing");
  });
});

test("MEASUREMENT: undici strips Authorization cross-origin and keeps it same-origin", async () => {
  // The table at the top of this file, executed. It is a fact about the RUNTIME, not about our code,
  // and it decides what the hermes tests below can prove -- so it is measured in the same run rather
  // than remembered from a probe. A Node that changed this would redden here, next to the reason.
  await withTwoHosts(async ({ firstUrl, saw }) => {
    await fetch(`${firstUrl}/p`, {
      headers: { authorization: `Bearer ${BEARER_SECRET}`, "x-hermes-session-key": API_KEY_SECRET },
    });
    assert.equal(saw.length, 1, "the cross-origin hop did not land");
    assert.equal(saw[0].headers.authorization, undefined,
      "Authorization survived a cross-origin redirect -- the comment table above is now wrong");
    assert.equal(saw[0].headers["x-hermes-session-key"], API_KEY_SECRET,
      "a custom header was stripped cross-origin -- the table above is now wrong");
  });

  const hop = sameOriginRedirector();
  const hopUrl = await listen(hop.server);
  try {
    await fetch(`${hopUrl}/start`, { headers: { authorization: `Bearer ${BEARER_SECRET}` } });
    assert.equal(hop.seen.length, 1, "the same-origin hop did not land");
    assert.equal(hop.seen[0].headers.authorization, `Bearer ${BEARER_SECRET}`,
      "Authorization was stripped same-origin -- then the bearer needs no policy and this file "
      + "should say so instead");
  } finally {
    await close(hop.server);
  }
});

// ── the production transports ───────────────────────────────────────────────────────────────────

test("httpCall does not hand X-API-Key to a host it was redirected to", async () => {
  await withTwoHosts(async ({ firstUrl, saw, redirected }) => {
    const restoreEnv = pointTheBridgeAt(firstUrl, API_KEY_SECRET);
    try {
      // The module resolves its endpoint and key at IMPORT, so the env has to be set first and the
      // import made unique -- a cached module would carry a previous test's endpoint.
      const url = new URL(pathToFileURL(path.join(BRIDGE, "aify-service-endpoint.mjs")));
      url.searchParams.set("runtime-redirect-gate", String(Date.now()));
      const { httpCall } = await import(url.href);

      await assert.rejects(
        () => httpCall("GET", "/agents"),
        (error) => {
          // A 302 arrives INTACT and fails `res.ok` like any other non-2xx. That is the honest
          // answer -- a redirect is not an API response -- and it is what the caller must see.
          assert.match(String(error?.message), /HTTP 302/,
            `expected the 302 to surface as an error, got: ${error?.message}`);
          return true;
        },
        "httpCall accepted the redirect as an answer",
      );

      assert.ok(redirected.length >= 1, "the redirector was never asked, so nothing was tested");
      assert.deepEqual(saw, [],
        `the second host received ${saw.length} request(s) carrying: ${everythingItSaw(saw)}`);
    } finally {
      restoreEnv();
    }
  });
});

test("the hermes client does not follow a redirect, on any of its credential-carrying calls", async () => {
  // WHAT THIS PROVES is that the second host is never asked at all. That is the assertion, and it is
  // not the same as "the bearer did not arrive": per the measurement above undici would have stripped
  // the bearer on this cross-origin hop anyway, so asserting only on the token would pass with the
  // policy deleted. `X-Hermes-Session-Key` on `createRun` would NOT have been stripped, and neither
  // would the bearer on a same-origin hop -- but the honest general rule is the one measured here,
  // that a redirect is not an answer and the request stops.
  const { createHermesApiServerClient } = await import(
    pathToFileURL(path.join(BRIDGE, "hermes-apiserver-client.js")).href
  );
  const client = createHermesApiServerClient();

  const calls = [
    ["health", (baseUrl) => client.health({ baseUrl, key: BEARER_SECRET })],
    ["createRun", (baseUrl) => client.createRun({
      baseUrl, key: BEARER_SECRET, input: "hello", sessionKey: API_KEY_SECRET,
    })],
    ["stopRun", (baseUrl) => client.stopRun({ baseUrl, key: BEARER_SECRET, runId: "r1" })],
    ["runEvents", (baseUrl) => client.runEvents({ baseUrl, key: BEARER_SECRET, runId: "r1" })],
  ];

  for (const [name, call] of calls) {
    await withTwoHosts(async ({ firstUrl, saw, redirected }) => {
      // Some of these resolve on a 302 and some reject; either is acceptable. What is NOT acceptable
      // is the second host being asked, so that is the only thing asserted.
      await call(firstUrl).catch(() => {});
      assert.ok(redirected.length >= 1, `${name} never reached the redirector`);
      assert.deepEqual(saw, [],
        `${name} followed the redirect: the second host received ${saw.length} request(s) `
        + `carrying: ${everythingItSaw(saw)}`);
    });
  }
});

test("NEGATIVE CONTROL: the recorder can still see a leak after the tests above", async () => {
  // The assertions above are all "saw nothing". If the recorder had stopped working part-way through
  // this file -- a server left closed, a list never wired -- they would every one of them pass. So
  // ask it once more, at the end, to report a leak it genuinely receives.
  await withTwoHosts(async ({ firstUrl, saw }) => {
    await fetch(`${firstUrl}/anything`, { headers: { "x-api-key": API_KEY_SECRET } });
    assert.equal(saw.length, 1, "the recorder stopped recording");
    assert.match(everythingItSaw(saw), new RegExp(API_KEY_SECRET),
      "the recorder no longer reports credentials it receives");
  });
});
