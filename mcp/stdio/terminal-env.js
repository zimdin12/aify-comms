import { normalizeRuntime, prepareManagedCodexHome, sessionEnvVarsForRuntime } from "./runtimes.js";

export function terminalChildEnv({
  baseEnv = process.env,
  runtime = "",
  sessionHandle = "",
  workspace = "",
  terminal = {},
  terminalId = "",
  agentInfo = {},
  prepareCodexHome = prepareManagedCodexHome,
  managedViaWrapper = false,
} = {}) {
  const key = normalizeRuntime(runtime || terminal.runtime || "");
  const handle = String(sessionHandle || "").trim();
  const runtimeConfig = agentInfo?.runtimeConfig || terminal.runtimeConfig || {};
  const managedModel = String(agentInfo?.model || runtimeConfig.model || "").trim();
  const managedEffort = String(runtimeConfig.effort || runtimeConfig.thinking || "").trim();
  const env = {
    ...baseEnv,
    AIFY_RUNTIME: key,
    AIFY_AGENT_ID: terminal.agentId || "",
    AIFY_COMMS_AGENT_ID: terminal.agentId || "",
    AIFY_AGENT_CWD: workspace || "",
    AIFY_SESSION_HANDLE: handle,
    CLAUDE_SESSION_ID: baseEnv.CLAUDE_SESSION_ID || "",
    CODEX_THREAD_ID: baseEnv.CODEX_THREAD_ID || "",
    HERMES_SESSION_ID: baseEnv.HERMES_SESSION_ID || "",
    PI_SESSION_ID: baseEnv.PI_SESSION_ID || "",
    AIFY_ENVIRONMENT_BRIDGE: "0",
    AIFY_MANAGED_DISPATCH: "0",
    AIFY_TERMINAL_ID: terminalId || "",
    // Declare the spawn context: this PTY was created by aify-comms as a
    // managed wrapper, not by a human running the wrapper interactively.
    // The wrapper inherits this and the inner mcp/stdio/server.js child reads
    // it for the /agents register call so the service knows this is a managed
    // session, not a resident one. Operator-launched wrappers don't have this
    // env set and auto-detect via TTY.
    AIFY_SESSION_MODE: "managed",
    // Only true wrapper-backed managed runtimes should set this. Pi/OpenCode
    // stay native managed and must not make their child bridge advertise
    // channel/resident claim modes.
    AIFY_MANAGED_VIA_WRAPPER: managedViaWrapper ? "1" : "0",
  };
  if (managedModel) env.AIFY_MANAGED_MODEL = managedModel;
  if (managedEffort) env.AIFY_MANAGED_EFFORT = managedEffort;
  for (const name of sessionEnvVarsForRuntime(key)) {
    env[name] = handle;
  }
  if (key === "codex") {
    env.CODEX_HOME = prepareCodexHome({ workspace: workspace || "", model: managedModel, effort: managedEffort });
  }
  return env;
}
