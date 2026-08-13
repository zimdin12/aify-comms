// Which hermes gateway this bridge talks to, and in what order it decides.
//
// `AIFY_HERMES_GATEWAY_URL` is the live delivery address for a resident hermes agent. Twenty-one places read
// it and none of the resolution was reachable from a test: it lived in `server.js`, the bin entry point, and
// nothing imports that.
//
// THE PRECEDENCE IS WHAT MATTERS AND IT COMES FROM AN INCIDENT. hermes's YAML `${VAR}` interpolation falls
// back to the LITERAL placeholder when the variable is unset in hermes's own environment. On 2026-05-25 an
// agent had `"${AIFY_HERMES_GATEWAY_URL}"` stored as its gatewayUrl, its capability check failed, and
// delivery was rejected. Getting this order wrong does not degrade gracefully — it records a placeholder as
// an agent's delivery address.
//
// EVERYTHING HERE RUNS IN CHILD PROCESSES. The resolution happens at module load and reads a marker file, so
// a decision made once per process is the only thing there is to observe. No in-process assertion could see
// more than one case.
//
// NO TOKEN VALUE APPEARS IN THIS FILE. `AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER` holds the NAME of an
// environment variable, not its contents; the tests below assert that a NAME propagates and never construct
// a token.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "hermes-gateway-config.mjs")).href;

const { writeGatewayUrlMarker } = await import("../hermes-endpoint.js");

// Read the resolved pair out of a fresh process. `tmp` becomes the marker directory so a marker can be
// planted where `readGatewayUrlMarker` will find it.
function resolve({ env = {}, marker = null } = {}) {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "aify-gw-"));
  try {
    if (marker) {
      // PLANTED WITH THE PRODUCTION WRITER, not with a hand-built file. My first version created
      // `<tmp>/aify-hermes-gateway/<id>.json`; the real marker is a single file `aify-hermes-gateway-<id>`
      // with no extension. The fixture was wrong and the code was right — the sixth invented-shape error in
      // this decomposition. Using `writeGatewayUrlMarker` makes the test immune to that: if the on-disk
      // shape ever changes, both halves move together and this file needs no edit.
      const ok = writeGatewayUrlMarker(marker.agentId, marker.gatewayUrl, {
        gatewayTokenEnv: marker.gatewayTokenEnv || "",
        tempDir: tmp,
      });
      assert.equal(ok, true, "the marker fixture must actually be written");
    }
    const out = execFileSync(
      process.execPath,
      ["--input-type=module", "-e",
        "const m = await import(" + JSON.stringify(LEAF) + ");"
        + " process.stdout.write(JSON.stringify({"
        + "   url: m.AIFY_HERMES_GATEWAY_URL,"
        + "   tokenEnvName: m.AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER,"
        + " }));"],
      {
        env: {
          ...process.env,
          TEMP: tmp, TMP: tmp,
          AIFY_HERMES_GATEWAY_URL: "", AIFY_AGENT_ID: "",
          ...env,
        },
        encoding: "utf-8",
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    return JSON.parse(out);
  } finally { rmSync(tmp, { recursive: true, force: true }); }
}

test("a real ws:// URL in the environment is accepted", () => {
  assert.equal(resolve({ env: { AIFY_HERMES_GATEWAY_URL: "ws://127.0.0.1:9147/ws" } }).url, "ws://127.0.0.1:9147/ws");
  assert.equal(resolve({ env: { AIFY_HERMES_GATEWAY_URL: "wss://gw.example/ws" } }).url, "wss://gw.example/ws");
  assert.equal(resolve({ env: { AIFY_HERMES_GATEWAY_URL: "  ws://spaced/ws  " } }).url, "ws://spaced/ws", "trimmed");
});

test("THE INCIDENT CASE: an unexpanded ${...} placeholder is treated as ABSENT, never stored", () => {
  // The literal string hermes's interpolation leaves behind. Accepted, it becomes an agent's delivery
  // address and every capability check against it fails.
  const r = resolve({ env: { AIFY_HERMES_GATEWAY_URL: "${AIFY_HERMES_GATEWAY_URL}" } });
  assert.equal(r.url, "", "a placeholder must resolve to empty, not to itself");
});

test("anything that is not a WebSocket URL is rejected, including an http one", () => {
  // The scheme test is the guard. An http URL is a plausible-looking value that cannot carry delivery.
  for (const value of ["http://127.0.0.1:9147", "https://gw.example", "127.0.0.1:9147", "not a url", "  "]) {
    assert.equal(resolve({ env: { AIFY_HERMES_GATEWAY_URL: value } }).url, "",
      `${JSON.stringify(value)} must not be accepted as a gateway URL`);
  }
});

test("PRECEDENCE: with no usable env value, the agent's marker is used", () => {
  // The normal case on every gateway-host launch, not an edge case: the host spawns this child with the
  // variable still unexpanded because it cannot inject its own URL at spawn time.
  const r = resolve({
    env: { AIFY_AGENT_ID: "agent-a", AIFY_HERMES_GATEWAY_URL: "${AIFY_HERMES_GATEWAY_URL}" },
    marker: { agentId: "agent-a", gatewayUrl: "ws://127.0.0.1:9200/ws" },
  });
  assert.equal(r.url, "ws://127.0.0.1:9200/ws", "the marker must supply the URL the env could not");
});

test("PRECEDENCE the other way: a usable env value WINS over a marker", () => {
  // Asserted explicitly because the two branches are ordered and the order is invisible from either half
  // alone. If the marker won, an operator's explicit configuration would be silently overridden by a file.
  const r = resolve({
    env: { AIFY_AGENT_ID: "agent-a", AIFY_HERMES_GATEWAY_URL: "ws://from-env/ws" },
    marker: { agentId: "agent-a", gatewayUrl: "ws://from-marker/ws" },
  });
  assert.equal(r.url, "ws://from-env/ws", "the environment must win when it holds a real URL");
});

test("a marker for a DIFFERENT agent is not used", () => {
  // The marker is agent-keyed. Reading another agent's would point this bridge at someone else's gateway.
  const r = resolve({
    env: { AIFY_AGENT_ID: "agent-a" },
    marker: { agentId: "agent-b", gatewayUrl: "ws://someone-else/ws" },
  });
  assert.equal(r.url, "", "another agent's marker must not resolve this agent's gateway");
});

test("a placeholder AGENT ID does not go looking for a marker", () => {
  // Same unexpanded-`${}` failure one level up: the agent id can arrive as a literal placeholder too, and a
  // marker lookup keyed on it would either miss or, worse, match a file named after the placeholder.
  const r = resolve({
    env: { AIFY_AGENT_ID: "${AIFY_AGENT_ID}" },
    marker: { agentId: "${AIFY_AGENT_ID}", gatewayUrl: "ws://should-not-be-found/ws" },
  });
  assert.equal(r.url, "", "a placeholder agent id must not resolve a marker");
});

test("the token ENV VAR NAME propagates from the marker — and it is a name, not a secret", () => {
  // The constant's name invites the opposite reading. The marker records WHICH variable holds the gateway
  // token; the token itself is never read, stored or logged here. This test passes a name and asserts the
  // name arrives, which is the whole contract.
  const r = resolve({
    env: { AIFY_AGENT_ID: "agent-a" },
    marker: { agentId: "agent-a", gatewayUrl: "ws://127.0.0.1:9200/ws", gatewayTokenEnv: "HERMES_GATEWAY_TOKEN" },
  });
  assert.equal(r.tokenEnvName, "HERMES_GATEWAY_TOKEN", "the variable NAME must propagate");
  assert.doesNotMatch(r.tokenEnvName, /^ws/, "…and it is a name, not the URL");
});

test("the token env name is EMPTY unless a marker supplied one", () => {
  // It is only ever set on the marker path. An env-resolved gateway leaves it blank, and a caller must not
  // read a stale name as evidence that a token variable was configured.
  assert.equal(resolve({ env: { AIFY_HERMES_GATEWAY_URL: "ws://x/ws" } }).tokenEnvName, "");
  assert.equal(resolve({}).tokenEnvName, "", "no env and no marker leaves both empty");
});

test("no gateway at all resolves to empty strings, not undefined", () => {
  // Callers test truthiness on these. `undefined` would work today and break the moment one is interpolated
  // into a message or a config, which is how the original placeholder incident reached an agent's record.
  const r = resolve({});
  assert.equal(r.url, "");
  assert.equal(r.tokenEnvName, "");
});

test("server.js declares neither — exactly one owner", async () => {
  const { declaringModules } = await import("./bridge-sources.mjs");
  for (const name of ["AIFY_HERMES_GATEWAY_URL", "AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER"]) {
    assert.deepEqual(
      declaringModules(name), [{ file: "hermes-gateway-config.mjs", kind: "binding" }],
      `${name} must be declared exactly once, by its owner`,
    );
  }
});
