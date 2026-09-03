#!/usr/bin/env node
// The reference this installer writes must be one the daemon that reads it will accept.
//
// CRED-L2, external review round 7 -- and NOT the security hole it was filed as, which is worth
// stating because the filing would send someone looking for a traversal bug that is not there.
// `service-registry.mjs` checked only that `credentialRef` was a non-empty string, while the comment
// beside that check claims "a registry entry cannot point that daemon at a path of its choosing".
// The claim is TRUE. It is aify-env that makes it true, refusing a bad ref at READ time.
//
// SO THE DEFECT IS TIMING. The installer wrote whatever `CREDENTIAL_REF` held, reported success, and
// aify-env then declined to resolve it: two components each behaving correctly, an operator with a
// credential that does not work, a green install, and nothing anywhere connecting the two. That is
// the shape this repo keeps paying for -- a failure that arrives far from its cause.
//
// WHY A SECOND COPY OF THE GRAMMAR EXISTS. aify-env owns this rule; it resolves the name under its
// own root. aify-comms cannot import it -- separate packages, and aify-env is not a dependency. The
// alternative to duplicating is to keep writing refs the consumer rejects, which IS the defect. So
// the copy is a cache of somebody else's decision, and this file is what keeps it honest: both
// implementations are driven over one corpus and any disagreement fails.
//
// IT FAILS RATHER THAN SKIPS when the aify-env checkout is absent. A cross-repo proof that quietly
// does not run is worse than none, because the report still reads green -- the exact reason
// `env-client-against-real-aify-env.test.js` was changed to fail the same way.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { credentialRefProblem as ours } from "../credential-ref.mjs";
import { CREDENTIAL_DIR_NAME as ours_CREDENTIAL_DIR_NAME } from "../doctor-api-key.mjs";
import { SERVICE_NAME, upsertService } from "../service-registry.mjs";

const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const THEIRS_PATH = path.join(AIFY_ENV, "lib", "credential-store.mjs");

/** Every shape worth disagreeing about, including the ones only one rule catches. */
const CORPUS = [
  // acceptable
  "aify-comms", "aify-comms.key", "a", "A1_b-c.d", "x".repeat(64),
  // empty / oversize
  "", "x".repeat(65),
  // path separators and traversal, which is what the filing was worried about
  "../secret", "a/b", "a\\b", "/etc/passwd", "C:/keys/x", "..", ".",
  // hidden files
  ".hidden", ".env",
  // Windows devices, with and without an extension
  "con", "CON", "nul.key", "com1", "lpt9.txt", "prn",
  // charset edges
  "a b", "a$b", "café", "a\nb", "a\tb",
  // non-strings
  null, undefined, 42, {},
];

test("the aify-env checkout is present, because this proof needs the real implementation", () => {
  assert.ok(
    fs.existsSync(THEIRS_PATH),
    `aify-env is not checked out at ${AIFY_ENV}, so the grammar this installer copies cannot be `
    + "compared against the one that actually decides. Set AIFY_ENV_REPO, or clone it. This FAILS "
    + "rather than skips: a cross-repo agreement nobody ran must not read as an agreement held.",
  );
});

test("both implementations agree on every shape in the corpus", async () => {
  const { credentialRefProblem: theirs } = await import(`file://${THEIRS_PATH.split("\\").join("/")}`);

  // POSITIVE CONTROL on the import: a checker that returned "" for everything would make the
  // comparison below trivially true, and so would one that rejected everything.
  assert.equal(theirs("aify-comms"), "", "aify-env's checker rejects a plainly valid ref");
  assert.ok(theirs("../secret"), "aify-env's checker accepts a traversal ref");

  const disagreements = [];
  for (const ref of CORPUS) {
    const mine = ours(ref) !== "";
    const other = theirs(ref) !== "";
    if (mine !== other) {
      disagreements.push(`${JSON.stringify(ref)}: aify-comms ${mine ? "refuses" : "accepts"}, `
        + `aify-env ${other ? "refuses" : "accepts"}`);
    }
  }
  assert.deepEqual(
    disagreements, [],
    "the copied grammar has drifted from the one that actually decides:\n  "
    + disagreements.join("\n  ")
    + "\nEvery disagreement is either a ref this installer writes and aify-env then refuses to "
    + "resolve, or one it refuses to write that would have worked.",
  );
});

// -- and the writer actually uses it ---------------------------------------------------------------

const entry = (credentialRef) => ({
  endpoint: "http://127.0.0.2:1",
  endpointEnv: ["AIFY_COMMS_URL"],
  keyEnv: ["AIFY_API_KEY"],
  credentialRef,
  mcp: [{ name: "aify-comms", command: "node", args: ["/x/server.js"] }],
});

test("a ref aify-env would refuse is not written", () => {
  // THE CALL SITE, not the predicate. A grammar proven in isolation still leaves the writer free to
  // ignore it, which is how the original defect existed at all.
  const result = upsertService("", SERVICE_NAME, entry("../secret"));
  assert.equal(result.ok, false, "the registry writer accepted a ref the consumer will refuse");
  assert.match(result.errors.join(" "), /credentialRef/, "the error does not name the field");
  assert.match(result.errors.join(" "), /path separators/, "the error does not say what is wrong");
});

test("and an acceptable one still writes", () => {
  // CONTRADICTION ARM. Refusing everything would satisfy the test above and break every install.
  const result = upsertService("", SERVICE_NAME, entry("aify-comms.key"));
  assert.equal(result.ok, true, `a valid ref was refused: ${result.errors.join("; ")}`);
  assert.match(result.text, /aify-comms\.key/, "the accepted ref did not reach the file");
});

test("omitting the field entirely is still fine", () => {
  // The ordinary path: most installs store no credential, and `undefined` means "carry forward what
  // is published" rather than "write an empty one". Validating it as a string would break every one.
  const withoutRef = entry(undefined);
  delete withoutRef.credentialRef;
  assert.equal(upsertService("", SERVICE_NAME, withoutRef).ok, true);
});

test("AND WE LOOK FOR THE CREDENTIAL WHERE AIFY-ENV PUTS IT", async () => {
  // The second cached decision on this seam, added 2026-09-03 with D11. The doctor now resolves its
  // API key from aify-env's credential store when there is no checkout to read a `.env` from, which
  // means aify-comms has to know that daemon's directory layout -- and cannot import it, for the
  // same packaging reason the grammar above is duplicated.
  //
  // A WRONG DIRECTORY FAILS EXACTLY LIKE AN ABSENT KEY. The resolver reads quiet on ENOENT, by
  // design: no credential store is the ordinary state on a host that never installed aify-env. So a
  // rename on their side would not throw, would not warn, and would simply return the doctor to
  // being blind from every directory but the checkout -- the defect this was written to close,
  // silently restored. That is precisely the failure an agreement test exists to make loud.
  assert.ok(
    fs.existsSync(THEIRS_PATH),
    `aify-env checkout not found at ${AIFY_ENV}. This proof FAILS rather than skips: a cross-repo `
    + "agreement that quietly does not run leaves the report green while proving nothing.",
  );
  const theirs = await import(`file://${THEIRS_PATH.split("\\").join("/")}`);
  assert.equal(
    ours_CREDENTIAL_DIR_NAME, theirs.CREDENTIAL_DIR_NAME,
    "aify-comms looks for credentials in a different directory than aify-env writes them to",
  );
});
