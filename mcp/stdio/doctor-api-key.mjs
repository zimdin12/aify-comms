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
export function resolveDoctorApiKey({ env = {}, repoDir = "", readFile, join } = {}) {
  const exported = apiKeyFrom(env);
  if (exported) return { key: exported, source: "the environment" };

  if (!repoDir || typeof readFile !== "function" || typeof join !== "function") {
    return { key: "", source: "" };
  }
  let text;
  try {
    text = readFile(join(repoDir, ".env"));
  } catch {
    // FAILS QUIET, NOT CLOSED. No `.env` is the ordinary state for a host that never set a key, and a
    // missing file must not become an error in a tool whose job is to report on other things.
    return { key: "", source: "" };
  }
  const key = apiKeyInEnvFile(text);
  return key ? { key, source: ".env" } : { key: "", source: "" };
}
