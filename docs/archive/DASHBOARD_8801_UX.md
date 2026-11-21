# 8801 Dashboard — UX Vision & Recommendations

> **HISTORICAL UX BLUEPRINT.** The replacement dashboard shipped. Use
> `service/new_dashboard/` for current behavior and `AGENTS.md` for the canonical six-state
> status contract (`working`, `online`, `available`, `blocked`, `offline`, `stopped`).

Companion to [DASHBOARD_8801_PARITY.md](DASHBOARD_8801_PARITY.md). The
parity contract is the correctness GATE; this doc is the DIRECTION. Slice
roadmap below sits on top of the parity foundations (F1–F3).

## 0. What it IS (and isn't)

The dashboard is the operator's **human-in-the-loop control plane** for a
service that **unifies multiple agent harnesses behind one interface and
lets those agents talk to each other**. Operator framing (verbatim):
*"this service allows us to use different harnesses via same interface +
we provide methods for them to communicate with each other."*

**Mental model — the hierarchy is environments → sessions → chat+console:**

- **Environments** — hosts/machines that can run agent sessions.
- **Sessions** (per environment) — a registered agent identity, on one
  runtime (Claude / Codex / Pi / Hermes / OpenCode), in one execution
  mode (managed / resident). The session IS the agent from the
  operator's perspective.
- **Each session has two views: chat + console.** Direct messages ARE
  the chat for that session. Console is the optional terminal view.

**Diagnostics tier (not primary surface):** Work loops and runs are
*mostly diagnostics* — when something needs investigating, when a
contract is stuck, when a run failed and you need the timeline. They
belong in an inspector/drawer, not the front door.

**Five jobs in priority order:**

1. **Awareness** — what's working, what needs me, what's broken.
2. **Conversation** — send work to a session and see the answer in
   thread; switch between sessions fast.
3. **Operation + editing** — spawn, stop, steer, retry, edit session
   config, bulk-act across sessions — without dropping to CLI.
4. **Diagnostics** — runs/work-loop/audit when a chat doesn't tell the
   whole story.
5. **Trust** — be the source of truth (F1/F2 in the parity contract is
   how it's earned back).

It is **not**: a code editor, a metrics/observability dashboard
(Grafana's job), or a multi-user collab surface. Single operator,
operations focus.

## 1. Principles

- **Awareness first, inventory second.** Lead with what's HAPPENING and
  what NEEDS ME; large static tables are on-demand, not the landing.
- **Sessions are the workspace.** The primary unit of the UI is the
  session (chat + console as two views of one thing). Everything else
  orbits the session.
- **Multi-harness parity is structural.** Claude / Codex / Pi / Hermes /
  OpenCode are peers in the same layout — runtime is a small badge, not
  a separate page or palette. If a runtime-specific affordance is needed
  it appears inline in the session view, not in a parallel UI tree.
- **One canonical truth.** Status text + dot + badge always agree (F2);
  multi-tab/PC converge without manual refresh (F1); console is a view,
  never a delivery path.
- **Threaded conversations are the primary surface.** A direct message
  to a session IS the chat for that session; reply threads inline.
- **Diagnostics are a drawer, not a page.** Runs, work-loop, audit
  belong in an inspector that opens FROM a session/message, with a
  global diagnostics view as an explicit destination — not as the
  default landing.
- **Bulk + edit are baseline, not a roadmap item.** Multi-select with
  bulk actions and inline editing apply everywhere they make sense
  (sessions, messages, runs, contracts, environments); a slice that
  ships a list without considering bulk-select and edit is incomplete.
- **Intervention without leaving the row.** From any active session/run:
  steer, interrupt, queue-after, retry, open console, see audit — inline.
- **Truncation honesty.** "N most recent matching" everywhere a list is
  bounded; never let truncation read as "only N exist".
- **Explainability on every status.** Tap/hover a status chip → WHY (run
  id, since when, blocker reason, last-seen, environment).
- **Confirmation discipline.** Destructive actions confirm with
  consequence ("kills run X and 3 controls"); non-destructive don't.

## 2. Information architecture

The IA is **environments → sessions → session view (chat + console)**.
Diagnostics (runs, work-loop, audit) is a drawer that opens from a
session/message context, with a separate global "Diagnostics" destination
for cross-session investigation.

**Top-level destinations** (the only first-class navigation entries):

- **Sessions** — the workspace; default landing.
- **Environments** — where sessions live; spawn-from-here flows; bulk
  environment edits.
- **Diagnostics** — runs, work-loop, audit (cross-session view); also
  reachable inline from any session/message.
- **Settings** — service config; runtime defaults; reachable as a sheet.

A persistent **Needs-Attention** strip (overdue / blocked / failed /
working-no-progress) appears above every destination so awareness isn't
gated on which view is open.

**Desktop (≥1024px) — three-pane responsive shell:**

```
┌──────────────┬──────────────────────────────┬───────────────┐
│  Rail        │  Center: SESSION VIEW        │  Side         │
│  envs +      │  ┌─ Chat ─┬─ Console ─┐      │  details /    │
│  sessions +  │  │ thread │ pty view  │      │  diagnostics  │
│  attention   │  └────────┴───────────┘      │  drawer /     │
│  + activity  │  composer (bottom)           │  bulk-edit    │
└──────────────┴──────────────────────────────┴───────────────┘
```

- **Rail:** Environments grouped; under each, its sessions (collapsible)
  with status chip + runtime badge. Pinned Needs-Attention on top; a
  thin Activity feed underneath. Multi-select with checkbox toggling
  enables a bulk-action toolbar that floats over the rail.
- **Center:** the SESSION VIEW. Two tabs: **Chat** (threaded
  conversation — the primary surface) and **Console** (PTY watch, opt-in
  input). Composer anchored at bottom of Chat. Runtime is a tiny badge
  in the header; the layout is identical regardless of harness.
- **Side:** contextual — message details, run inspector drawer for the
  currently inspected message/run, bulk-edit panel when multi-select is
  active, audit timeline for the session.

**Mobile (≤414px) — single-pane + bottom tab bar:**

```
┌─────────────────────────────┐
│  Header (env→session, ⚠ N)  │
├─────────────────────────────┤
│                             │
│  Active pane (full width)   │
│  Sessions / Session view /  │
│  Diagnostics / Settings     │
│                             │
├─────────────────────────────┤
│ [Sessions][Diagnostics]     │
│ [Activity][Settings]        │
└─────────────────────────────┘
```

- Bottom tab bar = top-level navigation matches the four destinations.
- Session view occupies the screen; Chat ↔ Console swap via a segmented
  control inside the session header. Swipe horizontally between sessions
  within the same env.
- Diagnostics opens as a fullscreen sheet from any row; bulk-select via
  long-press; bulk-actions appear in a contextual bar at the bottom
  (replacing the tab bar while selection is active).
- Side pane (details) becomes a slide-up drawer.
- Pull-to-refresh; offline indicator in the header.

**Tablet (414–1024px):** two-pane (Rail + Center), Side collapses to a
slide-over drawer; thresholds chosen for real device widths.

## 3. Per-flow UX recommendations

**Primary (session-centric)**

- **Sessions rail** — grouped by environment; collapsible env headers
  with session counts; per row: status chip + runtime badge + last
  activity. Multi-select via checkbox/long-press → bulk toolbar
  (multi-stop / multi-restart / multi-delete / bulk-edit). Drag/drop
  ordering optional; keyboard j/k to move.
- **Session view — Chat tab (primary surface)** — threaded view of the
  conversation with this session: operator message → agent activity
  (status pulses, run id) → reply, with an inline run-inspector affordance
  on each turn. Composer at bottom (type/priority/queue-if-busy, image
  paste preserved). Multi-select messages → bulk re-route, copy, delete.
- **Session view — Console tab** — opt-in input toggle; decoupled from
  delivery (parity contract). Readable/scrollable on mobile. Audit
  affordance to show input contract events that landed in this terminal.
- **Awareness strip** — pinned above all destinations: counts +
  one-click filter for overdue / blocked / failed / working-no-progress.
- **Spawn (from environment)** — within an Environments destination,
  inline "new session here" form: dynamic runtime/env dropdowns
  (existing), fresh-context warning (existing), template picker. Mobile:
  stepper. Bulk-spawn across environments where appropriate.
- **Environments** — list with status, bridge identity, runtimes
  available, cwd roots. Editable inline (cwd roots, label, defaults).
  Multi-select → bulk re-register, mute, restart-aware actions.

**Diagnostics (drawer + global view)**

- **Run inspector (universal drawer)** — opens from any row (message in
  chat, runs list, attention strip). Shows: bounded timeline of events,
  current status with the canonical "why", controls (steer / interrupt /
  queue / retry / close), conversation context, links to the source
  message and the resulting reply. Drawer on desktop, fullscreen sheet
  on mobile.
- **Diagnostics destination** — cross-session list of runs and
  work-loop contracts with state chips + filters. Server-filtered (parity
  must-have). Multi-select → bulk-close / bulk-remind / bulk-cancel
  (addresses the "answered-elsewhere can't cleanly close" gap I flagged).
- **Activity feed** — human-readable stream of dispatch events / status
  transitions / completions / failures across sessions. Filterable by
  env / runtime / session / kind.

**Cross-cutting**

- **Settings** — grouped, searchable, per-runtime collapsibles. Inline
  edit; Mobile: drawer/sheet, never full-page. Bulk apply where it makes
  sense (e.g., set default model across a runtime's sessions).
- **Search / Cmd-K palette** — across environments / sessions /
  messages / runs / artifacts. Mobile keeps a header search bar.

## 4. Mobile primary subset (what mobile is FOR)

Mobile is for monitor + read + minimal-intervene, not heavy admin.
Explicitly first-class on mobile:

- See "Needs Attention" + agent statuses.
- Read a thread / write a reply.
- Open the run inspector and do close / steer / interrupt / retry.
- See an agent's console (read-only).
- Receive WS-driven updates without manual refresh.

Explicitly NOT mobile-first (works, but optimized for desktop):

- Bulk multi-select admin operations beyond a small selection.
- Full settings editing.
- Side-by-side run comparison.
- Spawn-form expert mode.

Responsive rules (carry forward from parity contract): ≤414px, ≥44px
touch targets, single-column grids for actions, no horizontal-scroll
traps, mobile-safe preview (long bodies expand, never break layout).

## 5. Cross-cutting UX

- **Status chips** — color + shape/icon (colorblind-safe baseline);
  every chip has a "why" affordance (hover desktop, tap mobile) that
  shows the canonical reason from F2.
- **Time** — always relative ("3m ago"); tooltip/long-press reveals ISO.
- **Keyboard shortcuts** — j/k between agents, `/` search/palette, `c`
  compose, `r` reply, `[` `]` toggle drawer, `?` shortcut overlay.
- **Density toggle** — comfortable vs compact rows; persists per device.
- **Empty states** — guide the next action ("no active runs — send
  something" with one-tap composer), never just blank.
- **Toasts** — for transient feedback (sent, queued, failed); confirm
  banners for destructive consequences.
- **Connection indicator** — single derived state (WS readyState + last
  successful poll); never two contradicting indicators.

## 6. Non-goals

- No code editing / no replacing an agent's own session.
- No analytics/observability stack (Grafana territory).
- No multi-user collaboration.
- No big-bang rewrite; everything ships behind the parity gate per
  [DASHBOARD_8801_PARITY.md](DASHBOARD_8801_PARITY.md).

## 7. Slice roadmap (recommended sequencing, post-foundations)

After F1/F2/F3 and the current polish slice, this is the suggested
order. Each slice gated by the parity contract; bulk-select + inline
edit are BASELINE in every list slice (not a separate slice). Old 8800
remains authoritative until the replacement gate is met.

1. **Session-centric IA** — env→session rail with collapsible env
   groups; runtime badge; multi-select toggling a bulk-action toolbar.
   This re-roots the navigation around the operator's mental model.
2. **Session view (Chat + Console tabs)** — threaded chat as the primary
   surface, composer with type/priority/queue-if-busy, image paste; the
   Console tab is the existing terminal view inside the session shell.
3. **Universal run-inspector drawer** — opens from any chat turn or
   row; works as a fullscreen sheet on mobile; bounded event timeline +
   controls (steer/interrupt/queue/retry/close).
4. **Status "why" explainer** on every chip — tiny, very high trust win.
5. **Diagnostics destination** (cross-session runs + work-loop) — with
   server-side filtering, bulk-close / bulk-remind / bulk-cancel.
6. **Environments destination + inline spawn** — env list with inline
   edit, "new session here" stepper, bulk re-register.
7. **Activity feed** — filterable cross-session audit stream.
8. **Cmd-K / search palette** — across all entities.
9. **Mobile responsive shell** — bottom tab bar (4 destinations),
   sheets, drawers, swipe-between-sessions, long-press bulk-select.
10. **Keyboard shortcuts + density toggle**.
11. **Settings as a sheet** (not a page), bulk-apply where applicable.

Each slice: behavioral assertion attached (desktop AND mobile viewport),
old dashboard authoritative until the slice's flow flag is green, F1–F3
foundational invariants enforced.
