#!/usr/bin/env node
// This bridge's cached custody rules must agree with the daemon that owns them.
//
// WHY A CACHED COPY AT ALL. aify-env owns the credential store and is not a dependency of this
// package, so the alternative to duplicating its rules is to keep reading credentials the owner would
// refuse. That is exactly what happened: I implemented the ref grammar and the byte decoding and
// SKIPPED custody, judging the ACL and ownership checks unreachable without aify-env's helpers.
// Review disproved that on this host -- a credential written through aify-env's real secure writer
// was accepted by both readers; after a real `icacls` grant of Everyone read, aify-env returned
// CREDENTIAL_INSECURE ("readable by Everyone (a group)") and this bridge still handed back the key.
//
// SO THE DUPLICATION IS DELIBERATE AND THIS TEST IS ITS PRICE, the same arrangement
// `credential-ref.mjs` already has: one corpus, both implementations, and a failure the moment they
// disagree.
//
// IT FAILS WHEN THE CHECKOUT IS ABSENT rather than skipping. A cross-repo proof that quietly does not
// run is worse than none, because the report still reads green -- this repo has paid for that twice.

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

import {
  aclPrincipalLeaf,
  aclProblem,
  currentWindowsOwner,
  custodyProblem,
  custodyProblemFor,
  parseIcaclsAces,
} from "../credential-custody.mjs";

const AIFY_ENV = process.env.AIFY_ENV_DIR || path.join(homedir(), "projects", "aify-env");
const OWNER = path.join(AIFY_ENV, "lib", "credential-fs.mjs");

/** A stat as `lstat` returns one, built from the properties the rules actually read. */
function statLike({ symlink = false, file = true, size = 10, nlink = 1, uid = 1000, mode = 0o600 }) {
  return {
    isSymbolicLink: () => symlink,
    isFile: () => file,
    size, nlink, uid, mode,
  };
}

/**
 * The corpus. Every case is one the store can really be in, and each is fed to BOTH implementations.
 * Windows cases carry `icacls` text in the shape aify-env measured on this host.
 */
const CORPUS = [
  ["a private posix file", { platform: "linux", stats: statLike({}), uid: 1000 }],
  ["group-readable posix", { platform: "linux", stats: statLike({ mode: 0o640 }), uid: 1000 }],
  ["world-readable posix", { platform: "linux", stats: statLike({ mode: 0o644 }), uid: 1000 }],
  ["owned by another uid", { platform: "linux", stats: statLike({ uid: 2000 }), uid: 1000 }],
  ["a hard-linked file", { platform: "linux", stats: statLike({ nlink: 2 }), uid: 1000 }],
  ["a symlink", { platform: "linux", stats: statLike({ symlink: true }), uid: 1000 }],
  ["a directory", { platform: "linux", stats: statLike({ file: false }), uid: 1000 }],
  ["an oversized file", { platform: "linux", stats: statLike({ size: 99999 }), uid: 1000 }],
  ["no stat at all", { platform: "linux", stats: null, uid: 1000 }],
  ["windows, owner only", {
    platform: "win32", stats: statLike({}), owner: "HOST\\op", aclPath: "C:\\s\\k.key",
    aclText: "C:\\s\\k.key HOST\\op:(F)\r\n",
  }],
  ["windows, Everyone granted", {
    platform: "win32", stats: statLike({}), owner: "HOST\\op", aclPath: "C:\\s\\k.key",
    aclText: "C:\\s\\k.key HOST\\op:(F)\r\n                Everyone:(R)\r\n",
  }],
  ["windows, SYSTEM beside the owner", {
    platform: "win32", stats: statLike({}), owner: "HOST\\op", aclPath: "C:\\s\\k.key",
    aclText: "C:\\s\\k.key HOST\\op:(F)\r\n                NT AUTHORITY\\SYSTEM:(F)\r\n",
  }],
  ["windows, a group with Modify", {
    platform: "win32", stats: statLike({}), owner: "HOST\\op", aclPath: "C:\\s\\k.key",
    aclText: "C:\\s\\k.key HOST\\CodexSandboxUsers:(I)(M)\r\n                HOST\\op:(I)(F)\r\n",
  }],
  ["windows, unreadable acl", {
    platform: "win32", stats: statLike({}), owner: "HOST\\op", aclPath: "C:\\s\\k.key", aclText: "",
  }],
  ["windows, Administrators", {
    platform: "win32", stats: statLike({}), owner: "HOST\\op", aclPath: "C:\\s\\k.key",
    aclText: "C:\\s\\k.key HOST\\op:(F)\r\n                BUILTIN\\Administrators:(F)\r\n",
  }],
];

test("THE CHECKOUT IS PRESENT — this proof does not quietly skip", () => {
  assert.ok(
    existsSync(OWNER),
    `aify-env is not at ${AIFY_ENV}, so the rules this module caches were compared against nothing. `
    + "Set AIFY_ENV_DIR. This FAILS rather than skips on purpose: a cross-repo proof that does not "
    + "run still reads green.",
  );
});

test("POSITIVE CONTROL: the corpus contains both verdicts", () => {
  // A corpus of all-refusals or all-acceptances would make agreement trivial. Both implementations
  // have to be exercised in both directions for the comparison below to mean anything.
  const verdicts = CORPUS.map(([, input]) => Boolean(custodyProblem(input)));
  assert.ok(verdicts.includes(true), "no case in the corpus is refused");
  assert.ok(verdicts.includes(false), "no case in the corpus is accepted");
});

test("EVERY CASE GETS THE SAME VERDICT FROM BOTH IMPLEMENTATIONS", async () => {
  // `pathToFileURL`, not a hand-built URL: a Windows absolute path is not a valid file:// URL
  // and the ESM loader refuses it with "protocol 'c:'".
  const owner = await import(pathToFileURL(OWNER).href);
  const theirs = owner.fileSecurityProblem;
  assert.equal(typeof theirs, "function", "aify-env no longer exports fileSecurityProblem");

  const disagreements = [];
  for (const [name, input] of CORPUS) {
    const mine = custodyProblem(input);
    const owners = theirs(input);
    // COMPARED AS VERDICTS, not as prose: what must match is refuse-or-accept, and the REASON where
    // both refuse. A wording change in either repo is not a disagreement about custody.
    if (Boolean(mine) !== Boolean(owners) || (mine && owners && mine !== owners)) {
      disagreements.push(`${name}: mine ${JSON.stringify(mine)} vs aify-env ${JSON.stringify(owners)}`);
    }
  }
  assert.deepEqual(disagreements, [],
    "the cached custody rules have drifted from the daemon that owns them");
});
test("the pieces the rules are built from", () => {
  // Named here because they are the parts most likely to drift from aify-env independently of the
  // verdict function, and because a parse that understood nothing must never read as "no problems".
  assert.equal(aclPrincipalLeaf("HOST\\op"), "op");
  assert.equal(aclPrincipalLeaf("Everyone"), "everyone");
  const aces = parseIcaclsAces("C:\\s\\k.key HOST\\op:(F)", { path: "C:\\s\\k.key" });
  assert.equal(aces.length, 1, "the one ACE on the path line was not parsed");
  assert.equal(aces[0].leaf, "op");
  assert.match(aclProblem([], { owner: "HOST\\op" }), /unknown/,
    "an empty parse read as 'no grants, therefore private'");
  assert.equal(aclProblem(aces, { owner: "HOST\\op" }), "", "the owner was treated as an offender");
  assert.equal(currentWindowsOwner({ USERDOMAIN: "HOST", USERNAME: "op" }), "HOST\\op");
  assert.equal(currentWindowsOwner({ USERNAME: "" }), "");
});

test("custodyProblemFor FAILS CLOSED on a path it cannot inspect", () => {
  // It runs at module load in every bridge with no environment key. A throw there takes the bridge
  // down; a silent "" reads a secret out of a file nobody checked.
  const reason = custodyProblemFor("/no/such/credential.key", {
    platform: "linux", lstat: () => { throw Object.assign(new Error("nope"), { code: "ENOENT" }); },
  });
  assert.match(reason, /could not inspect/);
});
