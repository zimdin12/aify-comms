#!/usr/bin/env node
// A claimer with no key in its environment must still authenticate.
//
// THE OUTAGE, measured on the operator's host 2026-09-07. `install.sh` had set an `API_KEY`, and the
// service enforces it from its next restart. Claude agents were fine: their channel sidecar runs as
// an MCP CHILD and `~/.claude.json` carries the key in that server's `env` block. Hermes agents all
// went deaf, because their claimer is a STANDALONE process --
//
//     nohup node hermes-managed-host.js run <agent>   (hermes-aify, line 605)
//
// -- which inherits `AIFY_SERVER_URL` (exported at line 185) and NO key: neither launcher exports
// one, and the aify-wrapper template has no placeholder for it. Every 30-second liveness beat came
// back 401 and was swallowed, so the loop registered once at startup and never again. The service
// correctly concluded there was no claimer and refused to deliver, and FIVE agents read `online`
// while accepting no work -- `lastSeen` is refreshed by a separate registration beat, so every badge
// stayed green.
//
// MEASURED, WITH CONTROLS: all 8 claude agents at 0 minutes against all 5 hermes agents 12 to 148
// minutes stale; and an unauthenticated POST to /agents/<id>/heartbeat returns 401 where the same
// beat carrying the key returns 404. Five review dispatches died on the 180s no-claimer backstop.
//
// THE CREDENTIAL WAS ON DISK THE WHOLE TIME. `~/.aify/services.json` published a `credentialRef`, the
// file under `~/.aify/credentials/` existed, and its contents authenticate. The registry, the ref
// grammar, the store and the writer all worked; only `aify-http.mjs` -- the module every runtime
// component resolves its key through -- could not read it. `doctor-api-key.mjs` could, which is why
// the doctor always reported a key while the agents had none.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CREDENTIAL_DIR_NAME,
  MAX_CREDENTIAL_BYTES,
  REGISTRY_ENV_NAME,
  credentialRefIn,
  decodeCredentialBytes,
  keyForEndpoint,
  registryEntryFor,
  sameEndpoint,
} from "../registry-credential.mjs";
import { SERVICE_NAME } from "../service-name.mjs";
import { SERVICE_NAME as REGISTRY_SERVICE_NAME } from "../service-registry.mjs";

const HOME = "/home/op";
const REGISTRY = "/home/op/.aify/services.json";
const CREDENTIAL = "/home/op/.aify/credentials/aify-comms-abc123.key";
const ENDPOINT = "http://127.0.0.1:8800";

const join = (...parts) => parts.join("/");

/** A filesystem that answers only the paths it was given, and throws for anything else. */
function fsWith(files) {
  return (path) => {
    if (Object.prototype.hasOwnProperty.call(files, path)) return files[path];
    throw Object.assign(new Error(`ENOENT: ${path}`), { code: "ENOENT" });
  };
}

const REAL_HOST = fsWith({
  [REGISTRY]: JSON.stringify({
    services: { "aify-comms": { endpoint: "http://127.0.0.1:8800", credentialRef: "aify-comms-abc123.key" } },
  }),
  [CREDENTIAL]: "s3cret\n",
});

/**
 * The reader as the resolver calls it: ENDPOINT-BOUND, because a key is now only ever resolved for
 * the destination the registry named it for. `realpath` is the identity here so the on-disk-name
 * check always agrees; the tests that care about it inject their own.
 */
const read = (readFile, extra = {}) => keyForEndpoint({
  env: {}, readFile, join, homeDir: HOME, endpoint: ENDPOINT, realpath: (x) => x,
  // Custody is satisfied by default so these cases exercise the check each is NAMED for; the custody
  // test below injects its own verdicts. The real `custodyProblemFor` runs against a real filesystem
  // and would refuse every fixture path here, which would make every test pass for the wrong reason.
  custody: () => "",
  ...extra,
});

test("THE FIX: a claimer with an empty environment finds the key the registry names", () => {
  const { key, source } = read(REAL_HOST);
  assert.equal(key, "s3cret", "the standalone claimer still cannot authenticate");
  assert.match(source, /credential store/, "the source must say where the key came from");
});

test("it defaults to THIS service's name rather than making the caller retype it", () => {
  // `SERVICE_NAME` has one owner by design -- "a second hand-typed copy of an identity is how two
  // files come to disagree about who you are". A caller that had to pass "aify-comms" would be that
  // second copy.
  assert.equal(read(REAL_HOST).key, "s3cret");
});

test("NEGATIVE CONTROL: another service's credential is not ours", () => {
  // The registry is shared — other installers write their own entries into it. Reading a neighbour's
  // credential would authenticate as somebody else, which is worse than having no key.
  const other = fsWith({
    [REGISTRY]: JSON.stringify({
      services: { "aify-dashboard": { credentialRef: "aify-dashboard-xyz.key" } },
    }),
    "/home/op/.aify/credentials/aify-dashboard-xyz.key": "not-ours",
  });
  assert.equal(read(other).key, "");
});

test("A REF THAT IS A PATH IS REFUSED, not opened", () => {
  // The registry is written by other installers, so `../` in a ref is not hypothetical, and this
  // function opens whatever it is handed. It uses the same grammar aify-env applies at read time.
  for (const ref of ["../../etc/passwd", "sub/dir.key", "/abs.key", ".", ".."]) {
    const hostile = fsWith({
      [REGISTRY]: JSON.stringify({ services: { "aify-comms": { endpoint: ENDPOINT, credentialRef: ref } } }),
      [CREDENTIAL]: "s3cret",
      "/home/op/.aify/credentials/../../etc/passwd": "root:x:0:0",
    });
    const { key } = read(hostile);
    assert.equal(key, "", `a ref of ${JSON.stringify(ref)} was resolved instead of refused`);
  }
});

test("every missing piece reads as no key, and NOTHING throws", () => {
  // This resolves at module load in every bridge process. An exception here takes a bridge down over
  // a missing file rather than leaving it unauthenticated, which is strictly worse.
  const cases = {
    "no registry at all": fsWith({}),
    "registry is not JSON": fsWith({ [REGISTRY]: "{{{ not json" }),
    "registry names no ref": fsWith({ [REGISTRY]: JSON.stringify({ services: { "aify-comms": {} } }) }),
    "credential file absent": fsWith({
      [REGISTRY]: JSON.stringify({ services: { "aify-comms": { endpoint: ENDPOINT, credentialRef: "aify-comms-abc123.key" } } }),
    }),
    "credential file empty": fsWith({
      [REGISTRY]: JSON.stringify({ services: { "aify-comms": { endpoint: ENDPOINT, credentialRef: "aify-comms-abc123.key" } } }),
      [CREDENTIAL]: "   \n",
    }),
  };
  for (const [name, readFile] of Object.entries(cases)) {
    assert.doesNotThrow(() => read(readFile), name);
    assert.equal(read(readFile).key, "", name);
  }
  assert.equal(keyForEndpoint().key, "", "called with nothing, it invented a key");
});

test("AIFY_SERVICE_REGISTRY relocates the registry", () => {
  const elsewhere = fsWith({
    "/opt/registry.json": JSON.stringify({
      services: { "aify-comms": { endpoint: ENDPOINT, credentialRef: "aify-comms-abc123.key" } },
    }),
    [CREDENTIAL]: "s3cret\n",
  });
  const { key } = read(elsewhere, { env: { AIFY_SERVICE_REGISTRY: "/opt/registry.json" } });
  assert.equal(key, "s3cret");
});

// ── the two security findings the review reproduced ─────────────────────────────────────────────

test("R2: a key is NEVER paired with an endpoint the registry did not name it for", () => {
  // Reproduced by review with synthetic receivers: the URL came from ambient environment variables
  // while the key came from the registry, so a stale or foreign endpoint received a credential that
  // had never been on this process's HTTP path. Refusing is the only safe answer — a service secret
  // must not be handed to an arbitrary inherited destination.
  assert.equal(read(REAL_HOST, { endpoint: "http://10.1.2.3:9999" }).key, "",
    "the stored key was sent to an endpoint the registry never named");
  assert.equal(read(REAL_HOST, { endpoint: "" }).key, "",
    "a caller that cannot say where it is sending was still given a secret");
  // ...and not so strict that it refuses the ordinary case: one destination, three spellings.
  for (const spelling of ["http://localhost:8800", "http://127.0.0.1:8800/", "http://127.0.0.1:8800"]) {
    assert.equal(read(REAL_HOST, { endpoint: spelling }).key, "s3cret", `refused ${spelling}`);
  }
  assert.equal(sameEndpoint("not a url", "not a url"), false, "unparseable compared equal to itself");
});

test("R3: the store's decoding contract, not readFileSync().trim()", () => {
  // Every one of these was ACCEPTED by the first version of this reader and REFUSED by aify-env's.
  // A credential that differs from what the store wrote is not this service's key.
  assert.equal(decodeCredentialBytes(Buffer.from("s3cret\n")), "s3cret", "the ordinary case broke");
  assert.equal(decodeCredentialBytes(Buffer.from("s3cret")), "", "no trailing newline was accepted");
  assert.equal(decodeCredentialBytes(Buffer.from("s3cret\r\n")), "", "CRLF was accepted");
  assert.equal(decodeCredentialBytes(Buffer.from("s3cret\n\n")), "", "a stray extra newline was accepted");
  assert.equal(decodeCredentialBytes(Buffer.from("s3c\u0000ret\n")), "", "an embedded NUL was accepted");
  assert.equal(decodeCredentialBytes(Buffer.from([0xff, 0xfe, 0x0a])), "", "invalid UTF-8 was accepted");
  assert.equal(decodeCredentialBytes(Buffer.from(`${"k".repeat(MAX_CREDENTIAL_BYTES + 1)}\n`)), "",
    "an oversized credential was accepted");
  assert.equal(decodeCredentialBytes(Buffer.from(" s3cret \n")), "", "surrounding whitespace was accepted");
  assert.equal(decodeCredentialBytes(null), "");
});

test("R3: a file whose REAL name differs only in case is refused", () => {
  // On Windows and on macOS's default volume `Foo.key` and `foo.key` are ONE file, so two services
  // whose refs differ only in case silently share a credential and each reads the other's. aify-env
  // returns CREDENTIAL_INSECURE for that spelling mismatch; a grammar check alone cannot see it.
  const cased = (real) => read(REAL_HOST, { realpath: (p) => p.replace("aify-comms-abc123.key", real) });
  assert.equal(cased("aify-comms-abc123.key").key, "s3cret", "the matching case was refused");
  assert.equal(cased("AIFY-COMMS-ABC123.KEY").key, "",
    "a file whose on-disk name differs only in case was read as ours");
});

test("R3: a path that cannot be canonicalised is not one to read a secret from", () => {
  assert.equal(read(REAL_HOST, { realpath: () => { throw new Error("EPERM"); } }).key, "");
});

// ── the CALL SITE, not just the helper ──────────────────────────────────────────────────────────
//
// The tests above prove the resolver works. They would ALL stay green if `aify-http.mjs` never
// called it -- which is exactly the defect being fixed, from the other end: a correct helper that
// the runtime path does not use. Verified by mutation: deleting the fallback from `aify-http.mjs`
// leaves every test above passing. So this one drives the real module, in a real process, with a
// real home directory.

import { chmodSync, mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

// SEALED, never `{ ...process.env }`. This test spawns a child that resolves an API key, and this
// suite runs on a host with a LIVE fleet: an unsealed child would inherit the operator's real
// carriers and could authenticate against the running service. `sealedChildEnv` deletes every live
// carrier, and a gate fails any test file that skips it.
import { sealedChildEnv } from "./_child-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const HTTP_MODULE = path.join(HERE, "..", "aify-http.mjs");
// THE MODULE THAT WAS ACTUALLY BROKEN. `server.js`, `claude-channel.js`, `hermes-channel.js`,
// `hermes-managed-host.js`, `notify-check.js` and `runtimes-codex.js` import `API_KEY` from HERE,
// not from aify-http. Fixing only aify-http repaired the delivery loops and left every MCP tool
// returning 401 — reported by the reviewer as "native comms_send returned HTTP401 twice this turn,
// despite incoming delivery working". A test for one call site would have missed the other.
const ENDPOINT_MODULE = path.join(HERE, "..", "aify-service-endpoint.mjs");

/** A throwaway home holding just a registry and the credential it names. */
function homeWithCredential(secret) {
  const home = mkdtempSync(path.join(tmpdir(), "aify-claimer-"));
  mkdirSync(path.join(home, ".aify", "credentials"), { recursive: true });
  writeFileSync(path.join(home, ".aify", "services.json"), JSON.stringify({
    services: { "aify-comms": { endpoint: "http://127.0.0.1:8800", credentialRef: "aify-comms-t.key" } },
  }));
  // A trailing newline on purpose: aify-env writes one, and the resolver has to trim it. A fixture
  // without it would pass while the real store failed.
  const file = path.join(home, ".aify", "credentials", "aify-comms-t.key");
  writeFileSync(file, secret + "\n");
  // AND LOCKED DOWN, as aify-env's writer leaves it. Without this the child's REAL custody check
  // refuses the fixture -- correctly, since a temp file inherits broad grants -- and the test would
  // fail for a reason unrelated to what it measures. Locking it here means the call-site tests
  // exercise the custody path end to end rather than around it.
  try {
    if (process.platform === "win32") {
      execFileSync("icacls", [file, "/inheritance:r", "/grant:r", `${process.env.USERNAME}:(F)`],
        { stdio: "ignore" });
    } else {
      chmodSync(file, 0o600);
    }
  } catch {
    // A host that cannot lock it down sees the custody refusal, which is the honest outcome.
  }
  return home;
}

/** Import aify-http.mjs in a fresh process and report the key it resolved. */
function resolvedKeyWith(env, moduleFile = HTTP_MODULE, name = "AIFY_API_KEY") {
  // `pathToFileURL` rather than building the URL by hand: a Windows path needs its separators and
  // drive letter encoded, and hand-rolling that is how this line was wrong the first time.
  const url = pathToFileURL(moduleFile).href;
  return execFileSync(process.execPath, [
    "-e", `import(${JSON.stringify(url)}).then(m => process.stdout.write(m[${JSON.stringify(name)}]))`,
  ], { env, encoding: "utf8" }).trim();
}

test("THE CALL SITE: aify-http resolves the store key when the environment carries none", () => {
  const home = homeWithCredential("from-the-store");
  // The registry is a SEALED FILE CARRIER: the helper points it at a sealed path rather than
  // unsetting it, because unset would fall back to the real home. A test wanting a real registry
  // names one through `extra`, which wins.
  // AIFY_SERVER_URL is required now, and that is the fix, not an inconvenience: a process that
  // cannot say where it is sending is not given a secret to send there.
  const env = sealedChildEnv({
    HOME: home, USERPROFILE: home,
    AIFY_SERVER_URL: "http://127.0.0.1:8800",
    AIFY_SERVICE_REGISTRY: path.join(home, ".aify", "services.json"),
  });
  assert.equal(resolvedKeyWith(env), "from-the-store",
    "the module every runtime component resolves its key through still cannot read the credential store");
});

test("THE CALL SITE: the environment still wins over the store", () => {
  // An operator or a test must be able to override without touching aify-env's store, and a host
  // with no registry must behave exactly as it did before this fallback existed.
  const home = homeWithCredential("from-the-store");
  const env = sealedChildEnv({
    HOME: home, USERPROFILE: home, AIFY_API_KEY: "from-the-env",
    AIFY_SERVER_URL: "http://127.0.0.1:8800",
    AIFY_SERVICE_REGISTRY: path.join(home, ".aify", "services.json"),
  });
  assert.equal(resolvedKeyWith(env), "from-the-env");
});

// ── the pieces the resolver is built from ───────────────────────────────────────────────────────

test("the identity has ONE owner, and the re-export keeps it that way", () => {
  // `service-name.mjs` holds it so a module wanting only the NAME need not load the registry parser;
  // `service-registry.mjs` re-exports it so every existing importer is unchanged. If those two ever
  // disagree, the thing this repo warns about has happened -- two files disagreeing about who we are.
  assert.equal(SERVICE_NAME, "aify-comms");
  assert.equal(REGISTRY_SERVICE_NAME, SERVICE_NAME, "the owner and its re-export disagree");
});

test("credentialRefIn reads one service's ref, and tolerates a registry it cannot parse", () => {
  const registry = JSON.stringify({
    services: { "aify-comms": { credentialRef: "ours.key" }, "aify-dashboard": { credentialRef: "theirs.key" } },
  });
  assert.equal(credentialRefIn(registry, "aify-comms"), "ours.key");
  assert.equal(credentialRefIn(registry, "aify-dashboard"), "theirs.key", "it cannot read another entry");
  assert.equal(credentialRefIn(registry, "not-installed"), "");
  // An unreadable registry is a state `service-registry.mjs` deliberately REFUSES to repair, so it
  // can legitimately sit there broken. A resolver that threw would take a bridge down over it.
  for (const junk of ["{{{", "", null, undefined, "[]"]) {
    assert.equal(credentialRefIn(junk, "aify-comms"), "", `${JSON.stringify(junk)} was not tolerated`);
  }
});

test("the store's layout is named, not inlined", () => {
  // The doctor's report quotes this and the resolver opens it; two spellings would drift.
  assert.equal(CREDENTIAL_DIR_NAME, "credentials");
  assert.equal(REGISTRY_ENV_NAME, "AIFY_SERVICE_REGISTRY");
});

test("THE OTHER CALL SITE: aify-service-endpoint's API_KEY reads the store too", () => {
  // This is the one `server.js` and every channel sidecar import. It resolved the environment alone
  // while aify-http resolved its own copy — so the first fix repaired inbound delivery and left the
  // MCP tools 401ing. There is one resolver now, and this test is what would catch it splitting again.
  const home = homeWithCredential("from-the-store");
  // AIFY_SERVER_URL is required now, and that is the fix, not an inconvenience: a process that
  // cannot say where it is sending is not given a secret to send there.
  const env = sealedChildEnv({
    HOME: home, USERPROFILE: home,
    AIFY_SERVER_URL: "http://127.0.0.1:8800",
    AIFY_SERVICE_REGISTRY: path.join(home, ".aify", "services.json"),
  });
  assert.equal(resolvedKeyWith(env, ENDPOINT_MODULE, "API_KEY"), "from-the-store",
    "server.js and the channel sidecars still cannot authenticate without an environment key");
});

test("the two modules resolve the SAME key, because one of them is an alias", () => {
  const home = homeWithCredential("one-key");
  // AIFY_SERVER_URL is required now, and that is the fix, not an inconvenience: a process that
  // cannot say where it is sending is not given a secret to send there.
  const env = sealedChildEnv({
    HOME: home, USERPROFILE: home,
    AIFY_SERVER_URL: "http://127.0.0.1:8800",
    AIFY_SERVICE_REGISTRY: path.join(home, ".aify", "services.json"),
  });
  assert.equal(
    resolvedKeyWith(env, HTTP_MODULE, "AIFY_API_KEY"),
    resolvedKeyWith(env, ENDPOINT_MODULE, "API_KEY"),
    "aify-http and aify-service-endpoint disagree about the key — they are two spellings again",
  );
});

test("R3: CUSTODY IS CHECKED, and a file the store would refuse yields no key", () => {
  // The half I skipped and review disproved: a credential written by aify-env's real writer was
  // accepted by both readers until Everyone was granted read, at which point aify-env returned
  // CREDENTIAL_INSECURE and this reader still handed back the key.
  assert.equal(read(REAL_HOST, { custody: () => "readable by Everyone (a group)" }).key, "",
    "a group-readable credential was still read");
  // ...and a custody check that cannot run is a refusal, never a pass.
  assert.equal(read(REAL_HOST, { custody: () => "could not inspect the file: EPERM" }).key, "");
  assert.equal(read(REAL_HOST, { custody: () => "" }).key, "s3cret", "a private file was refused");
});

test("R2: the resolver reports WHICH endpoint its key is authorised for", () => {
  // `httpCall` fails over across several destinations, and the header used to be attached before that
  // loop -- so a matching primary returning 503 sent the registry credential to the next URL in the
  // list. The caller can only authorise per destination if the resolver says which one it meant.
  const resolved = read(REAL_HOST);
  assert.equal(resolved.endpoint, ENDPOINT, "the key came back without the endpoint it opens");
  assert.equal(read(REAL_HOST, { endpoint: "http://10.1.2.3:9999" }).endpoint, "",
    "a refusal still named an endpoint");
});
