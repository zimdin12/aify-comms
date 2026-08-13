// Reading a field the service stores as JSON text, when it may not be JSON text.
//
// `runtimeConfig` and `runtimeState` arrive as strings from a database column, and every one of this
// function's six call sites reads one of them. But the same field also arrives ALREADY PARSED from some
// paths, absent from a fresh agent, empty from one that has never had state, and occasionally malformed
// from a half-written write. `parseJson` collapses all five shapes into "an object, or the caller's
// fallback".
//
// v0.5.4 layer 0 of the server.js decomposition. Reviewer-cleared as a pre-spawn owner: it is one of the
// three unowned things `spawnTriggeredAgent` reaches for, and it had no test.
//
// THE `typeof value === "object"` BRANCH IS THE ONE THAT LOOKS WRONG AND IS NOT. It passes an
// already-parsed object straight through, which makes the function idempotent — `parseJson(parseJson(x))`
// is `parseJson(x)`. Without it, a caller handed a real object would get the fallback, silently losing
// the state it was holding, because `JSON.parse` on an object stringifies it to "[object Object]" first
// and throws.
//
// IT SWALLOWS MALFORMED JSON, and that is a deliberate tradeoff worth knowing rather than a missing
// error path. A corrupt `runtimeState` reads exactly like an absent one, so a caller cannot tell a
// damaged record from a new agent. That is the right default here — a bridge that refused to start
// because one agent's state column was truncated would be worse — but it means corruption is invisible
// rather than reported. `local-store.mjs`'s `readAgents` makes the same trade for the same reason.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

export function parseJson(value, fallback) {
  if (value == null || value === "") return fallback;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}
