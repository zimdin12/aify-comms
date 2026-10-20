#!/usr/bin/env node
// Write the registration spec for one commit, bound to that commit (guided install S1).
//
//   service-spec-cli.mjs <repo-root> <revision> <version> <out-file>
//
// Run by `scripts/stamp.sh` beside the build stamp. The service serves the spec only while `buildSha`
// equals the build it is running, so a spec left over from another build is never served.
//
// THE MANIFEST IS THE COMMIT'S, not the working tree's: each file's bytes come from git's object store
// at <revision>, so an uncommitted edit, or a checkout's line-ending conversion, never reaches it.
// When the commit cannot be bound (no GitHub origin, the revision is absent), the artifact is
// `{pending: <reason>}` and carries no hash.

import { spawnSync } from "node:child_process";
import { renameSync, writeFileSync } from "node:fs";

import { commsServiceSpec, manifestText, sha256 } from "./service-spec.mjs";

const STDIO_DIR = "mcp/stdio";
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

/** {source, manifestSha256} for `revision`, or a pending source naming why it could not be bound. */
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
    const manifestSha256 = sha256(Buffer.from(manifestText(committedFiles(repoRoot, revision)), "utf8"));
    return { source: { git: { repo, revision } }, manifestSha256 };
  } catch (error) {
    return { source: { pending: `the bridge files at ${revision} could not be read: ${error.message}` } };
  }
}

const [, , repoRoot, revision, version, outFile] = process.argv;
if (!repoRoot || !/^[0-9a-f]{40}$/.test(revision || "") || !version || !outFile) {
  process.stderr.write("usage: service-spec-cli.mjs <repo-root> <40-hex revision> <version> <out-file>\n");
  process.exit(EXIT_USAGE);
}
const spec = commsServiceSpec({ version, ...artifactSource(repoRoot, revision) });
const temp = `${outFile}.${process.pid}.tmp`;
writeFileSync(temp, `${JSON.stringify({ buildSha: revision, spec }, null, 2)}\n`);
renameSync(temp, outFile);
const { source } = spec.artifacts[0];
process.stdout.write(`service spec: ${source.pending ? `artifact pending (${source.pending})` : `bound to ${revision}`}\n`);
