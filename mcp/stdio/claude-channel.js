#!/usr/bin/env node

import fs from "fs";
import os from "os";
import path from "path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadSettingsEnv } from "./load-env.js";
import { defaultMachineId } from "./runtimes.js";
import { writeRuntimeMarker, removeRuntimeMarker } from "./runtime-markers.js";

loadSettingsEnv();

const SERVER_URL = process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "";
const API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";
const MACHINE_ID = defaultMachineId();
const POLL_MS = Number(process.env.AIFY_COMMS_CHANNEL_POLL_MS || process.env.AIFY_CLAUDE_CHANNEL_POLL_MS || 3000);
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();

// Write our claude-code runtime marker from this long-lived bridge process.
// This must happen here, not in the wrapper's bash CLI call, because on
// Git Bash for Windows `$$` is an MSYS shell PID and isProcessAlive() from
// node cannot see it — listRuntimeMarkers would auto-delete the wrapper's
// marker on first read. node's process.pid is a real Windows PID.
const MARKER_CWD = process.cwd();
try {
  writeRuntimeMarker("claude-code", MARKER_CWD, {
    channelEnabled: true,
  });
} catch (error) {
  console.error("[aify-channel] failed to write runtime marker:", error?.message || String(error));
}

function removeOwnMarker() {
  try {
    removeRuntimeMarker("claude-code", MARKER_CWD);
  } catch {
    // best effort — a dead PID will get auto-cleaned on next listRuntimeMarkers anyway
  }
}
process.on("exit", removeOwnMarker);
process.on("SIGINT", () => { removeOwnMarker(); process.exit(130); });
process.on("SIGTERM", () => { removeOwnMarker(); process.exit(143); });

// No activeRunId tracking. The channel bridge claims a dispatch, delivers
// it to the Claude session via MCP notification, and immediately marks the
// run as completed in the same tick. Previously the bridge left runs in
// "running" state indefinitely — it has no way to track whether Claude
// actually processed the work, so runs hung until the 2-hour timeout and
// blocked all subsequent dispatches for the agent.

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readBoundAgentId() {
  // Read the agent binding from the PID-keyed temp file written by
  // server.js on comms_register. We no longer fall back to {cwd}/.aify-agent
  // because that file is shared across all sessions in the same directory
  // and causes cross-talk when multiple agents run in the same folder.
  const candidates = [
    path.join(TMP_DIR, `aify-agent-${process.ppid || process.pid}`),
    path.join(TMP_DIR, `aify-agent-${process.pid}`),
  ];
  for (const candidate of candidates) {
    try {
      const value = fs.readFileSync(candidate, "utf-8").trim();
      if (value) return value;
    } catch {
      // keep looking
    }
  }
  return "";
}

async function httpCall(method, endpoint, body = null) {
  if (!SERVER_URL) return null;
  const url = `${SERVER_URL}/api/v1${endpoint}`;
  const options = { method, headers: {} };
  if (API_KEY) options.headers["X-API-Key"] = API_KEY;
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json();
}

function dispatchContent(agentId, run) {
  const body = String(run.body || "").replace(/```/g, "'''");
  return [
    `Aify resident trigger for agent "${agentId}".`,
    `Run ID: ${run.id}`,
    `From: ${run.from}`,
    `Type: ${run.type}`,
    `Subject: ${run.subject}`,
    `Priority: ${run.priority || "normal"}`,
    run.messageId ? `Message ID: ${run.messageId}` : "",
    "",
    "Treat this as a real wake-up event for the current session.",
    "Do the requested work directly.",
    run.messageId
      ? `When you reply, include inReplyTo="${run.messageId}" so the sender sees your response linked to their original message.`
      : "Reply through aify when the task is done.",
    "",
    "Task body:",
    "```",
    body,
    "```",
  ].filter(Boolean).join("\n");
}

function controlContent(agentId, control) {
  const body = String(control.body || "").replace(/```/g, "'''");
  const lines = [
    `Aify ${control.action} for agent "${agentId}".`,
    control.from ? `Requested by: ${control.from}` : "",
  ];
  if (body) {
    lines.push("", "```", body, "```");
  }
  if (control.action === "interrupt") {
    lines.push("", "Stop your current task as soon as practical. Send a brief status reply.");
  } else if (control.action === "steer") {
    lines.push("", "Apply this guidance to your current work.");
  }
  return lines.filter(Boolean).join("\n");
}

const mcp = new Server(
  { name: "aify-comms-channel", version: "3.6.6" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
    },
    instructions:
      'Events from aify resident dispatch arrive as <channel source="aify-comms-channel" ...>. ' +
      "These are real wake-up events for the current session. Handle them directly in this session. " +
      "Use the existing comms_* tools to coordinate and reply. " +
      "When a dispatch event includes Message ID, include that same value as inReplyTo when you reply so the run can close automatically.",
  },
);

async function emitChannel(content, meta = {}) {
  await mcp.notification({
    method: "notifications/claude/channel",
    params: {
      content,
      meta,
    },
  });
}

async function pollLoop() {
  while (true) {
    try {
      if (!SERVER_URL) {
        await sleep(POLL_MS);
        continue;
      }

      const agentId = readBoundAgentId();
      if (!agentId) {
        await sleep(POLL_MS);
        continue;
      }

      // Claim → deliver → complete in one tick. No "running" state to hang.
      const claim = await httpCall("POST", "/dispatch/claim", {
        agentId,
        machineId: MACHINE_ID,
        bridgeId: `channel-${MACHINE_ID}`,
        executionModes: ["resident"],
      });
      if (claim?.run && claim.run.executionMode === "resident") {
        const runId = claim.run.id;
        await emitChannel(dispatchContent(agentId, claim.run), {
          event_type: "dispatch",
          agent_id: agentId,
          run_id: runId,
          from_agent: claim.run.from || "",
          message_id: claim.run.messageId || "",
          priority: claim.run.priority || "normal",
        });
        await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
          status: "completed",
          summary: "Delivered to Claude resident session",
          runtime: "claude-code",
          agentStatus: "active",
          appendEvent: "Delivered and completed by channel bridge",
          eventType: "delivered",
        });
      }

      // Poll for controls (interrupt/steer) independently of run tracking.
      // This makes comms_run_interrupt and comms_run_steer work for Claude
      // the same way they work for Codex — the sender uses the same tool
      // regardless of target runtime.
      const controlClaim = await httpCall("POST", "/dispatch/controls/claim", {
        agentId,
        machineId: MACHINE_ID,
      });
      for (const control of controlClaim?.controls || []) {
        await emitChannel(controlContent(agentId, control), {
          event_type: "control",
          agent_id: agentId,
          run_id: control.runId || "",
          action: control.action || "",
          from_agent: control.from || "",
        });
        await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
          status: "completed",
          response: "Delivered to Claude resident session",
        });
      }
    } catch (error) {
      console.error("[aify-channel] tick error:", error?.message || String(error));
    }

    await sleep(POLL_MS);
  }
}

await mcp.connect(new StdioServerTransport());
pollLoop().catch((error) => {
  console.error("[aify-channel] fatal:", error);
  process.exit(1);
});
