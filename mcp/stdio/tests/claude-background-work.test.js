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
  READ, TranscriptFollower, backgroundTaskEvent, liveAfter, liveAfterRead, startBackgroundWorkReporter,
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
  const lines = (...l) => ({ status: READ.LINES, lines: l });
  const reset = { status: READ.RESET, lines: [] };
  const unavailable = { status: READ.UNAVAILABLE, lines: [] };
  // A RESUMED SESSION: a task started before this bridge, never finished, is history, not live work.
  fs.writeFileSync(file, shellStart("from-before") + NL);
  const follower = new TranscriptFollower();
  assert.deepEqual(await follower.read(file), reset, "the first look starts at the end");
  fs.appendFileSync(file, shellStart("b1") + NL + ended("b1").slice(0, 20));
  assert.deepEqual(await follower.read(file), lines(shellStart("b1")), "a half-written line waits");
  fs.appendFileSync(file, ended("b1").slice(20) + NL);
  assert.deepEqual(await follower.read(file), lines(ended("b1")), "and arrives whole");
  assert.deepEqual(await follower.read(file), lines(), "nothing new is an observation: lines, none of them");

  const other = path.join(dir, "after-clear.jsonl");
  fs.writeFileSync(other, shellStart("in-the-new-file-before-we-looked") + NL);
  assert.deepEqual(await follower.read(other), reset, "a new path is a reset, at its own end");
  fs.appendFileSync(other, agentStart("a1") + NL);
  assert.deepEqual(await follower.read(other), lines(agentStart("a1")));

  fs.writeFileSync(other, "");
  assert.deepEqual(await follower.read(other), reset, "a truncated file is a reset");
  assert.deepEqual(await follower.read(path.join(dir, "missing.jsonl")), unavailable, "an unreadable path is no evidence");
  assert.deepEqual(await follower.read(""), unavailable);
});

test("the live set after a look: lines applied, a reset retires the generation, unavailable changes nothing", () => {
  const live = new Set(["b1"]);
  assert.deepEqual([...liveAfterRead(live, { status: READ.LINES, lines: [ended("b1")] })], []);
  assert.deepEqual([...liveAfterRead(live, { status: READ.RESET, lines: [] })], [], "nothing counted from a source nobody reads now");
  assert.deepEqual([...liveAfterRead(live, { status: READ.UNAVAILABLE, lines: [] })], ["b1"]);
  assert.deepEqual([...live], ["b1"], "the input is untouched");
});

/** What was reported, as transitions: a repeated count is the lease being renewed, pinned below. */
const transitions = (posts) => posts.filter((count, i) => i === 0 || count !== posts[i - 1]);

/** The real follower and the real reporter, ticked by hand, posting into a list. */
function reporterOn(pathOf, { open } = {}) {
  const posts = [];
  let tick;
  const stop = startBackgroundWorkReporter({
    transcriptPath: pathOf,
    follower: new TranscriptFollower(open ? { open } : {}),
    post: async (count) => { posts.push(count); },
    setIntervalImpl: (fn) => { tick = fn; return { unref() {} }; },
    clearIntervalImpl: () => {},
  });
  // A STEP is one fresh look: the first await finishes any look already in flight (the one the
  // reporter starts on its own), the second makes a new one after whatever the test just wrote.
  return { posts, step: async () => { await tick(); await tick(); }, stop };
}

test("THE SEQUENCES FROM THE REVIEW, through the real follower and reporter", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-bg-seq-"));
  const at = (name) => path.join(dir, name);

  await t.test("CONTROL, the same source: a start then its end is [1, 0]", async () => {
    fs.writeFileSync(at("same.jsonl"), "");
    const r = reporterOn(() => at("same.jsonl"));
    await r.step();
    fs.appendFileSync(at("same.jsonl"), shellStart("a") + NL); await r.step();
    fs.appendFileSync(at("same.jsonl"), ended("a") + NL); await r.step(); await r.step();
    assert.deepEqual(transitions(r.posts), [1, 0]);
    r.stop();
  });

  await t.test("A NEW SOURCE retires the old generation: an end left in the old file is never needed", async () => {
    fs.writeFileSync(at("a.jsonl"), ""); fs.writeFileSync(at("b.jsonl"), "");
    let current = at("a.jsonl");
    const r = reporterOn(() => current);
    await r.step();
    fs.appendFileSync(at("a.jsonl"), shellStart("task-a") + NL); await r.step();
    current = at("b.jsonl"); await r.step();
    fs.appendFileSync(at("a.jsonl"), ended("task-a") + NL);
    for (let i = 0; i < 6; i += 1) await r.step();
    assert.deepEqual(transitions(r.posts), [1, 0], "never a lasting 1 after the switch");
    r.stop();
  });

  await t.test("A TRUNCATED SOURCE is a reset too", async () => {
    fs.writeFileSync(at("t.jsonl"), "");
    const r = reporterOn(() => at("t.jsonl"));
    await r.step();
    fs.appendFileSync(at("t.jsonl"), shellStart("task-t") + NL); await r.step();
    fs.writeFileSync(at("t.jsonl"), ""); await r.step(); await r.step();
    assert.deepEqual(transitions(r.posts), [1, 0]);
    r.stop();
  });

  await t.test("AN UNREADABLE SOURCE renews nothing and claims no zero: the service's lease runs out", async () => {
    fs.writeFileSync(at("gone.jsonl"), "");
    const r = reporterOn(() => at("gone.jsonl"));
    await r.step();
    fs.appendFileSync(at("gone.jsonl"), shellStart("task-g") + NL); await r.step();
    const seen = r.posts.length;
    fs.rmSync(at("gone.jsonl"));
    for (let i = 0; i < 6; i += 1) await r.step();
    assert.ok(seen > 0 && r.posts.every((count) => count === 1), "control: it was reported while seen");
    assert.equal(r.posts.length, seen, "then silence: no renewal, and no zero nobody observed");
    r.stop();
  });

  await t.test("UNAVAILABLE FOR A WHILE, then readable again: reporting resumes from what is seen", async () => {
    fs.writeFileSync(at("flaky.jsonl"), "");
    let failing = false;
    const r = reporterOn(() => at("flaky.jsonl"), {
      open: (p) => (failing ? Promise.reject(new Error("locked")) : fs.promises.open(p, "r")),
    });
    await r.step();
    fs.appendFileSync(at("flaky.jsonl"), shellStart("task-f") + NL); await r.step();
    const seen = r.posts.length;
    failing = true; await r.step(); await r.step();
    assert.equal(r.posts.length, seen, "silent while unreadable");
    failing = false;
    fs.appendFileSync(at("flaky.jsonl"), ended("task-f") + NL); await r.step(); await r.step();
    assert.deepEqual(transitions(r.posts), [1, 0], "and the end is seen once it is readable again");
    r.stop();
  });
});

test("the reporter posts the count while work runs, zero once when it ends, and nothing while idle", async () => {
  const seen = (...l) => ({ status: READ.LINES, lines: l });
  const batches = [seen(), seen(shellStart("b1"), agentStart("a1")), seen(), seen(ended("b1")), seen(ended("a1")), seen(), seen()];
  const posts = [];
  let failNext = false;
  let tick;
  const stop = startBackgroundWorkReporter({
    transcriptPath: () => "transcript.jsonl",
    follower: { read: async () => batches.shift() || seen() },
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

test("ONE READ AT A TIME: a tick during a slow read starts no second one, and hands back the first", async () => {
  // A read or a post slower than the interval would otherwise pile reads onto one stateful follower.
  let reads = 0;
  let release;
  let tick;
  const stop = startBackgroundWorkReporter({
    transcriptPath: () => "transcript.jsonl",
    follower: { read: () => { reads += 1; return new Promise((resolve) => { release = resolve; }); } },
    post: async () => {},
    setIntervalImpl: (fn) => { tick = fn; return { unref() {} }; },
    clearIntervalImpl: () => {},
  });
  const during = tick();
  assert.equal(reads, 1, "the look the reporter started on its own is the only one");
  release({ status: READ.LINES, lines: [] });
  await during;
  const next = tick();
  assert.equal(reads, 2, "control: once it finished, the next tick reads again");
  release({ status: READ.LINES, lines: [] });
  await next;
  stop();
});
