#!/usr/bin/env node
// Every skill file is held at its measured size, and the number may only go DOWN.
//
// WHY THIS REPLACES A FLAT CAP. `skill-consistency.test.js` capped four files at round numbers
// (16,000 / 20,000 / 3,500). Two problems, and the operator named both:
//
//   1. **It covered 4 of 17 files.** The other thirteen — ~231 KB, including the whole
//      `aify-comms-debug` reference set and `leading-a-team.md` — could grow unnoticed forever. Two
//      edits grew `leading-a-team.md` by 3 KB in one session and nothing said a word.
//   2. **A round number invites bumping.** An agent that hits "exceeds its 16,000-byte budget" can
//      make the test green by typing 17,000, and that reads like fixing the build. A cap with slack
//      in it is a cap you are allowed to grow into.
//
// A RATCHET fixes both, and this repo already trusts the shape: `no-unwatched-oversized-file.test.js`
// holds install.sh and styles.css at measured values that may only go down, and
// `test_leaves_do_not_import_the_carrier.py` does the same for reconciler imports. The numbers below
// are MEASURED, not rounded up, so growing a file by one byte fails — and the fix is to pay for it
// somewhere, or to change the number deliberately in the same commit and say why.
//
// **Raising a ceiling is a decision, not a repair.** If a skill genuinely needs more room, take it
// from another file, split the file, or write down in the commit what the reader gains for the bytes
// every agent now pays on every turn. What you must not do is nudge the number to make a red test
// green; that is the move this file exists to make visible.
//
// WHY BYTES AT ALL. A skill is not read on demand — the always-loaded ones enter every agent's
// context every session, so a byte there is paid by every agent on every turn rather than once by a
// reader. The references are cheaper (they cost a read when a pointer fires) but not free, and a
// 29 KB reference is roughly 7k tokens for whoever follows the pointer.

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SKILLS = path.join(REPO, ".claude", "skills");

// The `.agents/` mirror is byte-identical by `test_skill_mirror_parity.py`, so measuring one side
// measures both. Scanning both would double every number and hide which copy grew.

//: MEASURED 2026-08-19 (re-measured after the debug-reference prune). Not rounded up — see the header. May only go DOWN.
const CEILINGS = {
  "aify-comms/SKILL.md": 15_086,  // LOWERED AGAIN 2026-09-03, and the reason is that the
  // warning below became unnecessary. 15_103 on 2026-09-02 held a caution that `comms_envs`
  // reporting `online` did not mean a spawn could run -- prose compensating for a tool that
  // answered from the wrong field. The tool now reports the claim answer itself, so the skill
  // says how to READ it instead of warning about it, in fewer characters. Fixing the
  // instrument is the cheapest way to shrink a skill: the guidance it needed goes away.
  "aify-comms/references/building-software.md": 4_488,
  "aify-comms/references/leading-a-team.md": 19_462,
  // RAISED 2026-09-07 -- a DECISION, argued here as this file requires. An independent docs
  // audit found these files instructing an agent to restart aify-env as a routine remedy (it
  // ends every managed worker on the host), naming `api_v2.py` for a constant that lives in
  // `channel_delivery.py`, and defining `stopped` in a way that contradicts the other skill.
  // ~1.9 KB of stale-tier prose was DELETED to pay for the corrections; these 205 characters
  // are what is left, and what they buy is a recipe that no longer sends a reader to rebuild a
  // healthy service and three warnings that a step is the operator's, not an agent's.
  "aify-comms/references/operations.md": 11_604,
  "aify-comms/references/teamwork.md": 15_981,
  // 4_864 on 2026-08-30, measured. NEW FILE, so this is a first reading rather than a raise:
  // installing meant reading 1,227 lines of per-runtime guides and knowing which half applied
  // to the machine in front of you. Most of that is a question about the HOST, and
  // scripts/install-state.sh answers it -- which is what keeps this file short enough to be
  // read rather than skimmed.
  // LOWERED: the bare-command warning was tightened while keeping the phrase its gate pins.
  "aify-comms-install/SKILL.md": 4_846,
  "aify-comms-debug/SKILL.md": 3_117,
  "aify-comms-debug/references/codex.md": 14_114,
  // 18_454 -> 18_426 on 2026-08-30. It went DOWN while gaining a correction, so record why: the
  // `terminalRuntimes` paragraph named the bridge as the advertiser, which is now only true when
  // aify-env is not. Paid by dropping a merged branch name and a sentence restating what the
  // paragraph above it already said.
  "aify-comms-debug/references/dashboard-console.md": 18_025,
    // 26_968 -> 26_955 on 2026-09-05. It went DOWN while gaining a correction, so record why rather
  // than leaving the ceiling slack: two references cited files deleted with the
  // environment-bridge tier and now name the service modules that own those questions, and
  // instructions to `restart the environment bridge` became `restart aify-env` -- the component
  // v0.6.1 removed, told to an operator following a troubleshooting page.
  "aify-comms-debug/references/dispatch-bridges.md": 26_971,  // 25_917 -> 27_139 on
  // 2026-09-02 -> 27_131 -> 26_968 on 2026-09-03. The second paydown is v0.6.1 removing the
  // environment-bridge command: the fleet-death entry no longer has to teach a reader that a bare
  // `aify-comms` is dangerous, because it refuses. What it teaches instead is shorter and more
  // useful -- the failure MODE is supersession, aify-env supersedes the same way, so starting a
  // host tier stays the operator's action and never a check. A recovered instruction is cheaper
  // than the rule it replaces.
  // The raise before it added a failure CLASS that had cost the
  // operator a day and misled two agents into reporting the fleet ready: `comms_spawn`
  // refusing with 409 while `comms_envs` called the environment online, because `status` and
  // `bridgeLastSeen` answer different questions. The day after, the tool was fixed to ask the
  // right one -- so the entry no longer has to teach a reader to distrust it, and the section
  // shrank while gaining the note about which bridge builds still show the split.
  "aify-comms-debug/references/dispatch-delivery.md": 26_579,
  // 14_004 -> 14_926 on 2026-08-25. A DECISION, and here is what it buys.
  //
  // Managed spawns are delegated to aify-env from that date, and it is REQUIRED: the bridge refuses
  // rather than spawning locally. So "the managed run never started" acquired a new first cause, and
  // an agent debugging it without this entry chases cwd and launcher-path causes that no longer
  // apply -- the entries directly below this one. Every one of the three defects the first real spawn
  // exposed surfaces exactly this way.
  //
  // Paid by being the shortest thing that changes the reader's first move: it names the two commands
  // that answer the question and the three shapes the failure takes, and nothing else.
  // 14_926 -> 14_867 on 2026-09-05. It went DOWN while GAINING two safety facts, so record why
  // rather than leaving the ceiling slack. Added: `pkill -f aify-comms` matches the CHECKOUT PATH
  // two lines below it in the same recipe, so it kills the operator's own shell and no service;
  // and starting a second aify-env reaps the first one's workers. Paid for by retiring four stale
  // instructions to `restart the Windows aify-comms bridge` -- a component v0.6.1 removed, so the
  // file was telling an operator to restart something that does not exist.
  "aify-comms-debug/references/dispatch-launch.md": 14_970,
  "aify-comms-debug/references/hermes-session.md": 27_285,
  "aify-comms-debug/references/hermes-turns.md": 15_996,
  "aify-comms-debug/references/lifecycle.md": 6_704,
  "aify-comms-debug/references/pi.md": 7_316,
    // 21_920 -> 21_919 on 2026-09-05. It went DOWN while gaining a correction, so record why rather
  // than leaving the ceiling slack: two references cited files deleted with the
  // environment-bridge tier and now name the service modules that own those questions, and
  // instructions to `restart the environment bridge` became `restart aify-env` -- the component
  // v0.6.1 removed, told to an operator following a troubleshooting page.
  "aify-comms-debug/references/status-model.md": 21_919,
  "aify-comms-debug/references/status-symptoms.md": 19_372,
};

// An ALWAYS-LOADED file enters context whether or not it is needed, so it carries a hard limit on top
// of its ratchet. A reference is reached through a pointer and pays only when that pointer fires.
const ALWAYS_LOADED_LIMIT = 16_000;
const isAlwaysLoaded = (rel) => rel.endsWith("/SKILL.md");

function skillFiles(dir = SKILLS, acc = []) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) skillFiles(full, acc);
    else if (entry.endsWith(".md")) acc.push(path.relative(SKILLS, full).replace(/\\/g, "/"));
  }
  return acc;
}

const sizeOf = (rel) => readFileSync(path.join(SKILLS, rel), "utf8").length;

test("the scan reaches the skill tree at all", () => {
  // Without this, a renamed directory turns every assertion below into a vacuous pass.
  const found = skillFiles();
  assert.ok(found.length >= 10, `expected the skill corpus, found ${found.length} file(s)`);
  assert.ok(found.includes("aify-comms/SKILL.md"), "the main skill must be among them");
});

test("every skill file has a ceiling — a new one cannot arrive ungoverned", () => {
  // The hole the flat cap left: thirteen files nobody was watching. A skill added tomorrow inherits
  // the discipline instead of escaping it.
  const unwatched = skillFiles().filter((f) => !(f in CEILINGS)).sort();
  assert.deepEqual(
    unwatched,
    [],
    `skill files with no recorded ceiling: ${unwatched.join(", ")}. Measure them and add them here.`,
  );
});

test("every ceiling names a file that still exists", () => {
  const present = new Set(skillFiles());
  const missing = Object.keys(CEILINGS).filter((f) => !present.has(f)).sort();
  assert.deepEqual(missing, [], `ceilings for files that are gone: ${missing.join(", ")}`);
});

test("no skill file is above its ceiling", () => {
  const over = [];
  for (const [rel, limit] of Object.entries(CEILINGS)) {
    const actual = sizeOf(rel);
    if (actual > limit) over.push(`${rel}: ${actual} > ${limit} (+${actual - limit})`);
  }
  assert.deepEqual(
    over,
    [],
    `skill files grew past their ceiling:\n  ${over.join("\n  ")}\n`
      + "Pay for it elsewhere, split the file, or change the ceiling DELIBERATELY and say why in the "
      + "commit. Raising the number to clear a red test is the move this gate exists to catch.",
  );
});

test("no ceiling is left slack above the file it governs", () => {
  // The half that makes it a ratchet rather than a cap. Shrink a file and the number comes with it,
  // so the recorded size stays the real one and yesterday's headroom cannot be quietly re-spent.
  const slack = [];
  for (const [rel, limit] of Object.entries(CEILINGS)) {
    const actual = sizeOf(rel);
    if (actual < limit) slack.push(`${rel}: ceiling ${limit}, actual ${actual} (lower it by ${limit - actual})`);
  }
  assert.deepEqual(slack, [], `ceilings above their file:\n  ${slack.join("\n  ")}`);
});

test("always-loaded skills stay under the hard limit as well as their ratchet", () => {
  // The ratchet stops growth; this stops a SKILL.md ever being large in the first place, however it
  // got there. Every agent pays these bytes on every turn.
  const over = skillFiles()
    .filter(isAlwaysLoaded)
    .map((rel) => [rel, sizeOf(rel)])
    .filter(([, n]) => n > ALWAYS_LOADED_LIMIT)
    .map(([rel, n]) => `${rel}: ${n} > ${ALWAYS_LOADED_LIMIT}`);
  assert.deepEqual(over, [], `always-loaded skill over the hard limit:\n  ${over.join("\n  ")}`);
});
