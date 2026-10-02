// Are the hosts that define agents on this service still pushing those definitions? (P0 C11, arm 3)
//
// A host that stops pushing (its aify-env downgraded or stopped, or a new claimer that never pushes) leaves
// its definitions governing as last pushed. That is by design, and the operator must be able to see it.
// The SERVICE decides staleness, on each environment row (`definitions.notice`, api_core/definition_freshness.py);
// this row prints the service's sentence and decides nothing, so the dashboard badge and the doctor cannot
// disagree about the threshold.
//
// THREE ANSWERS THAT MUST STAY APART. An unreachable service is unknown. A service whose rows carry no
// `definitions` key at all predates this field, which is not the same as no host pushing: that is skipped.
// Every row carrying `definitions: null` is a real "no host pushes definitions here".

export function definitionsFreshVerdict({ environments }) {
  if (environments === null || environments === undefined) {
    return { skipped: false, ok: false, code: "unknown-service",
      detail: "the service did not list its environments, so whether hosts still push their definitions is unknown",
      fix: "see the service row" };
  }
  const rows = environments.filter((env) => env && typeof env === "object");
  if (rows.length && rows.every((env) => !("definitions" in env))) {
    return { skipped: true, detail: "this service predates definition freshness (no `definitions` on its environments)" };
  }
  const pushing = rows.filter((env) => env.definitions);
  const stale = pushing.filter((env) => String(env.definitions.notice || "").trim());
  if (stale.length) {
    return { skipped: false, ok: false, code: "not-refreshed",
      detail: `${stale.map((env) => env.definitions.notice).join("; ")}. Those definitions still govern as last pushed.`,
      fix: "on that machine, check aify-env is running and current: `aify-env doctor`" };
  }
  if (!pushing.length) return { skipped: false, ok: true, code: "none", detail: "no host pushes agent definitions to this service" };
  return { skipped: false, ok: true, code: "fresh",
    detail: `${pushing.length} host(s) push their agent definitions, each within the last 10 minutes` };
}
