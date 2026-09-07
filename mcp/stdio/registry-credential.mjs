// The key aify-env holds for this service, read the way that daemon stores it.
//
// WHY THIS IS ITS OWN MODULE, and it is the whole point of the change that created it. This logic
// lived inside `doctor-api-key.mjs`, so the ONLY thing that could find a key in the credential store
// was the doctor. Every runtime component resolves its key through `aify-http.mjs`, which reads
// environment variables and nothing else -- and it could not import the doctor's module to do
// better, because `tests/doctor-sources.mjs` walks the doctor's imports TRANSITIVELY: importing it
// from `aify-http.mjs` would make the entire bridge "the doctor" and put every file in this package
// under the doctor's staleness gates. So the subject moved out to where both callers can reach it,
// which is this repo's standing answer -- take out a subject somebody else also needs.
//
// WHAT IT COST TO NOT HAVE THIS, measured on the operator's host 2026-09-07. `install.sh` had set an
// `API_KEY`, and the service enforces it from the next restart. Claude agents were unaffected: their
// channel sidecar is an MCP CHILD, and `~/.claude.json` carries the key in that server's `env` block.
// The hermes claimer is a STANDALONE process -- `nohup node hermes-managed-host.js run <agent>`, from
// the launcher -- which inherits `AIFY_SERVER_URL` but no key, because no launcher exports one and
// the wrapper template has no placeholder for it. So every one of its 30-second liveness beats came
// back 401 and was swallowed. It registered once at startup and never again; the service correctly
// concluded there was no claimer and refused to deliver.
//
// The result was FIVE hermes agents reading `online` while accepting no work, and nothing said so:
// `lastSeen` is refreshed by a separate registration beat, so every badge stayed green. Measured
// twice, with a control -- all 8 claude agents at 0 minutes while all 5 hermes agents sat 12 to 148
// minutes stale, and an unauthenticated heartbeat returns 401 where the same beat with the key
// returns 404.
//
// THE CREDENTIAL WAS THERE THE WHOLE TIME. `~/.aify/services.json` published
// `credentialRef: "aify-comms-....key"`, the file existed under `~/.aify/credentials/`, and its
// contents authenticate. The registry, the ref grammar, the store and the writer all worked. Only the
// reader was missing from the path that needed it.

import { credentialRefProblem } from "./credential-ref.mjs";
import { SERVICE_NAME } from "./service-name.mjs";

//: aify-env's own layout: `<home>/.aify/credentials/<ref>`. Named rather than inlined because the
//: doctor's report quotes it and the resolver opens it.
export const CREDENTIAL_DIR_NAME = "credentials";

//: The registry's location, spelled the way `install.sh` and `scripts/install-state.sh` already
//: spell it. A third copy of one default, which is why it is named rather than inlined.
export const REGISTRY_ENV_NAME = "AIFY_SERVICE_REGISTRY";

/**
 * The `credentialRef` a registry names for one service, or "" when it names none.
 *
 * TOLERATES A REGISTRY IT CANNOT PARSE, because a caller wanting a hint must not fail over the file
 * it wanted the hint from -- and an unreadable registry is a thing `service-registry.mjs`
 * deliberately REFUSES to rewrite rather than repair, so it can legitimately be sitting there broken.
 */
export function credentialRefIn(registryText, serviceName) {
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
 *
 * NEVER THROWS. Callers resolve their key at module load, so an exception here would take down a
 * bridge over a missing file rather than leaving it unauthenticated.
 *
 * @returns {{key: string, source: string}} both "" when nothing usable was found
 */
export function keyFromCredentialStore({
  env = {}, readFile, join, homeDir = "", serviceName = SERVICE_NAME,
} = {}) {
  if (typeof readFile !== "function" || typeof join !== "function" || !homeDir || !serviceName) {
    return { key: "", source: "" };
  }
  const registryPath = String(env[REGISTRY_ENV_NAME] || "").trim()
    || join(homeDir, ".aify", "services.json");
  let ref = "";
  try {
    ref = credentialRefIn(readFile(registryPath), serviceName);
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
