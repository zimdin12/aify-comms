#!/usr/bin/env node

import fs from "fs";
import os from "os";
import path from "path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadSettingsEnv } from "./load-env.js";
import { defaultMachineId } from "./runtimes.js";

loadSettingsEnv();

const SERVER_URL = process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "";
const API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";
const MACHINE_ID = defaultMachineId();
const POLL_MS = Number(process.env.AIFY_CLAUDE_CHANNEL_POLL_MS || 3000);
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();

let activeRunId = "";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readBoundAgentId() {
  const candidates = [
    path.join(TMP_DIR, `aify-agent-${process.ppid || process.pid}`),
    path.join(TMP_DIR, `aify-agent-${process.pid}`),
    path.join(process.cwd(), ".aify-agent"),
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
    "Treat this as a real wake-up event for the existing Claude session.",
    "Do the requested work directly in this session.",
    run.messageId
      ? `When you reply through aify, include inReplyTo="${run.messageId}" so the resident dispatch run closes automatically.`
      : "Reply through aify when the task is done.",
    "",
    "Task body:",
    "```",
    body,
    "```",
  ].filter(Boolean).join("\n");
}

function controlContent(agentId, runId, control) {
  const body = String(control.body || "").replace(/```/g, "'''");
  const lines = [
    `Aify control event for agent "${agentId}".`,
    `Run ID: ${runId}`,
    `Action: ${control.action}`,
    control.from ? `Requested by: ${control.from}` : "",
  ];
  if (body) {
    lines.push("", "Control body:", "```", body, "```");
  }
  if (control.action === "interrupt") {
    lines.push("", "Stop the current aify task for this run as soon as practical, then send a brief status or result reply.");
  } else if (control.action === "steer") {
    lines.push("", "Apply this new guidance to the current aify task.");
  }
  return lines.filter(Boolean).join("\n");
}

const mcp = new Server(
  { name: "aify-claude-channel", version: "3.6.4" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
    },
    instructions:
      'Events from aify resident dispatch arrive as <channel source="aify-claude-channel" ...>. ' +
      "These are real wake-up events for the current session. Handle them directly in this session. " +
      "Use the existing cc_* tools to coordinate and reply. " +
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

      if (activeRunId) {
        const runStatus = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(activeRunId)}`);
        const run = runStatus?.run;
        if (!run || ["completed", "failed", "cancelled"].includes(run.status)) {
          activeRunId = "";
        }
      }

      if (!activeRunId) {
        const claim = await httpCall("POST", "/dispatch/claim", {
          agentId,
          machineId: MACHINE_ID,
          executionModes: ["resident"],
        });
        if (claim?.run && claim.run.executionMode === "resident") {
          activeRunId = claim.run.id;
          await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(claim.run.id)}`, {
            status: "running",
            runtime: "claude-code",
            agentStatus: "working",
            appendEvent: "Delivered to Claude resident channel",
            eventType: "runtime",
          });
          await emitChannel(dispatchContent(agentId, claim.run), {
            event_type: "dispatch",
            agent_id: agentId,
            run_id: claim.run.id,
            from_agent: claim.run.from || "",
            message_id: claim.run.messageId || "",
            priority: claim.run.priority || "normal",
          });
        }
      }

      if (activeRunId) {
        const controlClaim = await httpCall("POST", "/dispatch/controls/claim", {
          agentId,
          runId: activeRunId,
          machineId: MACHINE_ID,
        });
        for (const control of controlClaim?.controls || []) {
          await emitChannel(controlContent(agentId, activeRunId, control), {
            event_type: "control",
            agent_id: agentId,
            run_id: activeRunId,
            action: control.action || "",
          });
          await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
            status: "completed",
            response: "Delivered to Claude resident session",
          });
        }
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
