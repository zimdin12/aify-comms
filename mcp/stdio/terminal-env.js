import { normalizeRuntime, prepareManagedCodexHome } from "./runtimes.js";

export function terminalChildEnv({
  baseEnv = process.env,
  runtime = "",
  sessionHandle = "",
  workspace = "",
  terminal = {},
  terminalId = "",
  prepareCodexHome = prepareManagedCodexHome,
} = {}) {
  const key = normalizeRuntime(runtime || terminal.runtime || "");
  const handle = String(sessionHandle || "").trim();
  const env = {
    ...baseEnv,
    AIFY_RUNTIME: key,
    AIFY_AGENT_ID: terminal.agentId || "",
    AIFY_COMMS_AGENT_ID: terminal.agentId || "",
    AIFY_AGENT_CWD: workspace || "",
    AIFY_SESSION_HANDLE: handle,
    CLAUDE_SESSION_ID: key === "claude-code" ? handle : (baseEnv.CLAUDE_SESSION_ID || ""),
    CODEX_THREAD_ID: key === "codex" ? handle : (baseEnv.CODEX_THREAD_ID || ""),
    HERMES_SESSION_ID: key === "hermes" ? handle : (baseEnv.HERMES_SESSION_ID || ""),
    PI_SESSION_ID: key === "pi" ? handle : (baseEnv.PI_SESSION_ID || ""),
    AIFY_ENVIRONMENT_BRIDGE: "0",
    AIFY_MANAGED_DISPATCH: "0",
    AIFY_TERMINAL_ID: terminalId || "",
  };
  if (key === "codex") {
    env.CODEX_HOME = prepareCodexHome({ workspace: workspace || "" });
  }
  return env;
}
