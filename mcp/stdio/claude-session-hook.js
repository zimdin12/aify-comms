#!/usr/bin/env node
import { pathToFileURL } from "url";
import { writeClaudeSessionId } from "./claude-session-store.js";

// Claude SessionStart/UserPromptSubmit hook. Claude pipes JSON on stdin that
// includes {session_id, cwd, transcript_path}. We capture session_id keyed by
// AIFY_AGENT_ID (inherited from claude's env, which the managed wrapper
// exports). The bridge reads it back by the same agentId. agentId keying is
// robust on Windows where the hook command runs via a shell (so process.ppid
// is the shell, not claude). MUST always exit 0 and never throw — hooks block
// claude if they fail.

export function handleClaudeSessionHook({ stdin = "", env = process.env, dir } = {}) {
  try {
    const raw = String(stdin || "").trim();
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const sessionId = String(parsed?.session_id || "").trim();
    const agentId = String(env?.AIFY_AGENT_ID || env?.AIFY_COMMS_AGENT_ID || "").trim();
    if (!sessionId || !agentId) return;
    writeClaudeSessionId({ sessionId, agentId, dir });
  } catch {
    // swallow everything — never block claude
  }
}

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    try {
      process.stdin.setEncoding("utf-8");
      process.stdin.on("data", (chunk) => { data += chunk; });
      process.stdin.on("end", () => resolve(data));
      process.stdin.on("error", () => resolve(data));
    } catch {
      resolve(data);
    }
  });
}

async function main() {
  let stdin = "";
  try { stdin = await readStdin(); } catch { /* ignore */ }
  handleClaudeSessionHook({ stdin, env: process.env });
  process.exit(0);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
