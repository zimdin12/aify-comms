import { normalizeRuntime, prepareManagedCodexHome, sessionEnvVarsForRuntime } from "./runtimes.js";
import { withoutInheritedMarkers } from "./child-env-hygiene.mjs";

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
  // The Reset instruction, read from wherever the spawn recorded it.
  const resumePolicy = String(
    agentInfo?.runtimeState?.resumePolicy || terminal?.runtimeState?.resumePolicy || "",
  ).trim().toLowerCase();
  const env = {
    // STRIPPED FIRST, then the values this function owns are set below. The spread is how a bridge's
    // own ancestry reaches everything it starts, and it has been discovered one symptom at a time --
    // a role that overwrote every spawn's, a marker that turned off every transcript. The list lives
    // in child-env-hygiene.mjs so the third one is refused rather than found.
    //
    // The explicit assignments further down are NOT redundant with it: those set a value this
    // function is responsible for, which is a different act from removing one that leaked in.
    ...withoutInheritedMarkers(baseEnv),
    AIFY_RUNTIME: key,
    // FRESH CONTEXT, SET UNCONDITIONALLY -- including to "". The spread above carries the environment
    // BRIDGE's own environment into everything it launches, so a value merely left unset here is
    // INHERITED rather than absent, and one Reset would make every later spawn start fresh forever.
    // That is the AIFY_AGENT_ROLE lesson below, and this is the same shape.
    //
    // `resumePolicy: "fresh_context"` is written by session_restart.py on a Reset and carried by
    // spawn-loop.mjs. Until 2026-08-31 ONLY codex read it, so a hermes Reset reported success and
    // resumed anyway: comms-senior-dev stayed on a 5 JUNE conversation until it reached 1,122,638
    // tokens against a 900k window and could no longer answer at all. `runResolveSessionCli` reads
    // this and refuses every resume path.
    AIFY_HERMES_FRESH_CONTEXT: resumePolicy === "fresh_context" ? "1" : "",
    AIFY_AGENT_ID: terminal.agentId || "",
    AIFY_COMMS_AGENT_ID: terminal.agentId || "",
    // The spawn's ROLE, and it must be set unconditionally — including to "".
    //
    // Two bugs lived in its absence. The visible one: the inner mcp/stdio/server.js child read
    // `process.env.AIFY_AGENT_ROLE`, found nothing, fell back to "coder", and its self-register
    // sent that. Re-register is a full state refresh, so the spawn's real role was overwritten —
    // spawn a `tester`, get a `coder`, with nothing reporting a problem.
    //
    // The worse one: `...baseEnv` above spreads the ENVIRONMENT BRIDGE's environment, so if that
    // process had AIFY_AGENT_ROLE set, every worker it launched inherited it. Clearing the value
    // when the role is unknown is therefore part of the fix, not tidiness: an empty value makes the
    // child fall back to its own default, an inherited one makes it confidently wrong. Same
    // reasoning as AIFY_AGENT_ID being set explicitly on the line above.
    AIFY_AGENT_ROLE: String(agentInfo?.role || terminal.role || "").trim(),
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
