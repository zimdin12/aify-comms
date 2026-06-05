# Claude Console Interaction Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give managed claude a centralized console matching-rules layer that auto-answers the TUI prompts that currently freeze a freshly-spawned/restarted agent (resume-session prompt, compaction question, bypass-permissions accept), so a managed worker boots to a usable turn unattended.

**Architecture:** The host bridge owns the managed PTY — it can both *read* output and *type* into it (`terminal-runtime.js` `input()`). We add a pure rules registry (`claude-console-prompts.js`: `[{ name, match, answer, ... }]` + `matchConsolePrompt(tail)`) that recognizes a claude TUI prompt from the ANSI-stripped console tail and returns the keystrokes to answer it. `_handleOutput` runs the matcher on each frame and types the answer once per prompt appearance, gated to **managed claude only** with a kill-switch. This is the principled replacement for the scattered ad-hoc prompt handling ("the shitty version we have currently"), and it directly kills the boot-stuck class: resume prompt / compaction question / perms accept that strand a new managed claude.

**Tech Stack:** Node.js ESM (host bridge, `mcp/stdio/`), node:test for bridge unit tests.

**Operator policy (locked):** on the resume prompt, default to **Resume full session** — the prompt highlights "Resume from summary" by default, so the answer is **↓ then Enter** (`\x1b[B\r`).

**Dependency:** builds on `2026-06-05-managed-claude-console-working-signal.md` — reuses `stripAnsi` from `claude-console-spinner.js`. Land that plan first (or at least its Task 1).

**Safety contract:** auto-answer fires ONLY when `runtime === "claude-code"` AND `sessionMode === "managed"` (never types into a resident/operator terminal), each rule fires once per distinct on-screen appearance, and a kill-switch (`AIFY_NO_AUTO_ANSWER=1`) disables the whole layer.

---

### Task 1: Capture real prompt fixtures

**Files:**
- Create: `mcp/stdio/tests/fixtures/claude-console/resume-prompt.txt`, `.../compaction-prompt.txt`, `.../perms-accept.txt`

- [ ] **Step 1: Capture each prompt frame from a live managed claude**

These three prompts are version-dependent TUI text, so encode rules from real bytes, not memory. Trigger each and dump the raw console tail (escape codes included) via the console-tail tool / dashboard "copy console":

```bash
mkdir -p mcp/stdio/tests/fixtures/claude-console
# Resume prompt: restart a managed claude that has an existing session handle, capture
# the "Resume from summary / Resume full session" menu before answering:
$EDITOR mcp/stdio/tests/fixtures/claude-console/resume-prompt.txt
# Compaction question: resume a large session until claude asks the compaction question:
$EDITOR mcp/stdio/tests/fixtures/claude-console/compaction-prompt.txt
# Perms accept: launch claude WITHOUT a pre-accepted bypass and capture the
# "Bypass Permissions" accept dialog:
$EDITOR mcp/stdio/tests/fixtures/claude-console/perms-accept.txt
```

Expected: three files containing the literal on-screen prompt text. Note the exact distinctive phrase and the highlighted-default option in each (these become the `match` regex and decide the keystrokes).

- [ ] **Step 2: Commit the fixtures**

```bash
git add mcp/stdio/tests/fixtures/claude-console/resume-prompt.txt mcp/stdio/tests/fixtures/claude-console/compaction-prompt.txt mcp/stdio/tests/fixtures/claude-console/perms-accept.txt
git commit -m "test(fixtures): real claude TUI prompt frames for auto-answer rules"
```

---

### Task 2: Pure rules registry (`claude-console-prompts.js`)

**Files:**
- Create: `mcp/stdio/claude-console-prompts.js`
- Test: `mcp/stdio/tests/claude-console-prompts.test.js`

- [ ] **Step 1: Write the failing test**

```js
#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { matchConsolePrompt, CONSOLE_PROMPT_RULES } from "../claude-console-prompts.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = (n) => readFileSync(join(here, "fixtures/claude-console", n), "utf8");

// Resume prompt -> Resume full session = down + enter.
const resume = matchConsolePrompt(fx("resume-prompt.txt"));
assert.equal(resume?.name, "resume-full-session");
assert.equal(resume?.answer, "\x1b[B\r");

// A plain idle screen matches no rule.
assert.equal(matchConsolePrompt("│ > │\n  ? for shortcuts"), null);
assert.equal(matchConsolePrompt(""), null);

// Every rule has the required shape.
for (const r of CONSOLE_PROMPT_RULES) {
  assert.ok(r.name && r.match instanceof RegExp && typeof r.answer === "string");
}

// Only the live tail region is matched: a resume menu far up in scrollback under a
// current idle prompt does NOT match (avoid answering a scrolled-away prompt).
assert.equal(
  matchConsolePrompt(fx("resume-prompt.txt") + "\n" + "x\n".repeat(2000) + "│ > │\n  ? for shortcuts"),
  null,
);

console.log("claude-console-prompts.test.js: all assertions passed");
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node mcp/stdio/tests/claude-console-prompts.test.js`
Expected: FAIL — `Cannot find module '../claude-console-prompts.js'`.

- [ ] **Step 3: Write the registry**

Use the distinctive phrases recorded in Task 1 for the `compaction` and `perms` `match` regexes (the resume rule below is fully worked). Keystroke answers: `\r` = Enter, `\x1b[B` = Down, `\x1b[A` = Up.

```js
// Centralized console matching-rules layer for the managed-claude TUI. Each rule maps
// a recognizable prompt (ANSI-stripped) to the keystrokes that answer it. The host types
// the answer once per on-screen appearance. Replaces scattered ad-hoc prompt handling.
//
// Keystrokes: "\r" Enter, "\x1b[B" Down, "\x1b[A" Up.
//
// Rule contract: { name, match: RegExp, answer: string, mustAlsoMatch?: RegExp }.
// A rule fires only when `match` (and `mustAlsoMatch`, if present) hit the live tail
// region. Rules are tried in order; the FIRST match wins.
import { stripAnsi } from "./claude-console-spinner.js";

export const CONSOLE_PROMPT_RULES = [
  {
    // Resume prompt. Operator policy: choose "Resume full session". The menu highlights
    // "Resume from summary" by default, so move down once and confirm.
    name: "resume-full-session",
    match: /Resume full session/i,
    mustAlsoMatch: /Resume from summary/i,
    answer: "\x1b[B\r",
  },
  {
    // Compaction question on resume. Replace the regex with the distinctive phrase from
    // fixtures/claude-console/compaction-prompt.txt; answer per the captured default
    // (Enter accepts the highlighted option).
    name: "compaction-question",
    match: /compact[\s\S]{0,80}continue|continue without compact/i,
    answer: "\r",
  },
  {
    // Bypass-permissions accept dialog. Replace the regex with the distinctive phrase from
    // fixtures/claude-console/perms-accept.txt; answer to confirm the highlighted accept.
    name: "bypass-permissions-accept",
    match: /bypass permissions[\s\S]{0,120}(accept|yes, i accept|continue)/i,
    answer: "\r",
  },
];

// Match the live tail region against the rules. Returns the first matching rule or null.
// Only the last ~2KB of visible text is considered so a scrolled-away prompt is ignored.
export function matchConsolePrompt(rawTail = "") {
  const visible = stripAnsi(rawTail).slice(-2000);
  for (const rule of CONSOLE_PROMPT_RULES) {
    if (!rule.match.test(visible)) continue;
    if (rule.mustAlsoMatch && !rule.mustAlsoMatch.test(visible)) continue;
    return rule;
  }
  return null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node mcp/stdio/tests/claude-console-prompts.test.js`
Expected: PASS. Tune the `compaction`/`perms` regexes to the captured fixtures if their dedicated asserts (added once you extend the test with those fixtures) miss.

- [ ] **Step 5: Syntax-check and commit**

```bash
node --check mcp/stdio/claude-console-prompts.js
git add mcp/stdio/claude-console-prompts.js mcp/stdio/tests/claude-console-prompts.test.js
git commit -m "feat(console): pure auto-answer rules registry for claude TUI prompts"
```

---

### Task 3: Type the answer once per appearance, gated to managed claude

**Files:**
- Modify: `mcp/stdio/terminal-runtime.js` (import top; constructor ~line 138; `_handleOutput` ~line 325)
- Test: `mcp/stdio/tests/terminal-runtime-auto-answer.test.js`

- [ ] **Step 1: Write the failing test**

```js
#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { TerminalProcessManager } from "../terminal-runtime.js";

const here = dirname(fileURLToPath(import.meta.url));
const resumeFrame = readFileSync(join(here, "fixtures/claude-console/resume-prompt.txt"), "utf8");

// Capture what gets typed into the PTY.
function makeMgr(opts = {}) {
  const typed = [];
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, ...opts });
  mgr.input = (id, body) => typed.push([id, body]); // stub the PTY write
  return { mgr, typed };
}

// Managed claude: a resume prompt is auto-answered with down+enter, exactly once.
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t1", runtime: "claude-code", sessionMode: "managed", agentId: "a1", outputTail: "" };
  mgr.terminals.set("t1", st);
  await mgr._handleOutput("t1", st, resumeFrame);
  await mgr._handleOutput("t1", st, ""); // a follow-up frame with the prompt still up...
  st.outputTail = resumeFrame; // ...still showing
  await mgr._handleOutput("t1", st, "");
  assert.deepEqual(typed.filter((t) => t[0] === "t1").map((t) => t[1]), ["\x1b[B\r"]);
}

// Resident claude is NEVER auto-answered (no typing into an operator session).
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t2", runtime: "claude-code", sessionMode: "resident", agentId: "a2", outputTail: "" };
  mgr.terminals.set("t2", st);
  await mgr._handleOutput("t2", st, resumeFrame);
  assert.equal(typed.length, 0);
}

// Kill-switch disables it.
{
  const { mgr, typed } = makeMgr({ autoAnswer: false });
  const st = { id: "t3", runtime: "claude-code", sessionMode: "managed", agentId: "a3", outputTail: "" };
  mgr.terminals.set("t3", st);
  await mgr._handleOutput("t3", st, resumeFrame);
  assert.equal(typed.length, 0);
}

console.log("terminal-runtime-auto-answer.test.js: all assertions passed");
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node mcp/stdio/tests/terminal-runtime-auto-answer.test.js`
Expected: FAIL — nothing is typed (auto-answer not implemented).

- [ ] **Step 3: Add the import**

After the `claude-console-spinner.js` import added in the companion plan (or add it here if landing standalone), add to the top of `terminal-runtime.js`:

```js
import { matchConsolePrompt } from "./claude-console-prompts.js";
```

- [ ] **Step 4: Accept the `autoAnswer` option in the constructor**

In the constructor (line 138), add `autoAnswer = true` to the destructured options and store it. After `this.onHeal = onHeal;` (line 148) add:

```js
    // Auto-answer is on by default for managed claude; AIFY_NO_AUTO_ANSWER=1 (read by the
    // bridge that constructs this manager) or passing autoAnswer:false disables it.
    this.autoAnswer = autoAnswer !== false;
```

- [ ] **Step 5: Run the matcher in `_handleOutput`**

In `_handleOutput`, after the `state.consoleClass = ...` line (added by the companion plan; if landing standalone, after `state.outputTail = appendTail(...)`), insert:

```js
    // Console prompt auto-answer (managed claude only). Type the answer once per on-screen
    // appearance: track the answered rule; reset when the prompt clears so a later distinct
    // appearance is answered again. Never types into a resident/operator session.
    if (this.autoAnswer && state.runtime === "claude-code" && state.sessionMode === "managed") {
      const rule = matchConsolePrompt(state.outputTail);
      if (rule && state.answeredPrompt !== rule.name) {
        state.answeredPrompt = rule.name;
        try { this.input(id, rule.answer); } catch { /* terminal may have exited mid-frame */ }
      } else if (!rule) {
        state.answeredPrompt = null;
      }
    }
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `node mcp/stdio/tests/terminal-runtime-auto-answer.test.js`
Expected: PASS — `all assertions passed`.

- [ ] **Step 7: Syntax-check and commit**

```bash
node --check mcp/stdio/terminal-runtime.js
git add mcp/stdio/terminal-runtime.js mcp/stdio/tests/terminal-runtime-auto-answer.test.js
git commit -m "feat(console): auto-answer claude TUI prompts (managed-only, once-per-appearance)"
```

---

### Task 4: Wire the kill-switch from the bridge env

**Files:**
- Modify: `mcp/stdio/server.js` (the `new TerminalProcessManager({...})` / `new TerminalRuntime` construction, ~line 765)
- Test: covered by Task 3's kill-switch case (no new test).

- [ ] **Step 1: Pass `autoAnswer` from the env at construction**

In the `const TERMINAL_MANAGER = new TerminalProcessManager({` options object (line ~765), add:

```js
  autoAnswer: process.env.AIFY_NO_AUTO_ANSWER !== "1",
```

- [ ] **Step 2: Syntax-check and commit**

```bash
node --check mcp/stdio/server.js
git add mcp/stdio/server.js
git commit -m "feat(console): AIFY_NO_AUTO_ANSWER kill-switch for prompt auto-answer"
```

---

### Task 5: Improve the channel auto-enter using the same engine

**Files:**
- Modify: `mcp/stdio/claude-console-prompts.js` (add a channel-enter rule)
- Test: `mcp/stdio/tests/claude-console-prompts.test.js` (extend) + fixture `.../channel-enter.txt`

- [ ] **Step 1: Capture the current channel-enter prompt frame**

The existing "shitty version" of channel auto-enter is the `--dangerously-load-development-channels` flow. Capture the frame claude shows when a channel is offered/entered:

```bash
$EDITOR mcp/stdio/tests/fixtures/claude-console/channel-enter.txt
```

- [ ] **Step 2: Write the failing assertion (extend the prompts test)**

```js
const channel = matchConsolePrompt(fx("channel-enter.txt"));
assert.equal(channel?.name, "channel-enter");
assert.equal(typeof channel?.answer, "string");
```

- [ ] **Step 3: Run to verify it fails**

Run: `node mcp/stdio/tests/claude-console-prompts.test.js`
Expected: FAIL — `channel` is null (no rule yet).

- [ ] **Step 4: Add the channel-enter rule**

Append to `CONSOLE_PROMPT_RULES` in `claude-console-prompts.js`, using the distinctive phrase from the captured fixture and the keystroke that accepts the channel (record the highlighted default from the fixture):

```js
  {
    // Channel auto-enter: accept the development-channels prompt so a dispatched channel
    // wake lands instead of stranding at the prompt. Phrase/answer taken from
    // fixtures/claude-console/channel-enter.txt.
    name: "channel-enter",
    match: /development-channels|enter channel|join channel/i,
    answer: "\r",
  },
```

- [ ] **Step 5: Run to verify it passes**

Run: `node mcp/stdio/tests/claude-console-prompts.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/claude-console-prompts.js mcp/stdio/tests/claude-console-prompts.test.js mcp/stdio/tests/fixtures/claude-console/channel-enter.txt
git commit -m "feat(console): channel auto-enter via the console rules engine"
```

---

### Task 6: Docs + deploy + live verification

**Files:**
- Modify: `DECISIONS.md`, `.claude/skills/aify-comms-debug/SKILL.md`, `.agents/skills/aify-comms-debug/SKILL.md`

- [ ] **Step 1: Document the rules layer in `DECISIONS.md`**

Add a 2026-06-05 entry: the host bridge owns the managed PTY and runs a centralized console matching-rules layer (`claude-console-prompts.js`) to auto-answer claude TUI prompts (resume → full session via ↓+Enter; compaction question; bypass-permissions accept; channel auto-enter). Gated to managed claude only, once per appearance, kill-switch `AIFY_NO_AUTO_ANSWER=1`. Rationale: these prompts otherwise freeze a freshly-spawned/restarted managed claude; the rules are TUI-version-dependent and centralized so a claude TUI change is a one-file fix.

- [ ] **Step 2: Update the troubleshooting skill (both copies, byte-identical)**

Add a "managed claude freezes on boot at a prompt (resume / compaction / permissions)" entry to both `.claude/skills/aify-comms-debug/SKILL.md` and `.agents/skills/aify-comms-debug/SKILL.md`: cause = an unanswered TUI prompt; fix = the console rules layer auto-answers it; if a NEW prompt appears after a claude update, capture its frame into `mcp/stdio/tests/fixtures/claude-console/` and add a rule; disable with `AIFY_NO_AUTO_ANSWER=1`.

- [ ] **Step 3: Deploy the bridge**

```bash
node --check mcp/stdio/claude-console-prompts.js && node --check mcp/stdio/terminal-runtime.js && node --check mcp/stdio/server.js
bash install.sh --client claude http://localhost:8800
```

Expected: all checks pass; installer copies the bridge to `~/.aify-comms`.

- [ ] **Step 4: Live-verify boot is unattended**

Restart a managed claude that has an existing session handle (so it hits the resume prompt). Watch the Console.

Expected: the resume menu appears and is auto-answered (cursor moves down, full session resumes) within one output frame; the agent reaches a usable turn without operator keystrokes. Repeat for a session large enough to trigger the compaction question.

- [ ] **Step 5: Commit the docs**

```bash
git add DECISIONS.md .claude/skills/aify-comms-debug/SKILL.md .agents/skills/aify-comms-debug/SKILL.md
git commit -m "docs(console): auto-answer rules layer for managed-claude boot prompts"
```

---

## Self-Review

**Spec coverage:** fixtures (Task 1) ✓; rules registry + matcher (Task 2) ✓; resume-full-session = ↓+Enter per operator policy (Task 2) ✓; compaction + perms rules (Task 2, tuned from fixtures) ✓; once-per-appearance typing gated to managed claude (Task 3) ✓; resident never typed + kill-switch (Tasks 3, 4) ✓; channel auto-enter on the same engine (Task 5) ✓; docs + deploy + live verify (Task 6) ✓.

**Placeholder scan:** the resume rule is fully concrete (match + `\x1b[B\r`). The compaction/perms/channel `match` regexes are written but explicitly tuned to the Task-1/Task-5 captured fixtures — the capture is a concrete step producing ground-truth files, and each rule ships with a starter regex + the exact file to validate against, not a "fill in later" hole.

**Type consistency:** rule shape `{ name, match, answer, mustAlsoMatch? }` consistent across registry, `matchConsolePrompt`, and all tests; `matchConsolePrompt` reused identically in Task 3; `state.answeredPrompt` set/reset only in `_handleOutput` (Task 3); `this.autoAnswer` set in constructor (Task 3) and fed from env (Task 4); `this.input(id, body)` is the existing PTY-write method (terminal-runtime.js:483), stubbed in the Task 3 test. `stripAnsi` imported from `claude-console-spinner.js` (companion plan Task 1).
