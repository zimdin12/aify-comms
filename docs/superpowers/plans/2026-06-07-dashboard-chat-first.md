# Dashboard: Chat-First + Persistent State + Chat Analytics — Implementation Plan

> Execute task-by-task; steps use `- [ ]`. Target is the LIVE dashboard only.

**Goal:** (a) Chat becomes the landing page (swap with Control Room); (b) UI state persists across
refresh (collapsed sidebar stays collapsed); (c) clicking an open DM entry toggles it closed and
shows useful chat analytics (messages/hour, total minutes working, per-agent counts, busiest hours).

**Target (confirmed by research):** the LIVE user-facing dashboard is the legacy single-file
`service/dashboard.html`, served at `/api/v1/dashboard` (root `/` redirects there;
`service/main.py:372`, `api_v2.py:19567`). `new_dashboard/` (port 8801) is an opt-in preview and is
NOT touched by this plan — so the "don't touch new_dashboard/" constraint is satisfied.

**Tech stack:** vanilla JS in `service/dashboard.html`; FastAPI/SQLite for one additive analytics
endpoint. Deploy = `docker compose up -d --build` (dashboard.html + api_v2.py are COPY'd) — but this
plan only IMPLEMENTS + COMMITS; the operator rebuilds/tests later.

**Pitfall to honor:** `messages.timestamp` is epoch-ms INTEGER; `dispatch_runs.*_at` are ISO TEXT —
working-minutes math must use `julianday()` on run columns, never epoch arithmetic.

---

### Task 1: Chat as the landing page

**Files:** `service/dashboard.html`

- [ ] **Step 1:** `initialPageFromLocation()` (~`:2141-2145`): change the no-stored-page fallback from
  `'dashboard'` to `'chat'`.
- [ ] **Step 2:** `showPage()` (~`:2163`): change the unknown-name coercion default `'dashboard'` → `'chat'`.
- [ ] **Step 3:** `pageTitle()` (~`:2159`): keep the map correct (Chat title stays "Chat"; Control stays
  "Control Room"); only the DEFAULT changes.
- [ ] **Step 4:** Initial HTML active state: move `class="active"` from `#page-dashboard` (~`:1175`) and
  `#nav-dashboard` (~`:1136`) to `#page-chat` / `#nav-chat` so the pre-JS first paint is Chat. Reorder
  the sidebar nav so Chat is listed first (Control second).
- [ ] **Step 5:** Decision (decided): a returning user with a stored `aifyDashboardPage` keeps their last
  page — Chat is the DEFAULT for fresh/invalid state, not a forced override. (Least surprising; honors
  the existing persistence.)
- [ ] **Step 6:** `python -c "import ast"` is N/A (HTML); load-check by grepping the changed identifiers.
  Commit.

---

### Task 2: Persist sidebar collapse across refresh

The sidebar toggle is an inline `onclick="...classList.toggle('collapsed')"` (`:1131`) with NO
persistence. Mirror the EXISTING `toggleChatInspector()` persistence pattern (`:6491-6494` write +
`:2076` hydrate).

**Files:** `service/dashboard.html`

- [ ] **Step 1:** Add `function toggleSidebar()` near `toggleChatInspector`: toggle `collapsed` on
  `#sidebar` AND `localStorage.setItem('aifySidebarCollapsed', collapsed ? '1':'0')`.
- [ ] **Step 2:** Replace the inline `onclick` at `:1131` with `onclick="toggleSidebar()"`.
- [ ] **Step 3:** Add a hydration line near the other boot reads (`:2068-2093`): if
  `localStorage.getItem('aifySidebarCollapsed')==='1'` add `collapsed` to `#sidebar` on load (before
  first paint where possible).
- [ ] **Step 4:** Commit.

---

### Task 3: Additive analytics endpoint for per-agent + busiest-hours + working-minutes

The existing `GET /analytics` (`api_v2.py:19264`) returns global `messagesPerHour`/totals but no
per-agent breakdown, hour-of-day histogram, or working-minutes. Add a small, additive endpoint —
no change to the existing `/analytics` (keeps 8801 parity safe).

**Files:** Modify `service/routers/api_v2.py`; Test: `service/tests/test_chat_analytics.py`

- [ ] **Step 1: Write the failing test** — seed a couple of `messages` (epoch-ms) between two agents +
  a `dispatch_runs` row with `started_at`/`finished_at` (ISO) for the target; call
  `GET /api/v1/analytics/agent/{agentId}`; assert the response has `messageTotal`, `messagesPerHourOfDay`
  (24 buckets), `byPeer` (counts per other agent), and `workingMinutes` (> 0 from the run). Run → FAIL.
- [ ] **Step 2: Implement `GET /agents/{agent_id}/analytics`** near the existing `/analytics` (~`:19420`):
  - `messageTotal`: `COUNT(*) FROM messages WHERE (from_agent=? OR to_agent=?) AND source='direct'`.
  - `byPeer`: `GROUP BY` the other party.
  - `messagesPerHourOfDay`: 24 buckets via
    `strftime('%H', datetime(timestamp/1000,'unixepoch'))` over that agent's direct messages.
  - `workingMinutes`: `SUM((julianday(finished_at)-julianday(started_at))*1440) FROM dispatch_runs
    WHERE target_agent=? AND started_at IS NOT NULL AND finished_at IS NOT NULL` (guard NULLs; clamp ≥0).
  - Use the `get_db()` + `try/finally await db.close()` pattern. Add `/agents/{id}/analytics` is already
    under the agent routes; no auth-allowlist change needed (same as other agent reads).
- [ ] **Step 3:** Run the test → PASS. `python -c "import ast; ast.parse(...)"`. Commit.

---

### Task 4: Click an open DM → toggle closed + show the analytics panel

**Files:** `service/dashboard.html`

- [ ] **Step 1:** In the delegated chat-item click handler (~`:3019-3024`) / `selectChatConversation()`
  (~`:5919`): if the clicked `data-chat-key === window._chatSelected`, TOGGLE — clear the selection and
  set `window._chatAnalyticsKey = key` (the agent to show analytics for); else select normally and clear
  `_chatAnalyticsKey`. Re-render.
- [ ] **Step 2:** In `renderChat()` chat-main branch (~`:6045-6086`): when `window._chatAnalyticsKey` is
  set and nothing is selected, render an analytics view into the chat-main/timeline area instead of the
  message timeline — fetch `GET /api/v1/analytics/agent/{key}` and show: total messages, messages/hour-of
  -day (a simple bar list reusing the Analytics page's bar style ~`:4535`), top peers, and total working
  minutes (format `Xh Ym`). Add a small back/close affordance that clears `_chatAnalyticsKey` and
  reselects the DM.
- [ ] **Step 3:** Persist the analytics-open state if cheap (`aifyChatAnalyticsKey`) so a refresh keeps it
  (optional, mirrors the other localStorage keys).
- [ ] **Step 4:** Commit.

---

## Self-Review
- **Spec coverage:** (a) Task 1; (b) Task 2; (c) Tasks 3+4. All on the live `dashboard.html`;
  `new_dashboard/` untouched.
- **Risk:** Tasks 1/2/4 are isolated JS; Task 3 is a NEW additive endpoint (existing `/analytics`
  unchanged → 8801 parity safe). Timestamp-type pitfall called out (julianday for runs).
- **No placeholders:** exact line anchors + the existing patterns to mirror (`toggleChatInspector`,
  `renderAnalytics`) are named.
