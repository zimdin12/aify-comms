// The key aify-env holds for this service, read the way that daemon reads it, and only for the
// endpoint the registry names it for.
//
// WHY THIS IS ITS OWN MODULE. The logic lived inside `doctor-api-key.mjs`, so the only thing that
// could find a key in the credential store was the doctor. Every runtime component resolves its key
// through `aify-service-endpoint.mjs`, which read environment variables and nothing else -- and it
// could not import the doctor's module to do better, because `tests/doctor-sources.mjs` walks the
// doctor's imports TRANSITIVELY: importing it there would make the entire bridge "the doctor".
//
// WHAT ENV-ONLY COST, measured 2026-09-07. A standalone claimer (`nohup node
// hermes-managed-host.js run <agent>`, from the launcher) inherits `AIFY_SERVER_URL` and no key.
// Once the service enforced `API_KEY`, every 30-second liveness beat returned 401 and was swallowed:
// five hermes agents read `online` while claiming nothing, and `lastSeen` kept refreshing off a
// separate registration beat so every badge stayed green.
//
// TWO SECURITY FINDINGS AGAINST THE FIRST VERSION OF THIS FILE, both reproduced by review (R2, R3):
//
//   * IT PAIRED A STORED SECRET WITH AN AMBIENT DESTINATION. The key came from the registry; the URL
//     came from whatever `CLAUDE_MCP_SERVER_URL` / `AIFY_SERVER_URL` happened to hold. A stale or
//     foreign endpoint therefore received a credential that had never been on this process's HTTP
//     path before. `keyForEndpoint` now refuses unless the registry entry's OWN endpoint matches the
//     one the caller will actually use.
//
//   * IT WAS `readFileSync(...).trim()`, WHICH IS NOT THE STORE'S CONTRACT. A ref of `mixedcase.key`
//     read `MixedCase.key` on this filesystem, where aify-env returns CREDENTIAL_INSECURE -- two
//     services silently sharing one credential. CRLF, a stray extra newline, an embedded NUL,
//     oversized data and invalid UTF-8 were all accepted here and refused there.
//
// SO THIS IS A CACHED COPY OF SOMEBODY ELSE'S RULES, deliberately, exactly as `credential-ref.mjs`
// is: aify-env owns the contract and is not a dependency of this package, so the alternative to
// duplicating is to keep reading credentials the owner would refuse. `tests/the-credential-read-
// agrees-with-aify-env.test.js` drives BOTH implementations over one corpus and fails when they
// disagree -- and fails when the aify-env checkout is absent rather than skipping, because a
// cross-repo proof that quietly does not run is worse than none.
//
// FAIL CLOSED. Every check answers "" rather than a key. An unauthenticated call gets a legible 401;
// a call carrying a credential read out of the wrong file does not.

import { realpathSync } from "node:fs";
import path from "node:path";

import { credentialRefProblem } from "./credential-ref.mjs";
import { SERVICE_NAME } from "./service-name.mjs";

//: aify-env's own layout: `<home>/.aify/credentials/<ref>`.
export const CREDENTIAL_DIR_NAME = "credentials";

//: The registry's location, spelled the way `install.sh` and `scripts/install-state.sh` spell it.
export const REGISTRY_ENV_NAME = "AIFY_SERVICE_REGISTRY";

//: aify-env's `MAX_CREDENTIAL_BYTES`. Cached with the rest of the contract; the agreement test is
//: what keeps it equal to the owner's value.
export const MAX_CREDENTIAL_BYTES = 4096;

/**
 * The `credentialRef` and `endpoint` a registry declares for one service.
 *
 * TOLERATES A REGISTRY IT CANNOT PARSE: an unreadable registry is a state `service-registry.mjs`
 * deliberately REFUSES to rewrite rather than repair, so it can legitimately be sitting there broken.
 */
export function registryEntryFor(registryText, serviceName) {
  let parsed;
  try {
    parsed = JSON.parse(String(registryText ?? ""));
  } catch {
    return { ref: "", endpoint: "" };
  }
  const services = parsed && typeof parsed === "object" ? parsed.services : null;
  const entry = services && typeof services === "object" ? services[serviceName] : null;
  if (!entry || typeof entry !== "object") return { ref: "", endpoint: "" };
  const ref = typeof entry.credentialRef === "string" ? entry.credentialRef.trim() : "";
  const endpoint = typeof entry.endpoint === "string" ? entry.endpoint.trim() : "";
  return { ref, endpoint };
}

/** Kept for the doctor, which asks only which ref a registry names. */
export function credentialRefIn(registryText, serviceName) {
  return registryEntryFor(registryText, serviceName).ref;
}

/**
 * Do two endpoints name the same service instance?
 *
 * COMPARED AS URLs, not as strings. `http://localhost:8800`, `http://127.0.0.1:8800/` and
 * `http://127.0.0.1:8800` are one destination written three ways, and this bridge itself rewrites
 * localhost to the IPv4 loopback -- so a textual comparison would refuse the ordinary case and send
 * nobody a key. Anything unparseable is not equal to anything, including itself.
 */
export function sameEndpoint(a, b) {
  const norm = (value) => {
    const text = String(value || "").trim();
    if (!text) return null;
    let url;
    try {
      url = new URL(text);
    } catch {
      return null;
    }
    const host = url.hostname === "localhost" ? "127.0.0.1" : url.hostname;
    const port = url.port || (url.protocol === "https:" ? "443" : "80");
    return `${url.protocol}//${host}:${port}`;
  };
  const left = norm(a);
  const right = norm(b);
  return Boolean(left) && left === right;
}

/**
 * The bytes of a credential file, decoded under aify-env's rules.
 *
 * EVERY REFUSAL HERE IS ONE THE OWNER MAKES. Strict UTF-8 because Node's default decoder substitutes
 * U+FFFD and would turn a corrupt file into a plausible key that matches nothing; a required trailing
 * newline because that is how the store writes them, so a file without one was written by something
 * else; no control characters and no surrounding whitespace because a key with them is not the key
 * anybody configured.
 *
 * @returns {string} the credential, or "" for anything the store would refuse
 */
export function decodeCredentialBytes(bytes) {
  if (!bytes) return "";
  const buffer = Buffer.isBuffer(bytes) ? bytes : Buffer.from(String(bytes), "utf8");
  if (buffer.byteLength > MAX_CREDENTIAL_BYTES) return "";
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return "";
  }
  if (!text.endsWith("\n")) return "";
  const value = text.slice(0, -1);
  if (value === "") return "";
  if (Buffer.byteLength(value, "utf8") > MAX_CREDENTIAL_BYTES) return "";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return "";
  if (value.trim() !== value) return "";
  return value;
}

/**
 * Refuse a path whose real on-disk name differs from the one asked for.
 *
 * A CUSTODY FAILURE, NOT A TYPO. On Windows and on macOS's default volume `Foo.key` and `foo.key`
 * are ONE file, so two services whose refs differ only in case silently share a credential and each
 * reads the other's. `realpath.native` returns the TRUE on-disk name, and re-confirms containment
 * against the RESOLVED path so a reparse point that survived cannot land the read outside the root.
 *
 * BOTH SIDES CANONICALISED: on Windows a temp path arrives in 8.3 form (`ADMINI~1`) while
 * `realpath.native` returns the long name -- comparing raw would refuse every credential the store
 * had just written.
 */
function pathIsWhatWasAskedFor(target, realpath) {
  try {
    const real = realpath(target);
    if (path.basename(real) !== path.basename(target)) return false;
    return path.dirname(real) === realpath(path.dirname(target));
  } catch {
    return false;
  }
}

/**
 * The key for THIS endpoint, or "" -- never a key for a destination the registry did not name it for.
 *
 * @param {object} deps
 * @param {string} deps.endpoint  the URL this process will actually send to
 * @param {(p: string) => Buffer|string} deps.readFile  read raw bytes; a Buffer is what lets the
 *        decoder judge UTF-8 and size honestly
 * @returns {{key: string, source: string}} both "" when nothing usable and safe was found
 */
export function keyForEndpoint({
  env = {}, readFile, join, homeDir = "", endpoint = "",
  serviceName = SERVICE_NAME, realpath = realpathSync.native,
} = {}) {
  const nothing = { key: "", source: "" };
  if (typeof readFile !== "function" || typeof join !== "function" || !homeDir || !serviceName) {
    return nothing;
  }
  // NO ENDPOINT, NO KEY. A caller that cannot say where it is sending cannot be given a secret to
  // send there.
  if (!String(endpoint || "").trim()) return nothing;

  const registryPath = String(env[REGISTRY_ENV_NAME] || "").trim()
    || join(homeDir, ".aify", "services.json");
  let entry;
  try {
    entry = registryEntryFor(readFile(registryPath), serviceName);
  } catch {
    return nothing;
  }
  if (!entry.ref || credentialRefProblem(entry.ref)) return nothing;
  // THE BINDING. The registry says which endpoint this credential opens; if the caller is pointed
  // somewhere else, that is exactly the case where handing over the key is wrong.
  if (!sameEndpoint(entry.endpoint, endpoint)) return nothing;

  const file = join(homeDir, ".aify", CREDENTIAL_DIR_NAME, entry.ref);
  if (!pathIsWhatWasAskedFor(file, realpath)) return nothing;
  let value;
  try {
    value = decodeCredentialBytes(readFile(file));
  } catch {
    return nothing;
  }
  return value ? { key: value, source: "aify-env's credential store" } : nothing;
}
