#!/usr/bin/env node
// A claude session's background work, read from its transcript (claude-background-work.mjs).
//
// The record shapes below are the ones observed in a real session file on 2026-10-01 (ids and text
// invented): a background start is `toolUseResult.backgroundTaskId`, a background agent is
// `toolUseResult.status: "async_launched"` with `agentId`, and an end is a `queue-operation` enqueue
// carrying a task notification. The rest of the cases are the traps a looser reader falls into.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  TranscriptFollower, backgroundTaskEvent, liveAfter, startBackgroundWorkReporter,
} from "../claude-background-work.mjs";

const NL = String.fromCharCode(10);
const shellStart = (id, auto = false) => JSON.stringify({
  type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_x", content: "" }] },
  toolUseResult: { stdout: "", stderr: "", interrupted: false, backgroundTaskId: id, assistantAutoBackgrounded: auto },
});
const agentStart = (id) => JSON.stringify({
  type: "user", toolUseResult: { isAsync: true, status: "async_launched", agentId: id, description: "research" },
});
const notification = (id, status) => ["<task-notification>", `<task-id>${id}</task-id>`, "<tool-use-id>toolu_x</tool-use-id>",
  "<output-file>C:\\tmp\\out</output-file>", `<status>${status}</status>`, "<summary>done</summary>", "</task-notification>"].join(NL);
const ended = (id, status = "completed", operation = "enqueue") =>
  JSON.stringify({ type: "queue-operation", operation, timestamp: "2026-10-01T00:00:00Z", content: notification(id, status) });

test("what one record says: starts, ends, and the records that look alike but say nothing", () => {
  assert.deepEqual(backgroundTaskEvent(shellStart("b1")), { started: "b1" });
  assert.deepEqual(backgroundTaskEvent(shellStart("b2", true)), { started: "b2" }, "an auto-backgrounded command is a start too");
  assert.deepEqual(backgroundTaskEvent(agentStart("a1")), { started: "a1" });
  for (const status of ["completed", "failed", "killed"]) {
    assert.deepEqual(backgroundTaskEvent(ended("b1", status)), { ended: "b1" }, status);
  }
  const nothing = [
    ended("b1", "running"),                                   // not a final status
    ended("b1", "completed", "remove"),                       // the dequeue of the same notification
    JSON.stringify({ type: "attachment", attachment: { type: "queued_command", prompt: notification("b1", "completed") } }),
    JSON.stringify({ type: "assistant", message: { role: "assistant", content: [{ type: "text", text: notification("b1", "completed") }] } }),
    JSON.stringify({ type: "user", message: { role: "user", content: `quoted: ${notification("b1", "completed")}` } }),
    JSON.stringify({ type: "user", toolUseResult: { stdout: "foreground", backgroundTaskId: "" } }),
    "{not json",
    "",
  ];
  for (const line of nothing) assert.equal(backgroundTaskEvent(line), null, line.slice(0, 80));
});

test("the live set: starts minus ends, an unknown end ignored, the input left alone", () => {
  const before = new Set(["old"]);
  const after = liveAfter(before, [shellStart("b1"), agentStart("a1"), ended("b1"), ended("never-started"), shellStart("a1")]);
  assert.deepEqual([...after].sort(), ["a1", "old"]);
  assert.deepEqual([...before], ["old"], "a new set; the caller's is untouched");
});

test("the follower reads only what is appended, whole lines, from where it first looked", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-bg-follow-"));
  const file = path.join(dir, "session.jsonl");
  // A RESUMED SESSION: a task started before this bridge, never finished, is history, not live work.
  fs.writeFileSync(file, shellStart("from-before") + NL);
  const follower = new TranscriptFollower();
  assert.deepEqual(await follower.read(file), [], "the first look starts at the end");
  fs.appendFileSync(file, shellStart("b1") + NL + ended("b1").slice(0, 20));
  assert.deepEqual(await follower.read(file), [shellStart("b1")], "a half-written line waits");
  fs.appendFileSync(file, ended("b1").slice(20) + NL);
  assert.deepEqual(await follower.read(file), [ended("b1")], "and arrives whole");
  assert.deepEqual(await follower.read(file), [], "nothing new, nothing read");

  const other = path.join(dir, "after-clear.jsonl");
  fs.writeFileSync(other, shellStart("in-the-new-file-before-we-looked") + NL);
  assert.deepEqual(await follower.read(other), [], "a new path starts at its own end");
  fs.appendFileSync(other, agentStart("a1") + NL);
  assert.deepEqual(await follower.read(other), [agentStart("a1")]);

  fs.writeFileSync(other, "");
  assert.deepEqual(await follower.read(other), [], "a truncated file restarts at its end");
  assert.deepEqual(await follower.read(path.join(dir, "missing.jsonl")), [], "an unreadable path reads nothing");
  assert.deepEqual(await follower.read(""), []);
});

test("the reporter posts the count while work runs, zero once when it ends, and nothing while idle", async () => {
  const batches = [[], [shellStart("b1"), agentStart("a1")], [], [ended("b1")], [ended("a1")], [], []];
  const posts = [];
  let failNext = false;
  let tick;
  const stop = startBackgroundWorkReporter({
    transcriptPath: () => "transcript.jsonl",
    follower: { read: async () => batches.shift() || [] },
    post: async (count) => {
      if (failNext) { failNext = false; throw new Error("service unreachable"); }
      posts.push(count);
    },
    setIntervalImpl: (fn) => { tick = fn; return { unref() {} }; },
    clearIntervalImpl: () => {},
  });
  const step = async () => { await tick(); };
  await new Promise((resolve) => setImmediate(resolve)); // the first tick, run at start
  assert.deepEqual(posts, [], "idle at zero: nothing sent");
  await step(); assert.deepEqual(posts, [2]);
  await step(); assert.deepEqual(posts, [2, 2], "refreshed every tick while it runs: the service holds it as a lease");
  await step(); assert.deepEqual(posts, [2, 2, 1]);
  failNext = true;
  await step(); assert.deepEqual(posts, [2, 2, 1], "a failed post...");
  await step(); assert.deepEqual(posts, [2, 2, 1, 0], "...is sent on the next tick, and zero is said once");
  await step(); assert.deepEqual(posts, [2, 2, 1, 0], "then nothing while idle");
  stop();
});
