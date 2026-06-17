# 8801 Dashboard — Parity Contract

> **STILL THE ACTIVE GATE (2026-06-10)** — refreshes pending per
> [DASHBOARD_REBUILD_PLAN.md](DASHBOARD_REBUILD_PLAN.md) §6: status flow = the 8-label contract
> incl. blocked/stale + display nuances; Sessions flow = the agent-centric model; new gated
> flows: chat-first landing + persisted UI state, per-DM analytics, version badge,
> delivery-truthfulness send toasts. F1/F2/F3 unchanged.

Status: **ACTIVE GATE.** Co-owned: comms-tech-lead (service/dashboard) +
comms-senior-dev (driving the slices). Derived from
[DASHBOARD_ARCHITECTURE_PLAN.md](DASHBOARD_ARCHITECTURE_PLAN.md) and the
2026-05 codebase analysis. The old dashboard remains **authoritative and
default** until a flow passes its assertions here.

## 0. The rule

- **Thin-but-correct is safe. Thick-but-divergent is forbidden.** 8801 may
  ship with fewer features; it may NOT ship a feature that contradicts the
  old dashboard or server truth.
- **API / MCP / bridge protocol are FROZEN.** 8801 consumes the same
  endpoints. Zero backend changes to make 8801 work — if a flow "needs" a
  backend change, that is a separate reviewed service change, not 8801 scope.
- **Per-flow enablement.** Each flow below is behind a flag. A flow is
  enabled in 8801 only when its behavioral assertions pass; until then 8801
  shows the old dashboard's behavior or an explicit "use classic dashboard
  for X" affordance — never a half-working version.
- The old dashboard is not retired until **every** flow passes + an explicit
  operator parity sign-off + a documented rollback path.

## 1. Foundational invariants (MUST exist before any flow parity)

These are the root-causes the analysis flagged (H11/H12). No flow may be
enabled until these three hold for 8801:

- **F1 — Single shared client state from websocket.** One client state
  object, populated/reconciled from the WS stream, is the sole source of
  truth. No per-tab divergence; no write-once caches that are never
  invalidated (the classic `_terminalDetails` bug). Multi-tab / multi-PC:
  two 8801 tabs MUST converge without manual refresh.
- **F2 — Canonical status resolver.** Exactly one function maps a server
  status → `{ label, dotKind, badges }` against a whitelisted `statusKind`
  set. Every consumer (text, dot, badge, input-enable, console state) uses
  it. No element may derive status independently. An unknown server status
  degrades to one defined `unknown` kind, identically everywhere.
- **F3 — Authoritative entity keying.** Terminal/pulse/session state keys on
  authoritative `(agentId, terminalId)` from the WS frame; frames whose
  `agentId` ≠ the current agent for that terminal are dropped, not rendered.

## 2. Per-flow parity checklist + behavioral assertions

Each flow: enabled only when ALL its assertions pass (Playwright/smoke per
the plan). Assertions are behavioral (observable user truth), not
implementation.

- **Status display** — label, dot, and badges always agree (F2); a working
  agent shows working, an idle one idle, within one refresh; no stale
  "working" after completion; no dot/text disagreement on any status incl.
  unknown/compound.
- **Console** — decoupled: opening/closing a console NEVER sends or drops a
  message; messaging works with console closed; a stopped console never
  shows cached output next to a "Start" button (F1); no raw control-byte
  artifacts; authoritative ownership (F3).
- **Chat send** — never wedges silently; in-flight guard with real
  abort+timeout; failure surfaces a toast; image paste → shared artifact +
  link; reply threads back into chat.
- **Sessions** — list/filter reflect server truth; delete + **bulk actions**
  (multi-select) are first-class; a deleted/stopped session updates all tabs.
- **Spawn** — form parity incl. dynamic runtime/env dropdowns, fresh-context
  warning, accurate regenerate; rejects invalid runtime/env with the real
  reason.
- **Settings** — read/write parity; a change reflects without manual reload.
- **Work Loop / reply contracts** — contract list + states match
  `comms_contracts`; reminder/blocked/skip semantics surfaced correctly;
  no human/`dashboard`-targeted contract shown as agent reply-debt.
- **Runs** — **server-filtered** (not client-filtered on a recent slice),
  bounded + ordered, backed by `idx_dispatch_runs_status_requested`; UI
  states "N most recent matching" so truncation ≠ "only N exist".
- **Agents/roster** — status via F2; presence/last-seen match server.

## 3. Mobile behavior (operator requirement)

- Responsive layout: no fixed-width/desktop-only assumptions; usable at
  ≤414px width.
- Touch targets ≥44px; console output readable and scrollable on mobile;
  no horizontal-scroll traps.
- Mobile-safe preview: long bodies/terminal output truncate with expand,
  not layout-breaking.
- Every enabled flow's assertions are also run at a mobile viewport — mobile
  is part of the parity bar, not a follow-up.

## 4. Replacement gate (old → 8801 default)

All of: (a) F1–F3 hold; (b) every §2 flow passes its assertions at desktop
**and** mobile viewport; (c) parity assertions live in CI/smoke and are
green; (d) explicit operator sign-off; (e) documented one-flag rollback to
the old dashboard. Missing any → 8801 stays opt-in, old stays default.

## 5. Out of scope / non-goals

- No backend/API/MCP changes to enable 8801.
- No big-bang cutover; no flow enabled without its assertion.
- No new features on 8801 before its underlying flow reaches parity (build
  new capability on the parity base, per the architecture plan).

## 6. Shipped on the parity base (2026-06-17 round)

Built on top of the parity foundation, not replacing it:

- **Chat overview (landing).** Re-clicking the open conversation in the rail
  closes it back to a "Chat overview": direct/channel/unread counts, 24h
  message volume, a fleet status breakdown, and a most-active-peers list
  (click to reopen). Per-agent analytics stays on its explicit action button.
- **Inline terminal in chat.** A `Messenger | Console` toggle in each DM
  header opens that agent's live terminal inline — the *same* console widget
  the Sessions page uses (`renderSessionConsole(session, targetEl)`): PTY
  xterm / hermes iframe / codex synth / start-console offer. The Sessions
  terminal is unchanged. Console upgrades borrowed from the hermes dashboard:
  OSC-52 clipboard, Ctrl+Shift+C/V copy-paste on the http LAN origin,
  wheel→cursor-key while a TUI owns the alt-screen, and resize-dedupe.
- **Real fleet analytics.** `GET /analytics` gained (additive, julianday-safe,
  windowed): dispatch success rate, open + overdue (>30min) reply contracts,
  fleet median reply, 14-day stacked completed/failed outcomes, an agent
  leaderboard with per-agent success rate, busiest channels, and
  failure-reason buckets. No token/cost metrics (not in the schema). The
  per-agent panel also surfaces `runs7d.open` and an all-time hour-of-day
  histogram.
- **Mobile (Android).** Verified on a 393px viewport: no horizontal overflow
  on Chat/Analytics, bottom tabbar, 2-up metric cards, 44px tap targets,
  `theme-color` + web-app-capable meta, `viewport-fit=cover` safe areas.
