// Which code is actually running — and the install shape where the old answer was wrong.
//
// The build tag is how a banner, a diagnostics string and the control plane's `bridgeBuild` each name the
// commit they are executing, in a repo where every deploy path fails silently. It has two sources and the
// ORDER is the point: `install.sh` copies the bridge to `~/.aify-comms/` and stamps `.aify-version` there,
// and that copy has no `.git`, so the stamp is the only evidence a normal install has.
//
// THE TESTS BUILD A REAL INSTALL-SHAPED TREE rather than describing one. `bridge-build.mjs` imports nothing
// but node builtins, so it can be copied alone into `<tmp>/mcp/stdio/` with a `<tmp>/.aify-version` beside
// it and imported for real — which is exactly the layout `install.sh` produces, and the one where the
// second implementation of this function used to answer "no-git".

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = path.join(STDIO, "bridge-build.mjs");

// Build `<root>/mcp/stdio/bridge-build.mjs` — the module resolves its evidence two levels up from itself,
// so this is the real install layout — optionally with a stamp and/or a git dir, and report the tag.
function tagIn({ stamp = null, git = null } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aify-build-"));
  try {
    const dir = path.join(root, "mcp", "stdio");
    fs.mkdirSync(dir, { recursive: true });
    fs.copyFileSync(SOURCE, path.join(dir, "bridge-build.mjs"));
    if (stamp !== null) fs.writeFileSync(path.join(root, ".aify-version"), stamp);
    if (git) {
      const gitDir = path.join(root, ".git");
      fs.mkdirSync(gitDir, { recursive: true });
      for (const [rel, body] of Object.entries(git)) {
        const f = path.join(gitDir, rel);
        fs.mkdirSync(path.dirname(f), { recursive: true });
        fs.writeFileSync(f, body);
      }
    }
    const url = pathToFileURL(path.join(dir, "bridge-build.mjs")).href;
    return execFileSync(process.execPath, ["--input-type=module", "-e",
      `const m = await import(${JSON.stringify(url)}); process.stdout.write(m.BRIDGE_BUILD_TAG);`],
    { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("A NATIVE-COPY INSTALL READS THE STAMP — the case that used to answer no-git", () => {
  // `install.sh` copies the bridge out of the repo, so there is no `.git` anywhere above it. Without the
  // stamp branch the tag is "no-git" and the one string whose job is proving which code runs cannot do it.
  // This is the shape the second, diverged implementation of this function was still failing on.
  const tag = tagIn({ stamp: "sha=577c7cacdc3b8c565d38b54fc530f2c3f0a24a6d\nshort=577c7ca\nbranch=main\n" });
  assert.equal(tag, "577c7ca", "the stamped short sha must be reported");
  assert.notEqual(tag, "no-git");
});

test("a stamp with only a full sha is used, and TRUNCATED", () => {
  // `short=` is preferred, but `sha=` is the documented fallback and was reachable-but-untested: every
  // fixture here used a 7-character `short=`, so `.slice(0, 12)` was a no-op and a mutation removing it
  // survived. A 40-character sha in a pasteable diagnostics string is the thing the truncation prevents.
  const forty = "0123456789abcdef0123456789abcdef01234567";
  assert.equal(tagIn({ stamp: `sha=${forty}\nbranch=main\n` }), forty.slice(0, 12),
    "with no short= line the full sha is used, cut to 12");
  // …and `short=` still wins when both are present, which is the ordinary stamp `install.sh` writes.
  assert.equal(tagIn({ stamp: `sha=${forty}\nshort=abc1234\n` }), "abc1234");
});

test("the stamp WINS over a git dir, because the stamp describes the copy that is running", () => {
  // If both exist, the stamp was written when this copy was made; `.git` would describe whatever the repo
  // has moved on to since. Reporting the repo's HEAD for an installed copy is the failure being prevented.
  const tag = tagIn({
    stamp: "short=aaaaaaa\n",
    git: { HEAD: "ref: refs/heads/main\n", "refs/heads/main": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n" },
  });
  assert.equal(tag, "aaaaaaa", "the stamp is the authority for an installed copy");
});

test("a REPO CHECKOUT with no stamp follows .git/HEAD, including through packed-refs", () => {
  // The developer case. Three shapes, because a ref can be loose, packed, or absent.
  assert.equal(
    tagIn({ git: { HEAD: "ref: refs/heads/main\n", "refs/heads/main": "1234567890abcdef1234\n" } }),
    "1234567890ab", "a loose ref is read and truncated to 12");
  assert.equal(
    tagIn({ git: { HEAD: "ref: refs/heads/main\n", "packed-refs": "# pack\nfedcba0987654321fedc refs/heads/main\n" } }),
    "fedcba098765", "a packed ref is found when the loose one is absent");
  assert.equal(
    tagIn({ git: { HEAD: "abcdef1234567890abcd\n" } }),
    "abcdef123456", "a detached HEAD is the sha itself");
});

test("EVERY FAILURE ANSWERS WITH A WORD, never a throw", () => {
  // A build tag that could throw would take down the thing it was added to describe — it is read at module
  // load, on the banner path. Each degenerate shape has its own word so the word itself is diagnostic.
  assert.equal(tagIn({}), "no-git", "nothing to read at all");
  assert.equal(
    tagIn({ git: { HEAD: "ref: refs/heads/gone\n" } }), "unknown-ref",
    "a ref that resolves to nothing is distinguishable from having no git at all");
  // A stamp that exists but says nothing usable must fall THROUGH to git rather than reporting junk.
  assert.equal(
    tagIn({ stamp: "short=unknown\n", git: { HEAD: "cafebabe12345678cafe\n" } }), "cafebabe1234",
    "an `unknown` stamp is not an answer and must not shadow a real one");
  assert.equal(tagIn({ stamp: "" }), "no-git", "an empty stamp file falls through too");
  assert.equal(tagIn({ stamp: "garbage with no fields\n" }), "no-git");
});

test("the tag is short enough to paste and stable across reads", () => {
  const tag = tagIn({ git: { HEAD: "0123456789abcdef0123456789abcdef01234567\n" } });
  assert.equal(tag.length, 12, "twelve characters — long enough to be unique, short enough to quote");
  assert.equal(tag, tagIn({ git: { HEAD: "0123456789abcdef0123456789abcdef01234567\n" } }), "deterministic");
});

test("ONE OWNER — the divergence that made this a module is gone", () => {
  // There were two implementations: `computeBridgeBuildTag` in `server.js` and `readBuildTag` in
  // `runtimes-exec.js`, the second being the same algorithm MINUS the stamp branch. Measured on the live
  // install (`~/.aify-comms`, stamp present, no `.git`), `diagnosticsFor()` reported `build=no-git` while
  // the banner reported the stamped sha — so the string that exists to prove which code runs could not.
  assert.deepEqual(declaringModules("BRIDGE_BUILD_TAG"), [{ file: "bridge-build.mjs", kind: "binding" }],
    "a second declaration is how the two answers diverged in the first place");
  assert.deepEqual(declaringModules("computeBridgeBuildTag"), [{ file: "bridge-build.mjs", kind: "function" }]);
  assert.deepEqual(declaringModules("readBuildTag"), [],
    "the diverged second implementation must be gone, not merely unused");

  for (const file of ["server.js", "runtimes-exec.js"]) {
    const src = fs.readFileSync(path.join(STDIO, file), "utf-8");
    assert.match(src, /from "\.\/bridge-build\.mjs"/, `${file} must take the tag from its owner`);
  }
});

test("both consumers report the SAME tag", () => {
  // The property the divergence broke. Asserted through the real diagnostics string, not by comparing
  // source, because the whole failure was two sources that looked equivalent and were not.
  const out = execFileSync(process.execPath, ["--input-type=module", "-e", `
    const { BRIDGE_BUILD_TAG } = await import(${JSON.stringify(pathToFileURL(SOURCE).href)});
    const { diagnosticsFor } = await import(${JSON.stringify(pathToFileURL(path.join(STDIO, "runtimes-exec.js")).href)});
    const m = diagnosticsFor("node").match(/build=(\\S+)/);
    process.stdout.write(JSON.stringify({ owner: BRIDGE_BUILD_TAG, diagnostics: m && m[1] }));
  `], { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
  const { owner, diagnostics } = JSON.parse(out);
  assert.ok(owner, "the owner must produce a tag");
  assert.equal(diagnostics, owner, "runtime diagnostics must name the same build as the owner");
});
