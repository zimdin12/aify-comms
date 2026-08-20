#!/usr/bin/env node
// The install-time chain, end to end across all three repos.
//
// aify-comms writes the registry. aify-wrapper reads it and bakes its fingerprint into a launcher. The
// checker reads that launcher back and says whether it still matches. Every hop has its own tests and
// the CHAIN had never run as one sequence — which is where a format disagreement hides, because each
// end is internally consistent.
//
// TWO EARLIER ATTEMPTS AT THIS TRACE WERE VACUOUS, and that is why it is a test rather than a note. Run
// by hand through a shell, `/tmp/x` reached node as `C:\tmp\x`, which does not exist — so the registry
// was absent at every hop, every fingerprint was the empty-registry digest, and "current" meant two
// nothings agreeing. It looked exactly like success. Here the paths are node's own throughout, and the
// first assertion is that the fingerprint is NOT the empty one.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

const WRAPPER_REPO = process.env.AIFY_WRAPPER_REPO || path.join(os.homedir(), "projects", "aify-wrapper");
const WRITER = path.join(path.dirname(new URL(import.meta.url).pathname).replace(/^\//, ""), "..", "register-service-cli.mjs");
const INSTALL = path.join(WRAPPER_REPO, "install.sh");
const CHECK = path.join(WRAPPER_REPO, "bin", "aify-wrapper-check.mjs");
const available = fs.existsSync(INSTALL) && fs.existsSync(CHECK);

/** Set, and reachable by nothing. */
const NOWHERE = "http://127.0.0.2:1";

const fingerprintOf = (launcher) => {
  const match = /HARNESS_REGISTRY_FINGERPRINT="([^"]*)"/.exec(fs.readFileSync(launcher, "utf8"));
  return match ? match[1] : null;
};

function workspace() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-chain-"));
  const bin = path.join(dir, "bin");
  fs.mkdirSync(bin, { recursive: true });
  return { dir, bin, registry: path.join(dir, "services.json") };
}

const install = (w) => spawnSync("bash", [
  INSTALL, "--client", "claude", "--endpoint", NOWHERE, "--render-only", w.bin, "--registry", w.registry,
], { encoding: "utf8", timeout: 120_000 });

const check = (w, extra = []) => spawnSync(process.execPath, [
  CHECK, "--dest", w.bin, "--registry", w.registry, ...extra,
], { encoding: "utf8", timeout: 60_000 });

test("aify-wrapper is checked out, so this chain can be exercised", () => {
  // Failing rather than skipping: "the chain is unverified" must not read as green.
  assert.equal(available, true, `aify-wrapper not found at ${WRAPPER_REPO}; set AIFY_WRAPPER_REPO`);
});

test("THE CHAIN: comms writes, wrapper bakes, the checker agrees — and heals after drift", (t) => {
  if (!available) return t.skip("aify-wrapper is not checked out");
  const w = workspace();
  try {
    // 1. aify-comms registers itself.
    const wrote = spawnSync(process.execPath, [WRITER, w.registry, NOWHERE, "/opt/aify/mcp/stdio"], {
      encoding: "utf8",
      timeout: 60_000,
    });
    assert.equal(wrote.status, 0, wrote.stdout + wrote.stderr);
    assert.ok(fs.existsSync(w.registry), "the writer reported success and wrote nothing");

    // 2. aify-wrapper installs against that exact file.
    const installed = install(w);
    assert.equal(installed.status, 0, installed.stdout + installed.stderr);

    const baked = fingerprintOf(path.join(w.bin, "claude-aify"));
    assert.ok(baked, "no fingerprint was baked into the launcher");

    // THE GUARD AGAINST A VACUOUS RUN. An absent registry fingerprints to a real digest of its own, so
    // a chain where nothing was found still produces matching values at every hop. Compare against the
    // empty case explicitly rather than trusting that the file was read.
    const emptyWorkspace = workspace();
    install(emptyWorkspace);
    const emptyDigest = fingerprintOf(path.join(emptyWorkspace.bin, "claude-aify"));
    fs.rmSync(emptyWorkspace.dir, { recursive: true, force: true });
    assert.notEqual(baked, emptyDigest, "the launcher was built from an ABSENT registry; the chain is vacuous");

    // 3. The checker agrees.
    assert.equal(check(w, ["--strict"]).status, 0, "a freshly installed launcher read as stale");

    // 4. A second service registers later.
    const registry = JSON.parse(fs.readFileSync(w.registry, "utf8"));
    registry.services["aify-graph"] = { endpoint: "http://127.0.0.2:2", endpointEnv: ["AIFY_GRAPH_URL"], mcp: [] };
    fs.writeFileSync(w.registry, `${JSON.stringify(registry, null, 2)}\n`);

    const stale = check(w, ["--json"]);
    const report = JSON.parse(stale.stdout);
    assert.equal(report.ok, false, "registering a service did not make the launcher stale");
    assert.deepEqual(report.stale.map((r) => r.name), ["claude-aify"]);
    assert.equal(report.stale[0].installed, baked, "the checker misread what the launcher carries");
    assert.notEqual(report.stale[0].expected, baked);

    // 5. Reinstalling heals it. Without this the fingerprint is a way to report a problem nobody can fix.
    assert.equal(install(w).status, 0);
    assert.equal(check(w, ["--strict"]).status, 0, "reinstalling did not clear the staleness");
  } finally {
    fs.rmSync(w.dir, { recursive: true, force: true });
  }
});

test("STRICT OPT-IN crosses the chain: an opted-in service reaches the launcher", (t) => {
  if (!available) return t.skip("aify-wrapper is not checked out");
  // The other value the registry carries into a launcher, and the one with a real consequence if it
  // arrives wrong: a service's own endpoint under its own declared env name.
  const w = workspace();
  try {
    spawnSync(process.execPath, [WRITER, w.registry, NOWHERE, "/opt/aify/mcp/stdio"], { timeout: 60_000 });
    const registry = JSON.parse(fs.readFileSync(w.registry, "utf8"));
    registry.services["aify-graph"] = {
      endpoint: "http://127.0.0.2:2",
      endpointEnv: ["AIFY_GRAPH_URL"],
      strictMcp: true,
      mcp: [{ name: "aify-graph", command: "node", args: ["/opt/graph/server.js"] }],
    };
    fs.writeFileSync(w.registry, `${JSON.stringify(registry, null, 2)}\n`);

    assert.equal(install(w).status, 0);

    const launcher = fs.readFileSync(path.join(w.bin, "claude-aify"), "utf8");
    const encoded = /printf '%s' "([A-Za-z0-9+/=]+)"/.exec(launcher);
    assert.ok(encoded, "no strict extras were baked for an opted-in service");

    const decoded = Buffer.from(encoded[1], "base64").toString("utf8");
    assert.match(decoded, /aify-graph/);
    assert.match(decoded, /AIFY_GRAPH_URL/, "the service's own env name did not survive the chain");
    assert.match(decoded, /127\.0\.0\.2:2/, "the service's own endpoint did not survive the chain");
    // And NOT under aify-comms' env names, which is the failure that would look fine until two
    // services disagreed about where they point.
    assert.doesNotMatch(decoded, /AIFY_SERVER_URL/);
  } finally {
    fs.rmSync(w.dir, { recursive: true, force: true });
  }
});
