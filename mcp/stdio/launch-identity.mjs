// Who this bridge process was LAUNCHED as: the agent id and role handed to it by its wrapper.
//
// v0.5.4 layer 0 of the server.js decomposition. These three names lived at server.js:231-236, read
// from 52 places, and belonged to nobody. They surfaced the same way `IS_REMOTE` did — recorded as a
// "dependency" of the dispatch tool group, because the group reads them. Fifty-two call sites say they
// are not the group's; they are a property of the process, fixed the moment it started.
//
// WHY IT IS FIXED AT START, WHICH IS THE WHOLE POINT OF THE MODULE. `AIFY_AGENT_ID` is exported by the
// `*-aify` wrapper at launch, and environment variables cannot be injected into a running process. A
// session that starts as a plain `claude` and only then calls `comms_register` can never acquire one —
// see `register-identity.js`, which exists entirely to warn about that case. So there is no "refresh"
// here and there must not be one: a module-load read is the correct shape, not a limitation.
//
// THE PLACEHOLDER CASE IS A REAL FAILURE, NOT DEFENSIVENESS. A wrapper or MCP config that writes
// `${AIFY_AGENT_ID}` without expanding it hands this process the literal seven-character string
// `${AIFY_AGENT_ID}` as an agent id. It is truthy, so every downstream `if (AIFY_AGENT_ID)` passes and
// the bridge registers an agent under a name no one can address. `cleanEnvPlaceholder` turns that into
// the empty string, which the same guards correctly read as "no identity".
//
// KNOWN INCONSISTENCY, DELIBERATELY NOT FIXED HERE. Roughly 29 other modules read
// `process.env.AIFY_AGENT_ID` directly and do NOT sanitize it, so an unexpanded placeholder still
// reaches them. `AIFY_AGENT_ROLE` below has the same gap one line down from the fix. Both are
// behavioural changes and this is a structural slice; naming them is what this comment is for, and the
// point of giving the canonical derivation an owner is that those readers now have something to
// converge on.

export function cleanEnvPlaceholder(value) {
  const s = String(value || "").trim();
  return /^\$\{[^}]+\}$/.test(s) ? "" : s;
}

export const AIFY_AGENT_ID = cleanEnvPlaceholder(process.env.AIFY_AGENT_ID || process.env.AIFY_COMMS_AGENT_ID || "");
export const AIFY_AGENT_ROLE = String(process.env.AIFY_AGENT_ROLE || process.env.AIFY_COMMS_AGENT_ROLE || "coder").trim();

// Whether this process was launched to serve DASHBOARD-MANAGED dispatch rather than as an interactive
// session. Set by the spawner, so it is launch identity in the same sense as the agent id above: fixed
// at start, and not something the process can acquire later.
//
// It joined this module in v0.5.4 while measuring the inbox tool group, where it looked like a group
// dependency because one tool reads it. Three readers across the bridge say otherwise.
export const IS_MANAGED_DISPATCH =
  ["1", "true", "yes"].includes(String(process.env.AIFY_MANAGED_DISPATCH || "").toLowerCase());
