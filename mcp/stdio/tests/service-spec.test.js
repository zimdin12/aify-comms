#!/usr/bin/env node
// The registration spec comms serves (guided install S1), as the stamp builds it.
//
// Each property is asked of the real code:
// - the spec describes the entry the installer writes today;
// - its file-set manifest lists the COMMIT's bytes under mcp/stdio and nothing else, and the artifact's
//   sha256 is the hash of exactly that manifest;
// - it carries no value from the environment;
// - building it reads no credential file, although its env-var names come from a module that resolves a
//   credential when imported.
// A build that cannot bind a commit says so with a pending source.
//
// Every git repo, home and registry here is a temp one; children run on `sealedChildEnv`.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { manifestText, sha256 } from "../file-set-manifest.mjs";
import { commsServiceSpec, fixedFields, renderStdioMcp } from "../service-spec.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SPEC_CLI = path.join(HERE, "..", "service-spec-cli.mjs");
const REGISTER_CLI = path.join(HERE, "..", "register-service-cli.mjs");
const ENDPOINT_MODULE = path.join(HERE, "..", "aify-service-endpoint.mjs");

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

function runSpec(repo, revision, { version = "1.2.3", env = {}, nodeArgs = [] } = {}) {
  const out = path.join(repo, "spec.json");
  const done = spawnSync(process.execPath, [...nodeArgs, SPEC_CLI, repo, revision, version, out],
    { encoding: "utf8", timeout: 60_000, env: sealedChildEnv(env) });
  assert.equal(done.status, 0, `${done.stdout}\n${done.stderr}`);
  return { stamped: JSON.parse(fs.readFileSync(out, "utf8")), text: fs.readFileSync(out, "utf8") };
}

const FILES = {
  "mcp/stdio/server.js": "server\n",
  "mcp/stdio/claude-channel.js": "channel\r\nwith a CR\n",
  "mcp/stdio/package-lock.json": "{}\n",
  "mcp/stdio/tests/a.test.js": "a test is tracked, so it is listed\n",
  "service/app.py": "outside\n",
};

/** The manifest the plan defines (`sha256sum` lines, sorted), computed here from the bytes written. */
function expectedManifest(files) {
  return Object.entries(files)
    .filter(([rel]) => rel.startsWith("mcp/stdio/"))
    .map(([rel, text]) => [rel.slice("mcp/stdio/".length), createHash("sha256").update(Buffer.from(text)).digest("hex")])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([rel, hash]) => `${hash}  ${rel}\n`)
    .join("");
}

test("the spec describes exactly the entry the installer writes today", () => {
  withTempDir((dir) => {
    const registry = path.join(dir, "services.json");
    const bridgeDir = "/opt/aify/mcp/stdio";
    const done = spawnSync(process.execPath, [REGISTER_CLI, registry, "http://127.0.0.2:1", bridgeDir],
      { encoding: "utf8", timeout: 60_000, env: sealedChildEnv({ CREDENTIAL_REF: undefined }) });
    assert.equal(done.status, 0, done.stderr);
    const entry = JSON.parse(fs.readFileSync(registry, "utf8")).services["aify-comms"];
    const spec = commsServiceSpec({ version: "x", source: { pending: "test" } });
    assert.deepEqual(spec.fixed, fixedFields());
    for (const [field, value] of Object.entries(spec.fixed)) {
      assert.deepEqual(entry[field], value, `fixed.${field} differs from the installed entry`);
    }
    // The S1 render of the spec's mcp, written out here from the document rather than imported.
    const rendered = spec.mcp.map(({ name, entry: file }) => ({ name, command: "node", args: [`${bridgeDir}/${file}`] }));
    assert.deepEqual(entry.mcp, rendered);
    assert.deepEqual(renderStdioMcp(bridgeDir), rendered);
    assert.equal(rendered.length, 2, "control: both servers are in the comparison");
  });
});

test("manifestText is sha256sum lines sorted by path, and refuses a path that could escape", () => {
  const text = manifestText([{ path: "b.js", bytes: Buffer.from("b") }, { path: "a.js", bytes: Buffer.from("a") }]);
  assert.equal(text, `${sha256(Buffer.from("a"))}  a.js\n${sha256(Buffer.from("b"))}  b.js\n`);
  for (const bad of ["../x.js", "/abs.js", "a\\b.js", "a\nb.js", ""]) {
    assert.throws(() => manifestText([{ path: bad, bytes: Buffer.from("") }]), /unusable path/, JSON.stringify(bad));
  }
});

test("the manifest lists the commit's bytes under mcp/stdio, and the artifact's sha256 hashes exactly it", () => {
  withTempDir((repo) => {
    const head = seedRepo(repo, FILES);
    // An uncommitted edit, and an untracked file, must not reach a manifest bound to the commit.
    fs.writeFileSync(path.join(repo, "mcp/stdio/server.js"), "edited after the commit\n");
    fs.writeFileSync(path.join(repo, "mcp/stdio/untracked.js"), "never committed\n");
    const { stamped } = runSpec(repo, head);
    const [artifact] = stamped.spec.artifacts;
    const manifest = stamped.manifests["comms-bridge"];
    assert.equal(stamped.buildSha, head);
    assert.deepEqual(artifact.source, { git: { repo: "owner/repo", revision: head } });
    assert.equal(manifest, expectedManifest(FILES));
    assert.equal(artifact.sha256, createHash("sha256").update(Buffer.from(manifest, "utf8")).digest("hex"));
    assert.equal(artifact.manifest.path, "/.well-known/aify-service/artifacts/comms-bridge.manifest");
    for (const entry of artifact.entries) assert.match(manifest, new RegExp(`  ${entry}\\n`), `${entry} is not listed`);
    assert.notEqual(manifest, expectedManifest({ ...FILES, "mcp/stdio/server.js": "edited after the commit\n" }),
      "control: the working-tree edit would change the manifest");
  });
});

test("a build that cannot bind a commit serves a pending artifact with no hash and no manifest", () => {
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
      assert.equal("sha256" in artifact || "manifest" in artifact, false, name);
      assert.deepEqual(stamped.manifests, {}, name);
    });
  }
});

/** A fake home holding a registry that binds a credential to `endpoint`, as aify-env writes them. */
function plantCredential(dir, endpoint) {
  const home = path.join(dir, "home");
  const registry = path.join(home, ".aify", "services.json");
  const credential = path.join(home, ".aify", "credentials", "aify-comms.key");
  fs.mkdirSync(path.dirname(credential), { recursive: true });
  fs.writeFileSync(registry, JSON.stringify({ version: 1, services: { "aify-comms": {
    endpoint, credentialRef: "aify-comms.key", mcp: [] } } }));
  fs.writeFileSync(credential, "planted-stored-key-4411\n", { mode: 0o600 });
  return { home, registry, credential };
}

/**
 * A preload that records, in `<dir>/spy.log`, every path read or stat'ed through node:fs, and one
 * `loaded <pid>` line per process it runs in. It locates its log from its own URL, so it works in a
 * child given no environment.
 */
function spyPreload(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "spy.mjs");
  fs.writeFileSync(file, `
import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
const log = new URL("./spy.log", import.meta.url);
const append = fs.appendFileSync;
append(log, "loaded " + process.pid + "\\n");
for (const name of ["readFileSync", "lstatSync", "statSync"]) {
  const original = fs[name];
  fs[name] = function (p, ...rest) { append(log, String(p) + "\\n"); return original.call(this, p, ...rest); };
}
syncBuiltinESMExports();
`);
  const log = path.join(dir, "spy.log");
  return { preload: pathToFileURL(file).href, read: () => (fs.existsSync(log) ? fs.readFileSync(log, "utf8") : "") };
}

test("building the spec reads no registry or credential, wherever one is planted, though the names' owner would", () => {
  withTempDir((dir) => {
    const repo = path.join(dir, "repo");
    fs.mkdirSync(repo);
    const head = seedRepo(repo, FILES);
    const endpoint = "http://127.0.0.2:1";
    const { home, registry, credential } = plantCredential(dir, endpoint);
    // A matching registry everywhere the environment can point: the parent's AIFY_SERVICE_REGISTRY, the
    // home default, and the temp root (where the previous design chose its sealed path).
    const tempRoot = path.join(dir, "tmp");
    fs.mkdirSync(tempRoot);
    for (const planted of [path.join(tempRoot, "services.json"), path.join(tempRoot, "aify-service-spec-sealed-0", "services.json")]) {
      fs.mkdirSync(path.dirname(planted), { recursive: true });
      fs.copyFileSync(registry, planted);
    }
    const env = { HOME: home, USERPROFILE: home, AIFY_SERVER_URL: endpoint, AIFY_SERVICE_REGISTRY: registry,
      TMP: tempRoot, TEMP: tempRoot, TMPDIR: tempRoot };

    const cli = spyPreload(path.join(dir, "cli-spy"));
    runSpec(repo, head, { env, nodeArgs: ["--import", cli.preload] });
    const seen = cli.read();
    // The prohibited reads first, so a failure names what was read.
    assert.equal(seen.includes(registry), false, `the spec build read the registry:\n${seen}`);
    assert.equal(seen.includes(credential) || seen.includes(path.join(".aify", "credentials")), false,
      `the spec build touched a credential:\n${seen}`);
    assert.equal(seen.includes(tempRoot), false, `the spec build read under the temp root:\n${seen}`);
    // Then liveness: the spy ran in the CLI and in the child that loads the names' owner.
    assert.equal((seen.match(/^loaded \d+$/gm) || []).length, 2, `the spy did not run in both processes:\n${seen}`);

    // ADVERSE CONTROL: the owner imported in this same environment reads the planted registry and
    // reaches the credential, so the instrument above can see the reads it reports absent.
    const control = spyPreload(path.join(dir, "control-spy"));
    const done = spawnSync(process.execPath, ["--import", control.preload, "--input-type=module", "-e",
      `await import(${JSON.stringify(pathToFileURL(ENDPOINT_MODULE).href)});`],
    { encoding: "utf8", timeout: 60_000, env: sealedChildEnv(env) });
    assert.equal(done.status, 0, done.stderr);
    assert.equal(control.read().includes(registry), true, `control: no registry read:\n${control.read()}`);
    assert.equal(control.read().includes(credential), true, `control: the credential was not reached:\n${control.read()}`);
  });
});

test("no key, AIFY_* value or stored credential reaches the spec", () => {
  withTempDir((dir) => {
    const repo = path.join(dir, "repo");
    fs.mkdirSync(repo);
    const head = seedRepo(repo, FILES);
    const endpoint = "http://127.0.0.2:1";
    const { home, registry } = plantCredential(dir, endpoint);
    const planted = {
      AIFY_API_KEY: "planted-aify-key-91c2", CLAUDE_MCP_API_KEY: "planted-claude-key-0b7a",
      AIFY_AGENT_ID: "planted-agent-55d1", AIFY_SERVER_URL: endpoint,
      AIFY_SERVICE_REGISTRY: registry, HOME: home, USERPROFILE: home,
    };
    const { text } = runSpec(repo, head, { env: planted });
    for (const value of ["planted-stored-key-4411", "planted-aify-key-91c2", "planted-claude-key-0b7a",
      "planted-agent-55d1", "127.0.0.2"]) {
      assert.equal(text.includes(value), false, `${value} reached the spec`);
    }
    // Control: the same check finds a value that is there.
    assert.equal(runSpec(repo, head, { version: "planted-version-3d" }).text.includes("planted-version-3d"), true);
  });
});
