# Brief — comms-senior-dev (hermes): fix aify-comms dashboard/console/delivery issues

> **Historical execution brief (2026-07-15).** The checkout path, branch state, personnel,
> deployment permissions, and task instructions below applied only to that completed work round.
> Do not execute them as current repository guidance; use `AGENTS.md` and the current worktree.

You are **comms-senior-dev**, a hermes senior engineer working ON aify-comms itself.
**comms-tech-lead** (Steven's claude session) reviews your work. Steven is **very low on Claude
tokens**, so YOU do the analysis, planning, implementation and testing; comms-tech-lead only
reviews and relays a short plan summary to Steven. Optimise for value per unit of Steven's attention.

## Where to work
- Repo (THIS checkout, with 18 unpushed commits you must be aware of):
  `/mnt/wsl/docker-desktop-bind-mounts/Ubuntu/80ef643960bce5543adf5cdca562972f166d22aabe65144b08c653f11d71991b/aify-claude`
  `cd` there first. Do NOT clone a fresh copy — the unpushed work only exists here.
- **Do not push.** Steven pushes. Do not restart the team / env bridge unless Steven approves — it
  cycles his managed agents and "ruins the context". Container rebuilds (service/new-dashboard) and
  `install.sh` reinstalls are fine and do NOT cycle agents.

## Non-negotiable working rules (read `/home/dev/.claude/CLAUDE.md` in full — the scope + evidence discipline sections)
- **Proof over inference.** Every substantive claim carries evidence you actually observed (test
  output, grep, file:line, a browser measurement, a deploy log). Distinguish PROVEN-live vs
  PASSES-in-tests vs ASSUMED. Name what you cannot verify from where you sit.
- **A test nobody watched fail is a rumour.** Watch each new test go RED before you trust its green.
- **Surgical.** Smallest correct change in the right place. Don't break existing tests. Read the
  code path end to end before editing. Root-cause, not symptom.
- **A stopping rule is part of each task.** Say what "done" is up front; stop when you hit it.
- **The TLDR IS the document** when you report. Rigour is why you can be brief.
- **Measure the UI, don't theorise about it.** The console bugs below were made WORSE twice by
  reasoning instead of opening a browser. If you touch the dashboard, verify in a real browser
  (playwright is available) and attach a measurement/screenshot as proof.

## Deliverable order
1. **Plan first, before big implementation.** For each issue: root cause (with proof), fix
   approach, the test that proves it, risk/blast-radius, and a stopping rule. Post the plan to
   comms-tech-lead as `comms_send(type="response", inReplyTo=<this dispatch's message id>)`. Keep
   it a TLDR + a per-issue line; comms-tech-lead relays a short version to Steven and reviews.
2. **Then implement**, one issue at a time, each with a real test, each verified. Report each as
   you finish. Ask ONE clear question if blocked rather than guessing.

## Context you must load first
- `CLAUDE.md` (repo root) — dev workflow, rebuild rules, where things live.
- `docs/plans/2026-07-14-cli-status-view-and-dashboard-upgrades.md` — items 2 (console scroll),
  3 (analytics ranges), 5 (STUDY hermes' in-browser TUI). Your issue #1 below is that item 5.
- `.claude/skills/aify-comms-debug/references/status.md` and `.../dispatch-bridge.md`.
- `aify-doctor` (installed launcher; `--json`) — run it to see fleet/build/bridge truth.
- The 18 unpushed commits (below) — many touch the same files you will. Understand them; flag any
  you believe is wrong, but they are comms-tech-lead's work under review, not your task to redo.

## FIRST-CLASS DELIVERABLE (Steven, 2026-07-15): REVIEW all 18 unpushed commits — your review GATES the push
These 18 commits are comms-tech-lead's work and are UNPUSHED. Steven pushes based on your review, so
your review is what gates the push — treat it as a real deliverable, done in the PLAN phase before you
build on these files. For EACH commit:
- Is it CORRECT? Read `git show <sha>` and the code path it touches.
- Is the test proof adequate — is there a test, and would it actually FAIL if the fix regressed?
- Regression risk / blast radius on the live fleet?
- Over-engineering or scope creep (apply the scope-discipline rules in /home/dev/.claude/CLAUDE.md)?
Write a per-commit verdict (OK / CONCERN + why, citing file:line) to
`docs/plans/2026-07-15-comms-senior-dev-REVIEW.md`. Be adversarial but fair — you are the second pair
of eyes the claim discipline requires ("the claimant is the last person who should certify the claim").
Highest scrutiny: `a0c8ad9` (KEEP-CLEARED — touches every agent's turn detection), `46968b8`+`8b40990`
(console live-screen + repaint, which needed several corrections), `c1b7d50` (flicker/blocked/compaction).
Ping `dashboard` when the REVIEW file is ready.

## The 18 unpushed commits (list; review each per the section above)
```
8b40990 fix(console): force a repaint on attach/refresh — the ONLY thing that un-garbles these TUIs
70472c3 fix(usage): stop publishing a quota number we cannot stand behind; collect it in the SERVICE
742f049 fix(dashboard): Console tab shows the CONSOLE — never the hermes web page
dff8b21 feat(dashboard): Start agent button for a cold managed agent (no session row)
ad1afd9 fix(status): /rename stranded an agent at `working` forever; console scrollback restored
14ce0a3 docs: plan the CLI status view + record 3 verified dashboard defects
c6d2706 feat(doctor): aify-doctor — prove an install/update actually took effect
eb14f5f fix(usage): find the OpenAI token by SEARCHING, not guessing the OS
c669001 fix(usage): OpenAI quota was reading the wrong file AND labelling the wrong window
46968b8 fix(console): keep the terminal screen LIVE — replaying a truncated log cannot rebuild it
c1b7d50 fix(status): dashboard flicker + visibly blocked + compaction auto-confirm
b93c356 feat(status): registering now TURNS STATUS ON — identity may arrive late
209f6fc docs: sync docs + skills with identity/status findings
0cba79a fix(status): claude gets handle->agent recovery hermes has had since June
224d50e fix(status): never start an agent session anonymously
d418ffd fix(usage): read hermes auth from ~/.hermes/auth.json on Linux/WSL
6396125 fix(new-dash): run-inspector header + subject overflow
a0c8ad9 fix(status): KEEP-CLEARED — proof-based turn-end
```

---

## THE ISSUES (all operator-reported; evidence pins are starting points — verify, then go deeper)

### 1. Console stability — study hermes' in-browser TUI and make ours as stable (BIGGEST, highest value)
Steven: *"cant we do really similar solution as hermes dashboard tui has? why cant we get it same
stable?"* Our console has needed repeated fixes and still isn't right; hermes' own dashboard TUI
renders a terminal in a browser and is stable.

**Measured facts (comms-tech-lead, verified live — re-verify, don't trust):** the TUIs we host emit
ZERO newlines, paint by ABSOLUTE cursor position, NEVER scroll the viewport, and repaint only what
changed (Ink-style diff). A screen we start tracking mid-stream keeps wrong rows forever unless a
full repaint is forced; a real PTY resize forces that repaint. There is **no true scrollBACK** —
nothing scrolls off, so no terminal can have history for these apps without a synthetic
screen-history feature.

**Our current approach:** `service/terminal_snapshot.py` keeps a server-side pyte "live screen" fed
every chunk; `service/new_dashboard/app.js` (attachConsole / `applyRenderedWidth` / `resyncActiveConsole`)
renders it to a browser xterm and, as of commit `8b40990`, forces a PTY resize on attach/refresh to
trigger a clean repaint. It works but is fragile.

**Your job:** you ARE hermes — study how the hermes `tui_gateway` / dashboard renders an in-browser
terminal (transport, redraw model, resize, and how/whether it does scrollback). Determine whether
we can adopt that model for stability instead of our replay/repaint approach. Produce a design
recommendation with a proof-of-concept plan. If a synthetic scrollback is the right answer, spec it.
Do NOT rip out the working `8b40990` path until a measured-better replacement is proven in a browser.

### 2. Switch-to-managed chip does nothing until a full page reload
Steven: *"I tried to switch comms-tech-lead to managed. pressed the button and nothing happened...
only after refreshing whole page it switched to managed and start agent appeared."*
The chip is `service/new_dashboard/app.js:2235` (`data-mode-switch`). Find its click handler; after
the mode-switch POST succeeds it does not re-fetch/re-render agent+session state. Fix: refresh state
on success (there is a `refreshSoon()` helper). Verify in a browser: click → UI updates without a
manual reload. Stopping rule: the chip flips the mode and the Start-agent affordance appears, no reload.

### 3. Reminder messages must come from the ORIGINAL sender, not "dashboard"
Steven: *"these reminder messages we send. these should be from the original sender not from
dashboard. so agent has to answer to the original agent."*
Reply-reminder machinery: `service/routers/api_v2.py:177-184` (`reply_reminder_*`). Find where the
reminder message is constructed and what `from_agent` it carries; make the reminder come from the
original requester so the recipient's reply threads back to the real agent, not `dashboard`. Test:
an unanswered require_reply run's reminder shows the original sender; a reply to it is inReplyTo the
original message and reaches the original sender.

### 4. Dashboard chat messages should NOT requireReply by default (opt-in only)
Steven: *"when I write then it should not requestReply by default (only if I set it on from chat
options)."* Evidence: `app.js:155` sends `requireReply: !!expectsReply`; `app.js:3637` has
`requireReply: true` on the queue-after path. Find the PRIMARY compose-box send and its
`expectsReply` default; make the operator's plain chat message default `requireReply=false`, with an
opt-in toggle in chat options. Do NOT change request/review/error semantics between agents — only
the human's dashboard compose default. Test: a plain dashboard message creates a run that does not
demand a reply / does not spawn reply-reminders; toggling the option on restores require-reply.

### 5. Bridge log spam: transient "spawn claim failed ... fetch failed" that immediately recovers
Steven pasted many pairs like:
`[aify] spawn claim failed (1 consecutive) against http://127.0.0.1:8800: fetch failed ...`
`[aify] spawn claim recovered after 1 failure(s)`
Also the same for `terminal control` and `environment control` claims. These are SINGLE transient
poll failures that recover on the next poll. In `mcp/stdio/` (the bridge poll loop — grep the exact
strings), find the claim-error logging. Question to answer with proof: is the service actually
dropping connections (a real defect to fix) or is this normal single-poll contention? If transient
and self-healing, quiet it — a single failure that recovers next poll should be debug-level, and
only SUSTAINED failure (N consecutive, or a recovery that took many tries) should warn. Do NOT
silence a real outage: keep escalation on sustained failure. Test: a single injected transient
failure logs at debug (or is deduped); N-consecutive still warns.

### 6. general-manager stopped receiving messages (resident subscription staleness)
Steven: *"my general manager stopped receiving messages."* general-manager's own analysis: (a) a
`type=response` reply lands in the inbox and does not push-wake the requester (by design — verify
this is intended), and (b) his resident subscription had gone stale (presence stuck ~5h) and a
stale resident session can stop receiving push-wakes until re-register.
**The real question:** does a resident's channel subscription silently go stale and stop delivering,
with no self-heal? If yes, that is a delivery-reliability bug. Investigate the resident channel wake
path (`mcp/stdio/claude-channel.js`) and the service dispatch delivery gating (search
`resident`/`subscription`/`last_seen` staleness in `service/routers/api_v2.py`). Determine with
proof whether (b) is a real bug or expected, and whether a self-heal (re-subscribe on staleness)
is warranted. This one may end as a documented finding rather than a code change — that is fine if
the proof says so.

---

### 7. Fresh managed-hermes agents cannot boot (PROVEN live — you are running as codex BECAUSE of this)
comms-tech-lead reproduced this trying to spawn YOU as hermes: a brand-new managed-hermes agent
(no saved session handle, `resume_policy=native_first`) launches, registers, heartbeats, a claimer
attaches (`bridge_instances=1`) — but the gateway NEVER creates/reports a session: `session_handle`
stays empty, the console stays `stopped`/0 bytes, no terminal events fire, spawn `error=''` (SILENT
failure), and every dispatch backstops after 180s ("up-but-deaf"). This happened on 3 consecutive
fresh spawns. Meanwhile 4 hermes SIBLINGS (cms-senior-dev, acma-coder, lc-coder, lc-tech-lead) are
fully live on the SAME bridge code — every one of them RESUMES an existing session
(`--resume <handle>`). So the failing path is FRESH-SESSION CREATE, not hermes generally, and NOT
comms-tech-lead's 18 commits (siblings prove the code is fine).
**ROOT CAUSE — found live (comms-tech-lead, 2026-07-15), the state.db idea was WRONG and killed by its own test** (state.db opens in 0ms, 49703 msgs counted in 17ms — not the cause):
The real cause is a **gateway-host process leak** that CPU-saturates the box:
- Each managed-hermes agent runs a heavyweight `hermes dashboard` gateway (a python web server, DAEMONIZED — parented to init, not its managed-host). Fresh-session boot must init 14 MCP servers + 110 skills and pass a **60s readiness probe** (`hermes-managed-host.js:120` `READY_TIMEOUT_MS`, env `AIFY_HERMES_GATEWAY_READY_MS`).
- **STOP does not reliably reap the gateway host.** Proven: stopping 5 stale agents reaped their delivery-loop/daemon (91→59 procs) but **left 3 gateway hosts alive** (cms-tech-lead:9636, next-senior-dev:9095, next-tech-lead:8926, ~527MB) while 2 others (ci:9136, mp:9495) DID reap — inconsistent. So gateways accumulate: 11 gateways for 7 live agents.
- With ~90 hermes processes the box is CPU-contended, a fresh gateway can't finish booting within 60s, `ensureHost` throws "did not become ready within 60000ms", and the new agent lands **up-but-deaf** (registers + heartbeats, but session_handle empty / console 0 bytes / dispatches backstop). RAM is NOT the limit (7.6GB available) — it is process/CPU contention.
- comms-tech-lead manually reaped the 3 leaked gateways (58 procs) so YOU could boot.

**The fix (highest value in the set — this is why hermes has felt "so broken"):**
(a) managed-hermes STOP must reap the gateway host too, agent-scoped and reliably (find why it's inconsistent — `hermes-managed-host.js` stop path + the triad reap in `api_v2.py` stop-worker);
(b) an idle-timeout so an unused managed-hermes RELEASES its gateway and re-boots on the next dispatch (managed agents are supposed to rest cold at `available`, not hold a 700MB gateway forever);
(c) a boot-time sweep that reaps gateways whose owning agent is gone/stopped (mirror the marker-tombstone sweep already in the hermes block);
(d) consider whether `AIFY_HERMES_GATEWAY_READY_MS` should default higher than 60s given the boot cost. Test each with proof (spawn a fresh agent, watch it become live, stop it, watch the gateway actually die).

## How to report
- Post the PLAN first (`comms_send type=response inReplyTo=<dispatch msg id>` to `comms-tech-lead`).
- After each fix: a one-line result + the proof (test output / browser measurement / file:line).
- Keep everything in the repo/planning dir, never `/tmp`.
- If you must choose between correctness and scope, prefer a small correct fix + a Backlog note over
  a large speculative one. No MR/change without it tracing to an issue above.
