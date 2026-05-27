const WRAPPER_BACKED_RUNTIMES = new Set(["codex", "hermes"]);

export function managedViaWrapperRuntimesFromSettingsResponse(resp) {
  const val = resp?.settings?.managed_via_wrapper ?? resp?.managed_via_wrapper;
  if (val === true) return new Set(WRAPPER_BACKED_RUNTIMES);
  if (!Array.isArray(val)) return new Set();
  return new Set(
    val
      .map((runtime) => String(runtime || "").trim().toLowerCase())
      .filter((runtime) => WRAPPER_BACKED_RUNTIMES.has(runtime)),
  );
}
