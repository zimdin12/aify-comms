import assert from "assert";
import test from "node:test";
import { startClaudeTurnEndDetector } from "../claude-turn-end-detector.js";

// Tests for startClaudeTurnEndDetector (pure-event-status change #1). The
// periodic loop reads the claude transcript each tick and POSTs /turn-end when
// the conservative growth-keyed detector decides a turn ended — the
// hook-independent backstop for a missed Stop hook.
//
// ANTI-FEEDBACK-LOOP: the loop only reads transcript GROWTH and only ever POSTs
// /turn-end (a CLEAR). It never reads server status and never sets turn_busy.

test("a transcript that stops growing triggers exactly one /turn-end POST", async () => {
  const posts = [];
  // Sequence of transcript observations the loop will read on successive ticks.
  const obs = [
    { mtimeMs: 100, size: 10 }, // baseline
    { mtimeMs: 130, size: 40 }, // growth -> turn in flight, no post
    { mtimeMs: 130, size: 40 }, // NO growth after growth -> POST /turn-end
    { mtimeMs: 130, size: 40 }, // still no growth -> no re-post
    { mtimeMs: 130, size: 40 }, // still no growth -> no re-post
  ];
  let i = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 10,
    readTranscript: async () => obs[Math.min(i++, obs.length - 1)],
    postTurnEnd: async () => { posts.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, 120));
  stop();
  assert.strictEqual(posts.length, 1, `expected exactly one /turn-end POST; got ${posts.length}`);
});

test("a still-growing transcript never POSTs /turn-end", async () => {
  const posts = [];
  let size = 10;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 10,
    // Every read shows growth -> mid-turn, never ends.
    readTranscript: async () => ({ mtimeMs: 100 + size, size: (size += 30) }),
    postTurnEnd: async () => { posts.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.strictEqual(posts.length, 0, `a continuously-growing transcript must not fire turn-end; got ${posts.length}`);
});

test("missing params return a no-op stop fn and never throw", () => {
  const stop1 = startClaudeTurnEndDetector({});
  assert.strictEqual(typeof stop1, "function");
  stop1();
  const stop2 = startClaudeTurnEndDetector({
    intervalMs: 0, readTranscript: async () => null, postTurnEnd: async () => {},
  });
  assert.strictEqual(typeof stop2, "function");
  stop2();
});

test("a failed transcript read does not POST /turn-end (false-clear safety)", async () => {
  const posts = [];
  const obs = [
    { mtimeMs: 100, size: 10 }, // baseline
    { mtimeMs: 130, size: 40 }, // growth -> turn in flight
    null,                        // transient stat failure -> must NOT be read as turn-end
    { mtimeMs: 0, size: 0 },     // sentinel -> must NOT be read as turn-end
  ];
  let i = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 10,
    readTranscript: async () => obs[Math.min(i++, obs.length - 1)],
    postTurnEnd: async () => { posts.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.strictEqual(posts.length, 0, `failed/sentinel reads must not fire turn-end; got ${posts.length}`);
});
