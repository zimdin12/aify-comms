#!/usr/bin/env node
// The doctor sent no API key at all, and it went unnoticed until a key existed.
//
// THE INCIDENT, 2026-09-01. `API_KEY=banana` went into `.env` at the operator's request. Every doctor
// check that reads the service immediately began reporting "the service did not answer" --
// `context-window`, `session-handles`, `env-processes` -- and `bridge-current` VANISHED from the
// report entirely, taking the count from 15 to 14. The one tool whose whole job is to say what is
// really running went blind at the exact moment the fleet's posture changed, and it said the service
// was silent while the service was up and rejecting it.
//
// TWO SEPARATE DEFECTS, both latent while no key was set:
//
//   1. `doctor.js`'s fetch helper sent no `X-API-Key` header, and resolved no key from anywhere.
//   2. A 401 was folded into "unreachable" by `if (!res.ok) return null`, so the report told an
//      operator to check whether a running service was running.
//
// READING `.env` IS THE POINT, not a convenience. This is the same hole `scripts/api-key.sh` exists
// for: install.sh resolved the key from the SHELL only, so the moment an operator set `API_KEY` in
// `.env` the service began refusing callers while every client held no key -- and re-running the
// installer wrote the same keyless config again. A host-side tool that reads only its own environment
// cannot see what the service was started with, because the service reads the file.

import assert from "node:assert/strict";
import { test } from "node:test";

import { apiKeyInEnvFile, resolveDoctorApiKey } from "../doctor-api-key.mjs";
// DERIVED from the one module that owns the precedence. Retyping these names here would fork the
// very list the registry gate exists to keep single -- and this test would then pass while the
// bridge and the doctor disagreed about which variable carries a key.
import { API_KEY_ENV_NAMES, apiKeyFrom } from "../aify-service-endpoint.mjs";

const join = (a, b) => `${a}/${b}`;
const fileSaying = (text) => (path) => {
  if (path !== "/repo/.env") throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
  return text;
};
const noFile = () => { throw Object.assign(new Error("ENOENT"), { code: "ENOENT" }); };

// -- reading the file ------------------------------------------------------------------------------

test("a plain assignment is read", () => {
  assert.equal(apiKeyInEnvFile("API_KEY=banana\n"), "banana");
});

test("the shapes real .env files actually use", () => {
  assert.equal(apiKeyInEnvFile("export API_KEY=banana"), "banana", "export prefix");
  assert.equal(apiKeyInEnvFile('API_KEY="banana"'), "banana", "double quotes");
  assert.equal(apiKeyInEnvFile("API_KEY='banana'"), "banana", "single quotes");
  assert.equal(apiKeyInEnvFile("API_KEY = banana"), "banana", "spaces around =");
  assert.equal(apiKeyInEnvFile("# API_KEY=old\nAPI_KEY=banana\n"), "banana", "a commented-out line first");
  assert.equal(apiKeyInEnvFile("\n\nAPI_KEY=banana\n"), "banana", "blank lines");
});

test("a trailing comment is stripped, but a hash inside the value is not", () => {
  // Both directions matter: eating the hash would silently send a WRONG key, which reads exactly like
  // sending none, and this file exists because a key that is not sent is invisible.
  assert.equal(apiKeyInEnvFile("API_KEY=banana  # the live one"), "banana");
  assert.equal(apiKeyInEnvFile("API_KEY=ban#ana"), "ban#ana");
});

test("a file that says nothing about API_KEY yields nothing", () => {
  // NEGATIVE CONTROL. A reader that returned something here would attach a wrong header to every
  // request and turn a working doctor into a refused one.
  assert.equal(apiKeyInEnvFile("SERVICE_VERSION=0.6.0\nCOMPOSE_PROJECT_NAME=aify\n"), "");
  assert.equal(apiKeyInEnvFile("# API_KEY=commented-out-only\n"), "");
  assert.equal(apiKeyInEnvFile("API_KEY=\n"), "", "an empty assignment is not a key");
  assert.equal(apiKeyInEnvFile(""), "");
  assert.equal(apiKeyInEnvFile(undefined), "");
});

test("a name that merely ENDS with API_KEY is not API_KEY", () => {
  // `CLAUDE_MCP_API_KEY` lives in the same file and is a different variable. An unanchored match would
  // read it as the service key and send the wrong one.
  assert.equal(apiKeyInEnvFile("CLAUDE_MCP_API_KEY=other\n"), "");
  assert.equal(apiKeyInEnvFile("CLAUDE_MCP_API_KEY=other\nAPI_KEY=banana\n"), "banana");
});

// -- precedence ------------------------------------------------------------------------------------

test("the shell wins over the checkout", () => {
  // An operator who exported a key is pointing this run at something specific -- a remote endpoint, a
  // second service -- and a file in the checkout must not override what they just typed.
  const got = resolveDoctorApiKey({
    env: { [API_KEY_ENV_NAMES[0]]: "exported" }, repoDir: "/repo",
    readFile: fileSaying("API_KEY=in-the-file"), join,
  });
  assert.deepEqual(got, { key: "exported", source: "the environment" });
});

test("and .env is used when the shell says nothing", () => {
  // POSITIVE CONTROL for the case that was actually broken: the operator's key is in the file and
  // this doctor's shell has never heard of it.
  const got = resolveDoctorApiKey({
    env: {}, repoDir: "/repo", readFile: fileSaying("API_KEY=banana"), join,
  });
  assert.deepEqual(got, { key: "banana", source: ".env" });
});

test("every declared env name is honoured, in order", () => {
  // DERIVED from the exported list rather than retyped, so a name added there cannot be silently
  // unread -- the failure this whole file is about.
  for (const name of API_KEY_ENV_NAMES) {
    const got = resolveDoctorApiKey({ env: { [name]: "k" }, repoDir: "", join });
    assert.equal(got.key, "k", `${name} is declared but not read`);
    assert.equal(got.source, "the environment");
  }
  const first = API_KEY_ENV_NAMES[0];
  const second = API_KEY_ENV_NAMES[1];
  assert.equal(
    resolveDoctorApiKey({ env: { [first]: "a", [second]: "b" }, repoDir: "", join }).key, "a",
    "declaration order is not the precedence order",
  );
});

test("the environment half is whatever apiKeyFrom says, never a second opinion", () => {
  // NOT a trim test, and it started life as one. `apiKeyFrom` does not trim, so a whitespace-only
  // variable counts as a key -- and the BRIDGE sends it on that basis. Making the doctor tidier here
  // would mean the two disagree about whether a key exists, which is precisely the class of bug this
  // file was written for: one component believing it is authenticated while another does not.
  //
  // So the property worth pinning is agreement, not cleanliness. If `apiKeyFrom` ever learns to trim,
  // this follows it for free.
  for (const value of ["   ", "banana", ""]) {
    const env = { [API_KEY_ENV_NAMES[0]]: value };
    assert.equal(
      resolveDoctorApiKey({ env, repoDir: "", join }).key, apiKeyFrom(env),
      `the doctor and the bridge disagree about whether ${JSON.stringify(value)} is a key`,
    );
  }
});

// -- absence is quiet ------------------------------------------------------------------------------

test("no .env is the ordinary state, not an error", () => {
  // A host that never set a key must not have its doctor throw. Every other check would be lost with
  // it, which is a far worse outcome than one missing header.
  const got = resolveDoctorApiKey({ env: {}, repoDir: "/repo", readFile: noFile, join });
  assert.deepEqual(got, { key: "", source: "" });
});

test("no repo, no readers, no arguments at all", () => {
  assert.deepEqual(resolveDoctorApiKey({ env: {}, repoDir: "" }), { key: "", source: "" });
  assert.deepEqual(resolveDoctorApiKey({}), { key: "", source: "" });
  assert.deepEqual(resolveDoctorApiKey(), { key: "", source: "" });
});

test("source is reported so a refusal can name the file it came from", () => {
  // "the service refused the key from .env" and "refused the key you exported" send an operator to
  // different places. A bare boolean would send them to neither.
  assert.equal(resolveDoctorApiKey({ env: {}, repoDir: "/repo", readFile: fileSaying("API_KEY=x"), join }).source, ".env");
  assert.equal(resolveDoctorApiKey({ env: { [API_KEY_ENV_NAMES[0]]: "x" }, repoDir: "", join }).source, "the environment");
});

// ── the credential store: the only source that survives being run from the wrong folder (D11) ────
//
// The two sources above are the operator's shell and a file inside the CHECKOUT. An agent running
// `aify-comms doctor` from its own working directory has neither: the installed doctor lives under
// `~/.aify-comms`, whose parent is not a git checkout, so `findRepo()` returns null and there is no
// `.env` to read. Reproduced from the home directory 2026-09-03 -- EIGHT checks lost their answer
// (env-bridge, bridge-current, context-window, session-handles, env-processes, managed-orphans,
// gateway-orphans, api-exposure) against a service that was up and rejecting them.
//
// Verified against the REAL host as well as these fakes: with no repo and no shell key the resolver
// returns the store's key, and it is the SAME VALUE as the one in `.env` -- so the fallback
// authenticates identically rather than merely producing something.

/** A path-aware fake: the store case reads TWO files, which `fileSaying` above cannot express. */
const filesystem = (files) => (path) => {
  const key = String(path).split("\\").join("/");
  if (!(key in files)) throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
  return files[key];
};
const joinAll = (...parts) => parts.join("/");
const HOME = "/home/op";
const REGISTRY = `${HOME}/.aify/services.json`;
const registrySaying = (ref) => JSON.stringify({ version: 1, services: { "aify-comms": { credentialRef: ref } } });
const storeAt = (ref) => `${HOME}/.aify/credentials/${ref}`;

test("WITH NO REPO, the key comes from aify-env's credential store", () => {
  const got = resolveDoctorApiKey({
    env: {}, repoDir: "", homeDir: HOME, join: joinAll,
    readFile: filesystem({ [REGISTRY]: registrySaying("aify-comms.key"), [storeAt("aify-comms.key")]: "stored-key\n" }),
  });
  assert.deepEqual(got, { key: "stored-key", source: "aify-env's credential store" });
});

test("THE CHECKOUT STILL WINS over the store, so nothing that works today changes", () => {
  // The whole change is additive. A host with a `.env` resolves exactly as it did before, and this
  // is the assertion that says so -- both sources are present and readable here.
  const got = resolveDoctorApiKey({
    env: {}, repoDir: "/repo", homeDir: HOME, join: joinAll,
    readFile: filesystem({
      "/repo/.env": "API_KEY=from-dotenv",
      [REGISTRY]: registrySaying("aify-comms.key"),
      [storeAt("aify-comms.key")]: "stored-key",
    }),
  });
  assert.deepEqual(got, { key: "from-dotenv", source: ".env" });
});

test("a repo whose .env says nothing FALLS THROUGH to the store", () => {
  // The in-between case: a checkout was found, and it simply carries no key. Before this it ended
  // the search; the store is a further source, not a replacement for a missing file.
  const got = resolveDoctorApiKey({
    env: {}, repoDir: "/repo", homeDir: HOME, join: joinAll,
    readFile: filesystem({
      "/repo/.env": "# nothing here\nOTHER=1",
      [REGISTRY]: registrySaying("aify-comms.key"),
      [storeAt("aify-comms.key")]: "stored-key",
    }),
  });
  assert.equal(got.source, "aify-env's credential store");
});

test("A REF THAT IS A PATH IS REFUSED, not opened", () => {
  // The registry is a SHARED file other installers write, and this function opens whatever it is
  // handed. A ref carrying `../` is the one input here that could read a file nobody intended, so
  // it is refused by the same grammar aify-env applies at read time.
  const opened = [];
  const readFile = (path) => {
    opened.push(String(path));
    if (String(path) === REGISTRY) return registrySaying("../../.ssh/id_rsa");
    throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
  };
  const got = resolveDoctorApiKey({ env: {}, repoDir: "", homeDir: HOME, join: joinAll, readFile });
  assert.deepEqual(got, { key: "", source: "" });
  assert.deepEqual(opened, [REGISTRY], "a path-shaped credentialRef was opened");
});

test("AIFY_SERVICE_REGISTRY points the lookup elsewhere", () => {
  const got = resolveDoctorApiKey({
    env: { AIFY_SERVICE_REGISTRY: "/elsewhere/services.json" },
    repoDir: "", homeDir: HOME, join: joinAll,
    readFile: filesystem({
      "/elsewhere/services.json": registrySaying("other.key"),
      [storeAt("other.key")]: "elsewhere-key",
    }),
  });
  assert.equal(got.key, "elsewhere-key");
});

test("every way the store can say nothing is quiet, not an error", () => {
  const quiet = (files) => resolveDoctorApiKey({
    env: {}, repoDir: "", homeDir: HOME, join: joinAll, readFile: filesystem(files),
  });
  // No registry at all -- the ordinary state on a host that never installed aify-env.
  assert.deepEqual(quiet({}), { key: "", source: "" });
  // A registry that is not JSON. `service-registry.mjs` deliberately REFUSES to rewrite one it
  // cannot read, so a broken registry can legitimately be sitting there.
  assert.deepEqual(quiet({ [REGISTRY]: "{{{ not json" }), { key: "", source: "" });
  // A registry naming no ref for this service.
  assert.deepEqual(quiet({ [REGISTRY]: JSON.stringify({ services: { other: { credentialRef: "x" } } }) }), { key: "", source: "" });
  // A ref whose file is missing -- the credential was removed but the registry still names it.
  assert.deepEqual(quiet({ [REGISTRY]: registrySaying("gone.key") }), { key: "", source: "" });
  // A credential file that is empty or whitespace: present, and says nothing.
  assert.deepEqual(quiet({ [REGISTRY]: registrySaying("k"), [storeAt("k")]: "   \n" }), { key: "", source: "" });
});

test("no homeDir means no store lookup, rather than a path built from nothing", () => {
  // A caller that does not supply a home must not have `undefined/.aify/...` opened on its behalf.
  const opened = [];
  const got = resolveDoctorApiKey({
    env: {}, repoDir: "", join: joinAll, readFile: (p) => { opened.push(p); return ""; },
  });
  assert.deepEqual(got, { key: "", source: "" });
  assert.deepEqual(opened, []);
});
