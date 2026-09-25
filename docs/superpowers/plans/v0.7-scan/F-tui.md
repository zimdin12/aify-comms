# F. TUI and console — v0.7 scan

Lens: the aify-env view (daemon screen and `aify-env tui`), its console pane, `aify-env attach`, the
Herdr adapters in aify-env and aify-wrapper, and the points where the aify-comms web console shares a
PTY with them.

Read at aify-env `9a8642a` (0.6.8) and aify-wrapper `1946a9c`. Everything was read-only. Probes were
scratch scripts that imported pure `lib/` modules (never `bin/aify-env.mjs`) and called them with
literals. Each probe ran a control in the same run. The ten TUI unit files all pass on this tree
(tui 32, keys 45, console-session 43, frame 9, pane-buffer 37, screen-render 11, text-width 23,
keyboard-poisoned 17, daemon-view-keyboard 16, keys-documented 12). So every finding below is a gap
the tests do not cover, not a regression.

Evidence levels: **PROVEN** means a probe ran against the real module and showed the behaviour.
**READ** means the code path was traced, file:line cited, and not executed. **ASSUMED** means it is
inferred, usually about terminal or runtime behaviour outside this code.

---

### F1. In the daemon's own view, one Ctrl+C from any mode except confirm stops the environment and every managed worker
- where: aify-env/lib/keys.mjs:315, 380, 405, 450; lib/daemon-view.mjs:111; lib/dashboard.mjs:508; lib/tui.mjs:700-701; lib/usage.mjs:77-78
- failure/cost: In the daemon's terminal, Ctrl+C is mapped to `shutdown("keyboard")`, which takes down every managed agent on the host. This is by design in dashboard mode. But the same thing happens in `menu`, `start` and `picker`, which are exactly the modes where an operator reaches for Ctrl+C to *back out*. The on-screen hint for those modes offers `ctrl+] back`. Nothing on the daemon screen says what Ctrl+C does there, and only `--help` mentions it. There is no second keystroke and no prompt. The `confirm` mode already treats Ctrl+C as cancel, which shows the safer mapping exists.
- evidence: PROVEN. `routeKey("\x03", …)` per mode returned `interrupt` for dashboard, picker, menu and start, `confirm-cancel` for confirm, and forward-to-process for pty (probe7). `daemon-view.mjs:111` maps `onInterrupt` to `shutdown?.("keyboard")`. `dashboard.mjs:508` calls it with no guard. `tui.mjs:700-701` adds no Ctrl+C hint when `canQuit` is false, which is the daemon case.
- severity: P1
- effort: S
- fix sketch: In `menu`, `start` and `picker`, route Ctrl+C to the mode's own close action, as `confirm` already does. In dashboard mode on the daemon, make Ctrl+C a question: "stop the environment and its N workers? y". Also show a dim `ctrl+c stops everything` hint when there is no `onQuit`. Signal-delivered SIGINT keeps its current meaning.

### F2. `aify-env tui` still sends each keystroke as its own concurrent HTTP request, so typed text can arrive scrambled
- where: aify-env/bin/aify-env-tui.mjs:52-66; lib/dashboard.mjs:518; the fix that already exists: lib/input-sender.mjs:1-18, used only by bin/aify-env-attach.mjs:152-158
- failure/cost: The operator reported "text is scrambled (letters in wrong place)" on 2026-09-19. The root cause was independent fire-and-forget POSTs, and it was fixed for `attach` with `InputSender` (one request in flight, the rest coalesced). The `tui` client's `onInput` is still a bare `fetch(... /input)` per chunk, and `dashboard.mjs:518` does not await it. Under load, keys typed into an attached pane in `aify-env tui` can reach the agent out of order. The daemon's own view is not affected, because it calls `runner.write` synchronously.
- evidence: READ. `aify-env-tui.mjs:52-66` has an async fetch per call and no queue. `tests/typed-input-arrives-in-the-order-it-was-typed.test.js:1-16` documents that this send shape scrambles, and proves it with a control. That test covers `InputSender` only, and no test references the tui client's input path.
- severity: P1
- effort: S
- fix sketch: Build one `InputSender` per attached target in `aify-env-tui.mjs` and route `onInput` through it. Reuse `attach`'s socket fast path if `health.inputSocket` is present.

### F3. The pane's log path copies a process's raw non-colour escapes onto the operator's terminal
- where: aify-env/lib/pane-buffer.mjs:344-345; lib/text-width.mjs:33, 276-329; lib/process-registry.mjs:51
- failure/cost: When a stream's retained lines hold no cursor-move sequence, the pane prints the raw lines through `clipToWidth`. That function only understands SGR colour codes. So alternate-screen switches, full reset, scroll-region setting and clipboard-write escapes pass straight through to the operator's terminal. Each is re-sent whenever that row changes. The result is a flipped or reset screen that the differential writer no longer models, and an agent can write the operator's clipboard. This happens even when an emulator exists, because `view()` takes the log path for any stream it does not classify as painting.
- evidence: PROVEN (probe1). A plain line came back as plain text (control), and a cursor-move line was refused with the TUI notice (control). But `hi ESC[?1049h there`, `x ESC]52;c;…BEL y`, `before ESCc after` and `r ESC[1;5r s` all came back with the escape intact in the pane lines.
- severity: P2
- effort: S
- fix sketch: On the log path, strip everything except text and SGR, using the same regexes `cleanTail` and `sanitizeTitle` already use in process-registry.mjs. Or treat any non-SGR CSI/OSC/ESC-x as painting, so the stream goes to the emulator, which never emits escapes it did not build.

### F4. A notice containing a newline splits one frame row into several and throws off every row below it
- where: aify-env/lib/notices.mjs:31-42; lib/plugins/aify-comms/api.mjs:127-131; lib/tui.mjs:798-806
- failure/cost: `CommsApiError` includes up to 300 characters of the HTTP response body, and that message reaches the NOTICES section through `logLine`. A proxy's HTML error page, or any multi-line body, puts a raw `\n` inside one frame line. The frame writer addresses rows absolutely, so the screen moves out from under its model. This is the 2026-09-04 layout breakage again, arriving through the channel that was built to fix it. Titles are sanitized (`sanitizeTitle`) but notices are not.
- evidence: PROVEN (probe2). A single-line notice gave 0 frame lines containing `\n` (control). `POST /x -> 502: <html>\n<body>…` gave 1 frame line containing embedded newlines.
- severity: P2
- effort: S
- fix sketch: In `notices.add`, collapse whitespace and drop C0/C1/ESC, as `sanitizeTitle` does. Apply the same guard in `table()` for every cell, so the next unsanitized producer cannot do this again.

### F5. The pane cuts off the bottom of an agent's screen, where the input box is, and does not say so
- where: aify-env/lib/screen-render.mjs:47-50; lib/console-view.mjs:69-72; lib/console-session.mjs:608; lib/runner.mjs:112
- failure/cost: The emulated screen is sliced from the top (`all.slice(0, limit)`). The default PTY is 30 rows (runner.mjs:112) and the pane body is the terminal's rows minus 2, so on an ordinary terminal the last rows are dropped: prompt, status line, input box. A web console that resizes the PTY taller makes the loss larger. The title reports a column crop but never a row crop. When attached, the title says "typing here" while the line being typed into is off screen. The code comment at screen-render.mjs:47-49 already calls this open.
- evidence: PROVEN (probe8). With a 40-row screen, text on row 1 and a prompt on row 39, a 20-row pane returned only `["old transcript line"]`. The 40-row control returned the prompt.
- severity: P2
- effort: M
- fix sketch: Pass the producer's rows into `pane()` and crop to the bottom `height` rows, or to a window around the emulator's cursor row, at least when attached. Add `· N rows, cropped` to `paneTitle`, the same way the column crop is reported.

### F6. The pane title says "typing here" while keystrokes are being silently dropped
- where: aify-env/lib/console-view.mjs:60; lib/console-session.mjs:419-421, 550-553, 564-585; lib/tui.mjs:661-662
- failure/cost: Input is correctly gated on a clean streaming frame. But when the drawn frame is a refusal notice, the mode stays `pty`, and both the title (`· typing here`) and the hint (`keys go to this agent`) still claim input is live. Every key is discarded. Examples of refusal notices: waiting for a full repaint, concealed output, a TUI with no emulator, a failed stream. The operator types a message, sees nothing happen, and the text is gone.
- evidence: PROVEN (probe3). The control (clean frame) showed title `> alpha · typing here …` and forwarded `x`. With a refusal frame the title was identical, `toPty` was `null`, and there was no action.
- severity: P2
- effort: S
- fix sketch: Add an `inputLive` flag to `pane()`. When `attached && !inputLive`, the title reads `· input paused: <reason>` and the pty hint says so. Alternatively, drop back to dashboard mode when a refusal frame is drawn, which is what `notePaneRendered(false)` already does for an undrawn one.

### F7. A failed console stream is never retried; the pane stays "unavailable" until the operator moves off and back
- where: aify-env/lib/console-session.mjs:211-213; lib/output-follower.mjs:171-176, 451-457
- failure/cost: `syncProcesses` returns early whenever the selected id equals the watched id, whatever the follower's status. Once a follower is FAILED it stays failed for good: a daemon restart, a dropped connection, or "the stream ended without an exit". The pane shows `unavailable: …`, the stream is not re-opened, and an attached pane stays in `pty` mode with "typing here" (see F6). Recovery requires knowing to press `j`/`k` or `p` twice.
- evidence: PROVEN (probe4). After ten refreshes with a FAILED follower, `makeFollower` had been called once. The control, moving the selection, created a new follower.
- severity: P2
- effort: S
- fix sketch: In `syncProcesses`, if `follower.status === FAILED` and the row still exists, close it and reopen with a small backoff. GONE and EXITED stay terminal.

### F8. Reopening the start list draws the previous answer as current, and Enter starts from it
- where: aify-env/lib/console-session.mjs:330-334; lib/tui.mjs:162-177; lib/keys.mjs:384-389
- failure/cost: The comment says opening the list marks it unanswered so that stale rows are not chosen ("an agent that came up ten minutes ago"). Only `startAsked` is reset. `startable` and `startCount` keep the old rows. The renderer consults `asked` only when the list is empty, so stale rows are drawn with no "asking" marker, and Enter before the new answer lands starts the stale row. README.md:190-195 says a launch "replaces any leftover or unreported instance of that agent still running on this host". So acting on a stale row is not harmless. That consequence is ASSUMED, because it depends on the service's view at that moment.
- evidence: PROVEN (probe5). After open, answer, close and reopen, `asked:false`, the frame drew `❯ old-agent  offline` with no "asking", and Enter returned `startAgent: {id:"old-agent"…}`.
- severity: P2
- effort: S
- fix sketch: On `start-open`, clear `startable` and set `startCount` to 0, so the list shows "asking…" and Enter does nothing until the answer arrives. Or keep the rows dimmed with `(refreshing)` and refuse `chose:start` while `!startAsked`.

### F9. Starting and stopping from the view never report the outcome
- where: aify-env/lib/dashboard.mjs:494-496, 505; lib/daemon-view.mjs:134-150; bin/aify-env-tui.mjs:74, 82; lib/client-actions.mjs:89-129; lib/plugins/aify-comms/agent-starter.mjs:91-136
- failure/cost: `s`, choose, Enter closes the list, and then nothing. The service's refusal comes back as `{started:false, problem}` (agent-starter.mjs:93) and is discarded: `.catch(() => {})` with the resolved value unread. The same is true of `startKnownAgent`, and `performClientAction` returns a boolean nobody reads. A daemon-side stop failure is swallowed (daemon-view.mjs:149). The operator cannot tell "refused because it has a live session" from "starting", or "stop failed" from "stop is slow".
- evidence: READ. At each call site above the promise result is dropped. There is no `log`/notice in `agent-starter.start`.
- severity: P2
- effort: S
- fix sketch: Send every start or stop outcome to `notices`: "starting X", "start refused: <problem>", "stopped X", "stop failed: <reason>". The client gets the same through a local notice ring. Also show a transient "starting X…" line under the table until the next roster shows it.

### F10. Detaching from `aify-env attach` leaves the local terminal in the agent's modes
- where: aify-env/bin/aify-env-attach.mjs:116-132; lib/attach-screen.mjs:10-11, 24
- failure/cost: `restore()` turns off raw mode and writes nothing else. Anything the agent turned on stays on in the operator's shell after Ctrl+] or after the agent exits: alternate screen, hidden cursor, mouse tracking, bracketed paste, keyboard-protocol flags. The shell then prints onto the alt screen, the cursor stays invisible, and clicks type escape junk. attach-screen.mjs:11 acknowledges this ("detaching restores raw mode, not the screen") and repairs it only on the next attach.
- evidence: READ. restore() is at lines 117-126, and `leave()` calls it on every exit path (165, 184, 199, 218-222). None of them write a reset.
- severity: P2
- effort: S
- fix sketch: In `restore()`, write a leave sequence: `ESC[?1049l ESC[?25h ESC[?1000l ESC[?1002l ESC[?1003l ESC[?1006l ESC[?2004l ESC[<u ESC[0m`. It is harmless when a mode was not set.

### F11. An attached pane shows no cursor
- where: aify-env/lib/screen-emulator.mjs:153-202; lib/frame.mjs:56-58, 77
- failure/cost: The pane is rebuilt from cells and never draws the emulator's cursor, and the terminal cursor is parked below the frame at column 1. For a runtime that relies on the hardware cursor for its input line, the operator types into a pane with no visible caret. That runtime behaviour is ASSUMED for codex and hermes: Claude Code draws its own inverse block, the others do not.
- evidence: READ. A grep for `cursorX|cursorY` across lib/ returns nothing. `frameUpdate` always ends with `moveToRow(after.length + 1)`.
- severity: P2
- effort: S
- fix sketch: When `pane.attached`, render the cell at `buffer.active.cursorX/cursorY` in inverse, or, after writing the frame, move the hardware cursor to the pane's offset plus the cursor position.

### F12. When there are 0 or 1 processes, the height fitting skips the notice cap, so the frame overruns and is cut from the bottom
- where: aify-env/lib/tui.mjs:876-879; lib/console-view.mjs:194-198
- failure/cost: The early return at 879 (`all.length <= 1 && starting <= 1`) runs before the notice-trimming loop at 888. On an idle host (the common case) with a burst of notices, the frame is longer than the terminal. `composeConsole`'s `fit()` then cuts the bottom, so NOTICES reads "20 recent" while fewer are drawn, and TRAFFIC disappears.
- evidence: PROVEN (probe1). With 20 notices on a 24-row terminal, 0 processes gave 28 lines and 1 process gave 29. The control with 2 processes gave 24.
- severity: P3
- effort: S
- fix sketch: Run the notice loop before the process-count early return. Only the table-windowing passes need `all.length > 1`.

### F13. The view uses no alternate screen and never hides the cursor; full-width rows may lose their last character
- where: aify-env/lib/frame.mjs:28, 51-58, 62-67, 77; lib/tui.mjs:326-332
- failure/cost: The first frame erases the operator's visible screen instead of switching to an alternate one. The cursor stays visible and moves across every repainted row, at about 12 fps while a pane streams. Every heading rule is exactly `columns` wide and is followed by `ESC[K`. On xterm-style terminals, erase-line in the pending-wrap state erases the cell in the last column, so the rule loses its final glyph. That terminal behaviour is ASSUMED, not observed on this host.
- evidence: READ. A grep for `?1049h|?25l` in lib/bin finds only attach's leave sequence. `heading()` sizes the rule to `columns - used`.
- severity: P3
- effort: S
- fix sketch: Enter `ESC[?1049h ESC[?25l` on the first frame, and leave with `ESC[?25h ESC[?1049l` in `stop()`. Keep rows at `columns - 1`, or write `ERASE_LINE` only when the new row is shorter than the old one.

### F14. Keys that arrive coalesced in one chunk are dropped outside the picker
- where: aify-env/lib/keys.mjs:382-383, 406-407, 455-456
- failure/cost: Routing compares a whole chunk against one key (`chunk === DOWN`). A held arrow or a quick `jj` that arrives as one read on a busy daemon does nothing. keys.mjs:331-335 says this coalescing is ordinary on this daemon, and the picker already handles it. Dashboard, menu and start modes do not.
- evidence: PROVEN (probe7). `ESC[B` moved the selection and `ESC[BESC[B` returned null with the selection unchanged. `j` moved and `jj` did not.
- severity: P3
- effort: S
- fix sketch: Outside `pty` mode, split the chunk into keys (escape sequences whole, other characters one at a time) and route each. In `confirm`, keep treating a multi-key chunk as cancel.

### F15. Ctrl+L, the only way to recover a smeared screen, is not documented anywhere
- where: aify-env/lib/keys.mjs:60-63, 453; lib/usage.mjs:68-81; README.md:165-174; lib/tui.mjs:655-690
- failure/cost: Several paths in this file can smear the screen: F3, F4, a stray stderr write, an external resize. The recovery key is Ctrl+L. It is absent from `--help`, from the README key table and from every hint line.
- evidence: READ. A grep for `ctrl+l|repaint` in README.md and usage.mjs finds nothing.
- severity: P3
- effort: S
- fix sketch: Add `Ctrl+L  redraw the screen` to usage.mjs and the README table, and add it to VIEW_KEYS so the key-documentation test enforces it.

### F16. With the daemon not answering, PROCESSES claims "nothing started since this environment came up"
- where: aify-env/lib/dashboard.mjs:169; lib/tui.mjs:526-537
- failure/cost: When `/health` fails, `history` defaults to `{startedTotal: 0}`, and the idle branch then states a fact about an environment that did not answer: "no spawn has reached it yet". The header does say "not answering", but the PROCESSES section contradicts it. This is the false-green pattern the file warns about.
- evidence: PROVEN (probe6). A `collectSnapshot` whose fetch throws ECONNREFUSED rendered `◌ nothing started since this environment came up — no spawn has reached it yet`.
- severity: P3
- effort: S
- fix sketch: Carry an `answered` flag in the snapshot. When it is false, PROCESSES says "unknown — the environment is not answering".

### F17. The menu and the confirmation do not say what an action does
- where: aify-env/lib/tui.mjs:603-616; lib/keys.mjs:91
- failure/cost: The menu rows are bare verbs (`attach`, `stop`), and the prompt is `stop <name>? y to confirm`. It does not say that stop kills the process tree mid-turn, or that the service may respawn a managed agent. The operator has to know what stop means in each tier.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: Add one-line descriptions next to the menu rows, and state the consequence in the confirm line, for example `stop alpha? ends its process tree now (its turn is lost)`.

### F18. Several clients resize one PTY: last writer wins, and nothing is restored on detach
- where: aify-env/bin/aify-env-attach.mjs:188-205; lib/herdr.mjs:112 (Herdr panes run `attach --id`); lib/plugins/aify-comms/terminal-controls.mjs:339-349 (the web console's resize)
- failure/cost: An attach from a small terminal, a Herdr worker pane and the aify-comms web console each resize the same PTY. The other viewers then show a screen wrapped at someone else's width until the agent repaints. The impact on the others is ASSUMED, not reproduced. The same applies to input: there is no arbitration between an operator typing in attach and a service `input` control, so the two could interleave (ASSUMED).
- evidence: READ. Three independent resize paths exist, and none records or restores the previous geometry.
- severity: P3
- effort: M
- fix sketch: Decide a geometry policy, for example the smallest active viewer or the most recent focused one, and restore on detach. At minimum, show "resized by another viewer" in the pane title.

### F19. aify-env and aify-wrapper each have their own Herdr detector, and they disagree
- where: aify-env/lib/herdr.mjs:9-15, 24-37; aify-env/bin/aify-env-herdr.mjs:11; aify-env/docs/HERDR.md:23-33; aify-wrapper/lib/herdr-binary.mjs:36-65, 80-90; aify-wrapper/HERDR.md:79-84, 254-256
- failure/cost: aify-env's `detectHerdr` checks only PATH and `%LOCALAPPDATA%/Programs/Herdr/bin`. aify-wrapper checks `HERDR_BIN_PATH`, the `HERDR_HOME` standalone package, `HERDR_INSTALL_DIR`, then PATH. aify-wrapper's HERDR.md says Herdr is usually NOT on PATH at an ordinary Windows prompt. The two repos also disagree on the socket. aify-env maps a bare name to `\\.\pipe\…` and documents that example. aify-wrapper measured Herdr refusing a named pipe ("PermissionDenied"). So `aify-env herdr` can fail to find a Herdr that `herdr-aify` finds.
- evidence: READ. The two detectors are cited above. Which socket form Herdr 0.9.0 accepts is ASSUMED from aify-wrapper's recorded measurement.
- severity: P3
- effort: S
- fix sketch: Keep one resolver: aify-env imports or ports aify-wrapper's `resolveHerdrBinary`. Fix the named-pipe example in docs/HERDR.md, or make `socketPath` refuse the form Herdr cannot use.

### F20. aify-env's Herdr docs describe the integration as unbuilt, but it ships
- where: aify-env/README.md:97-103; aify-env/docs/HERDR.md:5-9, 51; against aify-env/lib/herdr-pane-opener.mjs:15-50, lib/daemon-view.mjs:75-78, 121-122; aify-wrapper/HERDR.md:7-9, 340-350
- failure/cost: The README says the "env-first workspace … automatic workspace lifecycle … are not implemented", and docs/HERDR.md says "No … service start picker … or automatic Herdr TUI launch is provided". In fact `herdr-aify env` runs the daemon in space 1, the daemon opens and closes a pane per worker, the view has `s` start, and the launcher attaches the Herdr TUI. An operator reading aify-env's docs gets the wrong model of what the keys and spaces do.
- evidence: READ. The cited lines conflict with each other.
- severity: P3
- effort: S
- fix sketch: Rewrite README §"Optional Herdr attachment" and docs/HERDR.md to describe the two paths (`aify-env herdr` attach adapter, `herdr-aify env` instance) and point to aify-wrapper/HERDR.md.

### F21. In `herdr-aify env`, leaving the Herdr session ends every managed worker, and the only warning is covered at once
- where: aify-wrapper/bin/herdr-aify.mjs:560-569
- failure/cost: This is the intended lifetime, and it is the operator's ruling. But a Herdr detach, which operators do by muscle memory in the resident mode where it is harmless, here tears down the dedicated env and all its workers. The warning is written to stderr immediately before the Herdr TUI takes the screen. That it covers the warning is ASSUMED.
- evidence: READ. `attaching` prints the warning, then `Promise.race([client.exited, …])`, then `shutdown(0)`.
- severity: P3
- effort: S
- fix sketch: Put the warning where it stays visible, for example in the daemon pane's header, by passing an instance-context flag to the view: "leaving this Herdr ends N workers". Or confirm on client exit when workers are live.

### F22. `aify-env attach` can drop keystrokes silently on detach and on send failure
- where: aify-env/bin/aify-env-attach.mjs:53-65, 128-132, 152-169; lib/input-sender.mjs:46-50, 53-72
- failure/cost: Ctrl+] calls `leave()`, which calls `process.exit` without awaiting `input.drained()`. That method's own doc says it exists "for a clean detach", but nothing in bin/ or lib/ calls it. Text typed just before detaching can be lost while a send is in flight, which is the loaded-host case InputSender was built for. `post()` swallows every error, so `InputSender.failed` can never count anything and a lost keystroke leaves no trace.
- evidence: READ. A grep for `drained()` outside input-sender.mjs finds only tests, which confirms the grep works and that no bin/lib caller exists.
- severity: P3
- effort: S
- fix sketch: `await input.drained()` with a short bound (for example 500 ms) before `restore()` on the detach path. Let `post` throw on network errors and non-2xx, and print the failed count at detach.

### F23. The view's key input decodes each stdin chunk separately, so a character split across chunks becomes U+FFFD before reaching the agent
- where: aify-env/lib/dashboard.mjs:470-475; lib/daemon-view.mjs:114-116
- failure/cost: stdin is never given an encoding, and `String(chunk)` decodes each Buffer on its own. A multi-byte character split across a read boundary, as in a large non-ASCII paste into an attached pane, reaches `runner.write` as replacement characters. `attach` avoids this with `binary`. How often this happens is ASSUMED to be rare.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: `input.setEncoding("utf8")`, which uses a StringDecoder across chunks, or keep one `StringDecoder` in `startDashboard`.

### F24. Dead constants and misplaced doc comments in the TUI modules
- where: aify-env/lib/panes.mjs:23-25 (`ESC`, `SGR`, `RESET` unused); lib/tui.mjs:83 (`SGR_RESET` unused), 97-104 (activityMark's doc sits above `rowMarker`), 271-273 (heading's doc above `exitDescription`), 648-654 (indentation broken), 828-834 (windowAround's doc above `DEFAULT_NOTICE_ROWS`); lib/console-session.mjs:243-247 (handleInput's doc above `noteStartable`), 322-329 (menu comment above the start-open line); lib/console-view.mjs:146-151 (composeConsole's doc above `paneWillBeDrawn`); lib/pane-buffer.mjs:31-37 (documents a constant that was deleted), 319-320, 335-343 (comments describing logic that now lives in `refusalReason`)
- failure/cost: A reader, or an agent, attaches each doc to the wrong function. Stale comments describe a 30-second window and a log-path refusal that are not where the comment says.
- evidence: READ. Unused-constant check: `grep -w` counts showed the constants appear only at their definitions or in comments.
- severity: P3
- effort: S
- fix sketch: Delete the dead constants and move each doc comment to the function it describes.

### F25. Shape: one ~460-line render function, and three host files within 30 lines of the 1000-line gate
- where: aify-env/lib/tui.mjs:368-826 (`drawDashboard`, file 913 lines); lib/keys.mjs:283-515 (`routeKey`, ~230 lines); lib/console-session.mjs:292-477 (`handleInput`, ~185 lines); bin/aify-env.mjs 984, lib/runner.mjs 979, lib/plugins/aify-comms/terminal-controls.mjs 973
- failure/cost: Each section of `drawDashboard` (header, services, health, processes, menu, hints, exits, notices, traffic) is its own concern, and several of the defects above sit in its seams (F12). The next TUI change to the daemon wiring will push `bin/aify-env.mjs` over the gate.
- evidence: READ (`wc -l`).
- severity: P3
- effort: M
- fix sketch: Split `drawDashboard` into one pure function per section that returns lines, with `renderDashboard` composing and fitting them. Split `routeKey` into a table of per-mode routers. Take a subject out of `bin/aify-env.mjs` before it reaches the gate.

### F26. Minor lifecycle loose ends
- where: aify-env/lib/daemon-view.mjs:157-161; lib/dashboard.mjs:115-118, 458
- failure/cost: (a) The daemon view's stdout `resize` listener is never removed; `stop()` returns `view.stop` only. It is harmless today because stop happens only at exit. (b) `draw()` has no in-flight guard. Service probes run in sequence with 1.5 s timeouts, so with two services down one collection outlasts the 2 s interval, collections overlap, and an older snapshot can paint after a newer one. The timing is ASSUMED, not measured.
- evidence: READ.
- severity: P3
- effort: S
- fix sketch: Return a stop that also removes the resize listener. Skip a tick while a `draw()` is in flight, or probe services in parallel.

---

## Checked and clean

- **Wrong-agent input on list shifts:** `syncProcesses` re-points an attached pane by identity and detaches when the identity is gone (console-session.mjs:184-193). Menu actions and menu-Attach resolve by `actionTargetId` and refuse when it is gone (373-382, 459-463). The confirm prompt names the bound target, not the cursor (tui.mjs:595-607). These are covered by the passing keyboard-poisoned and console-session suites.
- **Destructive actions:** stop needs the menu, a choice, and `y` from a single-key chunk. Every other key, including Ctrl+C and `yy`, cancels (keys.mjs:353-366). `restart` is not offered by either tier (daemon-view.mjs:121, client-actions.mjs:15).
- **Input readiness gate:** keys reach a process only when the pane is wide enough, a streaming frame was drawn, and nothing was refused (console-session.mjs:564-585). Narrowing and an undrawn frame both revoke attach (494-506, 533-554). Only the labelling is wrong (F6).
- **Detach byte:** only an exact `0x1d` chunk detaches, so a paste containing 0x1d reaches the process (keys.mjs:294; attach 164).
- **NaN or pasted-digit poisoning of the selection:** guarded (keys.mjs:463-471, 229-230).
- **Hint-line honesty per mode:** `q quit` is shown only where it quits (tui.mjs:700), and whole items are dropped by priority (233-244).
- **Emulated screen:** concealed cells become spaces of the right width, continuation cells are skipped, and SGR is emitted only when it changes, with a reset at end of row (screen-emulator.mjs:153-202, screen-style.mjs). Resizes are ordered against writes (output-follower.mjs:302-327). The backlog is released on every no-screen path (222-226, 249-259). Disposal advances the generation so late callbacks are ignored.
- **Width:** grapheme-clustered, emoji-presentation and keycap aware. Clipping never splits an escape or a cluster and closes open SGR (text-width.mjs). The width cache is bounded, including against sliced-string retention.
- **Differential frame writer:** the first frame clears, unchanged rows are untouched, a shorter frame erases below (frame.mjs). A failed write does not cache the frame (dashboard.mjs:396-410).
- **Titles from processes:** sanitized of C0/C1/ESC and length-capped (process-registry.mjs:168-177).
- **A TUI failure cannot take the daemon down:** the view start is wrapped in try/catch (daemon-view.mjs:97-166), the keypress handler catches (dashboard.mjs:519), repaint swallows (446-450), the refresh loop catches (458), and follower progress never throws into the read loop (output-follower.mjs:425-431). Timers are unref'd, and `stop()` clears the interval, the pane timer, the follower and the raw mode (dashboard.mjs:543-566).
- **Service or daemon down:** the start list reports a problem rather than "nothing to start" (tui.mjs:157-176, client-actions.mjs:53-77). The header says "not answering" (dashboard.mjs:144), apart from the PROCESSES wording in F16.
- **Hidden pane costs nothing:** no follower, emulator or buffer is opened while the pane is hidden or undrawable (console-session.mjs:199-213).
- **Attach target resolution:** an exact id wins, an ambiguous label is refused with the ids listed, and an id-less entry is not a target (attach-target.mjs).
- **Attach handshake:** the data listener and sender exist before `stdin.resume()`, so keys typed during the socket connect go over HTTP rather than being lost (aify-env-attach.mjs:134-171).
