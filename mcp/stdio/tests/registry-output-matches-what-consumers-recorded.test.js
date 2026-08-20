#!/usr/bin/env node
// What this service WRITES, pinned where it is written.
//
// aify-wrapper and aify-env each hold a recorded copy of this file's output and test against it. Those
// fixtures prove the consumers can parse what we produce -- on the day they were recorded. Neither repo
// can tell when we change. A contract asserted only by its consumers is one the producer can break
// silently, so the same bytes are pinned here too, against the real CLI rather than a description of it.
//
// WHAT IT ACTUALLY ADDS, measured by mutation rather than argued. I first wrote that dropping `version`
// would slip through; that was wrong, and running the mutation is what corrected it -- the existing
// suite catches that one, because a registry written without a version is refused on the next read.
// Two real gaps remain, and both of these pass the existing suite untouched:
//
//   * `command` renamed (node -> nodejs). Nothing asserted it. That string is what a launcher executes,
//     so the consequence is an MCP config invoking a binary that need not exist.
//   * key ordering destabilised. Both consumers hold BYTE-recorded fixtures, so unstable ordering makes
//     their suites flap with no change in meaning -- the kind of red that gets a gate switched off.
//
// MEASURED BEFORE WRITING: the CLI reproduces both consumer fixtures exactly, and those two fixtures
// are byte-identical to each other. This closes a hole rather than repairing a break.
//
// Changing the output means updating this fixture AND re-recording it in both consumer repos in the
// same change. Whichever you forget goes red in its own suite.

import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = path.join(HERE, "..", "register-service-cli.mjs");
const FIXTURE = path.join(HERE, "fixtures", "registry-as-consumers-record-it.json");

/** Set, and reachable by nothing. Never the live service. */
const NOWHERE = "http://127.0.0.2:1";

/** The directory the fixture was recorded with. Absolute and POSIX, as an installed bridge is. */
const BRIDGE_DIR = "/opt/aify/mcp/stdio";

const LF = String.fromCharCode(10);
const CRLF = String.fromCharCode(13, 10);
/** Line endings normalised: git rewrites them per checkout, so pinning those bytes tests the checkout. */
const read = (file) => fs.readFileSync(file, "utf8").split(CRLF).join(LF);

function writeRegistry() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reg-pin-"));
  const target = path.join(dir, "services.json");
  try {
    const res = spawnSync(process.execPath, [CLI, target, NOWHERE, BRIDGE_DIR], {
      encoding: "utf8",
      timeout: 60_000,
    });
    assert.equal(res.status, 0, `the CLI failed: ${res.stdout}${res.stderr}`);
    return read(target);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("the registry this service writes is exactly what the consumers recorded", () => {
  assert.equal(
    writeRegistry(),
    read(FIXTURE),
    "the registry output changed. Update this fixture AND re-record services-written-by-aify-comms.json "
    + "in aify-wrapper and aify-env in the same change, or their parsers are testing a shape we no "
    + "longer produce.",
  );
});

test("the pinned bytes carry the fields the consumers actually read", () => {
  // A positive control on the fixture itself. Byte-equality against an empty or truncated file would
  // pass just as well, and these four are what the two consumers reach for by name.
  const parsed = JSON.parse(read(FIXTURE));
  assert.equal(parsed.version, 1, "aify-wrapper refuses a registry whose version it does not know");
  const entry = parsed.services["aify-comms"];
  assert.equal(typeof entry.endpoint, "string", "aify-env skips a service with no string endpoint");
  assert.ok(Array.isArray(entry.endpointEnv) && entry.endpointEnv.length > 0);
  assert.ok(entry.mcp.every((server) => server.command && Array.isArray(server.args)));
});
