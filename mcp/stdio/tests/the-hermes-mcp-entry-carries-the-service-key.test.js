#!/usr/bin/env node
// Hermes' MCP entry must carry the API key, and the installer must actually pass it.
//
// THE DEFECT THIS PINS. Until 2026-08-30 the aify-comms entry written into hermes' `config.yaml`
// carried the service URL and no key. Measured with controls: `AIFY_API_KEY` appeared 0 times in
// the emitting function against 3 for `AIFY_SERVER_URL` (instrument works) and 0 for a string
// known absent (instrument can say no). `install_opencode_config` and `install_pi_config` both
// pass the key, and both of those installs are DISABLED -- the one runtime the fleet actually runs
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
// last test proves the INSTALLER passes a key to it. A module that is perfect and a call site that
// hands it nothing is a green suite over a dead feature, which this repo has shipped before.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  aifyEntryLines,
  configWithAifyEntry,
  patchHermesConfigFile,
  API_KEY_ENV_NAMES,
  FORWARDED_ENV_NAMES,
} from "../../../scripts/hermes-mcp-config.mjs";
import { tmpDir } from "./_tmpdir.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const SERVER_PATH = "/native/base/mcp/stdio/server.js";
const URL_ = "http://127.0.0.1:8899";

const entry = (apiKey) =>
  aifyEntryLines({ serverPath: SERVER_PATH, serverUrl: URL_, apiKey }).join("\n");

test("with no key configured, the entry carries no key line at all", () => {
  const text = entry("");
  for (const name of API_KEY_ENV_NAMES) {
    assert.ok(!text.includes(name), `${name} must be absent when there is no key`);
  }
  // POSITIVE CONTROL for the assertion above: the same search DOES find what is really there,
  // so the absences are real absences and not a broken search.
  assert.match(text, /AIFY_SERVER_URL: "http:\/\/127\.0\.0\.1:8899"/);
});

test("with a key configured, every key env name is emitted with that exact value", () => {
  const text = entry("sk-live-9c1f");
  for (const name of API_KEY_ENV_NAMES) {
    assert.ok(
      text.includes(`      ${name}: "sk-live-9c1f"`),
      `${name} must carry the literal key`,
    );
  }
});

test("the key is a LITERAL, never a ${VAR} hermes would resolve to nothing", () => {
  // Hermes filters env to _SAFE_ENV_KEYS and resolves ${VAR} from its own env, inherited from the
  // hermes-aify wrapper -- which does not export the key. An interpolation here could only ever
  // resolve to empty, turning a missing credential into a wrong one.
  const text = entry("sk-live-9c1f");
  for (const name of API_KEY_ENV_NAMES) {
    assert.ok(!text.includes(`${name}: "\${${name}}"`), `${name} must not be an interpolation`);
  }
  // Control: the forwarded names ARE interpolations, so this test can tell the two shapes apart.
  assert.ok(text.includes('AIFY_AGENT_ID: "${AIFY_AGENT_ID}"'));
});

test("the URL keeps its ${VAR} fallback when no URL was given, because the wrapper exports it", () => {
  const text = aifyEntryLines({ serverPath: SERVER_PATH, serverUrl: "", apiKey: "" }).join("\n");
  assert.ok(text.includes('AIFY_SERVER_URL: "${AIFY_SERVER_URL}"'));
  assert.ok(text.includes('CLAUDE_MCP_SERVER_URL: "${CLAUDE_MCP_SERVER_URL}"'));
});

test("every forwarded env name reaches the entry", () => {
  const text = entry("");
  for (const name of FORWARDED_ENV_NAMES) {
    assert.ok(text.includes(`      ${name}: "\${${name}}"`), `${name} missing`);
  }
});

test("an existing aify-comms entry is REPLACED, never duplicated, and neighbours survive", () => {
  const before = [
    "mcp_servers:",
    "  aify-comms:",
    "    command: node",
    "    env:",
    '      AIFY_SERVER_URL: "http://stale"',
    "  keepme:",
    "    command: bar",
    "",
  ].join("\n");
  const after = configWithAifyEntry(before, {
    serverPath: SERVER_PATH, serverUrl: URL_, apiKey: "sk-new",
  });
  assert.equal((after.match(/^ {2}aify-comms:$/gm) || []).length, 1, "exactly one entry");
  assert.ok(!after.includes("http://stale"), "the stale env block is gone");
  assert.ok(after.includes("  keepme:"), "an unrelated server is untouched");
  assert.ok(after.includes('AIFY_API_KEY: "sk-new"'));
});

test("patching twice is idempotent", () => {
  const dir = tmpDir("aify-hermes-cfg-");
  try {
    const file = path.join(dir, "config.yaml");
    fs.writeFileSync(file, "model: gpt-5\n");
    const opts = { serverPath: SERVER_PATH, serverUrl: URL_, apiKey: "sk-two" };
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

test("THE INSTALLER ACTUALLY PASSES A KEY -- the call site, not just the module", () => {
  // The failure this guards is a module that builds a perfect entry while install.sh hands it
  // three arguments and no key. Asserting the module alone would stay green through exactly that.
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
  assert.match(invocation, /"\$node_config_file"/, "the config file is argument 1");
  assert.match(invocation, /"\$node_server_path"/, "the bridge path is argument 2");
  assert.match(invocation, /"\$SERVER_URL"/, "the endpoint is argument 3");
  assert.match(invocation, /"\$api_key"/, "THE KEY is argument 4 -- the whole point");

  // And the variable is populated from the one resolver, not left empty.
  assert.match(installer, /api_key="\$\(aify_api_key\)"/);
});

test("the config module's own path is CONVERTED for a native-Windows node", () => {
  // REGRESSION, and it cost a whole client. `MSYS_NO_PATHCONV=1` is set on this invocation to protect
  // its ARGUMENTS -- two are already drive-letter paths a second conversion would mangle -- and it
  // also suppressed conversion of the script's own path. Native node.exe was handed a literal
  // `/c/Docker/...`, resolved it against the drive root, and died:
  //   Cannot find module 'C:\c\Docker\aify-comms\scripts\hermes-mcp-config.mjs'
  // Introduced 2026-08-30 by 9a2cfdca and caught by a redeploy the next day. Every hermes install in
  // between registered NO MCP server, and the installer reported a whole-client failure rather than
  // the one step that broke -- so the symptom pointed nowhere near the cause.
  //
  // THE GUARD IS `is_git_bash_windows`, not `hermes_runtime_is_native_windows` as the two call sites
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
