#!/usr/bin/env node
// Hermes' MCP entry must carry the API key, and the installer must actually pass it.
//
// THE DEFECT THIS PINS. Until 2026-08-30 the aify-comms entry written into hermes' `config.yaml`
// carried the service URL and no key. Measured with controls: `AIFY_API_KEY` appeared 0 times in
// the emitting function against 3 for `AIFY_SERVER_URL` (instrument works) and 0 for a string
// known absent (instrument can say no). The opencode and pi config writers
// passed the key, and both of those installs were DISABLED -- the one runtime the fleet actually runs
// on was the one without it. Consequence: setting `API_KEY` would 401 hermes on every call, with
// nothing naming the cause, so the documented way to secure the service broke the fleet instead.
//
// WHY IT SURVIVED. The logic was ~90 lines of JavaScript inside a single-quoted bash string in
// install.sh. Every hermes install test is, by its own docstring, an "install.sh static-text smoke
// check (no bash invocation)" -- a grep of the installer source, which proves a line was WRITTEN
// and never that the emitted config contains it. Extracting the logic to
// `scripts/hermes-mcp-config.mjs` is what makes the assertions below possible at all.
//
// BOTH HALVES ARE TESTED ON PURPOSE. Running the module proves the entry is built correctly; the
// installer test proves the INSTALLER passes a key to it. A module that is perfect and a call site that
// hands it nothing is a green suite over a dead feature, which this repo has shipped before.
//
// 0.9.11: the entry is the service's /mcp/http, the agent as hermes' `${AIFY_AGENT_ID}`. The stdio child an
// older install wrote is replaced whole.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  aifyEntryLines,
  configWithAifyEntry,
  patchHermesConfigFile,
} from "../../../scripts/hermes-mcp-config.mjs";
import { tmpDir } from "./_tmpdir.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const URL_ = "http://127.0.0.1:8899";

const entry = (apiKey) => aifyEntryLines({ serverUrl: URL_, apiKey }).join("\n");

test("the entry is the service's /mcp/http, the agent as hermes' ${AIFY_AGENT_ID}, the key literal", () => {
  assert.deepEqual(aifyEntryLines({ serverUrl: `${URL_}/`, apiKey: "sk-live-9c1f" }), [
    "  aify-comms:",
    `    url: "${URL_}/mcp/http"`,
    "    headers:",
    '      X-Aify-Agent-Id: "${AIFY_AGENT_ID}"',
    '      X-API-Key: "sk-live-9c1f"',
  ]);
});

test("with no key configured, the entry carries no key line at all", () => {
  const text = entry("");
  assert.ok(!text.includes("X-API-Key"), "the key header must be absent when there is no key");
  // POSITIVE CONTROL: the same search DOES find what is really there, so the absence is real.
  assert.ok(entry("k").includes("X-API-Key"));
});

test("the key is a LITERAL, never a ${VAR} hermes would resolve to nothing", () => {
  // The launchers do not export the key, so an interpolation could only ever resolve to empty,
  // turning a missing credential into a wrong one.
  const text = entry("sk-live-9c1f");
  assert.ok(text.includes('X-API-Key: "sk-live-9c1f"'));
  assert.ok(!/X-API-Key: "\$\{/.test(text), "the key must not be an interpolation");
  // Control: the agent IS an interpolation, so this test can tell the two shapes apart.
  assert.ok(text.includes('X-Aify-Agent-Id: "${AIFY_AGENT_ID}"'));
});

test("the URL keeps its ${VAR} fallback when no URL was given, because the wrapper exports it", () => {
  assert.ok(aifyEntryLines({ serverUrl: "", apiKey: "" }).includes('    url: "${AIFY_SERVER_URL}/mcp/http"'));
});

test("an older install's stdio entry is REPLACED whole, never duplicated, and neighbours survive", () => {
  const before = [
    "mcp_servers:",
    "  aify-comms:",
    '    command: "node"',
    "    args:",
    '      - "/native/base/mcp/stdio/server.js"',
    "    env:",
    '      TEMP: "/tmp"',
    '      AIFY_SERVER_URL: "http://stale"',
    "  keepme:",
    "    command: bar",
    "",
  ].join("\n");
  const after = configWithAifyEntry(before, { serverUrl: URL_, apiKey: "sk-new" });
  assert.equal(after, `mcp_servers:\n  aify-comms:\n    url: "${URL_}/mcp/http"\n    headers:\n      X-Aify-Agent-Id: "\${AIFY_AGENT_ID}"\n      X-API-Key: "sk-new"\n  keepme:\n    command: bar\n`);
});

test("patching twice is idempotent", () => {
  const dir = tmpDir("aify-hermes-cfg-");
  try {
    const file = path.join(dir, "config.yaml");
    fs.writeFileSync(file, "model: gpt-5\n");
    const opts = { serverUrl: URL_, apiKey: "sk-two" };
    patchHermesConfigFile(file, opts);
    const once = fs.readFileSync(file, "utf8");
    patchHermesConfigFile(file, opts);
    const twice = fs.readFileSync(file, "utf8");
    assert.equal(once, twice, "a second patch must change nothing");
    assert.equal((twice.match(/^ {2}aify-comms:$/gm) || []).length, 1);
    assert.ok(twice.includes("model: gpt-5"), "pre-existing config survives");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// The layout of the operator's hermes config (2026-09-28): `plugins.entries.aify-comms` is hermes' own
// plugin setting, and a two-space MCP block under `plugins:` is what earlier installs left there. The
// old patcher replaced the FIRST indented `aify-comms:` -- the plugin setting -- so each install
// destroyed it and added a second `plugins.aify-comms`, and hermes refused the file as a duplicate key.
const HERMES_LIKE = [
  "model: gpt-5",
  "plugins:",
  "  enabled:",
  "    - aify-comms",
  "  disabled: []",
  "  entries:",
  "    aify-comms:",
  "      allow_tool_override: false",
  "  aify-comms:",
  "    command: node",
  "    env:",
  "      AIFY_SERVER_URL: http://stale-in-plugins",
  "hooks:",
  "  post_tool_call: []",
  // Another section may name aify-comms for its own reasons; it carries no command and is not ours.
  "known_plugin_toolsets:",
  "  aify-comms:",
  "    - comms_send",
  "mcp_servers:",
  "  aify-project-graph:",
  "    command: node",
  "  aify-comms:",
  "    command: node",
  "    env:",
  "      AIFY_SERVER_URL: http://stale-in-mcp",
  "platform_toolsets: {}",
  "",
].join("\n");

const blocksUnder = (text, section) => {
  const lines = text.split("\n");
  let current = "";
  return lines.filter((line) => {
    if (/^[^\s#-][^:]*:/.test(line)) current = line.split(":")[0];
    return current === section && /^ {2}aify-comms:$/.test(line);
  }).length;
};

test("the entry is written under mcp_servers only, and hermes' own plugin setting survives", () => {
  const after = configWithAifyEntry(HERMES_LIKE, { serverUrl: URL_, apiKey: "sk-new" });
  assert.equal(blocksUnder(after, "mcp_servers"), 1, "exactly one entry under mcp_servers");
  assert.equal(blocksUnder(after, "plugins"), 0, "the MCP block left under plugins is removed");
  assert.match(after, /^ {2}entries:\n {4}aify-comms:\n {6}allow_tool_override: false$/m,
               "plugins.entries.aify-comms is hermes' plugin setting and must be untouched");
  assert.ok(!after.includes("stale-in-mcp") && !after.includes("stale-in-plugins"));
  assert.ok(after.includes("  aify-project-graph:") && after.includes("platform_toolsets: {}"), "neighbours survive");
  assert.match(after, /^known_plugin_toolsets:\n {2}aify-comms:\n {4}- comms_send$/m, "another section's aify-comms key is not ours to replace");
  assert.ok(after.includes('X-API-Key: "sk-new"'));
  assert.equal(configWithAifyEntry(after, { serverUrl: URL_, apiKey: "sk-new" }), after, "idempotent");
});

test("with only the plugin setting present, the entry is inserted under mcp_servers, not over it", () => {
  const withoutEntry = HERMES_LIKE.replace(/  aify-comms:\n    command: node\n    env:\n      AIFY_SERVER_URL: http:\/\/stale-in-plugins\n/, "")
    .replace(/  aify-comms:\n    command: node\n    env:\n      AIFY_SERVER_URL: http:\/\/stale-in-mcp\n/, "");
  assert.equal(blocksUnder(withoutEntry, "plugins") + blocksUnder(withoutEntry, "mcp_servers"), 0, "fixture control");
  const after = configWithAifyEntry(withoutEntry, { serverUrl: URL_, apiKey: "" });
  assert.equal(blocksUnder(after, "mcp_servers"), 1);
  assert.equal(blocksUnder(after, "plugins"), 0);
  assert.match(after, /^ {4}aify-comms:\n {6}allow_tool_override: false$/m);
});

test("THE INSTALLER ACTUALLY PASSES A KEY -- the call site, not just the module", () => {
  // The failure this guards is a module that builds a perfect entry while install.sh hands it
  // no key. Asserting the module alone would stay green through exactly that.
  const installer = fs.readFileSync(path.join(REPO, "install.sh"), "utf8");
  const lines = installer.split("\n");
  // Match the INVOCATION, not the comment above it that names the same file -- the first
  // version of this test matched the comment and passed while proving nothing.
  //
  // THE PATH IS RESOLVED INTO A VARIABLE FIRST, so the invocation names the variable rather than the
  // file. Follow the indirection instead of loosening the match: asserting on the assignment alone
  // would pass while nothing ran it, which is the exact failure the title of this test warns about.
  const assignedAt = lines.findIndex((l) => /^\s*local\s+\w+="[^"]*scripts\/hermes-mcp-config\.mjs"/.test(l));
  assert.ok(assignedAt >= 0, "install.sh must resolve the hermes config module path");
  const varName = /^\s*local\s+(\w+)=/.exec(lines[assignedAt])[1];
  const at = lines.findIndex((l) => new RegExp(`node "\\$${varName}"`).test(l));
  assert.ok(at >= 0, "install.sh must invoke the hermes config module through that resolved path");

  // The invocation continues onto the next line via a trailing backslash; read both.
  const invocation = lines.slice(at, at + 2).join(" ");
  assert.match(invocation, /"\$node_config_file" "\$SERVER_URL" "\$api_key"/,
               "the config file, the endpoint and THE KEY, in the module's argument order");

  // And the variable is populated from the one resolver, not left empty.
  assert.match(installer, /api_key="\$\(aify_api_key\)"/);
});

test("the config module's own path is CONVERTED for a native-Windows node", () => {
  // REGRESSION, and it cost a whole client. `MSYS_NO_PATHCONV=1` is set on this invocation to protect
  // its ARGUMENTS -- the config path is already a drive-letter path a second conversion would mangle --
  // and it also suppressed conversion of the script's own path. Native node.exe was handed a literal
  // `/c/Docker/...`, resolved it against the drive root, and died:
  //   Cannot find module 'C:\c\Docker\aify-comms\scripts\hermes-mcp-config.mjs'
  // Introduced 2026-08-30 by 9a2cfdca and caught by a redeploy the next day. Every hermes install in
  // between registered NO MCP server, and the installer reported a whole-client failure rather than
  // the one step that broke -- so the symptom pointed nowhere near the cause.
  //
  // THE GUARD IS `is_git_bash_windows`, not `hermes_runtime_is_native_windows` as the call sites
  // above it use, and that difference is load-bearing: those convert paths a native HERMES reads
  // later, while this one is consumed by the installer's OWN node, which under WSL is Linux node and
  // must keep a POSIX path. A reader tidying the inconsistency away breaks WSL installs.
  const installer = fs.readFileSync(path.join(REPO, "install.sh"), "utf8");
  const lines = installer.split("\n");
  const assignedAt = lines.findIndex((l) => /^\s*local\s+\w+="[^"]*scripts\/hermes-mcp-config\.mjs"/.test(l));
  assert.ok(assignedAt >= 0);
  const varName = /^\s*local\s+(\w+)=/.exec(lines[assignedAt])[1];

  // The conversion sits between the assignment and the invocation, and it converts THAT variable.
  const invokedAt = lines.findIndex((l) => new RegExp(`node "\\$${varName}"`).test(l));
  const between = lines.slice(assignedAt + 1, invokedAt).join("\n");
  assert.match(between, new RegExp(`${varName}="\\$\\(cygpath -w "\\$${varName}"\\)"`),
               "the module path is never converted, so native node gets a POSIX path it cannot open");
  assert.match(between, /is_git_bash_windows/,
               "the conversion is unguarded, which would hand Linux node a Windows path under WSL");
});

// The guided chain installs assets with no key (aify-env holds it), so the CLI is handed an empty one. The entry is
// where hermes reads the key, so the CLI resolves it the way every comms process does: the credential aify-env holds
// for exactly this endpoint (review of switch 1ed207d2). Control: a credential bound to another endpoint is not used.
for (const [bound, expected] of [["http://127.0.0.1:8899", "fixture-held-key"], ["http://127.0.0.1:9999", null]]) {
  test(`an install with no key gives hermes the credential aify-env holds for ${bound === URL_ ? "this" : "another"} endpoint`, async () => {
    const { execFileSync, spawnSync } = await import("node:child_process");
    const { sealedChildEnv } = await import("./_child-env.mjs");
    const home = tmpDir("aify-hermes-held-key-");
    try {
      const credentials = path.join(home, ".aify", "credentials");
      fs.mkdirSync(credentials, { recursive: true });
      const credential = path.join(credentials, "aify-comms-fixture.key");
      fs.writeFileSync(credential, "fixture-held-key\n", { mode: 0o600 });
      if (process.platform === "win32") {
        execFileSync("icacls", [credential, "/inheritance:r", "/grant:r", `${process.env.USERNAME}:(F)`], { stdio: "ignore" });
      }
      const registry = path.join(home, ".aify", "services.json");
      fs.writeFileSync(registry, JSON.stringify({ services: { "aify-comms": { endpoint: bound, credentialRef: "aify-comms-fixture.key" } } }));
      const config = path.join(home, "config.yaml");
      const run = spawnSync(process.execPath, [path.join(REPO, "scripts", "hermes-mcp-config.mjs"), config, URL_, ""], {
        encoding: "utf8", timeout: 30_000,
        env: sealedChildEnv({ HOME: home, USERPROFILE: home, AIFY_SERVICE_REGISTRY: registry }),
      });
      assert.equal(run.status, 0, run.stderr);
      const text = fs.readFileSync(config, "utf8");
      assert.match(text, /X-Aify-Agent-Id/, "CONTROL: the entry was written");
      if (expected) assert.match(text, new RegExp(`X-API-Key: "${expected}"`));
      else assert.doesNotMatch(text, /X-API-Key/, "a credential for another endpoint must not be sent here");
    } finally {
      fs.rmSync(home, { recursive: true, force: true });
    }
  });
}
