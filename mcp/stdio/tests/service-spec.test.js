#!/usr/bin/env node
// The registration spec comms serves (guided install S1), as the stamp builds it.
//
// Three properties, each asked of the real code: the spec describes the entry the installer writes
// today, its file-set hash binds the COMMIT's bytes under mcp/stdio and nothing else, and it carries no
// value from the environment. A build that cannot bind a commit says so with a pending source.
//
// Every git repo here is a temp repo; the CLI never reads the operator's checkout or registry.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { commsServiceSpec } from "../service-spec.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SPEC_CLI = path.join(HERE, "..", "service-spec-cli.mjs");
const REGISTER_CLI = path.join(HERE, "..", "register-service-cli.mjs");

function withTempDir(body) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-spec-"));
  try {
    return body(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function git(repo, ...args) {
  const done = spawnSync("git", ["-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
    "-c", "core.autocrlf=false", ...args], { encoding: "utf8" });
  assert.equal(done.status, 0, done.stderr);
  return done.stdout.trim();
}

/** A temp repo with files under mcp/stdio and one outside it, committed. Returns its HEAD. */
function seedRepo(repo, files, { origin = "https://github.com/owner/repo.git" } = {}) {
  git(repo, "init", "-q");
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(repo, rel)), { recursive: true });
    fs.writeFileSync(path.join(repo, rel), text);
  }
  if (origin) git(repo, "remote", "add", "origin", origin);
  git(repo, "add", "-A");
  git(repo, "commit", "-qm", "seed");
  return git(repo, "rev-parse", "HEAD");
}

function runSpec(repo, revision, { version = "1.2.3", env = {} } = {}) {
  const out = path.join(repo, "spec.json");
  const done = spawnSync(process.execPath, [SPEC_CLI, repo, revision, version, out],
    { encoding: "utf8", timeout: 60_000, env: { ...process.env, ...env } });
  assert.equal(done.status, 0, `${done.stdout}\n${done.stderr}`);
  return { stamped: JSON.parse(fs.readFileSync(out, "utf8")), text: fs.readFileSync(out, "utf8") };
}

const FILES = {
  "mcp/stdio/server.js": "server\n",
  "mcp/stdio/claude-channel.js": "channel\r\nwith a CR\n",
  "mcp/stdio/lib/a.mjs": "a\n",
  "service/app.py": "outside\n",
};

/** The manifest the plan defines, computed here from the bytes this test wrote. */
function expectedManifestSha(files) {
  const lines = Object.entries(files)
    .filter(([rel]) => rel.startsWith("mcp/stdio/"))
    .map(([rel, text]) => [rel.slice("mcp/stdio/".length), createHash("sha256").update(Buffer.from(text)).digest("hex")])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([rel, hash]) => `${hash}  ${rel}\n`)
    .join("");
  return createHash("sha256").update(lines).digest("hex");
}

test("the spec describes exactly the entry the installer writes today", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    const bridgeDir = "/opt/aify/mcp/stdio";
    const done = spawnSync(process.execPath, [REGISTER_CLI, registry, "http://127.0.0.2:1", bridgeDir],
      { encoding: "utf8", timeout: 60_000, env: { ...process.env, CREDENTIAL_REF: "" } });
    assert.equal(done.status, 0, done.stderr);
    const entry = JSON.parse(fs.readFileSync(registry, "utf8")).services["aify-comms"];
    const spec = commsServiceSpec({ version: "x", source: { pending: "test" } });
    for (const [field, value] of Object.entries(spec.fixed)) {
      assert.deepEqual(entry[field], value, `fixed.${field} differs from the installed entry`);
    }
    // The S1 render of the spec's mcp, written out here from the document rather than imported.
    const rendered = spec.mcp.map(({ name, entry: file }) => ({ name, command: "node", args: [`${bridgeDir}/${file}`] }));
    assert.deepEqual(entry.mcp, rendered);
    assert.equal(rendered.length, 2, "control: both servers are in the comparison");
  });
});

test("the file-set hash binds the commit's bytes under mcp/stdio, and only those", () => {
  withTempDir((repo) => {
    const head = seedRepo(repo, FILES);
    // An uncommitted edit, inside and outside mcp/stdio, must not reach a hash bound to the commit.
    fs.writeFileSync(path.join(repo, "mcp/stdio/server.js"), "edited after the commit\n");
    fs.writeFileSync(path.join(repo, "mcp/stdio/untracked.js"), "never committed\n");
    const { stamped } = runSpec(repo, head);
    const [artifact] = stamped.spec.artifacts;
    assert.equal(stamped.buildSha, head);
    assert.deepEqual(artifact.source, { git: { repo: "owner/repo", revision: head } });
    assert.equal(artifact.sha256, expectedManifestSha(FILES));
    assert.notEqual(artifact.sha256, expectedManifestSha({ ...FILES, "mcp/stdio/server.js": "edited after the commit\n" }),
      "control: the working-tree edit would change the hash");
  });
});

test("a build that cannot bind a commit serves a pending artifact with no hash", () => {
  const cases = {
    "no origin remote": { origin: null, revision: "head" },
    "an origin that is not GitHub": { origin: "https://gitlab.com/o/r.git", revision: "head" },
    "a revision the checkout lacks": { origin: undefined, revision: "e".repeat(40) },
  };
  for (const [name, { origin, revision }] of Object.entries(cases)) {
    withTempDir((repo) => {
      const head = seedRepo(repo, FILES, origin === undefined ? {} : { origin });
      const { stamped } = runSpec(repo, revision === "head" ? head : revision);
      const [artifact] = stamped.spec.artifacts;
      assert.equal(typeof artifact.source.pending, "string", name);
      assert.deepEqual(Object.keys(artifact.source), ["pending"], name);
      assert.equal("sha256" in artifact, false, name);
    });
  }
});

test("no key, AIFY_* value or stored credential reaches the spec", () => {
  withTempDir((repo) => {
    const head = seedRepo(repo, FILES);
    const home = path.join(repo, "home");
    fs.mkdirSync(path.join(home, ".aify", "credentials"), { recursive: true });
    fs.writeFileSync(path.join(home, ".aify", "credentials", "aify-comms.key"), "planted-stored-key-4411\n");
    const planted = {
      AIFY_API_KEY: "planted-aify-key-91c2", CLAUDE_MCP_API_KEY: "planted-claude-key-0b7a",
      AIFY_AGENT_ID: "planted-agent-55d1", AIFY_SERVER_URL: "http://planted-endpoint-7c:1",
      HOME: home, USERPROFILE: home,
    };
    const { text } = runSpec(repo, head, { env: planted });
    for (const value of ["planted-stored-key-4411", "planted-aify-key-91c2", "planted-claude-key-0b7a",
      "planted-agent-55d1", "planted-endpoint-7c"]) {
      assert.equal(text.includes(value), false, `${value} reached the spec`);
    }
    // Control: the same check finds a value that is there.
    assert.equal(runSpec(repo, head, { version: "planted-version-3d" }).text.includes("planted-version-3d"), true);
  });
});
