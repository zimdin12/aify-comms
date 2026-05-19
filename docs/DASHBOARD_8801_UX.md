# 8801 Dashboard — UX Vision & Recommendations

Companion to [DASHBOARD_8801_PARITY.md](DASHBOARD_8801_PARITY.md). The
parity contract is the correctness GATE; this doc is the DIRECTION. Slice
roadmap below sits on top of the parity foundations (F1–F3).

## 0. What it IS (and isn't)

The dashboard is the operator's **human-in-the-loop control plane** for a
multi-runtime agent fleet. Its job, in order:

1. **Awareness** — at a glance, what's working, what needs my attention,
   what's broken.
2. **Conversation** — send work to an agent and see the answer threaded
   in context.
3. **Inspection** — for any run/message/session, the audit trail and
   what to do about it.
4. **Operation** — spawn, stop, steer, retry, route — without dropping to
   CLI.
5. **Trust** — be the source of truth (the status whack-a-mole eroded
   this; F1/F2 in the parity contract is how it gets earned back).

It is **not**: a code editor, a metrics/observability dashboard
(Grafana's job), or a multi-user collab surface. Single operator,
operations focus.

## 1. Principles

- **Awareness first, inventory second.** Lead with what's HAPPENING and
  what NEEDS ME; large static tables (every agent, every run) are
  on-demand, not the landing.
- **One canonical truth.** Status text + dot + badge always agree (F2);
  multi-tab/PC converge without manual refresh (F1); console is a view,
  never a delivery path.
- **Threaded conversations are the primary surface.** Operator → agent
  → reply, in one threaded view — not a flat inbox.
- **Intervention without leaving the row.** From any active run: steer,
  interrupt, queue-after, retry, open console, see audit — inline drawer,
  not page navigation.
- **Truncation honesty.** "N most recent matching" everywhere a list is
  bounded; never let truncation read as "only N exist".
- **Explainability on every status.** Tap/hover a status chip → WHY (run
  id, since when, blocker reason, last-seen, environment).
- **Confirmation discipline.** Destructive actions confirm with
  consequence ("kills run X and 3 controls"); non-destructive don't.

## 2. Information architecture

**Desktop (≥1024px) — three-pane responsive shell:**

```
┌──────────────┬────────────────────────────┬────────────────┐
│  Rail        │  Center                    │  Side          │
│  agents +    │  threaded chat /           │  details /     │
│  attention + │  console / run inspector   │  audit /       │
│  activity    │  (one at a time)           │  controls      │
└──────────────┴────────────────────────────┴────────────────┘
```

- Rail: pinned "Needs Attention" group (overdue, blocked, failed,
  working-no-progress) on top; then agents grouped by role; then activity
  feed below.
- Center: the workspace — chat with one agent / their console / a run
  inspector / spawn form. Tabs within, not page-level navigation.
- Side: contextual details for whatever is selected in Center; collapses
  to overlay on smaller widths.

**Mobile (≤414px) — single-pane + bottom tab bar:**

```
┌─────────────────────────────┐
│  Header (status, search)    │
├─────────────────────────────┤
│                             │
│  Active pane (full width)   │
│                             │
├─────────────────────────────┤
│ [Comms] [Activity]          │
│ [Inspector] [Settings]      │
└─────────────────────────────┘
```

- Bottom tab bar = top-level navigation. Swipe between agent chats.
- Run inspector opens as a fullscreen sheet from any row.
- Side pane (details) becomes a slide-up drawer, not a column.
- Pull-to-refresh on lists; long-press for bulk-select.

**Tablet (414–1024px):** two-pane (Rail + Center), Side collapses to
drawer; thresholds chosen to match real device widths.

## 3. Per-flow UX recommendations

- **Awareness landing** — "Needs Attention" pinned section is the first
  thing visible: overdue contracts, blocked agents, failed runs,
  working-no-progress. One-tap to inspector for each.
- **Chat (threaded conversations)** — primary surface. Each thread shows
  operator message → agent activity (status pulses) → reply, with
  inline run inspector drawer. Compose anchored at the bottom; image
  paste already supported (preserve).
- **Run inspector (universal drawer)** — opens from any row (chat, runs
  list, attention). Shows: timeline of events, current status with
  explainer, controls (steer / interrupt / queue / retry / close / open
  console), conversation context, related messages. Drawer on desktop,
  fullscreen sheet on mobile.
- **Console** — read-only watch by default; explicit "send input" toggle.
  Decoupled from delivery (parity contract). Mobile: readable, scrollable,
  monospaced, expand/collapse long output.
- **Sessions** — list with bulk-select (multi-stop, multi-delete). Per
  row: status, last activity, owner mode, jump-to-chat.
- **Spawn** — form with dynamic runtime/env dropdowns (already shipping),
  fresh-context warning, template picker. Mobile: stepper, not all fields
  visible at once.
- **Settings** — grouped, searchable, per-runtime collapsibles. Mobile:
  drawer/sheet, never full-page navigation.
- **Work Loop / reply contracts** — list of open contracts with state
  chips; close/remind use existing audited endpoints. Bulk-cancel for
  superseded contracts (operator-requested — addresses the
  "answered-elsewhere can't cleanly close" gap I flagged).
- **Activity feed** — human-readable stream of dispatch events / status
  transitions / completions / failures across the fleet. Filterable.
- **Search / Cmd-K palette** — across agents / messages / runs /
  artifacts. Highest-value power-user feature; mobile keeps a simple
  search bar in the header.

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

After F1/F2/F3 and the current parity slice land, this is the suggested
order — each slice gated by the parity contract:

1. **Threaded chat surface** (replace flat inbox emphasis). Highest
   day-to-day value.
2. **Universal run inspector drawer** (works on mobile via sheet).
3. **Status "why" explainer** on every chip (tiny, high-trust impact).
4. **Activity feed** (the audit stream).
5. **Cmd-K / search palette** (power-user multiplier).
6. **Bulk actions toolbar** on multi-select (already a parity item;
   ship its real surface here).
7. **Mobile responsive shell** — bottom tab bar, sheets, drawers.
8. **Keyboard shortcuts + density toggle**.
9. **Settings reorg as a sheet, not a page**.

Each slice: behavioral assertion attached, mobile viewport in the
assertion, old dashboard authoritative until the slice's flow flag is
green.
