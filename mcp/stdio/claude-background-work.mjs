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
//           with that task id and a final status. Claude Code writes it when the task finishes;
//   stop:   a `toolUseResult` with `task_id` and the message "Successfully stopped task: <id>". A
//           stopped task gets NO notification: on 48 of this host's transcripts (2026-10-03), 57
//           stopped tasks and not one later end, so each read as running until the bridge exited.
// Only those record SHAPES count. The same tags quoted in conversation are `assistant` text.
//
// AN END CAN BE WRITTEN BEFORE ITS START: 76 of the 1,070 ends in those transcripts were (a task
// finished before its tool result was recorded). An end for a task not seen yet is remembered, and its
// start then counts nothing; ignored instead, the start that followed ran for ever (external review of
// 0.8.1, which counted about 1%).
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
    if (typeof result.task_id === "string" && result.task_id && typeof result.message === "string"
        && result.message.startsWith(`Successfully stopped task: ${result.task_id}`)) return { ended: result.task_id };
  }
  if (record.type === "queue-operation" && record.operation === "enqueue" && typeof record.content === "string") {
    const match = NOTIFICATION.exec(record.content);
    if (match && TERMINAL.has(match[2])) return { ended: match[1] };
  }
  return null;
}

/** No background work seen yet. */
export const NO_TASKS = Object.freeze({ live: new Set(), endedUnseen: new Set() });

/**
 * The tasks after `lines`. PURE: new sets, the input untouched.
 *   `live`: started and not ended. A second start of one it holds changes nothing.
 *   `endedUnseen`: ended before any start was seen. Its start, if it comes, counts nothing; an end for a
 *   task started before this bridge looked waits here for a start that never comes.
 *
 * ponytail: `endedUnseen` is not capped. A cap evicted an end whose start could still come, and that start
 * then read as running for ever (review of 0e23ded8, M-BG1). It grows by one short id per end with no start
 * in view: the tasks running when this bridge first looked, and any start never recorded. A reset (a
 * /clear, a new file) empties it. Bound it by age if a session ever shows otherwise.
 */
export function tasksAfter(tasks, lines) {
  const live = new Set(tasks.live);
  const endedUnseen = new Set(tasks.endedUnseen);
  for (const line of lines) {
    const event = backgroundTaskEvent(line);
    if (event?.started) {
      if (!endedUnseen.delete(event.started)) live.add(event.started);
    } else if (event?.ended && !live.delete(event.ended)) {
      endedUnseen.add(event.ended);
    }
  }
  return { live, endedUnseen };
}

/**
 * What one look at the transcript found. THREE OUTCOMES, never collapsed: "nothing new" is evidence
 * that nothing ended, "unavailable" is no evidence at all, and "reset" means the source changed, so
 * what was counted from the old one can no longer be seen to end (review of 3d5dc11e, R1 and R2).
 */
export const READ = Object.freeze({ LINES: "lines", UNAVAILABLE: "unavailable", RESET: "reset" });

/** Reads what is appended to one transcript, whole lines only, starting from where it first looks. */
export class TranscriptFollower {
  #path = "";
  #offset = -1;
  #partial = "";
  #open;

  constructor({ open = (p) => fs.open(p, "r") } = {}) { this.#open = open; }

  /**
   * @returns {Promise<{status: string, lines: string[]}>}
   *   LINES with the complete lines appended since the last read (possibly none);
   *   UNAVAILABLE when the transcript could not be read: no path, or open, stat or read failed;
   *   RESET on the first look at a path, a new path (a /clear starts a new file), or a file that
   *   shrank. The follower then starts at the file's end.
   */
  async read(path) {
    if (!path) return { status: READ.UNAVAILABLE, lines: [] };
    let fh;
    try { fh = await this.#open(path); } catch { return { status: READ.UNAVAILABLE, lines: [] }; }
    try {
      const { size } = await fh.stat();
      if (path !== this.#path || this.#offset < 0 || size < this.#offset) {
        this.#path = path;
        this.#offset = size;
        this.#partial = "";
        return { status: READ.RESET, lines: [] };
      }
      if (size === this.#offset) return { status: READ.LINES, lines: [] };
      const buffer = Buffer.alloc(size - this.#offset);
      await fh.read(buffer, 0, buffer.length, this.#offset);
      this.#offset = size;
      const text = this.#partial + buffer.toString("utf8");
      const lines = text.split("\n");
      this.#partial = lines.pop();
      return { status: READ.LINES, lines: lines.filter((line) => line.trim() !== "") };
    } catch {
      return { status: READ.UNAVAILABLE, lines: [] };
    } finally {
      await fh.close().catch(() => {});
    }
  }
}

/**
 * The tasks after one look. PURE.
 *   LINES: the lines applied.
 *   RESET: the old generation retired, no tasks. Its ends would land where nobody reads, so a count
 *          carried over could never reach zero; under-reporting until the next start is the bounded
 *          error, a `shell` that never ends is not. A late end of a retired task is remembered as unseen,
 *          and a start never follows it.
 *   UNAVAILABLE: unchanged, and the caller must not renew it as if it were observed.
 */
export function tasksAfterRead(tasks, { status, lines }) {
  if (status === READ.RESET) return NO_TASKS;
  if (status === READ.LINES) return tasksAfter(tasks, lines);
  return tasks;
}

/**
 * Follow the transcript and report the count: every tick while work runs (the service holds it as a
 * short lease), and once when it drops to zero. A failed post is retried on the next tick.
 *
 * NOTHING IS SENT WHILE THE TRANSCRIPT IS UNAVAILABLE. Renewing the last count would keep a lease
 * alive on evidence nobody can read, and posting zero would claim an observation nobody made; sent
 * nothing, the service's lease expires on its own and the agent reads as it otherwise would.
 *
 * @returns {() => void} stop
 */
export function startBackgroundWorkReporter({
  transcriptPath, post, intervalMs = 5_000, follower = new TranscriptFollower(),
  setIntervalImpl = setInterval, clearIntervalImpl = clearInterval,
}) {
  let tasks = NO_TASKS;
  let reported = 0;
  const once = async () => {
    try {
      const observed = await follower.read(transcriptPath());
      tasks = tasksAfterRead(tasks, observed);
      if (observed.status === READ.UNAVAILABLE) return;
      const count = tasks.live.size;
      if (count > 0 || reported > 0) {
        await post(count);
        reported = count;
      }
    } catch {
      // Unreported: the next tick tries again, and the service's lease bounds what a gap can show.
    }
  };
  // ONE READ AT A TIME. A tick that finds one in flight starts no second read; it hands back the one
  // running, so a caller that awaits a tick knows the look it waited for has finished.
  let inflight = null;
  const tick = () => {
    inflight ??= once().finally(() => { inflight = null; });
    return inflight;
  };
  const timer = setIntervalImpl(tick, intervalMs);
  timer?.unref?.();
  tick();
  return () => clearIntervalImpl(timer);
}
