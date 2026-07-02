// runtimes-hermes.js — Hermes Agent launcher resolution (PATH lookup plus
// absolute-path probing of known installer locations). Extracted verbatim
// from runtimes.js (task #123). runtimes.js re-exports the public surface.
import fs from "fs";
import { resolveExecutable, inspectShebang, bashShebangFallback, hasExecutable } from "./runtimes-exec.js";

// Common Hermes Agent install locations to probe when PATH lookup fails.
// Upstream installer (NousResearch/hermes-agent/scripts/install.ps1)
// drops Hermes into a per-user venv under AppData on Windows, which is
// NOT on the system PATH — only the User PATH env var, which child
// processes inherit only at process-spawn time. A bridge launched from
// a shell that predates the install never sees it. Probing absolute
// paths is the operator-friendly fallback so the bridge "just works"
// without requiring a setx + bridge-restart-from-fresh-shell dance.
function hermesProbePaths() {
  if (process.platform === "win32") {
    const userProfile = String(process.env.USERPROFILE || "").trim();
    if (!userProfile) return [];
    return [
      `${userProfile}\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe`,
      `${userProfile}\\.local\\bin\\hermes.exe`,
    ];
  }
  const home = String(process.env.HOME || "").trim();
  if (!home) return [];
  return [
    `${home}/.local/bin/hermes`,
    `${home}/.local/share/hermes/hermes-agent/venv/bin/hermes`,
  ];
}

export function defaultHermesCommand() {
  const configured = String(process.env.AIFY_HERMES_COMMAND || process.env.HERMES_COMMAND || "").trim();
  if (process.platform === "win32") {
    if (configured) return { command: configured, args: [] };
    if (hasExecutable("hermes")) return { command: "hermes", args: [] };
    for (const candidate of hermesProbePaths()) {
      try {
        if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
          return { command: candidate, args: [] };
        }
      } catch {
        // best-effort probe; ignore stat errors
      }
    }
    return { command: "hermes", args: [] };
  }
  const target = configured || "hermes";
  const resolved = resolveExecutable(target);
  if (resolved) {
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      return bashShebangFallback(resolved);
    }
    return { command: resolved, args: [] };
  }
  for (const candidate of hermesProbePaths()) {
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
        return { command: candidate, args: [] };
      }
    } catch {
      // best-effort
    }
  }
  return { command: target, args: [] };
}
