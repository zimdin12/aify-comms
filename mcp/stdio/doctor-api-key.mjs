// The key the doctor must send, resolved the way the SERVICE resolves it.
//
// THE DOCTOR SENT NO KEY AT ALL until 2026-09-01, and nobody noticed because nobody had set one. The
// moment `API_KEY` went into `.env`, every check that talks to the service started reporting "the
// service did not answer" -- `context-window`, `session-handles`, `env-processes` -- and
// `bridge-current` disappeared from the report entirely, taking the count from 15 to 14. The one tool
// whose job is to say what is really running went blind at exactly the moment the fleet's posture
// changed, and it reported silence from a service that was up and rejecting it.
//
// THE ENVIRONMENT HALF IS NOT DECIDED HERE. `apiKeyFrom` owns which variables carry a key and in what
// order, and this module CALLS it rather than repeating the list -- a fork would be a second
// precedence to keep in step, and the registry gate rightly fails any module that types those names
// itself. This file adds exactly one thing on top: the file.
//
// READ FROM `.env`, NOT ONLY FROM THE SHELL. This is the same hole `scripts/api-key.sh` was written
// for: install.sh resolved the service key from the environment only, so the moment an operator set
// `API_KEY` in `.env` the service began refusing callers while every installed client held no key,
// and re-running the installer wrote the same keyless config again. A host-side tool that reads only
// its own shell cannot see what the service was actually started with, because the service reads the
// file. The doctor already locates the repo for every other check; the file is right there.
//
// PRECEDENCE IS SHELL-FIRST, deliberately. An operator who exports a key is pointing this run at
// something specific -- a remote endpoint, a second service -- and a file in the checkout must not
// override the instruction they just typed.

import { apiKeyFrom } from "./aify-service-endpoint.mjs";
import { credentialRefProblem } from "./credential-ref.mjs";
import { SERVICE_NAME } from "./service-registry.mjs";

//: The name the SERVICE reads from `.env`, written ONCE. Deliberately not "the same name as the
//: shell variables": those are `apiKeyFrom`'s to know, and this spelling belongs to the file.
//:
//: A STRING, AND THE PATTERN BUILT FROM IT, rather than the name inlined in a regex literal.
//: `no-missing-sibling-imports` reports a name that a sibling exports and this module uses without
//: importing -- and `aify-service-endpoint.mjs` exports `API_KEY`, so the bare name sitting inside a
//: regex literal read as an undeclared use. The scan neutralises comments, template text and quoted
//: strings; a regex literal is the one place a name still looks like code. Naming it once is the
//: better shape anyway: the magic string had been repeated in a pattern.
const ENV_FILE_KEY_NAME = "API_KEY";
const ENV_FILE_KEY_LINE = new RegExp(`^(?:export\\s+)?${ENV_FILE_KEY_NAME}\\s*=\\s*(.*)$`);

/**
 * The `API_KEY=` value in a `.env`, or "" when the file says nothing usable.
 *
 * Deliberately small: this is not a dotenv parser and must not become one. It reads ONE key, ignores
 * comments and blank lines, and tolerates the `export ` prefix and surrounding quotes because all
 * three appear in real `.env` files. Anything it cannot understand reads as absent, which costs a
 * header rather than inventing a wrong one.
 *
 * ANCHORED ON THE WHOLE NAME. An unanchored match would read the neighbouring key-carrying variables
 * -- which live in this same file -- as the service key and send the wrong one, and a wrong key is
 * indistinguishable from no key in the report it produces.
 */
export function apiKeyInEnvFile(text) {
  for (const rawLine of String(text ?? "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = ENV_FILE_KEY_LINE.exec(line);
    if (!match) continue;
    let value = match[1].trim();
    // A trailing comment is only a comment when whitespace separates it from the value: `banana#1` is
    // a key, `banana  # the live one` is not.
    value = value.replace(/\s+#.*$/, "").trim();
    if ((value.startsWith('"') && value.endsWith('"') && value.length > 1)
      || (value.startsWith("'") && value.endsWith("'") && value.length > 1)) {
      value = value.slice(1, -1);
    }
    if (value) return value;
  }
  return "";
}

// ── the credential store, which is aify-env's layout and therefore a cached decision ────────────
//
// aify-comms cannot import `credential-store.mjs`: separate packages, and aify-env is not a
// dependency. `credential-ref.mjs` already documents this exact situation and this repo's standing
// answer to it -- an agreement test driving both implementations, not a refactor. The same applies
// here, so the directory name is written ONCE and proven against aify-env's own constant.
export const CREDENTIAL_DIR_NAME = "credentials";

//: The registry's location, spelled the way `install.sh` and `scripts/install-state.sh` already
//: spell it. A third copy of one default, which is why it is named rather than inlined.
const REGISTRY_ENV_NAME = "AIFY_SERVICE_REGISTRY";

/**
 * The `credentialRef` a registry names for one service, or "" when it names none.
 *
 * TOLERATES A REGISTRY IT CANNOT PARSE, because a doctor must not fail over a file it only wanted a
 * hint from -- and an unreadable registry is a thing `service-registry.mjs` deliberately REFUSES to
 * rewrite rather than repair, so it can legitimately be sitting there broken.
 */
function credentialRefIn(registryText, serviceName) {
  let parsed;
  try {
    parsed = JSON.parse(String(registryText ?? ""));
  } catch {
    return "";
  }
  const services = parsed && typeof parsed === "object" ? parsed.services : null;
  const entry = services && typeof services === "object" ? services[serviceName] : null;
  const ref = entry && typeof entry === "object" ? entry.credentialRef : "";
  return typeof ref === "string" ? ref.trim() : "";
}

/**
 * The key aify-env holds for this service, read the way that daemon stores it.
 *
 * REFUSES A REF THAT IS A PATH, using the grammar aify-env itself applies at read time. The registry
 * is a shared file that other installers write, so a ref carrying `../` is not hypothetical -- and
 * this function opens whatever it is handed. `credentialRefProblem` is already this repo's cached
 * copy of that rule, with an agreement test behind it; reusing it here means there is still one
 * spelling of the grammar rather than two.
 */
function keyFromCredentialStore({ env = {}, readFile, join, homeDir = "" }) {
  if (typeof readFile !== "function" || typeof join !== "function" || !homeDir) {
    return { key: "", source: "" };
  }
  const registryPath = String(env[REGISTRY_ENV_NAME] || "").trim()
    || join(homeDir, ".aify", "services.json");
  let ref = "";
  try {
    ref = credentialRefIn(readFile(registryPath), SERVICE_NAME);
  } catch {
    return { key: "", source: "" };
  }
  if (!ref || credentialRefProblem(ref)) return { key: "", source: "" };
  try {
    const value = String(readFile(join(homeDir, ".aify", CREDENTIAL_DIR_NAME, ref)) ?? "").trim();
    return value ? { key: value, source: "aify-env's credential store" } : { key: "", source: "" };
  } catch {
    return { key: "", source: "" };
  }
}

/**
 * The key this doctor run should send, and where it came from.
 *
 * `source` is returned because a check that cannot authenticate has to say WHICH key it tried. "the
 * service refused the key from .env" and "refused the key from the environment" send an operator to
 * different places; a bare boolean sends them to neither.
 *
 * @param {{env?: object, repoDir?: string, readFile?: (path: string) => string, join?: Function}} deps
 * @returns {{key: string, source: string}} source is "" when no key was found anywhere
 */
export function resolveDoctorApiKey({ env = {}, repoDir = "", readFile, join, homeDir = "" } = {}) {
  const exported = apiKeyFrom(env);
  if (exported) return { key: exported, source: "the environment" };

  const canRead = typeof readFile === "function" && typeof join === "function";
  if (repoDir && canRead) {
    let text = "";
    try {
      text = readFile(join(repoDir, ".env"));
    } catch {
      // FAILS QUIET, NOT CLOSED. No `.env` is the ordinary state for a host that never set a key, and
      // a missing file must not become an error in a tool whose job is to report on other things.
      text = "";
    }
    const key = apiKeyInEnvFile(text);
    if (key) return { key, source: ".env" };
  }

  // THE CREDENTIAL STORE, which is the only source that works WHEREVER THE DOCTOR RUNS (D11).
  //
  // The two sources above are the operator's shell and a file inside the checkout. An agent running
  // `aify-comms doctor` from its own working directory has neither: the INSTALLED doctor lives in
  // `~/.aify-comms`, whose parent is not a git checkout, so `findRepo()` returns null and there is no
  // `.env` to read. Reproduced from the home directory 2026-09-03 -- EIGHT checks lost their answer
  // (`env-bridge`, `bridge-current`, `context-window`, `session-handles`, `env-processes`,
  // `managed-orphans`, `gateway-orphans`, `api-exposure`) against a service that was up and rejecting
  // them. The one tool whose job is to say what is really running went blind by being run from the
  // wrong folder.
  //
  // LAST, NOT FIRST, and that ordering is deliberate: this is purely additive. Where a checkout is
  // present the resolution is byte-for-byte what it was, so nothing that works today changes.
  return keyFromCredentialStore({ env, readFile, join, homeDir });
}
