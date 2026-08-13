// Which runtime this bridge is driving, resolved once from the environment at startup.
//
// `AIFY_RUNTIME` names the coding agent this process serves — claude-code, codex, hermes, pi — and
// `adapterFor` turns that name into the adapter that knows how to read its transcript, find its session id
// and describe its environment. v0.5.4 layer 0 of the server.js decomposition.
//
// SIXTEEN READERS ACROSS SIX UNRELATED CONCERNS, which is what earned it an owner: the session-handle
// heartbeat's config, the claude turn-end detector, the CODEX turn detector, startup diagnostics,
// `computeInitialSessionHandle`, and `fillSessionHandleFromAdapter`. It came up while measuring the claude
// detector's state, was briefly grouped with it, and does not belong there — a name a detector reads is not
// a name the detector owns. Same shape `IS_REMOTE` and `AIFY_AGENT_ID` had, and the same mistake avoided.
//
// AN UNKNOWN RUNTIME IS NOT AN ERROR, and the `catch` is the whole reason this is five lines rather than
// one. `adapterFor` throws on a name it does not recognise, and a bridge that refused to start on an
// unfamiliar `AIFY_RUNTIME` would take down an agent for a value it does not need — most of what the bridge
// does works without an adapter. So the failure mode is deliberately `null`, and every one of the sixteen
// readers is written to expect it. `null` here means "no transcript-level integration", never "broken".
//
// IT IS `let` BECAUSE IT STARTS null AND MAY BE ASSIGNED ONCE. Nothing outside this module reassigns it, and
// the single assignment happens during this module's own evaluation, so every importer sees the final value.
// The name keeps its `__` prefix: renaming would touch sixteen call sites for no behavioural reason, and it
// reads oddly on an export — worth revisiting deliberately, not as a side effect of a relocation. Same call
// as `__markControllerStart`.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { adapterFor } from "./adapters/index.js";
export let __runtimeAdapter = null;
try {
  const __rt = String(process.env.AIFY_RUNTIME || "").trim();
  if (__rt) __runtimeAdapter = adapterFor(__rt);
} catch { /* unknown runtime — bridge continues without adapter */ }
