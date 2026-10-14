const WRAPPER_BACKED_RUNTIMES = new Set(["codex", "hermes"]);

// NOT KNOWN IS NOT EMPTY. An empty set means "no runtime is wrapper-backed", so this bridge claims and
// spawns managed codex and hermes runs itself. Reading an unreachable or malformed /settings as empty did
// exactly that whenever the first read after a bridge start failed (2026-10-14 audit, S1). A caller given
// this marker claims no wrapper-eligible managed run until the setting is known.
export const WRAPPER_SET_UNKNOWN = Symbol("managed_via_wrapper unknown");

export function managedViaWrapperRuntimesFromSettingsResponse(resp) {
  const val = resp?.settings?.managed_via_wrapper ?? resp?.managed_via_wrapper;
  if (val === true) return new Set(WRAPPER_BACKED_RUNTIMES);
  if (val === false) return new Set();
  if (!Array.isArray(val)) return WRAPPER_SET_UNKNOWN;
  // A member that is not a string is a malformed answer, not an empty one: `[null]` read as "no runtime
  // is wrapper-backed" let this bridge claim native managed codex and hermes (review of 0.9.5). An
  // unrecognised runtime NAME is still filtered out, for compatibility with a newer service.
  if (!val.every((runtime) => typeof runtime === "string")) return WRAPPER_SET_UNKNOWN;
  return new Set(
    val
      .map((runtime) => String(runtime || "").trim().toLowerCase())
      .filter((runtime) => WRAPPER_BACKED_RUNTIMES.has(runtime)),
  );
}
