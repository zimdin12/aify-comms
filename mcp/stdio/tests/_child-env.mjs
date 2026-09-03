// A child-process environment that cannot inherit the live agent's identity, service or session.
//
// WHY THIS EXISTS. Three review rounds in a row failed on the same shape: a test seals the variables it knows
// about, runs green on a machine where the others happen to be unset, and fails in a live wrapper environment
// where they are not. First the hermes active-session file (a Python test), then CLAUDE_MCP_SERVER_URL and
// CLAUDE_MCP_API_KEY (17 + 12 bridge tests), then this — a child process that inherited
// AIFY_HERMES_GATEWAY_URL / AIFY_HERMES_ACTIVE_SESSION_FILE because its parent passed the env map into the
// wrong parameter and silently overrode nothing.
//
// DELETION, NOT `KEY: ""`. Empty-string overrides do work on this platform (measured), but they are a value
// rather than an absence: code that checks `"KEY" in process.env`, or reads it before trimming, sees something.
// Deleting the key is the only form that means "this does not exist here", and it is one obvious thing rather
// than a per-test convention.
//
// A CHILD IS THE WORST PLACE TO GET THIS WRONG, which is why it gets a helper rather than a rule to remember:
// the parent's seals do not reach it, the failure is invisible on any machine where the carrier is unset, and
// the child usually reports the leak as a confusing assertion about something else.

// Everything a live wrapper exports that would make a spawned child act as, or talk to, the operator's
// running fleet. Both names of every aliased pair, because the modules read the legacy name FIRST.
export const LIVE_ENV_CARRIERS = Object.freeze([
  // which service answers, and with what credential
  "AIFY_SERVER_URL", "CLAUDE_MCP_SERVER_URL",
  "AIFY_API_KEY", "CLAUDE_MCP_API_KEY",
  "AIFY_SERVER_FALLBACK_URLS", "CLAUDE_MCP_FALLBACK_URLS",
  // which hermes session/gateway the child would bind to
  "AIFY_HERMES_ACTIVE_SESSION_FILE", "HERMES_TUI_ACTIVE_SESSION_FILE",
  "AIFY_HERMES_GATEWAY_URL", "HERMES_TUI_GATEWAY_URL",
  "HERMES_SESSION_ID", "HERMES_SESSION",
  // who the child would BE
  "AIFY_AGENT_ID", "AIFY_AGENT_ROLE", "AIFY_RUNTIME", "AIFY_TERMINAL_ID",
  // role flags that make a process take over fleet responsibilities — never inherited into a test child
  "AIFY_ENVIRONMENT_BRIDGE", "AIFY_ENVIRONMENT_ID", "AIFY_MANAGED_VIA_WRAPPER",
]);

/**
 * The files a child would find the LIVE FLEET through, even with every carrier above removed.
 *
 * MEASURED 2026-09-03, and it cost the operator's host its spawn claim for two minutes. The
 * cross-repo tests start a REAL aify-env from the checkout and seal `AIFY_SERVER_URL` — but
 * aify-env does not need it. It finds its service through the REGISTRY, `~/.aify/services.json`,
 * and its credential through `~/.aify` beside it. So the test daemon read the operator's real
 * registry, discovered the live aify-comms, loaded its plugin, and started CLAIMING against
 * production: `windows:StevenZ-L:default` changed hands, the operator's aify-env began answering
 * "not the claimer", and when the test daemon exited it left a stale claim behind. New spawns could
 * not be claimed by anything until a reconciler released it.
 *
 * SEALING AN ENV VAR IS NOT SEALING AN INPUT. This repo already has that lesson written down for
 * `.env` and for `~/.claude.json`; a registry on disk is the same shape, and the previous carrier
 * list looked complete precisely because every name in it was a variable.
 *
 * Pointed at a directory the test owns rather than unset: unset falls back to the home directory,
 * which is the file being sealed against.
 */
/** A path that exists nowhere, so a reader finds nothing rather than the operator's file. */
export const SEALED_PATH = "/aify-sealed-in-tests/does-not-exist.json";

export const LIVE_FILE_CARRIERS = Object.freeze([
  "AIFY_SERVICE_REGISTRY",
  "AIFY_ENV_PROCESS_RECORD",
]);

/**
 * AND ONE THAT HAS NO OVERRIDE, recorded rather than pretended away.
 *
 * `credentialRoot()` in aify-env is `path.join(os.homedir(), ".aify", "credentials")` with no
 * environment variable in front of it, so a child cannot be pointed away from the operator's real
 * credential store except by moving HOME. Sealing the registry is what stops a test daemon FINDING
 * a service to claim, which is the harm that was measured; the credentials it could still read are
 * the operator's own and it now has no service to spend them on.
 *
 * Named here so the next person does not add "AIFY_CREDENTIAL_ROOT" to the list above and believe
 * they have sealed something -- a variable nothing reads is a seal that cannot hold, and this repo
 * spent a night on defects of exactly that shape.
 */
export const UNSEALABLE_BY_ENV = Object.freeze(["aify-env credentialRoot() -> os.homedir()"]);

/**
 * `process.env` with every live carrier REMOVED, then `extra` applied.
 *
 * Pass this as `env` to spawn/spawnSync/fork. `extra` is for what the test wants the child to see — including
 * re-adding a carrier deliberately, e.g. a fake service URL.
 */
export function sealedChildEnv(extra = {}) {
  const env = { ...process.env };
  for (const name of LIVE_ENV_CARRIERS) delete env[name];
  // FILE CARRIERS ARE POINTED AWAY, NOT DELETED. Deleting one falls back to the home directory,
  // which is the file being sealed against -- so an unset `AIFY_SERVICE_REGISTRY` is not a seal, it
  // is the default. A caller that wants a real registry passes one through `extra`, which is applied
  // below and wins.
  for (const name of LIVE_FILE_CARRIERS) env[name] = SEALED_PATH;
  for (const [key, value] of Object.entries(extra)) {
    if (value === undefined) delete env[key];
    else env[key] = String(value);
  }
  return env;
}

/** The carriers a child would still inherit from `env`. Empty means sealed; used by tests and by the gate. */
export function leakedCarriers(env) {
  return LIVE_ENV_CARRIERS.filter((name) => env[name] !== undefined);
}
