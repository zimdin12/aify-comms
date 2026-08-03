// The release version is declared ONCE. This test is what makes that true rather than aspirational.
//
// Before 2026-08-03 four components each carried their own version literal and none tracked a
// release: the service reported 0.1.0 (a stale SERVICE_VERSION in .env), its own default said
// 4.0.0, the dashboard hardcoded 0.1.0, and the bridge said 4.0.0 in EIGHT hand-copied places
// (server.js twice — the MCP handshake and BRIDGE_VERSION, which also reaches the control plane as
// `bridgeVersion` — plus claude-channel.js, codex-session.js, hermes-session.js, runtimes-codex.js
// twice, and controllers/codex-legacy-controller.js). Meanwhile the project actually shipped v0.1,
// v0.1.1 and v0.1.2. No single edit could have fixed that, because there was no single place.
//
// Runtime constraint that shapes the design: install.sh copies ONLY mcp/stdio into ~/.aify-comms,
// so the repo root does not exist when the bridge runs, and the bridge is on a load-time budget
// (the native copy exists because a ~5s load blew hermes' 0.75s MCP-discovery window). So version.js
// holds a literal instead of reading VERSION or package.json at import — and this test, which runs
// in the checkout where all three files exist, is what keeps the three in agreement.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { AIFY_VERSION } from "../version.js";

const here = dirname(fileURLToPath(import.meta.url));
const stdioDir = join(here, "..");
const repoRoot = join(stdioDir, "..", "..");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const canonical = readFileSync(join(repoRoot, "VERSION"), "utf8")
  .split("\n")
  .map((line) => line.trim())
  .find((line) => line && !line.startsWith("#"));

test("the repo-root VERSION file is readable and looks like a version", () => {
  assert.ok(canonical, "VERSION must contain a non-comment, non-empty line");
  assert.match(canonical, /^\d+\.\d+\.\d+(?:[-+].+)?$/, `VERSION reads ${JSON.stringify(canonical)}`);
});

test("version.js matches the repo-root VERSION file", () => {
  assert.equal(AIFY_VERSION, canonical);
});

test("package.json matches the repo-root VERSION file", () => {
  const pkg = JSON.parse(readFileSync(join(stdioDir, "package.json"), "utf8"));
  assert.equal(pkg.version, canonical);
});

test("no bridge source file re-declares a version literal", () => {
  // The actual regression guard. Adding a new handshake with its own hand-typed version is
  // exactly how this got to eight copies, and it is invisible in review — so fail the suite on
  // any `version: "1.2.3"` / `"version": "1.2.3"` outside version.js and package.json.
  const files = [
    "server.js",
    "claude-channel.js",
    "codex-session.js",
    "hermes-session.js",
    "runtimes-codex.js",
    "runtimes.js",
    "controllers/codex-legacy-controller.js",
  ];
  const literal = /["']?version["']?\s*:\s*["']\d+\.\d+\.\d+["']/;
  for (const rel of files) {
    const source = readFileSync(join(stdioDir, rel), "utf8");
    const offender = source
      .split("\n")
      .map((line, i) => [i + 1, line])
      .find(([, line]) => literal.test(line));
    assert.equal(
      offender,
      undefined,
      offender ? `${rel}:${offender[0]} hardcodes a version — import AIFY_VERSION instead: ${offender[1].trim()}` : "",
    );
  }
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`  ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL ${name}`);
    console.log(`       ${error.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} version-consistency tests passed`);
if (failed) process.exit(1);
