// The key for each destination, resolved one way by every bridge component (v0.7, B5). The claude
// channel sidecar and the notify hook read the environment alone, so a host that enabled API_KEY
// after install had working MCP tools and a wake path that 401ed in silence.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { destinationKeyResolver } from "../aify-service-endpoint.mjs";

const HOME = "/home/op";
const ENDPOINT = "http://127.0.0.1:8800";
const files = {
  "/home/op/.aify/services.json": JSON.stringify({
    services: { "aify-comms": { endpoint: ENDPOINT, credentialRef: "aify-comms-abc123.key" } },
  }),
  "/home/op/.aify/credentials/aify-comms-abc123.key": "s3cret\n",
};
const readFile = (p) => {
  if (Object.prototype.hasOwnProperty.call(files, p)) return files[p];
  throw Object.assign(new Error(`ENOENT: ${p}`), { code: "ENOENT" });
};
const resolver = (env) => destinationKeyResolver(ENDPOINT, {
  env, readFile, joinPath: (...parts) => parts.join("/"), homeDir: HOME, realpath: (x) => x, custody: () => "",
});

test("with no key exported, the registry's credential opens its own endpoint and nothing else", () => {
  const keyFor = resolver({});
  assert.equal(keyFor(ENDPOINT), "s3cret");
  assert.equal(keyFor("http://localhost:8800"), "s3cret", "the other spelling of the same endpoint");
  assert.equal(keyFor("http://127.0.0.1:8900"), "", "never another destination");
});

test("an exported key is the operator's choice and goes wherever they pointed the process", () => {
  const keyFor = resolver({ AIFY_API_KEY: "exported" });
  assert.equal(keyFor(ENDPOINT), "exported");
  assert.equal(keyFor("http://192.0.2.10:8800"), "exported");
});

test("the sidecar and the hook both resolve through it", () => {
  for (const file of ["claude-channel.js", "notify-check.js"]) {
    const src = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
    assert.match(src, /destinationKeyResolver\(SERVER_URL\)/, file);
    assert.doesNotMatch(src, /apiKeyFrom\(\)/, `${file} must not fall back to an env-only read`);
  }
});
