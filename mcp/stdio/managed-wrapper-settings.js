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
  return new Set(
    val
      .map((runtime) => String(runtime || "").trim().toLowerCase())
      .filter((runtime) => WRAPPER_BACKED_RUNTIMES.has(runtime)),
  );
}
