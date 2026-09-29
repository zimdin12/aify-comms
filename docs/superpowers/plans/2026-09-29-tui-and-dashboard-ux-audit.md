# TUI and dashboard UX audit (2026-09-29)

Asked for by the operator: "currently they have bad UX and TUX. starting agent maybe should open something
that has a search ... in dashboard some of the elements and things are badly built and have bad UI + UX.
some stuff even have issues." A proposal list, not a plan of record: nothing here is built until the
operator picks from it.

**How it was gathered.** Two read-only code audits: the aify-env terminal UI, with frames rendered
from the pure modules `lib/tui.mjs` and `lib/keys.mjs`, and the dashboard (`service/new_dashboard/`),
probed in Node with a stubbed `document`. The live dashboard was not opened, because it needs the API
key. VERIFIED means the auditor read the code or ran the probe; INFERRED means neither. **Checked a
second time, at the source, before this was written:** D1-D4, T1 and T2, with D3 narrowed after review
and re-probed. Every other VERIFIED item is the auditor's reading alone, one instrument, and should be
re-read before it is built on. Line numbers are as of aify-env `8ce4414` and aify-comms `d7468486`.

## Bugs that ship today

| # | Where | What | Size |
|---|---|---|---|
| D1 | `console-actions.mjs:176` | **FIXED in 93a59bc2 (0.7.6).** The run inspector's "Open console" threw: it calls `renderSessionWorkspace()`, which lives in module-scoped `app.js` and is not injected (`REQUIRED` at `:35`). The drawer stays open and the "Unexpected error" toast fires. VERIFIED. | S |
| D2 | `session-rail.mjs:47-48` | The Sessions status filter matches `session.status` (`running`) against agent-status chips, so the Live preset shows no live session. Because the filter persists, the rail then says "No sessions yet". VERIFIED (read, plus the auditor's Node probe). | S |
| D3 | `work-loop-panels.mjs:76`, `summary-tiles.mjs:45-46`, `work-loop-actions.mjs:54` | Choosing a non-open value in the Work page's **State** dropdown replaces `state.contracts` with that state's contracts. "Needs Attention" and the Chat page's "Overdue work" and "Queued contracts" tiles count `state.contracts`, so they then describe the selected state instead of the open set. The Work page's own summary tiles read `state.contractsBase` and do not move. The **category** dropdown only re-renders the list and causes none of this. VERIFIED with an offline probe (stubbed document, real modules), holding one overdue and one queued open contract in `contractsBase`: State=open gives attention 2, overdue 1, queued 1; State=answered gives attention "clear", overdue 0, queued 0, while the Work tiles stay at Overdue 1, Open work 2; the category-change control is unchanged from open. A state that itself holds queued contracts keeps some counts, so the effect is drift, not always zero. | S |
| D4 | `chat.js:490-515` | Send has no in-flight guard and is not disabled, so a double press sends twice (the send can take up to 20 s). Type, priority and "Expects reply" stay set after sending. VERIFIED. | S |
| D5 | `work-loop-actions.mjs:183-205` | Bulk Remind/Close stops at the first failure: earlier items are done, the rest untried, and only a generic toast appears. VERIFIED (read). | S |
| D6 | `settings-fields.mjs:17-21` | A comment says setting keys reach `id="…"` unescaped because the schema is hard-coded, but the schema now comes from `GET /settings/schema`. Low risk, since the source is trusted; the stated reason is false. VERIFIED. | S |
| T1 | `lib/tui.mjs:751` | The picker's hint offers "q quit", but `q` types into the query there. `qMeansQuit` is a hand list, and the guard test only checks that `q` does *something*. VERIFIED. | S |
| T2 | `lib/console-session.mjs:492,540` | `attach-refused` (below 80 columns) is produced and never read: the key does nothing, silently, and the hint still offers `p`. VERIFIED. | S |

## Foundations: what makes the UX work possible without growing the big files

These come first because the operator-visible items below would otherwise land in files at the size
limit. `lib/tui.mjs` is 974 lines, 26 under the gate.

**Terminal UI (aify-env)**
1. **Split `tui.mjs` by responsibility** (M, mostly mechanical). `drawDashboard` is about 497 lines
   holding 10 sections, 3 overlays and the hint chain. Split it into `sections.mjs`,
   `process-table.mjs`, `overlays.mjs`, `hints.mjs` and `fit.mjs`, each a pure
   `(snapshot, ctx) -> lines`.
2. **One owner per mode** (M). A mode's routing lives in `keys.mjs`, its drawing and hint in
   `tui.mjs`, its data in `console-session.mjs` and its side effects in `dashboard.mjs`, with three
   hand lists beside `MODES` (`KEY_AT_A_TIME_MODES`, `RESELECTING_ACTIONS`, `qMeansQuit`). The fix
   is one keymap declaration per mode (key, label, action, drop priority), read by the router, the
   hint line and `--help`. That removes T1's class of defect.
3. **Nested overlay state** (M). Focus becomes `{mode, selected, count, paneHidden, overlay:{kind,
   cursor, query}}`. `reconcileFocus` currently rebuilds a flat literal twice, and its own comments
   record three fields lost that way.
4. **Move the start list out of `ConsoleSession`** into a `StartList` class (S). It is where the
   search query will live.
5. **UI colours apart from status colours** (S). Yellow, the `working` hue, is also used for every
   warning and notice. Green, the `online` hue, is also used for ok, `pty` and idle. A small
   `ui-palette.mjs` keeps the status hues meaning only their status.

**Dashboard (aify-comms)**
6. **One owner for status presentation** (M).
   - The problem: the dots match the aify-env palette, but the chips, tiles and charts drift. The
     `shell` chip is green, `available` is grey, `working` tiles have a blue border, and the
     Analytics run colours disagree with the Runs list. Sessions shows an idle live agent as a
     pulsing yellow "running". The Help legend lists six states where the code has nine. The Chat
     status chips are hand markup that leaves out `starting` and `misconfigured`, so a freshly
     spawned agent vanishes under any chip filter. VERIFIED.
   - The fix: status colour tokens on `:root`, chip tone derived from the dot, and chips and legend
     generated from `AGENT_STATUSES`, as `session-rail.mjs:91` already does. The colour test then
     covers every `STATUS_KINDS` entry, not just `shell`.
7. **One drawer owner** (S-M). Seven openers each add the `open` class and write
   `#inspector-content` themselves, and only the run inspector moves focus in and restores it. A
   closed drawer stays in the Tab order. A `drawer.mjs` with `openDrawer({kind, title, html})` owns
   focus, the title and `inert`.
8. **A pure `agentActions(agent, session)`** (M). It returns `[{id, label, tone, confirm}]`, and the
   drawer, the Sessions header and the identity directory all use it.
   - The drawer offers 13 buttons with overlapping verbs (Restart, Reset, Stop worker, Stop session,
     Delete session, Remove agent) and no Start, although a comment says the agent "can be started
     again from the same drawer".
   - "Switch to managed/resident" does not confirm.
   - The fix: one primary action per state and one stop vocabulary.
9. **Apply each data slice in one place** and delete the dead `flowGates` (M). `refresh-cycle.mjs`
   and `slice-loaders.mjs` both apply every slice, which gives one fact two writers. `flowGates` is
   computed and never read, but is injected into five modules.

## What the operator sees

**Terminal UI**
10. **The agents get the space** (M). VERIFIED from a rendered frame: at 100×24 with 12 agents the
    table shows **one** row. SERVICES, HEALTH, RECENT EXITS and TRAFFIC always render in full, and
    only notices, the table and the start list shrink. The fix is that each section has a compact
    form, and the fitting pass collapses the others to a line each before it touches the agent table.
11. **Searchable start picker** (M). This is the operator's example. Today `s` draws a list under the
    table: arrows and j/k only, no filter, no Esc. At 100×24 it shows 1 of 18 agents, and the frame
    runs 29 lines tall.
    - Most of what search needs already exists in the process finder: query editing, paste, "N of M"
      and keeping the selection across list changes.
    - Lift that into a pure `list-query.mjs` (`applyQueryKey`, `rankRows`: prefix, then word start,
      then substring, then subsequence), shared by both lists.
    - The start overlay replaces the table while open, shows a detail line (role, and why an agent
      cannot start), and keeps "starting X…" on screen until the worker appears, then selects it.
12. **An outcome line that is never trimmed** (S). Start and stop results go into NOTICES, which is
    the first section dropped. VERIFIED: gone at 24 and at 40 rows. The newest result should sit
    above the hint, with T2 said there ("widen to 80 columns to attach").
13. **Esc closes overlays, `?` opens help, `/` finds** (S each after item 2). Esc reuses the
    bracketed-paste flush timer, so an arrow key's bytes are never mistaken for an Esc.
14. **Columns drop by priority** (S). `SERVICE` reads "aify-comms" on every row and, with `ID` and
    `PID`, squeezes TITLE to about 30 characters at 100 columns.
15. **Friendlier `attach`** (S/M). Accept a unique prefix (`attach sc`). With no name, open the
    picker instead of printing a list.

**Dashboard**
16. **One "+ Agent" start flow** (L overall; the pure planner and the dialog are M, the progress card
    S).
    - Today there are five routes: a spawn form on Environments, plus four ways to start an
      existing agent, each calling a different endpoint.
    - After a spawn the only feedback is a toast. The form offers no model or effort, and it does not
      warn about an ID that already exists.
    - Proposed: a top-bar button and Ctrl+K open one dialog. Its search box filters existing agents
      and offers the single right action (Start, Restart, Open console).
    - Typing an unknown ID offers Create: environment cards with their reasons, runtime chosen from
      what the environment advertises, a workspace combobox of roots and recent workspaces, role
      suggestions, an Advanced section for model and effort (pre-filled from Settings), a
      multi-line initial task, and "start like…".
    - After submit the dialog becomes a live progress card (queued, claimed, running or failed),
      ending in Open console or Message.
    - Built as a pure `start-agent-plan.mjs` (`plan(state, form) -> {action, payload, problems}`)
      plus a DOM-only dialog.
17. **Filters say what they hide** (S). The Chat rail shows "N hidden by filters · Clear", as the
    Sessions rail already does. "Viewing as" moves out from under the Channels tab.
18. **Honest empty and failure states** (S). The Sessions empty state tells "filtered out" apart from
    "none". Bulk actions report "3 of 5 closed, 2 failed" (D5). The spawn table gets
    cancel/retry/open.
19. **Mobile** (M). A list-then-detail Chat, and the connection chip kept visible at 760 px or
    narrower (it is hidden today, VERIFIED at the CSS rule).
20. **Small items** (S each).
    - Message links open in a new tab.
    - Drop the read/stored badges that carry no information.
    - Remove the pi and OpenCode install links from Help.
    - Message rows get their own status vocabulary instead of borrowing run statuses.

## Larger, only if wanted
21. **Reorganise `styles.css`** (L). It is 1851 lines, ordered by workstream, with a late override
    layer "appended so same-selector rules win by source order". The fix is tokens, then base, then
    one section per component. It needs the measured ceiling moved, which is a reviewer's decision.
22. **Several consoles side by side** (L). `state.activeXterm` is a singleton, so only one console
    can exist at a time.

## Decisions for the operator
- Which items, in what order. Recommended: the bugs, then foundations 1-4 and 6-7, then 10-12 and 16.
- Whether `j`/`k` become text in the start picker, as they already are in the process finder. Search
  needs them.
- Whether dated incident history moves out of code comments. It is 36-57% of lines in several large
  modules (about half of `tui.mjs`), and moving it to DECISIONS or `docs/history/` would free room
  under the 1000-line gate without splitting anything.
- Whether several consoles side by side (22) and mobile (19) matter.
