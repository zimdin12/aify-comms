export function terminalControlFailurePatch(action = "", error) {
  const normalizedAction = String(action || "").trim().toLowerCase();
  const message = error?.message || String(error || "");
  const lateAfterExit = /terminal\s+"?.+?"?\s+is not running/i.test(message);
  if (lateAfterExit && ["input", "resize", "stop"].includes(normalizedAction)) {
    return { status: "failed", terminalStatus: "stopped", error: message };
  }
  return { status: "failed", terminalStatus: "failed", error: message };
}
