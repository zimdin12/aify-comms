// A claude session's BACKGROUND WORK, read from its transcript and reported to the service.
//
// THE OPERATOR, 2026-10-01: "i do not see cyan marker on you currently (but you have shell running)".
// A resident's prompt can be idle while background shells or agents still run; the service shows that
// as `shell` (cyan) once this bridge reports it (service/api_core/background_work.py).
//
// THE TRANSCRIPT, NOT THE PROCESS TABLE. Listing claude's children on Windows costs a PowerShell CIM
// query, measured at 656-1188 ms of CPU each, which every beat of every resident would pay. The
// transcript already records both ends, observed in a real session's file:
//   start:  a record whose `toolUseResult.backgroundTaskId` is set (a background or auto-backgrounded
//           shell), or whose `toolUseResult` says `status: "async_launched"` with an `agentId` (a
//           background agent, which counts the same, as hermes' background agents do);
//   end:    a `queue-operation` record, operation `enqueue`, whose content is a task notification
//           with that task id and a final status. Claude Code writes it when the task finishes.
// Only those record SHAPES count. The same tags quoted in conversation are `assistant` text.
//
// ONLY WORK STARTED SINCE THIS BRIDGE STARTED. The follower begins at the file's size when it first
// sees it, so a resumed session's old, unfinished tasks are never counted as running.

import fs from "node:fs/promises";

const TERMINAL = new Set(["completed", "failed", "killed"]);
const NOTIFICATION = /^\s*<task-notification>\s*<task-id>([^<\s]+)<\/task-id>[\s\S]*?<status>([a-z_]+)<\/status>/;

/**
 * What one transcript line says about background work. PURE.
 * @returns {{started: string}|{ended: string}|null}
 */
export function backgroundTaskEvent(line) {
  let record;
  try { record = JSON.parse(line); } catch { return null; }
  if (!record || typeof record !== "object") return null;
  const result = record.toolUseResult;
  if (result && typeof result === "object") {
    if (typeof result.backgroundTaskId === "string" && result.backgroundTaskId) return { started: result.backgroundTaskId };
    if (result.status === "async_launched" && typeof result.agentId === "string" && result.agentId) return { started: result.agentId };
  }
  if (record.type === "queue-operation" && record.operation === "enqueue" && typeof record.content === "string") {
    const match = NOTIFICATION.exec(record.content);
    if (match && TERMINAL.has(match[2])) return { ended: match[1] };
  }
  return null;
}

/**
 * The live set after `lines`. PURE: a new Set, the input untouched.
 * An end for a task this bridge never saw start is ignored, as is a second start of one it holds.
 */
export function liveAfter(live, lines) {
  const next = new Set(live);
  for (const line of lines) {
    const event = backgroundTaskEvent(line);
    if (event?.started) next.add(event.started);
    else if (event?.ended) next.delete(event.ended);
  }
  return next;
}

/** Reads what is appended to one transcript, whole lines only, starting from where it first looks. */
export class TranscriptFollower {
  #path = "";
  #offset = -1;
  #partial = "";
  #open;

  constructor({ open = (p) => fs.open(p, "r") } = {}) { this.#open = open; }

  /**
   * The complete lines appended to `path` since the last read. A new path starts at that file's end:
   * a session that moved on (a /clear starts a new file) is followed from its own present.
   */
  async read(path) {
    if (!path) return [];
    let fh;
    try { fh = await this.#open(path); } catch { return []; }
    try {
      const { size } = await fh.stat();
      if (path !== this.#path || this.#offset < 0 || size < this.#offset) {
        this.#path = path;
        this.#offset = size;
        this.#partial = "";
        return [];
      }
      if (size === this.#offset) return [];
      const buffer = Buffer.alloc(size - this.#offset);
      await fh.read(buffer, 0, buffer.length, this.#offset);
      this.#offset = size;
      const text = this.#partial + buffer.toString("utf8");
      const lines = text.split("\n");
      this.#partial = lines.pop();
      return lines.filter((line) => line.trim() !== "");
    } catch {
      return [];
    } finally {
      await fh.close().catch(() => {});
    }
  }
}

/**
 * Follow the transcript and report the count: every tick while work runs (the service holds it as a
 * short lease), and once when it drops to zero. A failed post is retried on the next tick.
 *
 * @returns {() => void} stop
 */
export function startBackgroundWorkReporter({
  transcriptPath, post, intervalMs = 5_000, follower = new TranscriptFollower(),
  setIntervalImpl = setInterval, clearIntervalImpl = clearInterval,
}) {
  let live = new Set();
  let reported = 0;
  let busy = false;
  const tick = async () => {
    if (busy) return;
    busy = true;
    try {
      live = liveAfter(live, await follower.read(transcriptPath()));
      const count = live.size;
      if (count > 0 || reported > 0) {
        await post(count);
        reported = count;
      }
    } catch {
      // Unreported: the next tick tries again, and the service's lease bounds what a gap can show.
    } finally {
      busy = false;
    }
  };
  const timer = setIntervalImpl(tick, intervalMs);
  timer?.unref?.();
  tick();
  return () => clearIntervalImpl(timer);
}
