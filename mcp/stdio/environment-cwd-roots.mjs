// The environment's effective cwd roots: what the SERVICE last told this bridge its roots are.
//
// Extracted from `server.js` (v0.6 Phase 1). Two small decisions that were unreachable by any test,
// because server.js exports nothing and — until the boot guard landed — could not even be imported
// without registering a bridge.
//
// WHY ONLY THE LOGIC MOVED, and not the state. `remoteEffectiveCwdRoots` is a module-level `let` in
// server.js: `heartbeatEnvironment` writes it from the heartbeat response, `effectiveEnvironmentPayload`
// reads it. Moving the reader here and leaving the writer there would split one piece of mutable state
// across two modules, which is the shape this repo has a standing rule about — closure-captured state
// with readers on both sides of a boundary has no owner. So the STATE stays in server.js and the two
// DECISIONS come here as pure functions: how a response becomes roots, and how roots become a payload.
//
// WHAT THE ROOTS ARE FOR. They gate which working directories this environment will host work in. Get
// the parse wrong and the bridge either advertises roots it cannot serve, or silently drops the ones it
// can — and the symptom is a spawn that is refused with no obvious cause.

/**
 * The roots from a heartbeat response, or `null` when the response says nothing about them.
 *
 * `null` and `[]` are DIFFERENT and the caller depends on it: `null` means "the service did not tell us"
 * (keep whatever we had), while `[]` means "the service told us there are none". Collapsing them would
 * make a malformed response quietly erase a working configuration.
 *
 * @param {any} response the parsed heartbeat response
 * @returns {string[]|null}
 */
export function parseEffectiveCwdRoots(response) {
  const roots = response?.environment?.cwdRoots;
  if (!Array.isArray(roots)) return null;
  return roots.map((root) => String(root || "").trim()).filter(Boolean);
}

/**
 * Apply the effective roots to an environment payload.
 *
 * Returns the payload UNCHANGED when there are no roots to apply, which is what keeps an empty or
 * absent list from overwriting whatever the payload already carried. Never mutates its argument — the
 * caller passes a freshly built payload today, and a mutation here would be invisible until somebody
 * reuses one.
 *
 * @param {object} payload
 * @param {string[]|null|undefined} roots
 */
export function withEffectiveCwdRoots(payload, roots) {
  if (roots && roots.length) {
    return { ...payload, cwdRoots: roots };
  }
  return payload;
}
