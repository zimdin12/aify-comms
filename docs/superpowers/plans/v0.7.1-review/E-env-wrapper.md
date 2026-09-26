# v0.7.1 review E: aify-env v0.6.8..v0.7.0 and aify-wrapper 1946a9c..34b2a95

Reviewed at aify-env tag `v0.7.0` (commit c4f4608) and aify-wrapper `34b2a95` (the sha aify-comms
pins; the three rendered templates in `mcp/stdio/node_modules/aify-wrapper` are byte-identical to it).
Line numbers are at those revisions.

**Result: no P1. One P2, nine P3. The wrapper range is sound apart from one test gap.**

How it was checked:
- Read every lib/bin diff in both ranges.
- Ran the 30 new or changed aify-env test files against a `git archive v0.7.0` copy in a temp dir:
  255 pass. `herdr-client.test.js` was skipped because it spawns `bin/aify-env.mjs`.
- Ran 33 mutations of the fixes in that copy. 30 turned a test red. Three survived: M21 is
  equivalent (`HERDR_BIN_PATH` is short-circuited before the candidate list), and M24 and M30 are
  E5.
- Ran two library-level repros under the temp copy.
- Ran the aify-comms tests `resume-recovery-sends-the-key` and `codex-wrapper-behaviour -t CODEX_HOME`.
  Both use a sealed HOME and their own port-0 service, and both pass.
- Nothing contacted 8800 or 8802. No daemon, TUI or wrapper was run against the real HOME.

## aify-env

### E1 (P2): after a list flap, starting an agent reports a false failure for a different agent in `aify-env tui`
- **Where:** `lib/console-session.mjs:388-389` sets and clears `actionTargetId`, and `:514-517`
  resolves `perform` for any `confirmed:` **or** `chose:` action. `lib/dashboard.mjs` `actOn` now
  reports the outcome (commit 5035200).
- **Scenario:**
  1. The operator opens `m` on alpha.
  2. One poll gets no answer, so `processes` is `[]`. `reconcileFocus` closes the menu without
     clearing `actionTargetId`.
  3. Alpha is back on the next poll.
  4. The operator starts bravo with `s`, then Enter. The result carries both
     `startAgent: bravo` and `perform: {action: "start", process: alpha}`.
  5. `performClientAction` returns `false` for anything that is not `stop`, so NOTICES shows
     `start of alpha failed: the environment did not accept it` beside `starting bravo`.
- **Evidence (RAN):** a ConsoleSession repro printed exactly that line. The control, with no flap,
  printed `perform null`. The daemon's `onAction` returns `undefined` for anything that is not
  `stop`, so only the `tui` client prints the false line. No process is acted on.
- **Introduced:** the stale id is pre-existing; reporting it as a failure is new in range (F9).
- **Fix:** resolve `perform` only for `confirmed:` actions, because a start already travels on
  `startAgent`. Or clear `actionTargetId` in `syncProcesses` when the reconciled mode is neither
  `menu` nor `confirm`.

### E2 (P3): `aify-env tui` restores the screen only on `q`, SIGINT and SIGTERM
- **Where:** `bin/aify-env-tui.mjs:89` and `lib/dashboard.mjs:421`.
- **Scenario:** since F13 the view enters the alternate screen with the cursor hidden. Two other
  exits leave the operator's terminal there with no cursor:
  - Ctrl+Break on Windows. SIGBREAK has no listener in the tui, and Node's default is to terminate.
    The daemon does listen for it.
  - Any uncaught exception.
- **Evidence:** READ. That Ctrl+Break still raises SIGBREAK in raw mode is ASSUMED, not measured.
  This is the tui-client twin of the recorded "killed daemon leaves the alternate screen" item.
- **Introduced:** in range (cb56171).
- **Fix:** add `SIGBREAK` and `SIGHUP` to the tui's signal list. As a catch-all, have the view
  expose a synchronous `restoreScreen()` and call it from `process.on("exit")`.

### E3 (P3): quitting `aify-env tui` drops keys still queued for an agent
- **Where:** `bin/aify-env-tui.mjs:49-51` (`onQuit` calls `process.exit(0)` straight away) and
  `lib/client-input.mjs:25-38` (the per-agent `InputSender`s are not reachable from outside).
- **Scenario:** on a loaded host, the operator types a line into the pane, presses Ctrl+], then `q`.
  The coalesced remainder, often the Enter, is still queued behind the in-flight POST and dies with
  the process. This is the F22 defect that `attach` fixed, in the other client.
- **Evidence:** READ.
- **Introduced:** the sender is new in range. Quit never drained, before or after.
- **Fix:** have `createClientInput` return `{send, drainedWithin(ms)}`, and make `onQuit` await
  `drainedWithin(500)` across the senders before it exits.

### E4 (P3): the pane title says rows are cropped when none are
- **Where:** `lib/console-view.mjs:106`.
- **Scenario:** `rowsCropped` compares the PTY height (`screenRows`) with the pane height. But
  `screenLines` keeps the last *painted* rows. A 50-row PTY with 10 painted rows in a 22-row pane
  shows everything, and the title still says `50 rows, cropped`.
- **Evidence:** READ.
- **Introduced:** in range (027db27).
- **Fix:** derive the claim from what `screenLines` dropped, for example have it report the number
  of painted rows it cut.

### E5 (P3): two new call sites have no test
- **Where:** `lib/dashboard.mjs:351` passes `endsWithHerdr` to the renderer, and
  `lib/console-view.mjs` `composeConsole` passes `bodyHeight` to `paneTitle`.
- **Evidence (RAN):**
  - M24 deleted the first; all 3 tests in `a-herdr-instance-says-leaving-ends-its-workers` stayed
    green.
  - M30 deleted the second; all 4 tests in `the-pane-shows-the-bottom-of-a-tall-screen` stayed green.
  - Both helpers are tested directly (M23 and M30b go red), so the disconnected link is the one
    nobody watches.
- **Fix:** drive `startDashboard({endsWithHerdr: true, once: true})` and `composeConsole` with a
  tall `screenRows`, and assert on the output.

### E6 (P3): a commit claims a fix that a later commit undid
- **Where:** `lib/keys.mjs:569-583`.
- **Scenario:** e21cace says "put each doc comment on the function it describes". cf097d6, which
  came after it, inserted `isNavigationKey` between `splitKeys`'s doc block and `splitKeys`.
- **Evidence (RAN):** a scan for stacked `*/` + `/**` found keys.mjs:578 as the only instance in the
  range's files.
- **Fix:** move the block.

### E7 (P3): the "newest release" fallback picks the older version
- **Where:** `lib/herdr.mjs:30`. The same code is in aify-wrapper `lib/herdr-binary.mjs`.
- **Scenario:** releases are sorted lexicographically, so `v0.10.0` sorts below `v0.9.0`. This is
  reached only when `packages/standalone/current` is missing.
- **Evidence:** READ.
- **Introduced:** in range (98b8eb5). It is also a second copy of one function across two repos.
- **Fix:** a numeric-aware sort (`localeCompare(b, undefined, {numeric: true})`) in both copies,
  or one shared module.

### E8 (P3): "stopped X" is claimed without being verified
- **Where:** `lib/action-outcome.mjs:34` and `lib/daemon-view.mjs:158`.
- **Scenario:** `{ok: true}` means `runner.stop` resolved. `stop` releases the registry entry first
  and does not check `killTree`'s result, so a process that survived the kill leaves the list and
  NOTICES says `stopped`.
- **Evidence:** READ (`lib/runner.mjs:815-827`, `lib/kill-tree.mjs:75-96`).
- **Introduced:** the wording is new in range; the stop semantics are pre-existing.
- **Fix:** word it `stop sent to X`, or check `isAlive(pid)` after the kill.

### E9 (P3, UX): whether a paste is inert depends on where the reads split
- **Where:** `lib/console-session.mjs:322-327`.
- **Scenario:** a paste is inert only while each read holds more than a lone command key. A paste
  split so that one read is exactly `\r` attaches on the list. A read made only of `j`, `k` and
  arrows moves the selection.
- **Evidence:** READ. How often Windows console reads split a paste is ASSUMED, not measured.
- **Fix (low-hanging):**
  - Put `?2004h` in `ENTER_VIEW` and `?2004l` in `LEAVE_VIEW`.
  - Treat `ESC[200~…ESC[201~` as one inert chunk outside `pty`, and pass it through whole inside it.
  - Accept a lone `ESC` read as "back" in the menu, the start list and find. It is the key an
    operator reaches for first.

### E10 (P3): the detach reset misses two modes
- **Where:** `lib/attach-screen.mjs:40-42`.
- **Scenario:** `LOCAL_SCREEN_LEAVE` does not reset `?1004` (focus reporting) or `?1` (DECCKM).
  An agent that set either leaves the shell receiving `^[[I`/`^[[O` on focus change.
- **Evidence:** READ. Which agents set these modes is ASSUMED.
- **Fix:** add `?1004l` and `?1l`.

## aify-wrapper

### W1 (P3): the hermes lookup has no behavioural test
- **Where:** `wrappers/hermes-aify.sh.in:402`.
- **Scenario:** the change is correct by reading: `AIFY_SERVER_URL` is exported from
  `HARNESS_ENDPOINT` at line 335, before it is used. The other two launchers are covered:
  - Claude: `resume-recovery-sends-the-key` runs the real launcher on a keyed service, including
    the bound-endpoint case (RAN, green).
  - Codex: a regex pin, plus the CODEX_HOME behaviour test (RAN, green).
  - Hermes: no suite in either repo references hermes and `agent-for-handle` together (grep,
    positively controlled on the codex pin).
- **Fix:** add a hermes case to `resume-recovery-sends-the-key.test.js`.

No defect found in the three wrapper commits.

## Checked and sound

- **Ctrl+C:** it backs out of find, the menu and the start list. On the daemon's list it opens
  `confirm:shutdown`, and only a lone `y`/`Y` confirms it; `\x03\x03` in one read is inert.
  `reconcileFocus` keeps the prompt on an idle host. In `pty` mode Ctrl+C still goes to the agent.
  M9 and M12 went red.
- **Reads with several keys:** a read is split only when it holds nothing but navigation keys.
  The picker, the confirmation and the pane take a read whole, the picker drops a pasted `\r`,
  and `ok\r` is inert. M3 went red.
- **Keys reach the right agent:** an attached pane stays bound by id across list changes
  (pre-existing), and a retry keeps `watchedId`.
- **Attach detach:** the queue drains, bounded at 500 ms, and the terminal modes are reset on every
  `leave`. M1 and M2 went red, including through ConPTY. `postJson` throws on non-2xx.
- **UTF-8 across reads:** handled by a `StringDecoder` (M4). The attach path is binary per byte, so
  it is unaffected.
- **Alternate screen:**
  - It is entered only for a live view, not for `--once`.
  - `LEAVE_VIEW` is written once, on `stop` (M8 and M11).
  - The daemon's shutdown calls `stopView` first.
  - The view-only takeover path restores the screen through the tui's SIGINT handler.
- **Frame erase:** `width` strips SGR, and emitted SGR is semicolon-only
  (`lib/screen-style.mjs:117`). M5 went red.
- **Refresh, resize and stream retry:**
  - One collection runs at a time (M6).
  - The daemon view removes its resize listener (M7).
  - A FAILED stream is retried and the old follower and emulator are disposed first (M10). EXITED
    and GONE stay final.
- **Everything drawn is text and colour only:** notices, table cells and the pane's log path all
  pass through `displayable` (M13, M14 and M15). The caret's coordinates are relative to the
  viewport (M20 and M28).
- **Herdr:**
  - `herdrSpaces` is true only for a dedicated instance whose socket is its own
    (`paneOpenerFor:145-149`).
  - With `withoutConsole`, `p` and Enter attach are blocked, matching docs/HERDR.md.
- **Modified older tests:** none was weakened. Each change follows a behaviour that changed on
  purpose.
- **Wrapper:**
  - `HARNESS_ENDPOINT` resolves before the lookup, and the empty-endpoint guard (exit 78) runs
    first.
  - A missing or old bridge script fails silently into the store fallback (`|| _aify_rec=""`).
  - The MSYS `@@BRIDGE_DIR@@` path reaches node: the rendered-launcher test proves it.
  - A backslash `CODEX_HOME` works with `[ -d ]` and `find`, RAN on this host with a negative
    control.
