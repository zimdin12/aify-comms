#!/usr/bin/env node
// install.sh gives hermes the servers a service opts into every session through scripts/hermes-session-servers.sh,
// which runs the PINNED aify-wrapper's hermes-config-cli.mjs: `--check` before hermes-aify is rendered (it starts no
// hermes), the write after it. Hermes has no per-session way in, so the write lands in the user's hermes config.
//
// Driven end to end: the script, the pinned package, and aify-wrapper's own stand-in for hermes' `config
// get|set|unset` (tests/fake-hermes.mjs in the sibling checkout, which the package does not ship). Every hermes root
// the tool looks under (home, LOCALAPPDATA, HERMES_HOME) is a temporary directory, so this host's hermes and any
// update it has pending are never read or run.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { sealedChildEnv } from "./_child-env.mjs";
import { siblingCheckout } from "./_sibling-checkout.mjs";
import { tmpDir } from "./_tmpdir.js";
import { INSTALL_SH, RENDER_ENV } from "./wrapper-harness.mjs";

const REPO = fileURLToPath(new URL("../../..", import.meta.url));
const SCRIPT = path.join(REPO, "scripts", "hermes-session-servers.sh");
const NOWHERE = "http://127.0.0.2:1";
const server = (args) => ({ endpoint: NOWHERE, endpointEnv: ["SVC_URL"], sessionInject: { mcp: true },
  mcp: [{ name: "svc-mcp", command: "node", args }] });
const OPTED_IN = { svc: server(["/s/server.js"]) };
const UNGIVABLE = { svc: server(["${HOME}/server.js"]) };  // hermes would expand ${...} in an argument
const NOTHING = { svc: { endpoint: NOWHERE, mcp: [{ name: "svc-mcp", command: "node", args: ["/s/server.js"] }] } };

function rig(services) {
  const fake = siblingCheckout("aify-wrapper", path.join("tests", "fake-hermes.mjs"));
  assert.ok(fake.dir, `no aify-wrapper checkout with tests/fake-hermes.mjs; looked in ${fake.looked.join(", ")} (set ${fake.variable})`);
  const dir = tmpDir("aify-hermes-session-");
  const home = path.join(dir, "home");
  fs.mkdirSync(home);
  const registry = path.join(dir, "services.json");
  fs.writeFileSync(registry, JSON.stringify({ version: 1, services }));
  const state = path.join(dir, "fake-state.json");
  const log = path.join(dir, "fake-calls.jsonl");
  fs.writeFileSync(state, "{}");
  fs.writeFileSync(log, "");
  const env = sealedChildEnv({
    HOME: home, USERPROFILE: home, LOCALAPPDATA: path.join(home, "AppData", "Local"), HERMES_HOME: path.join(home, ".hermes"),
    HERMES_RUNTIME_COMMAND: path.join(fake.dir, "tests", "fake-hermes.mjs"), FAKE_HERMES_STATE: state, FAKE_HERMES_LOG: log,
  });
  delete env.HERMES_DATA_DIR_SUFFIX;
  return {
    registry,
    run: (script = SCRIPT, ...flags) => spawnSync("bash", [script, ...flags, registry], { encoding: "utf8", env, timeout: 120_000 }),
    calls: () => fs.readFileSync(log, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line)),
    servers: () => JSON.parse(fs.readFileSync(state, "utf8")),
  };
}

test("THE WRITE gives hermes the opted-in server through its own config set, marked as aify-wrapper's", () => {
  const r = rig(OPTED_IN);
  const run = r.run();
  assert.equal(run.status, 0, run.stderr);
  assert.equal(r.servers()["svc-mcp"]?.["x-aify-owner"], "aify-wrapper", JSON.stringify(r.servers()));
  assert.ok(r.calls().some((args) => args[0] === "config" && args[1] === "set" && args[2] === "mcp_servers.svc-mcp"),
    `no config set for the server: ${JSON.stringify(r.calls())}`);
});

test("CONTROL: a registry with nothing opted in writes nothing", () => {
  const r = rig(NOTHING);
  const run = r.run();
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(r.servers(), {});
  assert.equal(r.calls().some((args) => args[1] === "set"), false, JSON.stringify(r.calls()));
});

test("--CHECK refuses what hermes could never be given, and starts no hermes", () => {
  const r = rig(UNGIVABLE);
  const run = r.run(SCRIPT, "--check");
  assert.equal(run.status, 78, `${run.status}: ${run.stderr}`);
  assert.match(run.stderr, /cannot be given to hermes/);
  assert.deepEqual(r.calls(), [], "the check ran hermes");
  // The case that tells a check from a write: a refusal stops both before hermes, a givable registry does not.
  const givable = rig(OPTED_IN);
  const control = givable.run(SCRIPT, "--check");
  assert.equal(control.status, 0, `CONTROL: a givable registry passes the check: ${control.stderr}`);
  assert.deepEqual(givable.calls(), [], "the check of a givable registry ran hermes, so it wrote");
  assert.deepEqual(givable.servers(), {});
});

test("A MISSING PINNED TOOL fails closed rather than reading as nothing opted in", () => {
  const r = rig(OPTED_IN);
  const lonely = path.join(tmpDir("aify-hermes-session-lonely-"), "scripts");
  fs.mkdirSync(lonely, { recursive: true });
  const copy = path.join(lonely, "hermes-session-servers.sh");
  fs.copyFileSync(SCRIPT, copy);
  const run = r.run(copy);
  assert.equal(run.status, 1, run.stderr);
  assert.match(run.stderr, /pinned hermes config tool .* is missing/);
  assert.deepEqual(r.calls(), []);
});

test("INSTALL.SH runs the check before hermes-aify is written: a refusal leaves no launcher", () => {
  const render = (services) => {
    const dir = tmpDir("aify-hermes-session-install-");
    const registry = path.join(dir, "services.json");
    fs.writeFileSync(registry, JSON.stringify({ version: 1, services }));
    const out = path.join(dir, "out");
    const result = spawnSync("bash", [INSTALL_SH, "--client", "hermes", NOWHERE, "--emit-wrappers", out],
      { encoding: "utf8", env: { ...RENDER_ENV, AIFY_SERVICE_REGISTRY: registry }, timeout: 180_000 });
    return { result, written: fs.existsSync(path.join(out, "hermes-aify")) };
  };
  const refused = render(UNGIVABLE);
  assert.equal(refused.result.status, 78, refused.result.stderr);
  assert.equal(refused.written, false, "the launcher was written before the refusal");
  const control = render(OPTED_IN);
  assert.equal(control.result.status, 0, control.result.stderr);
  assert.equal(control.written, true, "CONTROL: a givable registry renders the launcher");
});
