// A gateway that never serves is reported with the last line it wrote to stderr, not only "fetch failed".
//
// 2026-10-02: an unfinished `hermes update` made every dashboard boot fail on a file an elevated install had
// locked. Six managed starts failed as "did not become ready within 60000ms: fetch failed", and the reason
// sat in ~/.local/state/aify-comms/hermes-gateway-host-<port>.log.
//
// The live spawn hands the gateway a FILE DESCRIPTOR for stderr (child.stderr is null), so the fake writes
// through that fd, the way hermes does.

import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import { test } from "node:test";

import { ensureGatewayHost, lastGatewayLogLine } from "../hermes-gateway.mjs";

const DENIED = "hermes: source-update completion failed: [WinError 5] Access is denied: 'cua-driver.exe'";

function spawnWritingStderr(text) {
  return (cmd, args, opts) => {
    const fd = opts.stdio[2];
    if (text && typeof fd === "number") fs.writeSync(fd, text);
    const child = new EventEmitter();
    child.stderr = null;
    child.unref = () => {};
    return child;
  };
}

async function readinessFailure(stderrText) {
  try {
    await ensureGatewayHost({
      agentId: "readiness-probe", port: 9399, spawn: spawnWritingStderr(stderrText),
      fetchImpl: async () => { throw new Error("fetch failed"); },
      probeFirst: false, verifyWs: false, readyTimeoutMs: 50, readyIntervalMs: 5,
    });
  } catch (err) {
    return String(err.message);
  }
  assert.fail("a gateway that never serves must reject");
}

test("THE TIMEOUT QUOTES the gateway's last stderr line, under the prefix every reader matches", async () => {
  const message = await readinessFailure(`hermes: finishing an interrupted source update...\n${DENIED}\n`);
  assert.match(message, /did not become ready within 50ms/);
  assert.ok(message.endsWith(`its stderr ends: ${DENIED}`), message);
});

test("CONTROL: a gateway that wrote nothing still reports the fetch error", async () => {
  const message = await readinessFailure("");
  assert.match(message, /did not become ready within 50ms: fetch failed$/);
});

test("the last line is the last NON-BLANK line", () => {
  assert.equal(lastGatewayLogLine("a\r\n  b  \r\n\r\n"), "b");
  assert.equal(lastGatewayLogLine(""), "");
});
