# Dashboard Feature Parity — OLD vs NEW Inventory & Comparison

**Date:** 2026-06-18
**Mode:** Read-only investigation. No code changed.
**OLD dashboard:** `service/dashboard.html` (9080-line single-file HTML+CSS+JS), served at `/api/v1/dashboard`.
**NEW dashboard:** `service/new_dashboard/` (`index.html` + `app.js` ~181KB + `chat.js` + `analytics.js` + `status.js` + `theme.js` + `console-chooser.js` + `ui.js` + `util.js` + `styles.css`), served at root.

Both hit the same FastAPI under `/api/v1`.

---

## Page-level map

| OLD page (nav) | NEW page equivalent | Notes |
|---|---|---|
| Chat | Chat | Both present; feature deltas below |
| Control (Control Room / "live overview") | **(folded)** — metrics moved into the always-on **Needs Attention strip** + Analytics ops cards | No dedicated landing page in NEW |
| Work Loop (contracts) | Diagnostics (left half) | Renamed + merged with Runs |
| Environments | Environments | Both present |
| Sessions | Sessions | Both present |
| Runs (execution audit) | Diagnostics (right half) | Merged into Diagnostics |
| Analytics | Analytics | NEW is richer (leaderboard, outcomes, failures, pulse) |
| Artifacts (shared files) | Files | NEW adds upload form |
| Help (5-tab essay) | Settings → Help band (links + quick start + endpoint ref) | NEW deliberately does not re-bake the essay |
| Settings | Settings | Near-parity; same setting keys |

NEW navigation order: Chat, Sessions, Environments, Diagnostics, Analytics, Files, Settings (`index.html:38-44`). Mobile tabbar mirrors it (`index.html:465-471`).

---

## Comparison tables

Legend for **Gap?**: ✅ parity · ⚠️ weaker in NEW · ❌ absent in NEW · ➕ NEW-only (OLD lacked it).

### Chat

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Conversation rail (DMs + channels) | `renderChatRail()` dashboard.html:5744 | `chatConversationItems()`/`railItemHtml()` chat.js:28,110 | ✅ | — |
| Sort (activity/oldest/name/name-desc/runtime/status) | `setChatSort` dashboard.html:1314-1321 (6 incl. **oldest**) | `chat-sort` chat.js:92-98 (activity/unread/name/name-desc/status/runtime) | ⚠️ | N — adds **unread**, drops **oldest**; net neutral |
| Scope chips (all/dm/channel/favorites) | implicit via filters | `data-chat-scope` index.html:103-106 | ➕ | — NEW cleaner |
| Status multi-filter | full menu w/ presets all/none/live, 9 states incl. idle/stale/deleted dashboard.html:1327-1338 | dot chips, 6 states (working/online/available/blocked/offline/stopped) index.html:112-117 | ⚠️ | LOW — NEW drops idle/stale/deleted chips; minor |
| Unread-up / working-up ordering toggles | `setChatUnreadUp`, `setChatWorkingUp` dashboard.html:1345-1349 | `workingUp` toggle chat.js:34; unread-up folded into sort | ⚠️ | LOW |
| Conversation search | `chat-filter` oninput dashboard.html:1308 | `chat-filter` chat.js:31 | ✅ | — |
| **Global cross-conversation message search** | `renderGlobalMessageSearch()` dashboard.html:5777 (separate input + hit list, top 20) | searches loaded message bodies inside rail filter only chat.js:85-90; no dedicated results panel | ⚠️ | MEDIUM — OLD had a dedicated "search all loaded inbox messages" panel |
| Per-conversation message search | `chat-message-filter` dashboard.html:1382 | `chat-msg-search` chat.js:327 | ✅ | — |
| Viewing-as identity switcher | `chat-from` dashboard.html:1313 | `chat-identity` chat.js:366-373 | ✅ | — |
| Reply | `replyToChat()` dashboard.html:7013 | `data-chat-reply` chat.js:344-356 | ✅ | — |
| **Follow-up (linked request)** | `followUpChatMessage()` dashboard.html:7014 | not present | ❌ | LOW — reply+expects-reply approximates it |
| Mark read / unread | `markMessageRead()` dashboard.html:7019 | `data-msg-read` | ✅ | — |
| Unsend | `unsendChatMessage()` dashboard.html:7021 | `data-msg-unsend` app.js:148-156 | ✅ | — |
| Open message by ID | `openMessageById()` dashboard.html:5915 | only via run chip / thread | ❌ | LOW |
| **Composer: message Type selector** (info/request/review/approval/response/error) | `chat-type` dashboard.html:1402+ | auto: `request` if expects-reply else `info` chat.js:128 | ❌ | **MEDIUM** — operators lose ability to send review/approval/error types from UI |
| **Composer: Priority selector** (normal/high/urgent) | `chat-priority` dashboard.html | display-only badge chat.js:132-138; no input | ❌ | **MEDIUM** — cannot mark urgent from NEW |
| **Composer: Subject field** | `chat-subject-input` dashboard.html | display-only chat.js:149; no input | ❌ | **MEDIUM** — subjects matter for tasks/handoffs |
| Expects-reply toggle | (implicit via type=request) | `chat-expects-reply` index.html:145 | ➕ | — |
| Queue-if-busy | `chat-queue-btn` dashboard.html:1424 | `chat-queue` checkbox index.html:146 | ✅ | — |
| Send vs Queue | dedicated buttons dashboard.html:1424-1425 | one Send + queue checkbox | ✅ | — |
| Truthful delivery feedback (steered/queued/woke/stored) | inline response handling dashboard.html:8686+ | `deliveryToastFor()` chat.js:157-169 | ✅ | — |
| Artifact attach in composer | `chat-artifact-file`/`chooseChatArtifactFile()` dashboard.html:1419-1420 | `chat-attach-input` index.html:147 | ✅ | — |
| Image paste (Ctrl+V → artifact) | keydown/paste handler dashboard.html | not found in NEW | ❌ | LOW |
| Draft persistence per conversation | `saveCurrentChatDraft()` dashboard.html:5193 | `state.chat.drafts` chat.js:361 | ✅ | — |
| Favorites (star) | `toggleChatFavorite()` dashboard.html:5973 | `data-fav-toggle` chat.js:117; PATCH favorite app.js:172-181 | ✅ | — |
| Messenger ↔ Console mode | `setChatMode` dashboard.html:1377-1378 | `data-chat-view` chat.js:295-322 | ✅ | — |
| In-chat console (xterm PTY) | `renderConsoleChat()` dashboard.html:6416 | `mountChatConsole()` + console-chooser.js | ✅ | — |
| **Peek mode (view without auto-marking read)** | `chat-peek-mode` dashboard.html:1341 | not present | ❌ | **MEDIUM** — useful for triage without clearing unread |
| Compact list density toggle | `chat-compact-toggle` dashboard.html:1355 | not present | ❌ | LOW |
| Reset-view-filters button | `resetChatViewFilters()` dashboard.html:1358 | not present | ❌ | LOW |
| Scroll-to-bottom button | `chat-scroll-bottom` dashboard.html:1396 | follow-bottom logic chat.js:335-340 | ✅ | — |
| Channel create / join / leave / read | dashboard.html:8764/7172/7200/6229 | `data-chat-channel-action` chat.js:286-288 | ✅ | — |
| Channel member add/remove | `addSelectedChannelMember()` dashboard.html:7163 | `data-channel-add/remove-member` chat.js:282,292 | ✅ | — |
| Conversation inspector / details | `renderChatInspector()` dashboard.html:7033 (session block w/ many actions) | agent drawer `data-agent-drawer` app.js:2276-2314 | ⚠️ | see Sessions — some session actions absent |
| Per-agent chat analytics panel | `openChatAnalytics()` dashboard.html:6123 | `renderAnalyticsPanelHtml()` chat.js:173-211 | ✅ | NEW comparable |
| Reactions/emoji | none | none | ✅ | N — neither has it |

### Sessions / Runs

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Sessions grouped by environment (collapsible) | `renderSessions()` dashboard.html:7708 | `renderSessionRail()` app.js:1240 | ✅ | — |
| Status filter (presets all/none/live + states) | dashboard.html:1476-1489 | `data-session-status-*` app.js:1223-1237 | ✅ | — |
| Bulk select + stop + delete | `batchStop/DeleteSelectedSessions()` dashboard.html:8192-8251 | `data-bulk-session-action` app.js:1207-1217 | ✅ | — |
| Restart | `controlSession(id,'restart')` dashboard.html:8114 | `data-session-control` app.js:1794 | ✅ | — |
| Reset (fresh context / recreate) | `controlSession(id,'recreate')` dashboard.html | `data-session-control` recreate app.js:1795 | ✅ | — |
| Stop | `controlSession(id,'stop')` | `data-session-control` app.js:1796 | ✅ | — |
| Delete session record | `deleteSessionRecord()` dashboard.html:8165 | bulk delete + per-session | ✅ | — |
| Compact (same identity) | `continueSessionAs(id,false)` dashboard.html:7789 | continue/compact form app.js:2425-2447 | ✅ | — |
| Continue-as (split identity) | `continueSessionAs(id,true)` dashboard.html:7790 | continue form app.js:2425-2447 | ✅ | — |
| Interrupt active run | `interruptRun()` dashboard.html:4459 | run inspector `data-run-control="interrupt"` app.js:2179 | ✅ | — |
| Set native session handle | `setAgentSessionHandle()` dashboard.html:4878 (explicit action) | inside edit form `session-handle` app.js:2377 | ⚠️ | LOW — present but buried |
| **CLI takeover ("Pause for CLI" + resume command)** | `takeOverSessionInCli()` dashboard.html:4846; `sessionResumeCommand()` 4740 | not surfaced | ❌ | **MEDIUM** — needed to hand a managed session to a human CLI |
| **Compaction/continuation history viewer** | `viewCompactionHistory()` dashboard.html:7952 | not present (tracking exists via metadata, no viewer) | ❌ | LOW–MEDIUM |
| Mode switch (resident ↔ managed) | `agentModeSwitchAction()` dashboard.html:3911 | `data-mode-switch` chip app.js:1707-1715 | ✅ | NEW always-visible |
| Agent editor (env/runtime/workspace/handle/rename) | `openAgentEditor()` dashboard.html:3874 | edit form app.js:2352-2365 | ✅ | — |
| Remove/unregister agent | `deleteAgent()` dashboard.html | drawer action | ✅ | — |
| Console panel + xterm | dashboard.html:6416 | `session-console-panel` app.js:1762-1925 | ✅ | — |
| Console-chooser (Hermes tab / Codex live thread / PTY) | partial | `console-chooser.js`; open-hermes-tab, codex-console-connect app.js:1790-1791 | ➕ | — NEW richer |
| Activity log per session | — | `data-session-tab="activity"` app.js:1284-1310 | ➕ | — |
| Runs table + filters (status/from/to/runtime) | `renderRuns()` dashboard.html:8388 | Diagnostics right half app.js:2070-2104 | ✅ | — |
| Run search | `filterRuns()` dashboard.html:8553 | `run-search` app.js:2081 | ✅ | — |
| Run detail (events + controls + body/summary/error) | viewRun modal dashboard.html:8417-8453 | run inspector sheet app.js:2187-2237 | ✅ | NEW adds source-message context, event order toggle, load-more |
| Run: interrupt | dashboard.html:8459 | app.js:2179 | ✅ | — |
| Run: steer | `steerRun()` dashboard.html:8478 | `data-steer-run` app.js:2100,2178 | ✅ | — |
| **Run: retry** | none | `data-run-control="retry"` app.js:2181,2761 | ➕ | — |
| **Run: queue-after** | none | `data-run-control="queue-after"` app.js:2180,2754 | ➕ | — |
| **Run: close / open-console from inspector** | partial | `data-run-control` close/open-console app.js:2182-2183 | ➕ | — |

### Environments / Spawn

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Env summary stats | `renderEnvironmentStats()` dashboard.html:4035 | `environment-summary` app.js:1987 | ✅ | — |
| Spawn form (env/runtime/agentId/role/workspace/prompt) | dashboard.html:1442-1454 | `env-spawn-*` index.html:203-217 | ✅ | — |
| Spawn → POST /spawn-requests (managed-warm) | `createSpawnRequest()` dashboard.html:8835 | app.js:2659-2677 | ✅ | — |
| Env cards (status/runtimes/roots/last-seen) | `renderEnvironments()` dashboard.html:4628 | runtime-grid app.js:2026-2043 | ✅ | — |
| Spawn here (prefill) | `prefillEnvironmentSpawn()` dashboard.html:4665 | `data-env-spawn` app.js:2038 | ✅ | — |
| Stop bridge | `controlEnvironment(id,'stop')` dashboard.html:5144 | `data-env-control="stop"` app.js:2041 | ✅ | — |
| Forget (offline env) | `controlEnvironment(id,'forget')` | `data-env-control="forget"` app.js:2040 | ✅ | — |
| **Edit workspace roots (override)** | `openEnvironmentEditor()` dashboard.html:5070; PATCH `/environments/{id}/roots` | not present | ❌ | **MEDIUM** — operators can't override cwd roots from NEW |
| **Reset roots to bridge defaults** | `resetEnvironmentRoots()` DELETE `/environments/{id}/roots` | not present | ❌ | LOW (follows from above) |
| **Copy start command** (OS-specific) | `env-command` pre + copy dashboard.html:4699 | not present | ❌ | LOW–MEDIUM — handy onboarding aid |
| Spawn requests / history table | `renderSpawnRequests()` dashboard.html:7376 + show-history toggle | tracked via metadata, **no visible queue/history table** | ⚠️ | **MEDIUM** — OLD had a visible spawn queue with status/error per request |

### Work Loop / Contracts (NEW = Diagnostics left)

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Contract list + cards | `renderWorkLoop()`/`contractCard()` dashboard.html:4436,4366 | `contract-list` app.js:914-936 | ✅ | — |
| State filter | `contract-state` dashboard.html:1250-1261 | `contract-state` index.html:236-247 | ✅ | — |
| Category filter | `contract-category` dashboard.html:1262 | `contract-category` index.html:250-255 | ✅ | — |
| Contract stats panel (overdue/open/working/queued/missing/hidden) | `work-stats` dashboard.html:4453 | partial via Needs-Attention metrics | ⚠️ | LOW |
| Send due reminders | `runContractReminders()` dashboard.html:4487 | `send-reminders` index.html:232 | ✅ | — |
| **Preview reminders (dry-run)** | `runContractReminders(true)` dashboard.html:1240 | not present | ❌ | LOW–MEDIUM — safe preview before nudging |
| **Repair delivered reads (read-receipts)** | `repairContractReadReceipts()` dashboard.html:4501 | not present | ❌ | **MEDIUM** — hygiene action with no NEW equivalent |
| **Repair handoffs (Work Loop button)** | `repairPendingHandoffs()` dashboard.html:1242 | not present in Diagnostics (endpoint exists) | ❌ | **MEDIUM** — fixes terminal runs with no recorded handoff |
| Bulk select + close contracts | `selectVisibleContracts`/`closeSelectedContracts` dashboard.html:3659,3695 | `data-diagnostic-action` remind/close/inspect/clear app.js:998-1015 | ✅ | NEW spans contracts+runs |
| Remind single | `runContractReminders(false,id)` dashboard.html:4403 | `data-remind-contract` app.js:933 | ✅ | — |
| Close single | `closeWorkContract()` dashboard.html:4404 | `data-close-contract` app.js:933 | ✅ | — |
| **Hide / restore contract (local)** | `dismissContract()`/`restoreContract()` dashboard.html:3611-3628; show-hidden toggle | not present | ❌ | LOW |
| Inbox hygiene panel (unread/self-wakes/answered-unread/reminder policy) | `work-hygiene` dashboard.html:4480 | not present | ❌ | LOW |
| **Orphan-unread cleanup** | `/messages/cleanup/orphan-unread` dashboard.html:8900 | not surfaced | ❌ | LOW |

### Analytics

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Range selector (24h/30d/12m/all) | `setAnalyticsRange()` dashboard.html:4512 | `analytics-range` analytics.js:70-75 | ✅ | — |
| Message traffic chart (SVG) | `renderTrafficChart()` dashboard.html:4526 | `trafficChartHtml()` analytics.js:89-132 | ✅ | — |
| Range-scoped stat cards | `analytics-stats` dashboard.html:1522 | `statCardsHtml()` analytics.js:134-146 | ✅ | — |
| Capacity-now panel | dashboard.html:1538 | `healthGridHtml()` analytics.js:148-159 | ✅ | — |
| Run status mix | `renderRunStatusMix()` dashboard.html:4584 | `runStatusMixHtml()` analytics.js:161-174 | ✅ | — |
| **Operational KPIs** (success rate, median reply, open/overdue contracts) | none | `opsKpisHtml()` analytics.js:186-196 | ➕ | — |
| **Dispatch outcomes (14d stacked)** | none | `dispatchOutcomesHtml()` analytics.js:199-213 | ➕ | — |
| **Agent leaderboard** | none | `agentLeaderboardHtml()` analytics.js:216-229 | ➕ | — |
| **Busiest channels** | none | `busiestChannelsHtml()` analytics.js:232-242 | ➕ | — |
| **Failure reasons** | none | `failureReasonsHtml()` analytics.js:245-251 | ➕ | — |
| **Fleet pulse (windowed comms perf, board)** | none | `fleetPulseHtml()` analytics.js:34-68 (chat landing) | ➕ | — |

### Artifacts / Files

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| File list (name/from/size/desc/sharedAt) | `loadFiles()` dashboard.html:8563 | `files-list` app.js:236-252 | ✅ | — |
| Delete file | `deleteFile()` dashboard.html:8616 | `data-file-delete` app.js:249 | ✅ | — |
| Download | direct URL only (no button) | explicit download link app.js:248 | ➕ | — |
| **Upload form (file/name/desc, size-validated)** | composer-only upload | `files-upload-form` index.html:346-351; POST /shared app.js:253-277 | ➕ | — NEW adds a real upload UI |

### Diagnostics / Control (global ops)

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Control Room landing page (6 metric cards + columns) | `renderHomeControlRoom()` dashboard.html:4176 | **no landing page**; metrics in Needs-Attention strip app.js:899-911 + Analytics | ⚠️ | LOW — intentional consolidation |
| Needs-Attention strip | `homePageData()` dashboard.html:4070-4290 | `attention-strip` index.html:67-78; app.js:944-954 | ✅ | — |
| **Per-issue mute** | `toggleIssueMute()` dashboard.html:3582 | not present (filter/selection-based) | ❌ | LOW |
| **Per-issue dismiss + "dismiss listed"** | `dismissIssue()`/`dismissResolvedHomeIssues()` dashboard.html:3567,3718 | not present | ❌ | LOW |
| Live identities / working-now / queued columns | dashboard.html:4249-4289 | Analytics pulse board + metrics | ⚠️ | LOW |
| **Identity Directory modal** (roles, resident bindings, offline-CLI cleanup) | `openIdentityDirectory()` dashboard.html:3837 | not present | ❌ | **MEDIUM** — single place to audit/clean identities & offline CLI rows |

### Settings

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Appearance: title, theme, 3-color palette, preview tiles, live preview | dashboard.html:1823-1857 | SETTINGS_SCHEMA app.js:644-693; previewAppearance app.js:808 | ✅ | — |
| Runtime: claude/codex/pi model+effort, terminal-backing, eager-PTY, wrapper-runtimes, console-inject, auto-confirm-claude, manual-session-mode, worker-idle | dashboard.html:1922-1992 | same keys app.js:644-693 | ✅ | — |
| Work Loop: reply-contracts, reminder mins/repeat/max, contract-stale-hours | dashboard.html:2000-2020 | same keys | ✅ | — |
| Maintenance: retention, max-msgs, max-shared, refresh, liveness, stale, rotation, env-offline, run-stale (×2), resident-lease | dashboard.html:1864-1914 | same keys | ✅ | — |
| Save / Reset | dashboard.html:2025-2026 | `settings-save` app.js:824-847 | ⚠️ | LOW — NEW has Save; no explicit "Reset/reload" button (refresh re-pulls) |
| **Classic Settings escape hatch** | n/a | `open-classic-settings` → OLD dashboard index.html:363 | ➕ | — |
| Help (full 5-tab essay) | dashboard.html:1554-1804 | links + quick-start + endpoint ref index.html:372-451 | ⚠️ | N — intentional; canonical docs live in repo |

### Global / UX

| Feature | Old dashboard | New dashboard | Gap? | Worth porting? |
|---|---|---|---|---|
| Top bar title/subtitle | dashboard.html:1181 | `page-title`/`page-subtitle` index.html:56-57 | ✅ | — |
| Global find/search box | per-page | `global-filter` index.html:62 | ➕ | — |
| Connection/health indicator | `topbar-live` dashboard.html:1185 | `api-status` chip index.html:47; app.js:532 | ✅ | — |
| Version badge (sha + behind-count) | `refreshVersionBadge()` dashboard.html:9036 | `version-badge` app.js:3433-3451 | ✅ | — |
| Manual refresh | dashboard.html:1186 | `refresh` index.html:64 | ✅ | — |
| WebSocket realtime (terminal_output, agent_status, dispatch_*) | `connectDashboardSocket()` dashboard.html:2607 | `connectRealtimeSocket()` app.js:410-501 | ✅ | NEW handles more event types |
| Polling fallback (configurable) | setInterval dashboard.html:8984 | 15s setInterval app.js:3483 | ✅ | — |
| Toasts | `toast()` dashboard.html:2395 | ui.js | ✅ | — |
| Desktop notifications / sound | none | none | ✅ | N — neither |
| Modals / drawers | `openModal()` dashboard.html:2401 | inspector drawer + run sheet app.js:2497-2514 | ✅ | — |
| Context/actions menus | `.actions-menu` dashboard.html | inline buttons + drawer | ⚠️ | LOW |
| Copy-to-clipboard (http fallback) | `copyText()` dashboard.html:3274 | copy-run-id; OSC-52 console app.js:2207 | ⚠️ | LOW — fewer copy affordances |
| **Status "why" popover** (explain status) | status-note tooltip only | `status-why-popover` index.html:463; app.js:1075-1099 | ➕ | — NEW richer |
| Keyboard: Enter-send, Shift+Enter | dashboard.html:8748 | app.js:3377-3382 | ✅ | — |
| Keyboard: Ctrl+Shift+C copy console | dashboard.html:2971 | app.js:3222-3224 | ✅ | — |
| Keyboard: data-short nav hints (C/S/E/D/A/F/G) | none | `data-short` index.html:38-44 | ➕ | — |
| Sidebar collapse | `aifySidebarCollapsed` dashboard.html | `toggle-nav` app.js:3417 | ✅ | — |
| Mobile responsive + bottom tabbar | media queries + mobile rail | `mobile-tabbar` index.html:464; drag-to-close drawer app.js:3420 | ✅ | — |
| Status taxonomy/colors | dashboard.html:24-25,5343 | status.js + CSS s-* classes | ✅ | — |

---

## What NEW has that OLD lacks (additions — keep)

- **Run controls: retry, queue-after, close, open-console** from the run inspector (app.js:2180-2183).
- **Richer Analytics:** operational KPIs, 14-day dispatch outcomes, agent leaderboard, busiest channels, failure reasons, **fleet pulse** board with window selector (analytics.js).
- **Real Files upload UI** (file/name/desc, size-validated) — OLD only uploaded via chat composer.
- **Console-chooser**: Hermes-tab / Codex live-thread / PTY selection (console-chooser.js; app.js:1790-1791).
- **Status "why" popover** explaining how a status was derived (app.js:1075-1099).
- **Classic-settings escape hatch** + "Old dashboard" link (index.html:49,363).
- **Global find box**, **data-short** keyboard nav hints, drag-to-close mobile drawer.
- Run inspector source-message context, event order toggle, paginated load-more.

---

## Recommend porting — prioritized shortlist

### HIGH (operator-facing capability genuinely lost)
1. **Composer Type + Priority + Subject fields** — NEW auto-sets type=`info`/`request` and has no priority or subject input. Operators can no longer send `review`/`approval`/`error` typed messages, mark `urgent`, or set a task subject from the UI. (OLD: `chat-type`/`chat-priority`/`chat-subject-input` dashboard.html:1402+) — biggest functional regression.
2. **Identity Directory** — single modal to audit roles/resident bindings and clean up offline CLI rows. No NEW equivalent. (OLD `openIdentityDirectory()` dashboard.html:3837)
3. **Spawn requests queue/history view** — NEW shows no visible spawn-request table; a failed/queued spawn has nowhere to surface its status/error on Environments. (OLD `renderSpawnRequests()` dashboard.html:7376)

### MEDIUM (useful, has a workaround or is niche)
4. **Work Loop hygiene actions: Repair Delivered Reads, Repair Handoffs (button), Preview Reminders (dry-run)** — endpoints still exist; NEW Diagnostics only exposes "Send due reminders". (dashboard.html:4501, 1242, 1240)
5. **CLI takeover + resume command** on a session — hand a managed session to a human CLI. (OLD `takeOverSessionInCli()`/`sessionResumeCommand()` dashboard.html:4846,4740)
6. **Environment edit/reset workspace roots + copy start command** — operators can't override cwd roots or copy the OS-specific bridge start command from NEW. (OLD `openEnvironmentEditor()`/`resetEnvironmentRoots()` dashboard.html:5070; `env-command` 4699)
7. **Peek mode** in chat — triage unread without auto-marking read. (OLD `chat-peek-mode` dashboard.html:1341)
8. **Dedicated global cross-conversation message search** with a results panel (OLD `renderGlobalMessageSearch()` dashboard.html:5777) — NEW only filters the rail.

### LOW (nice-to-have / minor)
9. Compaction/continuation **history viewer** (OLD `viewCompactionHistory()` dashboard.html:7952).
10. Follow-up (linked request), open-message-by-ID, image-paste-to-artifact, chat compact-density toggle, reset-view-filters.
11. Per-issue **mute/dismiss** on Needs-Attention (OLD localStorage issue management) and the **inbox-hygiene** panel + orphan-unread cleanup.
12. Restore the **idle/stale/deleted** status chips and **oldest** sort option in chat filters.
13. Settings explicit **Reset/reload** button.

---

## Notes / caveats

- Several "gaps" are intentional consolidations the NEW design made on purpose: the Control Room landing page folded into the Needs-Attention strip + Analytics; the 5-tab Help essay replaced by doc links (NEW deliberately avoids a third copy of the status doc — see `index.html:369-371`); Runs merged into Diagnostics.
- All settings keys appear to be at parity (same `s-*`/setting-key names), so backend setting coverage is preserved.
- Endpoint coverage is broadly the same; the missing items above are **UI affordances for endpoints that still exist** (reminders dry-run, read-receipt repair, handoff repair, env roots PATCH/DELETE, orphan-unread cleanup, cli_takeover) rather than missing backend support — making them low-effort ports.
- This is an inventory + recommendation only. No implementation was performed.
