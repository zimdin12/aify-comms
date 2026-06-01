#!/usr/bin/env node
// Growth-based transcript activity (status-liveness fix 2026-06-01). Pure tests
// of transcriptIsGenerating: active iff the transcript ADVANCED since the last
// observation (ongoing generation), NOT merely touched recently (which caused
// the post-turn re-pulse keeping idle residents `working`).
import assert from "node:assert/strict";
import { transcriptIsGenerating } from "../transcript-activity.js";

// (a) no previous observation -> baseline, not active
assert.equal(
  transcriptIsGenerating(null, { mtimeMs: 1000, size: 50 }),
  false,
  "first observation establishes a baseline, not active",
);

// (b) curr mtime advanced -> active (ongoing generation)
assert.equal(
  transcriptIsGenerating({ mtimeMs: 1000, size: 50 }, { mtimeMs: 2000, size: 80 }),
  true,
  "newer mtime (and growth) means active",
);
assert.equal(
  transcriptIsGenerating({ mtimeMs: 1000, size: 50 }, { mtimeMs: 2000, size: 50 }),
  true,
  "newer mtime alone (size unchanged) still means active",
);

// (c) static observation (post-turn: same mtime AND size) -> NOT active
assert.equal(
  transcriptIsGenerating({ mtimeMs: 2000, size: 80 }, { mtimeMs: 2000, size: 80 }),
  false,
  "identical mtime+size (no growth) must NOT be active -> post-turn clear sticks",
);

// (d) size grew but mtime equal (fs mtime resolution coarse) -> active
assert.equal(
  transcriptIsGenerating({ mtimeMs: 2000, size: 80 }, { mtimeMs: 2000, size: 120 }),
  true,
  "byte growth at equal mtime means active",
);

// (e) curr older or equal/shrunk -> NOT active
assert.equal(
  transcriptIsGenerating({ mtimeMs: 2000, size: 80 }, { mtimeMs: 1500, size: 80 }),
  false,
  "older mtime with no growth must not be active",
);
assert.equal(
  transcriptIsGenerating({ mtimeMs: 2000, size: 80 }, { mtimeMs: 2000, size: 60 }),
  false,
  "shrunk size at equal mtime must not be active",
);

// curr missing / unresolvable -> NOT active (defensive)
assert.equal(transcriptIsGenerating({ mtimeMs: 2000, size: 80 }, null), false, "null curr -> not active");
assert.equal(transcriptIsGenerating({ mtimeMs: 2000, size: 80 }, { mtimeMs: 0, size: 0 }), false, "zero curr mtime -> not active");

// size absent on one side -> fall back to mtime comparison only
assert.equal(
  transcriptIsGenerating({ mtimeMs: 2000 }, { mtimeMs: 2000 }),
  false,
  "no size info and equal mtime -> not active",
);
assert.equal(
  transcriptIsGenerating({ mtimeMs: 2000 }, { mtimeMs: 2001 }),
  true,
  "no size info but newer mtime -> active",
);

// --- End-to-end: a turn streams (growth ticks), then ends with one final write
// (one growth tick) and then idles. Only the streaming + the single final-write
// tick are active; the idle ticks afterward are NOT (the bug being fixed). ---
{
  // sequence of (mtimeMs, size) observations across heartbeat ticks
  const obs = [
    { mtimeMs: 100, size: 10 }, // baseline (turn just started, first read)
    { mtimeMs: 130, size: 40 }, // streaming
    { mtimeMs: 160, size: 90 }, // streaming
    { mtimeMs: 200, size: 120 }, // final Stop-hook write (still growth)
    { mtimeMs: 200, size: 120 }, // idle: no further growth
    { mtimeMs: 200, size: 120 }, // idle: still no growth
  ];
  const verdicts = [];
  let prev = null;
  for (const curr of obs) {
    verdicts.push(transcriptIsGenerating(prev, curr));
    prev = curr;
  }
  assert.deepEqual(
    verdicts,
    [false, true, true, true, false, false],
    "baseline=false, streaming+final-write=true, idle ticks=false (no re-pulse)",
  );
}

console.log("transcript-activity.test.js: all assertions passed");
