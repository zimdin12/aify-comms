#!/usr/bin/env node
// Write the registration spec for one commit, bound to that commit (guided install S1).
//
//   service-spec-cli.mjs <repo-root> <revision> <version> <out-file>
//
// Run by `scripts/stamp.sh` beside the build stamp. It writes `{buildSha, spec, manifests}`; the service
// serves the spec and each manifest only while `buildSha` equals the build it is running, so a spec left
// over from another build is never served.
//
// THE MANIFEST IS THE COMMIT'S, not the working tree's: each file's bytes come from git's object store
// at <revision>, so an uncommitted edit, or a checkout's line-ending conversion, never reaches it.
// When the commit cannot be bound (no GitHub origin, the revision is absent), the artifact is
// `{pending: <reason>}` and carries no hash and no manifest.
//
// NO CREDENTIAL IS READ, and no path is chosen to make that so. The spec takes its env-var names from
// `aify-service-endpoint.mjs`, which resolves the service key when it is imported, through the registry
// and the credential bound to the endpoint its environment names. This process never imports it. The
// document is built in a CHILD whose environment holds no variable except SystemRoot (which node needs
// on Windows): with no endpoint named, the resolver returns before it reads any file
// (registry-credential.mjs, "NO ENDPOINT, NO KEY"), and no key variable exists to be read either.

import { spawnSync } from "node:child_process";
import { renameSync, writeFileSync } from "node:fs";

import { manifestText } from "./file-set-manifest.mjs";

const STDIO_DIR = "mcp/stdio";
const SPEC_MODULE = new URL("./service-spec.mjs", import.meta.url).href;
//: The child reads its inputs on stdin and prints the document; it is handed no environment to resolve from.
const BUILD_IN_CHILD = `
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
import(${JSON.stringify(SPEC_MODULE)}).then(({ commsServiceSpec }) => {
  process.stdout.write(JSON.stringify(commsServiceSpec(input)));
});`;

/** The document, built where no endpoint, key or registry can be named. */
function buildSpecInChild(input) {
  const env = process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {};
  const done = spawnSync(process.execPath, [...process.execArgv, "-e", BUILD_IN_CHILD],
    { input: JSON.stringify(input), env, encoding: "utf8", maxBuffer: 1 << 26 });
  if (done.status !== 0) throw new Error(`building the spec failed: ${String(done.stderr).trim().slice(0, 500)}`);
  return JSON.parse(done.stdout);
}
const EXIT_USAGE = 2;

function git(repoRoot, args, input) {
  const done = spawnSync("git", ["-C", repoRoot, ...args], { input, maxBuffer: 1 << 30 });
  if (done.status !== 0) {
    throw new Error(`git ${args[0]} failed: ${String(done.stderr || "").trim().slice(0, 300)}`);
  }
  return done.stdout;
}

/** `owner/repo` of a GitHub origin, or null. */
function githubRepo(url) {
  const match = /^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([\w.-]+\/[\w.-]+?)(?:\.git)?\/?$/
    .exec(String(url || "").trim());
  return match ? match[1] : null;
}

/** Every file under mcp/stdio at `revision`, as {path relative to mcp/stdio, bytes}. */
function committedFiles(repoRoot, revision) {
  const listing = git(repoRoot, ["ls-tree", "-r", "-z", "--full-tree", revision, "--", STDIO_DIR]).toString("utf8");
  const items = listing.split("\0").filter(Boolean).map((row) => {
    const [meta, path] = row.split("\t");
    const [, type, object] = meta.split(" ");
    if (type !== "blob") throw new Error(`${path} is a ${type}, not a file`);
    return { object, path: path.slice(STDIO_DIR.length + 1) };
  });
  if (!items.length) throw new Error(`${STDIO_DIR} is empty at ${revision}`);
  const out = git(repoRoot, ["cat-file", "--batch"], items.map(({ object }) => `${object}\n`).join(""));
  let at = 0;
  return items.map(({ object, path }) => {
    const headerEnd = out.indexOf(0x0a, at);
    const [got, , size] = out.subarray(at, headerEnd).toString("utf8").split(" ");
    if (got !== object) throw new Error(`cat-file answered ${got} for ${object}`);
    const start = headerEnd + 1;
    const bytes = out.subarray(start, start + Number(size));
    at = start + Number(size) + 1;
    return { path, bytes };
  });
}

/** {source, manifest} for `revision`, or a pending source naming why it could not be bound. */
function artifactSource(repoRoot, revision) {
  let origin = "";
  try {
    origin = git(repoRoot, ["remote", "get-url", "origin"]).toString("utf8");
  } catch {
    return { source: { pending: "the build checkout has no origin remote" } };
  }
  const repo = githubRepo(origin);
  if (!repo) return { source: { pending: "the build checkout's origin is not a GitHub repository" } };
  try {
    return { source: { git: { repo, revision } }, manifest: manifestText(committedFiles(repoRoot, revision)) };
  } catch (error) {
    return { source: { pending: `the bridge files at ${revision} could not be read: ${error.message}` } };
  }
}

const [, , repoRoot, revision, version, outFile] = process.argv;
if (!repoRoot || !/^[0-9a-f]{40}$/.test(revision || "") || !version || !outFile) {
  process.stderr.write("usage: service-spec-cli.mjs <repo-root> <40-hex revision> <version> <out-file>\n");
  process.exit(EXIT_USAGE);
}
const { source, manifest } = artifactSource(repoRoot, revision);
const spec = buildSpecInChild({ version, source, manifest });
const manifests = source.pending ? {} : { [spec.artifacts[0].id]: manifest };
const temp = `${outFile}.${process.pid}.tmp`;
writeFileSync(temp, `${JSON.stringify({ buildSha: revision, spec, manifests }, null, 2)}\n`);
renameSync(temp, outFile);
process.stdout.write(`service spec: ${source.pending ? `artifact pending (${source.pending})` : `bound to ${revision}`}\n`);
