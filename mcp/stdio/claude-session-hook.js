#!/usr/bin/env node
import { pathToFileURL } from "url";
import { writeClaudeSessionId, writeCapturedClaudeSessionIdForPid } from "./claude-session-store.js";
import { readAgentBindingFile } from "./binding-file.js";

// Claude SessionStart/UserPromptSubmit hook. Claude pipes JSON on stdin that
// includes {session_id, cwd, transcript_path}. We capture session_id keyed by
// AIFY_AGENT_ID (inherited from claude's env, which the managed wrapper
// exports). The bridge reads it back by the same agentId. agentId keying is
// robust on Windows where the hook command runs via a shell (so process.ppid
// is the shell, not claude). MUST always exit 0 and never throw — hooks block
// claude if they fail.
//
// IDENTITY MAY ARRIVE LATE (2026-07-14). A session launched without `--aify-agent`
// has no AIFY_AGENT_ID, and this hook used to DROP the session id on the floor —
// so even after `comms_register` told the bridge its agent id, the bridge could not
// resolve its own transcript and status stayed dead. That made "just register" fail
// for reasons no operator could see. Two fallbacks, in order:
//   1. the agent BINDING file (`comms_register` writes it, keyed by the claude pid) —
//      once registered, later hook fires key the store correctly;
//   2. a PID-keyed capture of the session id, so an EARLIER hook fire (SessionStart,
//      before any register) is not lost — `comms_register` promotes it to the
//      agent-keyed store the moment identity arrives.
// Together: register → the bridge knows both WHO it is and WHICH session → status works.

export function handleClaudeSessionHook({ stdin = "", env = process.env, dir, ppid } = {}) {
  try {
    const raw = String(stdin || "").trim();
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const sessionId = String(parsed?.session_id || "").trim();
    if (!sessionId) return;

    let agentId = String(env?.AIFY_AGENT_ID || env?.AIFY_COMMS_AGENT_ID || "").trim();
    const pid = Number(ppid ?? process.ppid) || 0;

    // (1) No env identity? Ask the binding file written by comms_register.
    if (!agentId && pid) {
      try { agentId = String(readAgentBindingFile({ pid, dir }).agentId || "").trim(); } catch { agentId = ""; }
    }

    if (agentId) {
      writeClaudeSessionId({ sessionId, agentId, dir });
      return;
    }

    // (2) Still anonymous — keep the session id keyed by the claude process so a
    // later comms_register can claim it. Without this, registering can never turn
    // status on for a session launched with no agent id.
    writeCapturedClaudeSessionIdForPid({ sessionId, pid, dir });
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
