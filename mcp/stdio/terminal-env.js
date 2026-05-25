import { normalizeRuntime, prepareManagedCodexHome, sessionEnvVarsForRuntime } from "./runtimes.js";

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
    CLAUDE_SESSION_ID: baseEnv.CLAUDE_SESSION_ID || "",
    CODEX_THREAD_ID: baseEnv.CODEX_THREAD_ID || "",
    HERMES_SESSION_ID: baseEnv.HERMES_SESSION_ID || "",
    PI_SESSION_ID: baseEnv.PI_SESSION_ID || "",
    AIFY_ENVIRONMENT_BRIDGE: "0",
    AIFY_MANAGED_DISPATCH: "0",
    AIFY_TERMINAL_ID: terminalId || "",
    // Declare the spawn context: this PTY was created by aify-comms as a
    // managed wrapper, not by a human running the wrapper interactively.
    // The wrapper (claude-aify / pi-aify / codex-aify / hermes-aify /
    // opencode) inherits this and the inner mcp/stdio/server.js child
    // reads it for the /agents register call so the service knows this
    // is a managed session, not a resident one. Operator-launched
    // wrappers don't have this env set and auto-detect via TTY.
    AIFY_SESSION_MODE: "managed",
    // Plan 5 follow-up (2026-05-26): mark the inner bridge as a wrapper
    // child so server.js:2033 adds `channel` + `resident` to its
    // executionModes for this agent. Without this flag, only the generic
    // `supportedExecutionModes` path fires and live testing showed the
    // bridge inside fresh dashboard-spawned hermes-aify / codex-aify /
    // pi-aify wrappers never claimed channel-mode runs (graph-senior-dev
    // and friends sat queued indefinitely). Mirrors the explicit-override
    // shape claude-channel.js uses inside claude-aify.
    AIFY_MANAGED_VIA_WRAPPER: "1",
  };
  for (const name of sessionEnvVarsForRuntime(key)) {
    env[name] = handle;
  }
  if (key === "codex") {
    env.CODEX_HOME = prepareCodexHome({ workspace: workspace || "" });
  }
  return env;
}
