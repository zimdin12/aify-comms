# v0.7.1 review, lens C: the web dashboard

Reviewed at tag `v0.7.0` (`dce0e771`). `service/new_dashboard` and `service/routers` are identical
between the tag and HEAD `3f588c5c`, so the line numbers below are valid for both. Range:
`git diff v0.6.22 v0.7.0 -- service/new_dashboard` (35 commits), read against
`v0.7-scan/C-dashboard.md` (C1-C26) and the finalization plan. Nothing touched the live service: no
browser, no request to :8800 or :8802.

Evidence levels: **RAN** means a scratch probe (under `mktemp -d`, not in the repo) imported the real
modules and reproduced the failure, with a control in the same run. **READ** means the cited lines
were read and not executed. Baseline: `node --test run-inspector chat session-rail refresh-cycle
change-refresh shared-files inspector-forms message-actions` gave 207 pass, 0 fail.

**Totals: 0 P1, 3 P2, 5 P3.** No XSS, no unconfirmed destructive action, and no fetch to a route the
service does not serve were found in the 0.7 diff (see "Checked and sound").

---

## P2

### C1. Run inspector: the refresh merge hides a live run's new events, and can leave a silent gap
- **where:** run-inspector.mjs:216 (`keepLoadedEvents(eventPage, previous?.events || [])`) and
  :236-242 (`keepLoadedEvents`); :315-316 (`loadMoreRunEvents` checks `loadingMore` only)
- **introduced:** 0.7.0 (`f59494c3`, the C4 fix)
- **failure:**
  1. *Ascending order.* The operator flips to oldest-first and presses Load more until `hasMore` is
     false. The run emits another event. The refresh fetches page one (the oldest 50), keeps the held
     tail, and takes `hasMore` from the previous state, which is false. The new event is not shown and
     no Load more button is rendered. Before 0.7 the refresh reset to page one with `hasMore: true`,
     so it could at least be reached. It now stays hidden until the drawer is reopened or the order is
     toggled.
  2. *Gap.* In newest-first order with more than one page loaded, more than 50 events arrive before
     the next refresh (a hidden tab catching up, or a reconnect). The new page one is spliced onto the
     old tail with nothing in common between them. Events in between are missing, `hasMore` is false,
     and nothing says anything is missing.
  3. *Race (READ).* A Load more pressed while a refresh fetch is in flight appends to the new object.
     The refresh then merges against `previous.events`, which is the snapshot taken before the
     appended page, so that page is dropped while `hasMore` keeps the value Load more set.
- **evidence:** RAN (1) and (2). Probe results: `asc: count 60 last e60 hasMore false` with e61 on the
  server. `gap: first e300, [49]=e251, [50]=e100, hasMore false`. Control (newest-first, all loaded,
  one new event): e61 is shown at the top. (3) is READ.
- **smallest fix:** keep the tail only when page one overlaps it (`events.some(e => heldIds.has(e.id))`);
  otherwise take page one alone. In `asc`, take `hasMore` from page one (`firstPage.hasMore ||
  held.hasMore`). Merge against `state.inspector.events`, not `previous.events`, and have
  `loadMoreRunEvents` return early while `state.inspector.loading` is set. Add tests for the asc and gap
  cases. The current test (run-inspector.test.mjs "A REFRESH KEEPS THE OLDER EVENTS") covers only the
  overlapping newest-first case.

### C2. The C8 fix does not fix its own headline case: Settings stays on "Loading settings…"
- **where:** app.js:242 `renderSection('settings', [state.settings], renderSettings)`; slice-loaders.mjs:84-86
- **introduced:** pre-existing. The 0.7 fix `2f2b1e62` claims to close it and does not.
- **failure:** `/settings/schema` fails at boot and `/settings` succeeds, so the page renders "Loading
  settings…". The new retry refetches the schema and `adoptSettingsSchema` fills `SETTINGS_SCHEMA`.
  But the section signature is only `state.settings`, whose values have not changed, so `renderSection`
  skips the repaint. No tab is rendered to click, and navigating to the page does not call
  `renderSettings`. The page stays on "Loading settings…" until a settings value changes or the page
  is reloaded.
- **evidence:** RAN, against the real `render-memo` + `settings-panel`. After the failed schema, the
  form reads "Loading settings…". After the schema is adopted and the same values are re-rendered, it
  still reads "Loading settings…". Control: changing a settings value renders the tabs.
- **smallest fix:** `renderSection('settings', [state.settings, SETTINGS_SCHEMA.length], renderSettings)`,
  plus a test that drives the retry and checks what is painted, not only the slice list.

### C3. The Help tab still tells the operator to run a bare `aify-comms` as the managed-agent bridge
- **where:** index.html:482 ("Install a client, then run a bridge."), :488-490 (`cd /path/to/workspace-parent`
  / `aify-comms  # aify-comms.cmd on native Windows`), :507 ("Managed agents are run by a bridge")
- **introduced:** pre-existing. C6 (`949e25eb`) fixed the Environments page and missed this card.
- **failure:** the operator's Quick start on Settings → Help gives, as the managed-agent workflow, the
  command that exits 2 since v0.6.1 (install.sh:1470-1474, "this command starts nothing. The host tier
  is aify-env."). Before v0.6.1 the same paste reaped the fleet. The C6 test
  (environments-panels.test.mjs, "THE PAGE TELLS THE OPERATOR TO RUN aify-env") renders only
  `renderRuntime` and the roots editor, so it cannot see this card.
- **evidence:** READ.
- **smallest fix:** replace lines 488-490 with `aify-env doctor` / `aify-env`, matching
  `HOST_START_COMMAND` (environments-panels.mjs:337), and change "bridge" to "aify-env" at :482 and
  :507. Extend the C6 test to scan index.html for a line starting with `aify-comms`.

---

## P3

### C4. Failed `channels` and `files` fetches are still never retried: the new `failed.push` calls are unreachable
- **where:** refresh-cycle.mjs:163 and :184 (`catch (_) { … failed.push('channels'|'files') }`);
  message-transport.mjs:16 and shared-files.mjs:32 both catch their own errors and resolve
- **introduced:** 0.7.0 (`2f2b1e62`). The dead code is new. The swallowing is old, and
  shared-files.mjs:21-24 already says the poll's catch "can never run".
- **failure:** a `/channels` or `/shared` failure is marked current, so it is not retried while the
  socket stays up. The same holds on the partial path, because `SLICE_LOADERS.channels`/`files` never
  reject. The C8 test rejects `/settings/schema` and `/stats` only.
- **evidence:** RAN. With a failing fetch, `loadFiles()` and `chatLoadChannels()` resolve. Control:
  `chatLoadConversation()` rejects with the same stub.
- **smallest fix:** have both loaders rethrow after `noteSliceFailure` (keeping last-good state), or
  return a failure flag the two call sites read. Add `/channels` to the C8 test's reject list.

### C5. The channel add-member guard freezes the whole action bar, not just the select
- **where:** chat.js:158-162
- **introduced:** 0.7.0 (`fd35687b`, C10)
- **failure:** the operator picks an agent in "+ Add member…" and then, without pressing Add, clicks
  Leave or a member's ✕. The action succeeds, but while the select holds a value the bar is not
  repainted. It keeps showing **Leave** after leaving, a stale member count, and a ✕ chip for the member
  just removed. A member added by another client does not appear either. Only Add or switching
  conversations clears the value.
- **evidence:** READ.
- **smallest fix:** always paint, reading the select's value before the paint and restoring it after
  if that option still exists. Alternatively, guard on `document.activeElement === select` only.

### C6. The Sessions rail still drops keyboard focus on its own actions (C12/C14 only partly met)
- **where:** session-rail.mjs:114 and :140 (`paintIfChanged`); session-click-handlers.mjs (every
  handler calls `renderSessionWorkspace`); keyboard-shortcuts.mjs:57-60
- **introduced:** pre-existing, and not closed by `7f16601a`/`2712b0a7`.
- **failure:** pressing Enter on a row body, Space on a filter chip, or ticking a row checkbox changes
  the rail's HTML (active class, `checked`, chip state). The rail is rebuilt and focus drops to
  `<body>`. C14 named "a chip pressed with Space loses focus immediately" and it still does. The new
  guard only helps when nothing changed.
- **evidence:** READ.
- **smallest fix:** in the three handlers, record `document.activeElement`'s identifying `data-*`
  attribute before `renderSessionWorkspace()` and re-focus the matching node after it.

### C7. A failed agent Processes read now sticks until the agent changes
- **where:** agent-drawer.mjs:156-166 (`if (!refreshing || changed) loadAgentProcesses(...)`)
- **introduced:** 0.7.0 (`d5fbaa96`, C9)
- **failure:** one transient `/terminals` failure paints "could not load" into the panel. Refreshes now
  skip the read unless the drawer HTML changed. For an offline agent, which has no heartbeat to move
  `data-rel-ts`, the error stays until the drawer is closed and reopened.
- **evidence:** READ.
- **smallest fix:** also reload when the panel shows an error (for example a `data-error` marker set by
  `renderAgentProcesses`).

### C8. Stale provenance comments
- **where:** 19 modules still say their declarations are "byte-identical to the one that stood in
  app.js". The proof is retired, and several were edited in 0.7: agent-drawer.mjs:20,
  environments-panels.mjs, session-rail.mjs, identity-directory.mjs, inspector-forms.mjs,
  boot-wiring.mjs, chat-click-handlers.mjs. keyboard-shortcuts.mjs:4-12 says "three of its four rules"
  and "byte-identical", and it now has a fifth rule. environments-panels.mjs:325-328 leaves
  `rootsPlaceholder`'s JSDoc stacked above `HOST_START_COMMAND`'s.
- **introduced:** 0.7.0 made the claims false.
- **smallest fix:** delete the byte-identity paragraphs, and move the `rootsPlaceholder` JSDoc down to
  its function.

### Cross-lens note (not dashboard)
install.sh:1447-1448 and :1472-1473, the `aify-comms` launcher, and CLAUDE.md ("a new aify-env
supersedes the running one and reaps its managed workers") say a second `aify-env` stops the first
one's agents. aify-env 0.7.0 refuses that takeover when agents are running and opens the view instead
(`~/projects/aify-env/bin/aify-env.mjs:16-28, 630-660`); only an idle one is superseded. The
dashboard's new wording (environments-panels.mjs:362) matches aify-env. The installer and CLAUDE.md do
not.

---

## Checked and sound
- **XSS in the diff:** every interpolation added in 0.7 that is not wrapped in `esc()` was listed and
  traced. Each one is a number, a constant, already-escaped HTML (`relTimeHtml`, `dangerActions`, the
  analytics `note`), or text passed to `toast`/`uiConfirm`, which render with `textContent`/`esc`
  (ui.js:25, :56-59). The new error texts (analytics, run, files) are escaped.
- **Destructive confirmations:** every `uiConfirm` was listed. Stop and recreate now use the danger
  tone, and bulk actions confirm once with the button's own word. The only `confirmAction=false`
  caller is the bulk loop after its single confirm. With "Stop bridge" removed, no remaining emitter
  sends `data-env-control="stop"` (grep hit only `forget`), so dropping stop from the confirm guard in
  `controlEnvironment` is safe.
- **Raw JSON drawers are gone:** a `JSON.stringify(…, null, 2)` grep of non-test modules found nothing.
  Positive control: the same pattern matches v0.6.22's run-inspector.mjs and app.js.
- **C9 server half:** `_LISTING_COLUMNS` (routers/terminals.py:180-184) covers every column
  `_terminal_session_to_dict` reads except `output`. The columns it leaves out (`activity_*`,
  `start_intent`) are not serialized anyway. The spawn-request `truncated` flag is served
  (routers/spawn_requests.py:228).
- **C1 (settings tab switch), C2/C7 (paged-in messages through `message-store.mjs`, with `update` and
  `remove` keyed on the same `id` as `mergeById`), C3 history error path, C4 still-current checks, C5
  pulse, C11 analytics error state, C13 identity directory, C15, C16, C17, C18, C19 chip restore, C20
  filter drop and no poll-path toast, C21, C22, C23, C24, C26 double-submit:** the code for each was
  read at the tag. The tests I read call the real module and have a control: C1, C6, C8, C9, C12, C14,
  C17, C19, C23, C26 and the run-inspector C3/C4 tests. I did not read the tests for C5, C11 or C24. `paintIfChanged`'s root check correctly repaints after the
  direct `innerHTML` writes at chat.js:129/:140/:204.
- **Dead exports removed in 0.7** (`renderStatusDot`, `shouldRefreshInspector`, `ignoredReason`,
  `asyncAction`, `currentApiBase`, `continueCliCommand`, `record-lookup.mjs`,
  `environment-start-command.mjs`): no remaining importer. `no-dead-imports.test.mjs` passes (4 of 4,
  run at the tag).
- **File sizes:** the largest non-test dashboard module is chat.js at 544 lines. app.js went from 996
  lines (v0.6.22) to 416.
