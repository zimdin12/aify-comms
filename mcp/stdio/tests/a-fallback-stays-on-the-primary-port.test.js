// A loopback primary's automatic fallbacks are the OTHER SPELLING of the same address (127.0.0.1 vs
// localhost), on the SAME PORT. Until 0.7.0 every loopback primary, on any port, gained
// 127.0.0.1:8800 and localhost:8800; a retriable ECONNREFUSED on :8900 (a second service, a test
// instance) moved the bridge to the service on :8800 and latched there (v0.7 scan B9). That is also
// why a dozen tests here bind 127.0.0.2: it was the only way to keep them off the live service.
import assert from "node:assert/strict";
import test from "node:test";
import { defaultFallbackServerUrls } from "../aify-service-endpoint.mjs";

test("a loopback primary falls back to the other spelling on its own port", () => {
  assert.deepEqual(defaultFallbackServerUrls("http://127.0.0.1:8900"), ["http://127.0.0.1:8900", "http://localhost:8900"]);
  assert.deepEqual(defaultFallbackServerUrls("http://localhost:8800/"), ["http://127.0.0.1:8800", "http://localhost:8800"]);
});

test("nothing is added for a non-loopback primary, or for one with no port", () => {
  assert.deepEqual(defaultFallbackServerUrls("http://192.0.2.10:8800"), []);
  assert.deepEqual(defaultFallbackServerUrls(""), []);
  assert.deepEqual(defaultFallbackServerUrls("http://127.0.0.1"), ["http://127.0.0.1", "http://localhost"]);
});
