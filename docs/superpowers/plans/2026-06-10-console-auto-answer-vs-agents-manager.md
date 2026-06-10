# Console auto-answer vs the background-agents manager — Fix Plan

**Incident:** while a managed claude runs SUBAGENTS, the console randomly lands in the
agent-selection screen ("selection?" issue). Reproduced from the live DB: Claude Code's
background-agents manager renders ❯-style row cursors and a footer
(`⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents · ↓ to manage · ↑/↓ to select ·
Enter to view`) where the spinner footer would be. The bridge auto-answer's
`bypass-permissions-accept` rule (`/bypass permissions[\s\S]{0,160}(accept|yes, i accept|continue)/`)
matched the ALWAYS-PRESENT footer chrome + an incidental "continue/accept" in a subagent's task
title; the ❯ row cursor satisfied the menu gate; the manager occluded the spinner so
`consoleClass` read `unknown` (not `working`) — all three safety gates passed and the bridge
typed Enter, which the manager interpreted as "Enter to view".

**Root cause class:** prompt rules that key on CHROME + nearby generic words instead of the
DIALOG'S OWN distinctive text. Any rule of that shape will misfire on new claude UI surfaces.

**Design principle (the non-hackfix):** a rule may fire only on text that exists ONLY in its
dialog (the warning line + its literal option strings, from the fixtures), and NO rule may fire
while the agents-manager chrome is visible (the manager means claude is orchestrating, never
stuck at a boot prompt).

## Tasks

- [x] **1. Tighten `bypass-permissions-accept` to the dialog shape** — fixture says
  "WARNING: Claude Code running in Bypass Permissions mode … ❯ Yes, I accept / No, exit".
  New match: `/bypass permissions mode[\s\S]{0,200}yes, i accept/i`. The footer chrome
  ("bypass permissions on (shift+tab…") has neither "mode" nor "Yes, I accept".
- [x] **2. Global agents-manager suppression** in `matchConsolePrompt`:
  `/← for agents|↑\/↓ to select|↓ to manage/` in the visible tail → return null for ALL rules.
- [ ] **3. Same-class audit of the sibling rules:**
  - compaction: drop the loose `compact[\s\S]{0,80}continue` alternative (matches prose like
    "compact the list and continue"); keep the dialog-literal `continue without compact`.
  - channel-enter: tighten `enter channel|join channel` (prose-able) to the fixture's
    distinctive `enter channel to receive`; keep `development-channels` (plugin name).
  - resume rule: already dialog-shaped (requires BOTH option strings + cursor math) — no change.
- [ ] **4. Regression tests** (`claude-console-prompts.test.js`): real agents-manager footer
  (+ subagent title containing "continue") fires NOTHING; the three fixtures still fire their
  rules; prose "compact … continue" / "join channel" no longer fire.
- [ ] **5. Deploy:** reinstall bridge (native copy) — wrapper restart is the operator's.

**Deliberately NOT changed:** the spinner classifier does not treat manager chrome as
`working` — the manager can stay on screen while everything is idle, so chrome alone is not
evidence of work (the transcript turn-detector + position-aware server hint already cover
status during subagent runs). Watch item: if status flaps to `online` during long manager-
occluded runs, revisit with a positive running-row signal (per-agent elapsed-time rows).
