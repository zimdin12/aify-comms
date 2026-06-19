#!/usr/bin/env node
// SECONDARY pure-event fix (2026-06-19): the claude Stop hook is NOT a reliable turn
// terminator — the managed claude-aify wrapper fires premature/duplicate Stop hooks BETWEEN
// the tool-bursts of one logical turn (documented in install.sh + the #224 history). Each
// premature Stop POSTed /turn-end and cleared the status mid-turn → the working→online→working
// flicker, which the (now-removed) 20s grace used to mask.
//
// This gate replaces the raw `curl .../turn-end` in the Stop hook. It reads the SAME structural
// transcript truth the bridge detector uses (turn-end-detector.classify on the transcript tail)
// and only SUPPRESSES the turn-end when the turn is CONFIRMED still in-flight. On a real end,
// an unreadable/ambiguous tail, or ANY error, it falls through to the normal /turn-end POST —
// so it can only remove a confirmed-premature clear and can NEVER cause a stuck-`working`
// (worst case = today's behavior). No timer; pure structural event.
//
// Input: the Claude Stop hook JSON on stdin ({ transcript_path, session_id, ... }).
// Env:   AIFY_AGENT_ID + AIFY_COMMS_URL (present in the claude hook env, set by the wrapper).
// Exit:  always 0 — a Stop hook must never block the agent.

import { readFileSync, fstatSync, openSync, readSync, closeSync } from "node:fs";
import { summarizeTranscriptTail } from "./adapters/claude.js";
import { classify } from "./turn-end-detector.js";

const TAIL_BYTES = 65536;

function readStdin() {
  try {
    return readFileSync(0, "utf8"); // fd 0 = stdin; hooks pipe a finite JSON payload
  } catch {
    return "";
  }
}

// Read the last ~64KB of a (possibly large) transcript file without slurping the whole thing.
function readTail(path) {
  const fd = openSync(path, "r");
  try {
    const size = fstatSync(fd).size; // stat the OPEN fd (not the path) — no TOCTOU on rotate/replace
    const start = Math.max(0, size - TAIL_BYTES);
    const len = size - start;
    if (len <= 0) return "";
    const buf = Buffer.alloc(len);
    const n = readSync(fd, buf, 0, len, start); // readSync may short-read — decode only what we got
    return buf.subarray(0, n).toString("utf8");
  } finally {
    closeSync(fd);
  }
}

async function postTurnEnd() {
  const agentId = process.env.AIFY_AGENT_ID;
  const base = process.env.AIFY_COMMS_URL;
  if (!agentId || !base) return; // mirror the hook's own guard
  const url = `${base.replace(/\/$/, "")}/api/v1/agents/${encodeURIComponent(agentId)}/turn-end`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2000); // match the curl --max-time 2
  try {
    // No body → stays the authoritative harness Stop signal (server distinguishes a
    // bridge-detector turn-end by its bridgeId; a bodyless POST is the Stop hook).
    await fetch(url, { method: "POST", signal: ctrl.signal });
  } catch {
    /* best-effort, exactly like the original `|| true` curl */
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  let suppress = false;
  try {
    const payload = JSON.parse(readStdin() || "{}");
    const transcriptPath = payload && payload.transcript_path;
    if (transcriptPath) {
      const summary = summarizeTranscriptTail(readTail(transcriptPath));
      // SUPPRESS only on a CONFIRMED in-flight turn. "ended"/"unknown"/any throw → post.
      suppress = classify(summary) === "in-flight";
    }
  } catch {
    suppress = false; // fail-safe: never suppress on error
  }
  if (!suppress) await postTurnEnd();
}

main().finally(() => process.exit(0));
