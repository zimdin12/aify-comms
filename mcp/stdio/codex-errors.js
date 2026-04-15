// Classification helpers for Codex thread/resume failures, plus the
// resident-vs-spawn path-format decision for turn/start requests.
//
// Kept in a standalone module so tests can import them without pulling
// in the full runtimes.js dependency graph (opencode-ai SDK, etc.).
// runtimes.js re-imports and uses these in the controller.

export function detectCodexResumeFailure(error) {
  const message = String(error?.message || error || "");
  const noRollout = message.includes("no rollout found for thread id");
  // "AbsolutePathBuf deserialized without a base path" and the newer
  // "AbsolutePathBufGuard" variant mean a path field Codex tried to
  // deserialize was not absolute on the current OS. During thread/resume
  // this can come from the stored rollout; during turn/start it comes
  // from the cwd / writable_roots fields we send. We treat both cases
  // as heal-worthy so the dispatch keeps going on a fresh thread.
  const corruptRollout =
    message.includes("AbsolutePathBuf deserialized") ||
    message.includes("AbsolutePathBufGuard");
  return {
    noRollout,
    corruptRollout,
    shouldHeal: noRollout || corruptRollout,
    healReason: corruptRollout ? "corrupt_rollout" : (noRollout ? "no_rollout" : null),
  };
}

// Decide the path format to send to Codex over JSON-RPC.
//
// Background: on Windows, defaultCodexCommand() returns `wsl.exe -e codex
// app-server`, so isWslCodexLauncher(launcher) is true by default. The
// previous (wrong) behavior ran every hostCwd through toWslPath, turning
// "C:/Docker/project" into "/mnt/c/Docker/project" regardless of whether
// we were going to spawn a WSL Codex ourselves or just connect to an
// app-server someone else already started.
//
// codex-aify is the "someone else" case: it launches a NATIVE codex on
// the host OS and publishes its ws:// URL via AIFY_CODEX_APP_SERVER_URL.
// When our bridge sends "/mnt/c/..." to that native Windows Codex, Rust's
// Path::is_absolute() returns false (no drive-letter prefix) and
// AbsolutePathBuf::deserialize throws "AbsolutePathBuf deserialized without
// a base path". Result: every resident dispatch on Windows fails.
//
// Rule: when appServerUrl is set, the remote Codex is native to this OS,
// so we send the host cwd with only backslash-to-forward-slash normalization
// (Codex accepts `C:/foo` on Windows; on Linux the replace is a no-op).
// When appServerUrl is empty, we're spawning our own Codex via launcher,
// so defer to the legacy launcher-dispatched transform.
export function resolveCodexRequestCwdFor({ hostCwd, appServerUrl, legacyTransform }) {
  const raw = String(hostCwd || "");
  if (appServerUrl) return raw.replace(/\\/g, "/");
  return legacyTransform(raw);
}
