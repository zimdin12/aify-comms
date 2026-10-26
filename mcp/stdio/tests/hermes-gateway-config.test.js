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
// EVERYTHING HERE RUNS IN CHILD PROCESSES. The resolution happens at module load, so a decision made once per
// process is the only thing there is to observe. No in-process assertion could see more than one case.
//
// 0.9.10: the environment is the only source. The launcher starts the gateway with its URL already in its
// environment, and the marker file comms wrote until then, with its precedence cases, is gone.

import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "hermes-gateway-config.mjs")).href;

// Resolve in a fresh process with a private home and temp root.
function resolve({ env = {} } = {}) {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "aify-gw-"));
  try {
    const out = execFileSync(
      process.execPath,
      ["--input-type=module", "-e",
        "const m = await import(" + JSON.stringify(LEAF) + ");"
        + " process.stdout.write(JSON.stringify({"
        + "   url: m.AIFY_HERMES_GATEWAY_URL,"
        + " }));"],
      {
        env: {
          ...sealedChildEnv(),
          TEMP: tmp, TMP: tmp, TMPDIR: tmp,
          HOME: path.join(tmp, "home"), USERPROFILE: path.join(tmp, "home"),
          HERMES_HOME: path.join(tmp, "home", ".hermes"),
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

test("no gateway at all resolves to empty strings, not undefined", () => {
  // Callers test truthiness on these. `undefined` would work today and break the moment one is interpolated
  // into a message or a config, which is how the original placeholder incident reached an agent's record.
  assert.equal(resolve({}).url, "");
});
