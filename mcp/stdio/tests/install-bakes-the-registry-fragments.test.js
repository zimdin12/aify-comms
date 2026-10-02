#!/usr/bin/env node
// install.sh bakes the registry's MCP fragments into the launchers it renders, and stops on a registry
// the pinned aify-wrapper refuses (scripts/registry-fragment.sh).
//
// Until 0.8 it substituted an empty strict fragment unconditionally (KNOWN_ISSUES: strict MCP gap), and
// the default-mode session fragment, added by aify-wrapper 1498037, had no value at all. A fragment
// rendered empty reads exactly like a registry with nothing opted in, so the refusal must stop the install.
//
// Rendered with `--emit-wrappers` against a temporary registry and this checkout as the native base;
// nothing is installed and no launcher runs.

import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { tmpDir } from "./_tmpdir.js";
import { INSTALL_SH, RENDER_ENV } from "./wrapper-harness.mjs";

const NOWHERE = "http://127.0.0.2:1";
const COMMS = { "aify-comms": { endpoint: NOWHERE, endpointEnv: ["AIFY_SERVER_URL"], mcp: [{ name: "aify-comms", command: "node", args: ["/b/server.js"] }] } };
const DASHBOARD = { endpoint: "http://127.0.0.2:9700", credentialRef: "aify-dashboard.key", sessionInject: { mcp: true },
  mcp: [{ name: "aify-dashboard", command: "node", args: ["--experimental-strip-types", "/d/bridge/main.ts", "serve"] }] };

function render(registryText) {
  const dir = tmpDir("aify-fragments-");
  const registry = path.join(dir, "services.json");
  fs.writeFileSync(registry, registryText);
  const out = path.join(dir, "out");
  const result = spawnSync("bash", [INSTALL_SH, "--client", "claude", NOWHERE, "--emit-wrappers", out],
    { encoding: "utf8", env: { ...RENDER_ENV, AIFY_SERVICE_REGISTRY: registry }, timeout: 180_000 });
  const launcher = path.join(out, "claude-aify");
  return { result, text: fs.existsSync(launcher) ? fs.readFileSync(launcher, "utf8") : null };
}

/** The session fragment a rendered launcher carries: its one decode into the per-session config file. */
const sessionFragment = (text) => {
  const sites = [...text.matchAll(/printf '%s' "([^"]*)" \| base64 -d > "\$AIFY_MCP_CONFIG"/g)];
  assert.equal(sites.length, 1, "the extractor must find exactly the one decode site");
  return sites[0][1];
};

test("AN OPTED-IN SERVICE is baked into the launcher as the session fragment the pinned package computes", () => {
  const { result, text } = render(JSON.stringify({ version: 1, services: { ...COMMS, "aify-dashboard": DASHBOARD } }));
  assert.equal(result.status, 0, result.stderr);
  const baked = sessionFragment(text);
  assert.ok(baked, "no session fragment found in the rendered launcher");
  const doc = JSON.parse(Buffer.from(baked, "base64").toString("utf8"));
  assert.deepEqual(Object.keys(doc.mcpServers), ["aify-dashboard"], "the opted-in service, and only it");
  assert.equal(text.includes("@@"), false, "a placeholder survived");
});

test("CONTROL: a registry with nothing opted in bakes an empty fragment", () => {
  const { result, text } = render(JSON.stringify({ version: 1, services: COMMS }));
  assert.equal(result.status, 0, result.stderr);
  assert.equal(sessionFragment(text), "");
});

test("THE CODEX FRAGMENT is the third field, as the pinned package computes it, and an empty field stays a field", () => {
  // scripts/registry-fragment.sh prints "<strict>|<session>|<codex>"; install.sh reads the three by position. No
  // template carries @@SESSION_MCP_CODEX_B64@@ yet, so the field is read here, from the script itself.
  const fields = (registryText) => {
    const dir = tmpDir("aify-fragments-");
    const registry = path.join(dir, "services.json");
    fs.writeFileSync(registry, registryText);
    const run = spawnSync("bash", [path.join(path.dirname(INSTALL_SH), "scripts", "registry-fragment.sh"), registry],
      { encoding: "utf8", env: RENDER_ENV, timeout: 120_000 });
    return { run, fields: run.stdout.split("|") };
  };
  const opted = fields(JSON.stringify({ version: 1, services: { ...COMMS, "aify-dashboard": DASHBOARD } }));
  assert.equal(opted.run.status, 0, opted.run.stderr);
  assert.equal(opted.fields.length, 3, opted.run.stdout);
  const words = Buffer.from(opted.fields[2], "base64").toString("utf8").split(String.fromCharCode(0));
  assert.ok(words.some((w) => w.includes("aify-dashboard")), `the opted-in service is not in codex's words: ${words}`);
  assert.equal(opted.fields[0], "", "nothing set strictMcp, so the strict field is empty and still in its place");
  assert.ok(opted.fields[1], "the claude session fragment is still the second field");
  const none = fields(JSON.stringify({ version: 1, services: COMMS }));
  assert.equal(none.run.stdout, "||", "CONTROL: nothing opted in, three empty fields");
  const keyed = fields(JSON.stringify({ version: 1, services: { "aify-comms": { ...COMMS["aify-comms"], keyEnv: ["SYNTHETIC_KEY"] },
    "aify-dashboard": { ...DASHBOARD, endpointEnv: ["SYNTHETIC_KEY"] } } }));
  assert.equal(keyed.run.status, 78, "a registry handing a neighbour's key to an opted-in service stops the install");
  // A refusal only the codex verb makes: the registry parses, and both claude fragments compute, but a service
  // keeping its key in AIFY_AGENT_ID would have it forwarded to every codex MCP server.
  const agentKey = fields(JSON.stringify({ version: 1, services: { "aify-comms": { ...COMMS["aify-comms"], keyEnv: ["AIFY_AGENT_ID"] },
    "aify-dashboard": DASHBOARD } }));
  assert.equal(agentKey.run.status, 78, `the codex verb's own refusal stops the install too: ${agentKey.run.stdout}`);
  assert.match(agentKey.run.stderr, /cannot be given to codex/);
});

test("A REGISTRY THE PACKAGE REFUSES stops the install, and no launcher is written", () => {
  const { result, text } = render("{not json");
  assert.equal(result.status, 78, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /is not usable/);
  assert.equal(text, null);
});
