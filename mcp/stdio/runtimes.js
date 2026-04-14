import { randomUUID } from "crypto";
import { spawn } from "child_process";
import readline from "readline";

const RUNTIME_ALIASES = new Map([
  ["claude", "claude-code"],
  ["claude-code", "claude-code"],
  ["claude_code", "claude-code"],
  ["codex", "codex"],
  ["generic", "generic"],
]);

function spawnProcess(command, args, options = {}) {
  return spawn(command, args, {
    cwd: options.cwd,
    env: { ...process.env, ...(options.env || {}) },
    stdio: ["pipe", "pipe", "pipe"],
    shell: false,
  });
}

function quoteForDisplay(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function buildSystemPrompt(agentId, agentInfo, run) {
  return [
    `You are agent "${agentId}" with role "${agentInfo.role || "agent"}".`,
    `You were dispatched by "${run.from}".`,
    agentInfo.instructions ? `Standing instructions: ${agentInfo.instructions}` : "",
    "Treat the dispatched message as the current task and work on it directly.",
  ].filter(Boolean).join("\n");
}

function buildUserPrompt(run) {
  return [
    `Dispatch task (${run.type}): ${run.subject}`,
    "",
    run.body || "",
  ].join("\n");
}

function defaultCodexCommand() {
  if (process.platform === "win32") {
    const systemRoot = process.env.SystemRoot || "C:\\Windows";
    return { command: `${systemRoot}\\System32\\wsl.exe`, args: ["-e", "codex", "app-server"] };
  }
  return { command: "codex", args: ["app-server"] };
}

function isWslCodexLauncher(launcher) {
  if (process.platform !== "win32") return false;
  const command = String(launcher?.command || "").toLowerCase().replace(/\\/g, "/");
  return command.endsWith("/wsl.exe") || command === "wsl.exe";
}

function toWslPath(inputPath) {
  const value = String(inputPath || "").trim();
  if (!value) return value;
  const normalized = value.replace(/\\/g, "/");
  const match = normalized.match(/^([A-Za-z]):\/(.*)$/);
  if (!match) return normalized;
  const drive = match[1].toLowerCase();
  const rest = match[2];
  return `/mnt/${drive}/${rest}`;
}

function codexWorkingPath(launcher, cwd) {
  if (!isWslCodexLauncher(launcher)) return cwd;
  return toWslPath(cwd);
}

function codexSpawnCwd(launcher, cwd) {
  if (!isWslCodexLauncher(launcher)) return cwd;
  return process.env.USERPROFILE || process.env.HOMEDRIVE && process.env.HOMEPATH
    ? `${process.env.HOMEDRIVE || "C:"}${process.env.HOMEPATH || "\\Users\\Default"}`
    : "C:\\";
}

function defaultClaudeCommand() {
  return { command: "claude", args: [] };
}

function getRuntimeConfig(agentInfo) {
  return agentInfo.runtimeConfig || {};
}

export function normalizeRuntime(runtime) {
  const key = String(runtime || "generic").trim().toLowerCase();
  return RUNTIME_ALIASES.get(key) || key || "generic";
}

export function canLaunchRuntime(runtime) {
  return normalizeRuntime(runtime) === "claude-code" || normalizeRuntime(runtime) === "codex";
}

export function controlCapabilitiesForRuntime(runtime) {
  switch (normalizeRuntime(runtime)) {
    case "codex":
      return { interrupt: true, steer: true };
    case "claude-code":
      return { interrupt: true, steer: false };
    default:
      return { interrupt: false, steer: false };
  }
}

export function defaultSessionHandleForRuntime(runtime) {
  switch (normalizeRuntime(runtime)) {
    case "codex":
      return process.env.CODEX_THREAD_ID || "";
    case "claude-code":
      return process.env.CLAUDE_SESSION_ID || "";
    default:
      return "";
  }
}

function createRpcClient(proc, { onNotification, onStderr }) {
  const pending = new Map();
  let nextId = 1;

  const stdout = readline.createInterface({ input: proc.stdout });
  stdout.on("line", (line) => {
    const text = line.trim();
    if (!text) return;
    let message;
    try {
      message = JSON.parse(text);
    } catch {
      return;
    }

    if (Object.prototype.hasOwnProperty.call(message, "id")) {
      const pendingRequest = pending.get(message.id);
      if (!pendingRequest) return;
      pending.delete(message.id);
      if (message.error) pendingRequest.reject(new Error(message.error.message || JSON.stringify(message.error)));
      else pendingRequest.resolve(message.result);
      return;
    }

    if (message.method && onNotification) {
      onNotification(message);
    }
  });

  const stderr = readline.createInterface({ input: proc.stderr });
  stderr.on("line", (line) => {
    if (onStderr) onStderr(line);
  });

  function send(payload) {
    proc.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  function request(method, params, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      pending.set(id, {
        resolve: (result) => {
          clearTimeout(timer);
          resolve(result);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      send({ jsonrpc: "2.0", id, method, params });
    });
  }

  function notify(method, params) {
    send({ jsonrpc: "2.0", method, params });
  }

  return { request, notify };
}

function createClaudeController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const config = getRuntimeConfig(agentInfo);
  const launcher = defaultClaudeCommand();
  const sessionId = runtimeState?.sessionId || randomUUID();
  const maxTurns = String(config.maxTurns || 15);
  const timeoutMs = Number(config.timeoutMs || 15 * 60 * 1000);
  const args = [
    ...launcher.args,
    "-p",
    "--output-format", "text",
    "--session-id", sessionId,
    "--max-turns", maxTurns,
    "--append-system-prompt", buildSystemPrompt(agentId, agentInfo, run),
  ];

  if (agentInfo.model) {
    args.push("--model", agentInfo.model);
  }

  const proc = spawnProcess(launcher.command, args, { cwd: agentInfo.cwd || process.cwd() });
  const chunks = [];
  const errChunks = [];
  let settled = false;
  let interrupted = false;

  callbacks.onRuntimeState?.({ sessionId });

  proc.stdout.on("data", (chunk) => chunks.push(chunk));
  proc.stderr.on("data", (chunk) => errChunks.push(chunk));
  proc.stdin.write(buildUserPrompt(run));
  proc.stdin.end();

  const timer = setTimeout(() => {
    if (!settled) {
      proc.kill("SIGTERM");
    }
  }, timeoutMs);

  const promise = new Promise((resolve, reject) => {
    proc.on("error", (error) => {
      settled = true;
      clearTimeout(timer);
      reject(error);
    });

    proc.on("close", (code) => {
      settled = true;
      clearTimeout(timer);
      const stdout = Buffer.concat(chunks).toString("utf-8").trim();
      const stderr = Buffer.concat(errChunks).toString("utf-8").trim();
      if (interrupted) {
        resolve({
          status: "cancelled",
          summary: stdout || stderr || "Run interrupted",
          runtimeState: { sessionId },
        });
        return;
      }
      if (code === 0) {
        resolve({
          status: "completed",
          summary: stdout || "(no output)",
          runtimeState: { sessionId },
        });
        return;
      }
      reject(new Error(stderr || stdout || `Claude exited with code ${code}`));
    });
  });

  return {
    capabilities: controlCapabilitiesForRuntime("claude-code"),
    interrupt: () => {
      interrupted = true;
      if (!settled) proc.kill("SIGTERM");
    },
    steer: async () => {
      throw new Error('Runtime "claude-code" does not support steer');
    },
    promise,
  };
}

function createCodexController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const config = getRuntimeConfig(agentInfo);
  const launcher = defaultCodexCommand();
  const timeoutMs = Number(config.timeoutMs || 20 * 60 * 1000);
  const hostCwd = agentInfo.cwd || process.cwd();
  const cwd = codexWorkingPath(launcher, hostCwd);
  const spawnCwd = codexSpawnCwd(launcher, hostCwd);
  const model = agentInfo.model || config.model || "gpt-5.4";
  const effort = config.effort || "medium";
  const summaryMode = config.summary || "concise";
  const approvalPolicy = config.approvalPolicy || "never";
  const networkAccess = config.networkAccess !== false;
  const proc = spawnProcess(launcher.command, launcher.args, { cwd: spawnCwd });

  let activeTurnId = null;
  let activeThreadId = runtimeState?.threadId || null;
  let finalText = "";
  let finalStatus = "failed";
  let finalError = "";
  let settled = false;
  let rejectPromise;
  let interrupted = false;

  const rpc = createRpcClient(proc, {
    onNotification: (message) => {
      const params = message.params || {};
      if (message.method === "turn/started" && params.turn?.id) {
        activeTurnId = params.turn.id;
        callbacks.onRefs?.({ turnId: activeTurnId });
        callbacks.onEvent?.("turn", `Started turn ${activeTurnId}`);
      } else if (message.method === "turn/completed") {
        finalStatus = params.turn?.status || "completed";
        if (params.turn?.error?.message) {
          finalError = params.turn.error.message;
        }
        if (finalStatus === "completed" || finalStatus === "interrupted" || finalStatus === "failed") {
          settled = true;
        }
      } else if (message.method === "item/agentMessage/delta") {
        const delta = params.delta || "";
        if (delta) finalText += delta;
      } else if (message.method === "item/completed" && params.item?.type === "agentMessage") {
        finalText = params.item.text || finalText;
      } else if (message.method === "error" && params.error?.message) {
        finalError = params.error.message;
      }
    },
    onStderr: (line) => {
      const text = quoteForDisplay(line);
      if (text) callbacks.onEvent?.("stderr", text);
    },
  });

  const promise = new Promise(async (resolve, reject) => {
    rejectPromise = reject;
    const timer = setTimeout(() => {
      if (!settled) {
        proc.kill("SIGTERM");
        reject(new Error(`Codex run timed out after ${timeoutMs}ms`));
      }
    }, timeoutMs);

    try {
      await rpc.request("initialize", {
        clientInfo: {
          name: "aify-claude",
          title: "aify-claude dispatch bridge",
          version: "3.0.0",
        },
      });
      rpc.notify("initialized", {});

      const startThread = async () => {
        const threadStartParams = {
          model,
          cwd,
          approvalPolicy,
          personality: "friendly",
          serviceName: "aify-claude",
        };
        let started;
        try {
          started = await rpc.request("thread/start", {
            ...threadStartParams,
            sandbox: "workspace-write",
          }, 60000);
        } catch (error) {
          const message = error?.message || "";
          if (!message.includes("unknown variant `workspace-write`")) {
            throw error;
          }
          started = await rpc.request("thread/start", {
            ...threadStartParams,
            sandbox: "workspaceWrite",
          }, 60000);
        }
        return started.thread?.id;
      };

      if (!activeThreadId) {
        activeThreadId = await startThread();
      } else {
        try {
          const resumed = await rpc.request("thread/resume", {
            threadId: activeThreadId,
            personality: "friendly",
          }, 60000);
          activeThreadId = resumed.thread?.id || activeThreadId;
        } catch (error) {
          const message = error?.message || "";
          if (!message.includes("no rollout found for thread id")) {
            throw error;
          }
          callbacks.onEvent?.("thread", `Discarding stale thread ${activeThreadId}`);
          activeThreadId = await startThread();
        }
      }

      callbacks.onRuntimeState?.({ threadId: activeThreadId });
      callbacks.onRefs?.({ threadId: activeThreadId });
      callbacks.onEvent?.("thread", `Using thread ${activeThreadId}`);

      const turn = await rpc.request("turn/start", {
        threadId: activeThreadId,
        input: [{ type: "text", text: `${buildSystemPrompt(agentId, agentInfo, run)}\n\n${buildUserPrompt(run)}` }],
        cwd,
        approvalPolicy,
        sandboxPolicy: {
          type: "workspaceWrite",
          writableRoots: [cwd],
          networkAccess,
        },
        model,
        effort,
        summary: summaryMode,
        personality: "friendly",
      }, 60000);

      activeTurnId = turn.turn?.id || activeTurnId;
      callbacks.onRefs?.({ threadId: activeThreadId, turnId: activeTurnId });

      const poll = setInterval(() => {
        if (!settled) return;
        clearInterval(poll);
        clearTimeout(timer);
        if (finalStatus === "completed") {
          resolve({
            status: "completed",
            summary: finalText.trim() || "(no output)",
            runtimeState: { threadId: activeThreadId },
            externalRefs: { threadId: activeThreadId, turnId: activeTurnId },
          });
          proc.kill("SIGTERM");
          return;
        }
        if (finalStatus === "interrupted" || interrupted) {
          resolve({
            status: "cancelled",
            summary: finalText.trim() || finalError || "Run interrupted",
            runtimeState: { threadId: activeThreadId },
            externalRefs: { threadId: activeThreadId, turnId: activeTurnId },
          });
          proc.kill("SIGTERM");
          return;
        }
        const detail = finalError || finalText || `Codex turn finished with status ${finalStatus}`;
        reject(new Error(detail));
        proc.kill("SIGTERM");
      }, 250);
    } catch (error) {
      clearTimeout(timer);
      reject(error);
      proc.kill("SIGTERM");
    }
  });

  return {
    capabilities: controlCapabilitiesForRuntime("codex"),
    interrupt: async () => {
      interrupted = true;
      if (!activeThreadId || !activeTurnId) {
        proc.kill("SIGTERM");
        return;
      }
      try {
        await rpc.request("turn/interrupt", {
          threadId: activeThreadId,
          turnId: activeTurnId,
        }, 30000);
      } catch (error) {
        if (rejectPromise) rejectPromise(error);
      }
    },
    steer: async (text) => {
      if (!activeThreadId || !activeTurnId) {
        throw new Error("No active Codex turn to steer");
      }
      if (!text || !String(text).trim()) {
        throw new Error("Steer body is required");
      }
      await rpc.request("turn/steer", {
        threadId: activeThreadId,
        input: [{ type: "text", text: String(text) }],
        expectedTurnId: activeTurnId,
      }, 30000);
      callbacks.onEvent?.("steer", `Steer applied to ${activeTurnId}`);
    },
    promise,
  };
}

export function detectRuntime(explicitRuntime) {
  if (explicitRuntime) return normalizeRuntime(explicitRuntime);
  if (process.env.AIFY_AGENT_RUNTIME) return normalizeRuntime(process.env.AIFY_AGENT_RUNTIME);
  if (process.env.CODEX_HOME || process.env.CODEX_SANDBOX) return "codex";
  if (process.env.CLAUDE_PROJECT_DIR || process.env.CLAUDECODE) return "claude-code";
  return "generic";
}

export function defaultCapabilitiesForRuntime(runtime, sessionMode = "resident") {
  const normalizedRuntime = normalizeRuntime(runtime);
  const normalizedMode = String(sessionMode || "resident").trim().toLowerCase();
  if (normalizedMode !== "managed") {
    return [];
  }
  switch (normalizedRuntime) {
    case "codex":
      return ["managed-run", "resume", "interrupt", "steer", "spawn"];
    case "claude-code":
      return ["managed-run", "resume", "interrupt", "spawn"];
    default:
      return [];
  }
}

export function defaultMachineId() {
  const host = process.env.AIFY_MACHINE_ID || process.env.COMPUTERNAME || process.env.HOSTNAME || "unknown-host";
  const wsl = process.env.WSL_DISTRO_NAME ? `wsl-${process.env.WSL_DISTRO_NAME}` : process.platform;
  return `${wsl}:${host}`;
}

export function launchRuntimeRun({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const runtime = normalizeRuntime(agentInfo.runtime || "generic");
  if (runtime === "codex") {
    return createCodexController({ agentId, agentInfo, run, runtimeState, callbacks });
  }
  if (runtime === "claude-code") {
    return createClaudeController({ agentId, agentInfo, run, runtimeState, callbacks });
  }
  return {
    capabilities: controlCapabilitiesForRuntime(runtime),
    interrupt: () => {},
    steer: async () => {
      throw new Error(`Runtime "${runtime}" does not support active dispatch`);
    },
    promise: Promise.reject(new Error(`Runtime "${runtime}" does not support active dispatch`)),
  };
}
