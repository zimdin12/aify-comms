# Dashboard v2 — Re-architecture Design

**Status:** DRAFT (brainstorming → for review by operator + comms-senior-dev)
**Date:** 2026-05-31
**Authors:** comms-tech-lead (+ comms-senior-dev, co-design)

## Goal

Replace the single-file `service/dashboard.html` (8,141 lines, one inline `<script>`/`<style>`, vanilla, no build) with a real framework-based SPA — **same capabilities and look, leveled up** — and retire the WIP `service/new_dashboard/`. The backend `/api/v1` REST + `/ws` WebSocket contract is **fixed and reused unchanged**; this is a frontend-only re-architecture.

## Why (problems with today's dashboard)

- One 463 KB HTML monolith → impossible to reason about in pieces, no module boundaries, no type safety.
- State is ~40 `window.*` globals + `localStorage`; re-render is full `innerHTML =` regeneration of whole panels.
- Real-time is "any WS event → debounced full `refreshDashboard()`" — refetches everything on every event.
- No build, no component reuse, no component/unit tests for the UI (only `new_dashboard/app.test.mjs` covers one helper).

## Decision — stack

- **React 18 + TypeScript + Vite** (static SPA; no SSR — internal real-time tool, no SEO need).
- **Server state:** TanStack Query (React Query) — caching, background refetch, and **targeted invalidation driven by WS events** (replaces the full-refresh model).
- **UI/local state:** Zustand (small, modern) for cross-cutting UI state (selected conversation, filters, theme); `localStorage` persistence for drafts/view-state (port existing keys).
- **Routing:** React Router — one route per feature area.
- **Terminals:** `@xterm/xterm` + fit/webgl addons wrapped in a `<Console>` component; `terminal_output` WS frames written directly to the keyed xterm instance (no query refetch).
- **Styling:** port the existing design tokens (theme CSS variables) to a global stylesheet + **CSS Modules** per component — preserves the current look exactly while giving scoped, maintainable styles. (Tailwind considered; rejected to avoid re-deriving the bespoke look.)
- **Tests:** Vitest + React Testing Library for components/hooks; a couple of Playwright smokes for the load-bearing flows (chat send, console attach). Replaces the lone `node:test` helper test.
- **Build/serve:** Vite builds to static assets; FastAPI serves the built `dist/` (replaces `new_dashboard_app.py`). Dev: Vite dev server with a proxy for `/api/v1` + `/ws` → `:8800`.

## Architecture

```
service/web/                      # new React app (source)
  src/
    main.tsx, App.tsx, routes.tsx
    lib/
      api.ts                      # typed fetch wrapper over /api/v1 (one place for base-url/api-key)
      queries.ts                  # TanStack Query hooks per resource (agents, sessions, runs, messages, ...)
      ws.ts                       # single WS connection → event bus → query invalidation + terminal routing
      types.ts                    # TS types for the API contract (agents, runs, sessions, events)
    store/                        # zustand slices (ui, chat, filters, theme)
    components/                   # shared: StatusDot, Console(xterm), MessageList(virtualized), RunInspector, ...
    features/                     # one folder per area, each owns its route + components + queries usage
      control-room/  work-loop/  chat/  environments/  sessions/  runs/  analytics/  files/  settings/  help/
    styles/                       # global tokens + theme variables (ported from dashboard.html)
  index.html, vite.config.ts, package.json, tsconfig.json
  dist/                           # build output served by FastAPI (gitignored)
```

**Data flow:** components read via TanStack Query hooks (`useAgents()`, `useRuns()`, …) → `api.ts` → `/api/v1`. A single `ws.ts` client receives `{event, data}` frames and (a) writes `terminal_output`/`terminal_started` straight to the matching `<Console>`, (b) for `message_sent` / `dispatch_*` / `agent_status` / `settings_updated` / `session_*` invalidates ONLY the affected query keys (granular, not a global refresh). This fixes the refetch-everything-on-every-event cost.

**Isolation:** each `features/<area>` is self-contained (its route, components, and the query hooks it uses) and can be built + tested independently. Shared primitives live in `components/` and `lib/`.

## Feature parity (the 10 areas from dashboard.html)

control-room (landing/insights), work-loop (contracts/reminders), chat (DMs + channels + inspector), environments (bridges + spawn), sessions (managed/resident + console attach + controls + mode-switch), runs (dispatch runs + inspector + steer/interrupt), analytics (traffic/capacity/run-mix), files (artifacts), settings (themes/maintenance/policy/contracts), help (docs). new_dashboard already proved sessions/environments/diagnostics/settings — those patterns port first.

**Leveled-up (beyond parity, YAGNI-checked):** granular WS updates (no full refresh); virtualized chat for large histories; surface `awaiting_reply` as a real field/badge (it's currently regex-inferred); truthful status everywhere (reuse the just-fixed status engine values); proper loading/error/empty states; type-safe API.

## Migration path (incremental, low-risk)

1. Scaffold `service/web/` (Vite+React+TS); port design tokens; stand up `api.ts` + `ws.ts` + `types.ts`.
2. Port feature areas in dependency order, each behind a route, verified against the live backend: sessions → environments → runs/work-loop → chat → control-room → analytics → files → settings → help.
3. Serve the built app on a path (e.g. `/v2`) alongside the legacy `:8800` dashboard during the port (operator can compare side-by-side).
4. At parity, cut over: FastAPI serves the v2 build at `/` (legacy kept at `/legacy` briefly), retire `dashboard.html` + `service/new_dashboard/` + `new_dashboard_app.py`.

## Forward-compatibility: auth (NEXT refactor, not this one)

Login/auth is a **separate, later** refactor — NOT built here — but the v2 architecture is shaped so it drops in cleanly with no rewrite:

- **Single API chokepoint:** ALL network access goes through `lib/api.ts` (REST) and `lib/ws.ts` (socket). Adding auth later = inject a token header + handle `401 → redirect to /login` in exactly one place each. No feature code changes.
- **App-level auth boundary:** routing is structured so an `<AuthGate>` can wrap the router (unauthenticated → `/login`, authenticated → the app) without touching feature routes. A `useSession()`/auth store slice is left as a stub that today returns "authed" (open), later reads a real session.
- **WS is token-ready:** the single WS client builds its URL in one spot, so a `?token=`/header can be added there later.
- **No auth assumptions leak into features:** components never read credentials directly — they call query hooks. So the security layer is purely additive next time.

This costs nothing now (it's just clean boundaries) and avoids a painful retrofit. The actual login UI, token issuance, and backend auth are explicitly deferred to the next refactor's own spec.

## Out of scope (this refactor)

- Login / auth / token issuance / backend security (the NEXT refactor — see Forward-compatibility above; we only leave clean seams).
- Backend/API changes (contract is fixed; one small optional add: expose `awaiting_reply` as a field instead of a status-note regex — flagged, not required).
- Multi-tenant.

## Open questions for review

1. Scope of first deliverable: **MVP-first** (sessions+chat+runs, the daily-driver core) then iterate, vs **full parity in one plan**? (Recommend MVP-first: ship the daily-driver, prove the architecture, then port the rest.)
2. Build on a fresh `service/web/` (recommended — clean slate, keep `new_dashboard/` as reference) vs evolve `new_dashboard/` in place?
3. CSS Modules (port the look) vs Tailwind (rebuild the look) — recommend CSS Modules.
