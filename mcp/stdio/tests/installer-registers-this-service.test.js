// install.sh's OWN registration call, executed with the arguments install.sh actually passes.
//
// register-service-cli.mjs is well tested and install-chain-across-three-repos.test.js exercises the
// whole registry chain — but that test calls the writer itself, with its own argument list. So the
// writer was asserted and the CALLER was not: install.sh could reorder its three arguments, point at
// the wrong file, or drop one, and every existing test would still pass. Registration is also
// non-fatal by design (`|| echo warning`), so a broken call prints one line into a long install and
// leaves the host with no registry entry, which is exactly the silent shape that half of today's
// defects had.
//
// It matters more since 2026-08-20, when the call MOVED ahead of wrapper rendering so a launcher would
// bake a fingerprint of a registry that already contains this service. A moved call is an unasserted
// call in a new place.
//
// The invocation is READ out of install.sh and then RUN. A test that only grepped for the line would
// prove a line was written; running it proves the three arguments still mean what the CLI reads.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { leakedCarriers, sealedChildEnv } from "./_child-env.mjs";

const BRIDGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(BRIDGE, "..", "..");
const INSTALL_SH = path.join(REPO, "install.sh");
const NOWHERE = "http://127.0.0.2:1";

/**
 * The argument list install.sh passes, with its shell variables resolved to the values a caller
 * supplies. Returns null when the invocation is not found in the shape this test understands, which
 * the first test turns into a failure rather than a silent skip.
 */
export function registrationArgvFrom(source, { registry, endpoint, bridgeDir }) {
  // The call spans lines with backslash continuations; join them before matching.
  const joined = source.split(String.fromCharCode(92) + String.fromCharCode(10)).join(" ");
  const line = joined
    .split(String.fromCharCode(10))
    .find((l) => l.includes("register-service-cli.mjs") && l.trimStart().startsWith("node "));
  if (!line) return null;

  // Paths are wrapped in `$(path_for_node "...")` so native Node can resolve them where the shell is
  // not converting. Unwrap that first, or the quoted-token scan below reads the helper name as an
  // argument -- which is how this test first reported a break that was only a shape change.
  const unwrapped = line.replace(/\$\(path_for_node "([^"]*)"\)/g, "$1");
  const quoted = [...unwrapped.matchAll(/"([^"]*)"/g)].map((m) => m[1]);
  // node "<script>" "<registry>" "<endpoint>" "<bridge-dir>"
  if (quoted.length < 4) return null;
  const resolve = (token) => token
    .split("$AIFY_BRIDGE_DIR").join(bridgeDir)
    .split("$AIFY_SERVICE_REGISTRY").join(registry)
    .split("${SERVER_URL:-$DEFAULT_AIFY_SERVER_URL}").join(endpoint);
  return quoted.slice(0, 4).map(resolve);
}

test("the invocation is found and every shell variable in it resolves", () => {
  const source = fs.readFileSync(INSTALL_SH, "utf8");
  const argv = registrationArgvFrom(source, {
    registry: "R", endpoint: "E", bridgeDir: "B",
  });
  assert.ok(argv, "install.sh no longer calls register-service-cli.mjs in a shape this test can read");
  assert.deepEqual(argv, ["B/register-service-cli.mjs", "R", "E", "B"],
    "the three arguments must still be registry, endpoint, bridge-dir, in that order");
  // Negative control: an unresolved `$` would mean this test substituted nothing and proved nothing.
  for (const token of argv) {
    assert.equal(token.includes("$"), false, `unresolved shell variable in ${token}`);
  }
});

test("running install.sh's own arguments produces a usable registry entry", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reg-call-"));
  const registry = path.join(dir, "services.json").split(String.fromCharCode(92)).join("/");
  const bridgeDir = BRIDGE.split(String.fromCharCode(92)).join("/");

  const argv = registrationArgvFrom(fs.readFileSync(INSTALL_SH, "utf8"), {
    registry, endpoint: NOWHERE, bridgeDir,
  });
  assert.ok(argv, "no invocation to run");

  execFileSync(process.execPath, argv, { encoding: "utf8" });

  const written = JSON.parse(fs.readFileSync(registry, "utf8"));
  assert.ok(written.services?.["aify-comms"], `no aify-comms entry: ${JSON.stringify(written)}`);
  assert.equal(written.services["aify-comms"].endpoint, NOWHERE,
    "the endpoint install.sh passes must be the one recorded");
});

test("a second run leaves the file byte-identical, so a reinstall does not move the fingerprint", () => {
  // A launcher bakes the registry's fingerprint. If registering twice changed the bytes, every
  // reinstall would produce a launcher that reports itself stale against the registry it was built
  // from -- the exact failure the move ahead of rendering was meant to prevent.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reg-twice-"));
  const registry = path.join(dir, "services.json").split(String.fromCharCode(92)).join("/");
  const bridgeDir = BRIDGE.split(String.fromCharCode(92)).join("/");
  const argv = registrationArgvFrom(fs.readFileSync(INSTALL_SH, "utf8"), {
    registry, endpoint: NOWHERE, bridgeDir,
  });

  execFileSync(process.execPath, argv, { encoding: "utf8" });
  const first = fs.readFileSync(registry, "utf8");
  execFileSync(process.execPath, argv, { encoding: "utf8" });
  assert.equal(fs.readFileSync(registry, "utf8"), first);
});

// MSYS path conversion, disabled.
//
// Git-Bash rewrites path-shaped arguments on their way to a native binary. That is a behaviour of the
// SHELL, not of this script, and anything that turns it off — MSYS2_ARG_CONV_EXCL, MSYS_NO_PATHCONV,
// or bash invoked from a non-MSYS parent — leaves native Node unable to resolve `/c/...`. Registration
// is non-fatal by design, so the install still reports success and the host simply has no registry
// entry: a launcher then never learns this service exists.
//
// Reported by comms-senior-dev in pre-deploy review, reproduced, and fixed by routing both paths
// through the installer's existing `path_for_node`. Its `cygpath -w` output survives argv untouched,
// because backslashes are only re-interpreted inside quoted strings, not in an argument vector.
//
// The same class was fixed in aify-wrapper's installer earlier, where the suite could not see it
// because every test ran through a shell that was quietly rescuing it.
test("registration survives a shell with path conversion turned off", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-reg-msys-"));
  const registry = path.join(dir, "services.json").split(String.fromCharCode(92)).join("/");
  const bridgeDir = BRIDGE.split(String.fromCharCode(92)).join("/");

  const argv = registrationArgvFrom(fs.readFileSync(INSTALL_SH, "utf8"), {
    registry, endpoint: NOWHERE, bridgeDir,
  });
  assert.ok(argv, "no invocation to run");

  // The installer converts before handing paths to node; this test runs what it would run, in the
  // environment that used to break it.
  const converted = argv.map((token) => (
    token.startsWith("/") || /^[A-Za-z]:/.test(token)
      ? execFileSync("cygpath", ["-w", token], { encoding: "utf8" }).trim()
      : token
  ));

  // sealedChildEnv, not a raw spread: a wrapper-launched shell carries the operator's service URL,
  // API key, hermes session and agent identity, and this child WRITES a registry. Passing them in
  // would be inheritance nobody asked for, into the one test here that produces a file.
  const childEnv = sealedChildEnv({ MSYS2_ARG_CONV_EXCL: "*", MSYS_NO_PATHCONV: "1" });
  assert.deepEqual(leakedCarriers(childEnv), [],
    "the child that writes a registry must inherit none of the operator's live carriers");
  execFileSync(process.execPath, converted, { encoding: "utf8", env: childEnv });

  const written = JSON.parse(fs.readFileSync(registry, "utf8"));
  assert.ok(written.services?.["aify-comms"], "the registry must be written with conversion disabled");
});
