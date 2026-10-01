# Known Issues & Concerns — aify-comms

What is open now: known limitations, deferred work, and things to watch. Complements
[DECISIONS.md](DECISIONS.md) (rationale) and the `aify-comms-debug` skill (troubleshooting). Resolved
and superseded entries are in [docs/history/KNOWN_ISSUES-archive.md](docs/history/KNOWN_ISSUES-archive.md),
kept as evidence. Last reviewed 2026-09-27.

## Left open by the external review of 0.7.1 (2026-09-26)

0.7.2 fixed the review's two security findings and items 3 and 6, and changed items 4 and 5 as far as
this host can prove: item 4's explicit-reply path is fixed and handling without a reply stays open, and
item 5's hook output is the shape Codex documents, not yet seen in a live Codex context. The plan
(`docs/superpowers/plans/2026-09-26-v0.7.1.md`, "0.7.2") lists each. These remain:

- **With no `OPERATOR_KEY`, any holder of the shared API key can send as `dashboard`, which skips the
  trust rule.** That is the v0.7.5 ruling (the API key is the trust boundary). An external key cannot
  send as it (`refuse_external_impersonation`, `service/api_core/external_keys.py`), and the dashboard
  page, which carries the operator key, needs the API key (`service/dashboard_access.py`). With
  `OPERATOR_KEY` set, no route accepts the name `dashboard` or `operator` without
  `X-Aify-Operator-Key`, checked before anything is looked up (since the review of 0.7.6; before it the
  key gated only unsend, channel delete and unshare). `test_no_route_accepts_an_unproven_operator_name.py`
  derives the routes from the app, so a new one cannot miss the check. An omitted name counts as
  `dashboard` where a route records it so (`recorded_operator_actor`); on a run control and a
  session-handle update it is an agent's ordinary call and is not the operator. The key proves the name,
  not the right to act: any API-key holder can stop or restart a worker under its own name. No agent
  id may be `dashboard` or `operator`, key or not (`refuse_a_reserved_agent_id` on register, rename and
  spawn): a rename to `dashboard` rewrote an agent's history to read as the operator's.
- **By design: a silent (inbox-only) message stays unread until `comms_inbox` reads it**, so a new
  session is shown it again. A message that woke a run is read when the run claims it, and a reply marks
  the message it answers read (0.7.2); the skill says so (0.7.4).
- **A claim attempt already running when its caller leaves still commits.** Since the 0.7.6 review (O1)
  the five claim long-polls read `http.disconnect` from `receive()` (`service/longpoll.py`): a caller
  that goes ends the wait, and no attempt runs after that. The attempt itself is one transaction of a
  few milliseconds, and a caller leaving inside it is not seen.
- **A late turn-end is matched to its turn only once the bridge is reinstalled** (0.7.6 review O2).
  `/turn-end` with a `runId` clears only when that run's turn is the open one in both
  `agent_turn_state` and `agent_status_state`; hermes' `clearTurn` sends the run it ends. The claude
  transcript, codex and resident-hermes detectors, hermes' clears and the heartbeat that starts a turn
  now stamp `firedAtUs` and `machineId`, so `api_core/hook_event_order.py` orders them with the hooks.
  An end carrying neither still clears whatever is open, which is every bridge installed before this,
  and `turn-busy-heartbeat.js`'s keep-alive `/turn-start` is still unstamped. A stamp is taken when the
  bridge posts, milliseconds after its read, so an end read just before a start and stamped just after
  it still wins. A stamp from a bridge whose machine id differs from the agent's registered one is
  refused, as a hook's is. Proven at the service and on the bridge's wire bodies in tests; not observed
  on a live host.
- **Every turn stamp is the host's wall clock, so a backward step of it can refuse an event** (0.7.6
  review). An event fired after the clock stepped back carries a time below the last one applied and
  is refused as out of order until the clock passes that time again; the refusal is logged
  (`hook_event_order.py`). The review measured one 347 ms backward step in 150 s on a WSL host and none
  in a 600 s run.
- **Where the hook shell has no `$EPOCHREALTIME` and `date` has no `%N`** (macOS's BSD `date`, most
  busybox builds), the hook passes no usable time and `agent-state-event.mjs` falls back to when node
  started, 32-160 ms late, as every shell did before 0.7.6. The parser's rejection of `%N` left
  unexpanded is tested; neither system has been run.
- **Fixed in aify-env fbc4373: a paste that pauses stays one paste.** Before, a pause of 500 ms or more closed it and the rest was typed, submitting early. A paste given up in part now closes on the detach key alone or after 5 s of silence (a lost end marker).
- **Codex's notify notice is proven against Codex's documentation only** (`additionalContext` "is added as
  extra developer context"), not observed in a live Codex agent's context.
- **The shutdown bound is proven by configuration only.** Whether `docker compose up -d --build` stops a
  container that was started with a 1 s stop timeout using the new 20 s grace period is unverified; after
  the deploy, the service log should not say "terminal output still queued after".
- **`test_turn_end_event_flips_managed_hermes_off_working_immediately` failed some of the reviewer's
  Linux runs.** It passed 30 of 30 serial runs here; the failure output was not available.
- **Ten Windows process-control tests fail in comms-senior-dev's isolated harness and pass on this
  host, at the same code.** That harness gives each suite a private HOME/APPDATA/TMP and sealed AIFY
  endpoints, and runs it with `subprocess.run`; whether those runs sat in a Windows Job, and with which
  limits, was not recorded and is unknown. Bridge: `a-default-argument-is-evaluated-in-production` (process enumeration
  omitted its own pid), `session-fixes` (EBUSY removing a scratch dir),
  `kill-prior-collects-what-a-previous-hermes-left`, `codex-legacy-controller-verbs`,
  `hermes-daemon-default-killtree` (a process survived a tree kill). aify-env: four takeover and
  supersession tests could not rebind ephemeral ports after stopping the predecessor, and the
  dedicated Windows Job isolation test timed out. Not classified: the harness difference is ASSUMED
  to be the cause, not shown.

## A `claude` run from an agent's own shell took that agent over (2026-09-26; refused since 0.7.5)

A child `claude` (`claude -p`, `claude mcp list`) inherits `AIFY_AGENT_ID` and the session id, so its
aify-comms bridge registers as the parent agent with the same session handle and takes the session over.
sc-manager did this four times on 2026-09-26, and each exit left it `stopped` for 2.5 hours. Since 0.7.4
the exit sets the agent `offline` instead of stopping it, and the parent's bridge takes the session back on
its next beat (`service/api_core/nested_session_handback.py`). A predecessor killed by a real relaunch never
beats, so nothing is handed back and the agent reads `offline` rather than `stopped` until its next launch.
The offer reaches every same-session bridge behind the lost one, so overlapping nested runs that exit
oldest-first still hand back; and a child's bridge that crashes without reporting its loss gives the
session back once it has not beaten for the resident lease and the parent's bridge beats (2026-09-29).
Bridges beat every 60 s against a 150 s lease, so a live child must miss two beats first; the case
that can still do it is a machine waking from sleep, where the parent may beat before a running child
and take the session back from it (not observed).
Since 0.7.5 the child's bridge registers the session Claude Code gave it rather than the parent's, so the
service refuses it (409, a different handle against a live owner) and it takes nothing over
(`sessionWhenStartedFromAClaudeShell` in `mcp/stdio/adapters/claude.js`). The signal was read from the
claude.exe build of 2026-09-26, not observed on a running nested bridge: Claude Code puts `CLAUDE_PID` in
every shell it runs and `CLAUDE_CODE_SESSION_ID` (its own session) in every stdio MCP server, and does not
set `CLAUDE_PID` for an MCP server, so only a claude started from a Claude Code shell hands its bridge
both. In a launcher-resumed session the two session ids were observed equal. ASSUMED, not checked: that a
nested `claude` keeps that behaviour in other Claude Code versions, and the managed-agent registration
path. With `env -u AIFY_AGENT_ID -u AIFY_COMMS_AGENT_ID claude ...` a bridge neither registers nor
reports resident-lost (`auto-registration.mjs`, `server.js` `shutdownWithStatus`).

## The resume-menu answer is tested against hand-written screens only (0.7.4)

Since 0.7.4 the service answers claude's resume menu with "Resume full session"
(`service/api_core/console_prompts.py`, rule `resume-full-session`; DECISIONS.md). The screens it is
tested with are written from the layouts recorded on 2026-08-01, not captured from a live worker, and the
menu has changed shape upstream before. It presses nothing unless it can see both the cursor and the
full-session row, so a new layout leaves the worker waiting at the menu, as before 0.7.4, rather than
choosing the summary. Since the review of 0.7.6 (F5) it reads only the last block drawn on screen,
whose rows must end it with nothing but a key hint or border below: before that, a live agent that
grepped a resume-menu fixture got arrow keys and Enter typed into its input box. That anchor is taken
from the captured development-channels dialog, and ASSUMED to hold for the resume menu. Any other
claude dialog is still left to the console.

## `scripts/stamp.sh` reads no checkout under a shell exporting `MSYS_NO_PATHCONV` (2026-09-26)

A shell that exports `MSYS_NO_PATHCONV=1` or `MSYS2_ARG_CONV_EXCL=*` (hermes' tool shell does) hands
native git a `/c/...` path it cannot use. With `GIT_SHA` unset, the stamp records sha `unknown`, so the
doctor's `service` row cannot match the build to a commit. With `GIT_SHA` set, the sha is that value
and `sha_from_env` is true (the doctor reports it as an override), but dirty is still recorded as
`false` whatever the tree holds, a falsely clean build. Either way, stamp and build from an ordinary
shell.
Running git from `cd "$REPO_ROOT"` instead of `git -C` would remove the dependence.

## Per-agent usage consumption has no collector

Since 0.7.4 the service reads both quota pools itself (`service/usage_openai.py`,
`service/usage_anthropic.py`), each at most once per `usage_poll_minutes` (default 5), from the
read-only `~/.codex` and `~/.claude` mounts; the Anthropic reading is proven by its tests against a
recorded response shape, not yet against the live endpoint from the container. What still has no
caller is `POST /usage/consumption`, the per-agent token rows (`collectConsumptionOnce` in
`mcp/stdio/usage-collector.js`, parked since the environment bridge was deleted in v0.6.3), so the
dashboard's Consumption section stays empty. aify-env, which already watches every process, is the
candidate caller.

## Found by the test-duplicate cleanup, not yet acted on (2026-09-19)

The v0.6.15 cleanup mutated product code to prove which tests cover what. These came out of that
work. Each was observed under a mutation or by reading; none has been seen misbehave live.

- **`_row_capabilities` raises `AttributeError` on a hermes row whose `runtime_config` is JSON
  `null`.** Whether a real row can ever hold `null` there is unchecked.
- **A comment in `service/api_core/status_inputs.py` says a test pins `resident_bridge_stale = True`.**
  None does: removing that line leaves every test green.
- **The `PrefetchedStatusSignals.load` prefetch in `service/routers/analytics.py` is reached by no test.**
- **The `orphan_messages.py` docstring calls `m.to_agent IS NOT NULL` load-bearing.** Dropping it
  alone changes no result.
- **`_managed_via_wrapper_for_runtime`'s pi branch never changes the answer**, because pi already
  fails the delivery-mode check first.
- **Three tests are still weak**: no mutation reddens
  `test_status_is_pure_event_long_ceiling_not_short_window`; the orphan-reaper assertion in
  `test_managed_hygiene_keeps_live_console` is spared by two guards at once; and
  `test_it_binds_exactly_one_parameter` errors at setup instead of failing.

The per-test record (every removal with its surviving test) is in
[the audit plan](docs/superpowers/plans/2026-09-17-test-suite-audit.md).

## One live instance per agent: what v0.6.8 deliberately left (2026-09-15)

v0.6.8 gives every launcher a per-agent lease on the host, so one agent runs once per host. The design
and the reasoning behind each item are in
[the plan's "As built" section](docs/superpowers/plans/2026-09-14-one-live-instance-per-agent.md).
These were left on purpose:

- **The queued-run backstop is refused by a live but deaf instance.** That is the operator's policy
  for automatic starts. aify-env has no respawn loop, so nothing retries in a loop; replacing the
  instance takes a dashboard Start/Restart.
- **Two hermes port markers naming one port make that port nobody's.** A leftover gateway on it is
  stopped by neither agent's reap. The next launch moves one agent to a new port, and
  `gateway-orphans` reports the gateway. Deciding which agent owns it needs evidence the markers do
  not carry.
- **A clock stepped backwards can defeat `seenAliveAtMs`.** It is compared with no tolerance, so after
  a VM resume or a large time sync a pid reused after the step could read as the recorded one.
- **claude's managed reap (`reap-managed-claude.js`) does its own process matching** after a
  successful claim, rather than reading the lease's process table.
- **Launchers rendered before v0.6.8 have no lease** until `install.sh` is re-run for that client.
  Their kill-prior no longer reaps by port or session either: `hermes-daemon-cli.js stop` reaps only
  for a launcher holding the lease.
- **A terminal host started inside an agent's session keeps that session until it is restarted.**
  Found live on 2026-09-15: a Herdr server started from comms-tech-lead's Claude Code session gave
  every pane that agent's id and conversation, and a bare `claude-aify` in one replaced the live
  agent. Since then only `--aify-agent` replaces, a launcher ignores an inherited session, and
  `herdr-aify` starts its server clean (aify-wrapper `lib/inherited-session.mjs`). A server started
  earlier, or started outside `herdr-aify`, still hands its environment to every pane: the launchers
  now ignore it there, but a bare runtime such as `claude` typed into that pane does not.
- **An agent that hosts another agent cannot be replaced until what it hosts is stopped.** External
  review, 2026-09-15: a replace stopped the whole tree of the live instance, including a Herdr server or
  aify-env started from its shell and every agent inside it. A process whose tree holds another agent's
  leased process is now never stopped, a replace of an instance hosting one exits 75 naming
  `hosts-another-agent`, and collection leaves it running. The operator stops the hosted agents, or the
  host, first. A daemon started from the session that hosts no agent is still stopped with it.
- **The PR #11 notification hooks post turns for an inherited agent id.** The Claude hooks in
  `~/.claude/settings.json` gate only on `AIFY_AGENT_ID` and `AIFY_COMMS_URL`, so a bare `claude` in a
  pane that inherited an agent's environment reports its turns as that agent. Deferred: the hook needs
  to check that it runs under the launcher holding that agent's lease.
- **A worker started through an aify-env older than `dddcbcc` loses its role, cwd, terminal id and model.**
  That aify-env hands the worker its own agent id beside the session marker, so the launcher reads the
  environment as another agent's session and hands the worker none of it. It can no longer start a kill
  (aify-wrapper `81e076a`), but the worker runs without its terminal binding. Updating aify-env, and the
  service to 67faa2fe or newer, removes the leak at the source.
- **A lease record written before v0.6.8's clock fix mixes two clocks.** On Linux a record whose start time
  could not be read keeps the moment the pid was seen alive; before `c65593c` that moment was wall-clock while
  start times are anchored, so the first start after the upgrade can misjudge such a record by the drift
  between them. It lasts one instance: the next record is written on one clock.
- **`gateway-orphans` is only tested below `doctor.js`.** The check's image reader is proven through
  `checkGatewayOrphans`, but the line in `doctor.js` that hands it `listening-ports.imageName` is reached
  by no test, because importing `doctor.js` runs the doctor. Removing it makes the climb stop at the
  listener, a narrower pid than a tree kill needs, never a wider one.
- **With no `OPERATOR_KEY`, `requestedBy: "dashboard"` is a string any caller can send**, and it
  replaces. With the key set it must present the key, like every other operator name.
- **Not yet proven on the live fleet.** The suites and mutation runs pass; no live hermes, claude or
  codex agent had been restarted through the new launchers when this was written.

The plan also records two smaller residuals: the dashboard "Start console" route and a dashboard
spawn-spec assignment both store START, and the lock takeover leaves a microsecond window for two
holders, where the loser writes no record but what it already stopped stays stopped.

## A snapshot taken mid-escape-sequence loses the parser's half-read state (2026-09-09)

FOUND BY REVIEW during the v0.6.3 whole-diff pass, reproduced against BOTH the original base and the
current tip, and LOGGED rather than fixed -- the fix is a change to what a snapshot IS, not a patch.

**The construction.** Feed a live screen `ESC[HAA`, then an INCOMPLETE `ESC[31;`, take a snapshot,
then feed the suffix `1mB`.

  * Parsed CONTINUOUSLY, the two halves join into `ESC[31;1m` and the screen reads **AAB**.
  * Reconstructed FROM THE SNAPSHOT and then given the suffix, the screen reads **AA1mB** -- the
    orphaned tail is printed as text, because the rebuilt parser never saw the opening half.

**Why it is real and not theoretical.** A PTY chunk boundary can fall anywhere, including inside a
CSI sequence, and every reader that rebuilds a screen from a snapshot rather than from the byte
stream inherits this. `_UNTERMINATED_PRIVATE_CSI_RE` already holds back a chunk that ends mid-private
-CSI for exactly this reason; that guard covers the PRIVATE sequences the fleet emits and not the
general case.

**Why it is not fixed here.** A live screen currently carries painted cells plus the sequence it has
consumed. Making a snapshot resumable means carrying the emulator's PARSER state as well -- pyte's
`Stream` has one and it is not part of any serialisation this service performs. That is a contract
change with its own blast radius, and this version already carries several console fixes.

**What it is NOT.** No live browser incidence is claimed. Nobody has seen a console garble traced to
this, and the reproduction is a constructed pair of feeds. It is logged because the fix is real work
and the knowledge is worth more written down than remembered.

## Gateway hosts that outlive their agent: what v0.6.8 left open

Since v0.6.8 a hermes gateway ends with its agent: it carries the launcher's lease pid as
`HERMES_PARENT_PID` (`gatewayOwnerEnv` in `hermes-gateway.mjs`), the lease's watch (aify-wrapper
`lib/agent-lease-watch.mjs`) stops what a killed launcher left, and the next start collects the rest.
Three gaps remain:

- A gateway started by a launcher older than v0.6.8 carries no parent pid.
- Two hermes port markers naming one port make that gateway nobody's, so neither agent's relaunch
  collects it.
- A gateway relaunched from an Administrator terminal (for example by `hermes update`) is reported
  (`unidentified`) but cannot be stopped from a non-elevated process.

`aify-comms doctor`'s `gateway-orphans` row reports all three. The measurements and the chain that
made a hard kill of the host tier orphan every gateway are in the archive.

A fourth, found 2026-09-28: pc-manager's resident gateway (started 2026-09-25) carried no
`AIFY_AGENT_LEASE`, so it had no `HERMES_PARENT_PID` and its launcher's death stopped nothing; what
started it without a lease is not known. Found on 2026-09-28 with no TUI (its session list was empty),
the agent still reading `online` through the gateway's own MCP bridge; pc-manager had sent nothing
since 2026-09-10. Its delivery loop left no log, so which path it was on is not known. Two holes are
closed. A connect the gateway rejects any way but "refused" (a stale token answers 403) used to be
retried for ever; the loop now reports the agent undeliverable after a bounded run of them and ends,
killing nothing. And `gateway-orphans` exempted a gateway with a live loop or a resident owner; it
now reports any gateway whose own `session.active_list` is a recognised empty list. A failed or
unrecognised read stays unknown in both.

## Two viewers of one agent share its terminal size: the one that types owns it (aify-env 3e8a7f4)

A worker has one PTY, shown in a herdr-aify pane (`aify-env attach`) and in the dashboard console.
The last resize used to win, so every other viewer showed redraws for a size it is not. On 2026-09-28
both scrambled workers had last been resized by the dashboard (157x32 and 157x29, the second half of
the repaint nudge a console sends when it opens, `dashboard-attach` in `terminal_controls`) while
their herdr panes were 40 rows. Since aify-env 3e8a7f4 each attach client names itself, and a
keystroke from a viewer that is not the owner gives the terminal that viewer's size before the key
lands (tmux `window-size latest`); if the PTY refuses that resize the key is withheld and the sender
told. Every dashboard console tab is ONE viewer, since a control carries no tab identity, so two tabs
of different sizes share the size last resized. Only the dashboard's console surfaces
(`dashboard:console`, `:attach`, `:refresh`, a namespace no agent id can enter) count as that viewer:
an auto-answer (`console-prompt`), an agent typing into a console, and a chat message or Compact from
the dashboard (bare `dashboard`, also what the service fills in for an unnamed caller) never resize.
A console tab loaded on older dashboard code sends an older name until it reloads: `dashboard-console`
from v0.7.6, bare `dashboard` from before it. Neither is a viewer to aify-env 0.7.2, so that tab's
keys do not restore its size, which is the old last-resize-wins behaviour. What remains: a pane stays scrambled until the operator types in it or resizes
it, opening or refreshing a dashboard console still takes the size, and what already reached herdr's
scrollback stays. Live only after an aify-env restart.

## A herdr pane's rows can land one column left on Windows (open; fixed in a later 0.7.x)

Leftover letters inside words ("bot-" for "both") and stray box-drawing at column 0, in herdr panes
only; the dashboard console of the same agent is clean. The agent's PTY is a ConPTY, which moves the
cursor down with a bare LF meaning "same column". `aify-env attach` writes that stream to herdr's
pane, itself a Windows console, and two layers turn the LF into CR+LF: libuv's TTY writer
(`process.stdout`) and the console's auto-return while `DISABLE_NEWLINE_AUTO_RETURN` is clear. Measured
through the system ConPTY and herdr's bundled 1.24 on 2026-09-30: a raw fd write with that flag set keeps
the column; any write through `process.stdout`, or with the flag clear, loses it. Translating LF was
tried four times and withdrawn (aify-env 1736898): an LF and its replacements differ under some parser
modes (reverse-wrap, the order of BS and LF), so a translation changes some streams. Setting the flag
from a PowerShell helper worked, but Windows Defender blocked the pattern (`Trojan:Win32/SuspExec.SE`),
so it was not shipped. The fix planned for a later 0.7.x sets the flag in-process (koffi, optional, like
node-pty) and writes raw.

## Attach and typing lag came from one icacls per credential read (aify-env 4044364)

The daemon ran `icacls` on the stored key's file on every plugin call and every HTTP request, and on
Windows the spawn runs on the event loop. A 30 s profile of the live daemon (2026-09-28) put 80% of
the main thread in that spawn; `/health` stalled 250-300 ms about twice a second, up to 3 s, and every
keystroke echo waited behind it, worse when agents produced more output. Now read once per change of
the file (device, inode, ctime; 60 s TTL). Measured in a harness: loop delay max 567 ms before,
17.7 ms after. Not yet measured on the live daemon, which needs an aify-env restart to load it.

## The session list calls a starting session with no terminal yet `stopped` (review, 2026-09-30)

`GET /sessions` serves a derived status (`_compute_session_display_status`,
`service/reconcilers/sessions.py`). For a managed session it reads `stopped` whenever the agent has no
live console, including a session still starting that has no terminal row yet. The conditional restart
guard (`_live_session_for`) correctly counts that session as live and refuses a second start with 409,
so the list and the guard disagree for that window. Reproduced offline by comms-senior-dev in review of
fbb4776a. The guard stays as it is: weakening it to match the list would allow a double start. Fix the
display on its own; its caller that matters is aify-env's start, which reads the list and then asks the
guard.

## Rare wrong statuses for a managed claude (traced 2026-09-26)

An unwatched console is NOT one: aify-env reads every terminal's output and reports its screen whether
or not anyone views it (`terminal-controls.mjs`, `runner.mjs`), and on 2026-09-26 all six live managed
claude agents read correctly, three working and three idle. Esc no longer latches `working` since 0.7.4
(`isInterruptMarker`, `adapters/claude.js`). A code trace left these, none seen live:

- **A turn past 30 minutes rests on the screen alone.** A hook-started turn stops counting after 30
  minutes; from then on `working` comes from aify-env's screen reading or the 20 s console lease. A
  layout that pushes the spinner out of the bottom twelve lines, or a footer without its `↓ N tokens`
  item, would read `online` (ASSUMED; depends on claude's layout).
- **Assistant text that looks like a spinner line** (a line starting `* `, `· ` or `✻ ` and ending in
  `…`) near the bottom of an idle screen reads `working` until the screen changes (ASSUMED).
- **A permission prompt the operator denies** reads `blocked` for up to 45 s, then `working` until
  the next turn event.

## An interrupt reaches the agent as a permission refusal (2026-08-25)

`comms_interrupt` works: three separate live processes were killed within five seconds of it, each
verified by pid, and a control run left alone ran ninety seconds untouched. What the AGENT is told is
wrong.

Claude Code cancels the in-flight tool call and the model sees **"The user doesn't want to proceed with
this tool use"** — the wording for a declined permission prompt. Observed live: the interrupted agent
concluded a permission gate existed, built two theories about what was being gated, reported both, and
corrected itself in the wrong direction. A production agent would stop retrying work nobody refused.

**The service half is fixed.** A run failed after a recorded interrupt now names it — the requester and
the time, out of `terminal_controls` — instead of listing four possible causes with a provider throttle
among them. See `api_core/authored_failures.py`.

**The runtime half is not ours.** That string comes from Claude Code, not from aify-comms, so it cannot
be corrected from here. Since 0.7.4 a completed interrupt of a claude agent leaves it one short unread
note, from `aify-comms`, saying who stopped it and when and that it was a stop, not a permission refusal
(`service/api_core/interrupt_notice.py`). It wakes nothing, so the agent reads it at its next turn,
after it may already have drawn the wrong conclusion in the interrupted one.

## A worker still inherits any harmful variable nobody has named

The service owns the list of variables a managed worker must not inherit from whatever started its
host (`NEVER_INHERITED` in `service/api_core/launch_env.py`) and sends it on
`GET /terminals/{id}/launch` as `unsetEnv`; aify-env removes those names before laying the launch
overlay on top. It is a denylist, so a harmful variable no one has named yet still reaches every
worker. The case that forced the list: `CLAUDE_CODE_CHILD_SESSION` from a host started inside a Claude
Code session turned transcript saving off in every managed claude (history in the archive).

## `undefined` is not a placeholder session handle, and JavaScript writes it (2026-08-17)

**Measured, not ruled on.** `HANDLE_PLACEHOLDERS` in `mcp/stdio/adapters/base.js` filters the strings
a shell writes when a variable was set from an empty expansion, so they never get registered as a
session id: `unknown`, `default`, `none`, `null`. The bridge is the only filter; the Python mirror was
deleted in 0.7.0 (`b2451d86`), and the service stores the handle it is sent.

What is missing is `undefined`, which is exactly what `String(undefined)` produces — and every bridge
in this fleet is Node. An unset value interpolated into `HERMES_SESSION_ID`, `CODEX_THREAD_ID` or
`CLAUDE_SESSION_ID` arrives as the literal text `undefined`, passes normalisation, and is registered
as the agent's `sessionHandle`. The symptom is a resume that cannot resolve, against a session named
`undefined`.

**Left unfixed and deliberately unasserted.** Widening the set changes handle normalisation for every
runtime, which is a reviewer's call rather than a test-slice fix.
`mcp/stdio/tests/adapters/contract.test.js` ("normalizeSessionHandle returns empty for placeholder")
pins two of the filtered strings, so adding `undefined` later does not fail a test.

## The managed-hermes gateway session buffers terminal frames differently from the other three (2026-08-13)

**Measured, not ruled on.** Four session classes carry a `_terminalSink` / `_terminalFlushChain` pair.
Three of them — `pi-session.js`, `codex-session.js`, `hermes-session.js` — share one design:
a `_terminalBuffer` array capped at `MAX_TERMINAL_FRAME_BUFFER_CHARS = 65536`, drained single-flight.
`hermes-managed-gateway-session.js` has **neither the buffer nor the constant** (2 of the 5 fields), and
appends each frame straight onto a promise chain.

Two behavioural consequences follow, and neither is written down anywhere:

1. **No backpressure cap.** The other three drop the oldest frames past 64KB. The gateway's chain has
   no bound, so a worker producing faster than the sink drains accumulates pending closures, each
   holding its frame text.
2. **Frames before the sink attaches are lost.** The gateway's `_pushTerminalFrame` opens with
   `if (!this._terminalSink) return;`. The other three buffer while detached and replay on
   `attachTerminalSink`, so opening the console shows what already happened; the gateway shows nothing
   before the moment of attach.

**Not filed as a bug, because it may be the intended trade.** Dropping frames is data loss, and a
lossless chain is a defensible choice for a gateway whose real TUI may arrive by another path. What is
*not* defensible is that the difference is invisible: the four classes look alike, share field names,
and disagree on both bounding and replay. Needs a ruling on which behaviour is correct for the gateway,
then an agreement test pinning it.

Related: the same "a fix landed on some copies of a duplicated helper and missed one" shape as the
`createDeferred` unhandled-rejection guard (v0.5.4) and the build-tag divergence before it. The
pi-side comments date the buffer design to bug-hunt audit **B-C1**; whether the gateway was in scope
for that audit is unresolved.

## A registry entry with `strictMcp: true` never reaches a launcher this installer renders (2026-10-01)

`render_wrapper_template` in `install.sh` substitutes `@@STRICT_EXTRA_MCP_B64@@` with nothing (line 509
on main 6c2367bf and on next), while aify-wrapper's own installer computes it with `registry-cli.mjs
strict-fragment-b64` and stops on a refusal. So on a host installed through this repo, claude-aify's
strict-MCP mode (`AIFY_CLAUDE_STRICT_MCP=1`) loads only aify-comms' bridge, whatever the registry opts in.
Found by dashboard-manager while checking where per-session MCP injection would have to land. To be
fixed in 0.8 with that injection's placeholder (`@@SESSION_MCP_B64@@`), both computed through the pinned
aify-wrapper's `registry-cli.mjs` and failing the install on a refusal, once that verb has passed review.

**Fixed on next/env-owned-agents (0.8), still open on main.** `scripts/registry-fragment.sh` computes both
fragments with the pinned package's verbs and fails on a refusal, and install.sh bakes them
(`mcp/stdio/tests/install-bakes-the-registry-fragments.test.js`). A host takes it at its 0.8 install.

## A claude MCP server inherits every variable the launcher holds, a neighbour service's key included (2026-10-01)

Claude Code's MCP `env` block is key-scoped (since 2.1.236): a variable it does not name is inherited by
the MCP server process from the launching environment. So any MCP server a claude-aify session starts,
aify-comms' bridge and a per-session one (`sessionInject`) alike, sees whatever the launcher's environment
holds, and that would include another service's key if something exported one there. The per-session
document itself never carries a key: aify-wrapper's `sessionMcpEntriesFor` binds each `endpointEnv` name to
the entry's endpoint and reads no environment (lib/registry.mjs at 1498037, asserted in
tests/session-mcp.test.js and in comms-senior-dev's review of 1498037). Measured by dashboard-manager. NOT
traced: whether any path exports a service key into a launcher's environment today; aify-comms' own entry
carries no `keyEnv`. One path checked: `HARNESS_EXTRA_ENV`, which every launcher exports verbatim, is read by
the four aify-wrapper templates and written by nothing in aify-comms, aify-env or aify-wrapper source
(searched 2026-10-01, the templates found as the positive control), so only an operator's own shell sets it. The verb is not the place to fix it, since it predates the verb and applies to every
MCP server Claude starts.

## What the review of 0.8's model and effort left as follow-ups (2026-10-01)

comms-senior-dev's review of P6r (3ada3809 / aify-wrapper 9455583) blocked on ten findings, repaired on
next; it disclosed these beside them, not as blockers:

- **Who may change an undefined managed agent's effort.** A defined agent's change needs the operator
  (403 without the key, C4); an undefined managed agent's is written with no operator proof, as its herdr
  space and usage source already are. Awaiting Steven's ruling on whether those edits become operator-only.
- **`runsWith` read during a definition push can mix the two.** `GET /agents/{id}` reads the agent row,
  then the definition rows; a push landing between them answers the new owner beside the old
  `definitionState` and values. The next read is consistent. The two-read shape predates `runsWith`.
- **A refused effort change carries no `appliesAt`.** Only an accepted change says when it applies; C12
  now says so.

## Open backlog, re-checked 2026-09-25

Each item below was read against the code on 2026-09-25; none has been seen misbehave live.

- **`PATCH /agents/{id}/ready` refreshes `turn_updated_at` on a row it does not own**
  (`update_agent_ready` in `service/routers/agents/liveness.py`). The delivery and status ceiling
  renews against that column only for a turn a live bridge owns, so each call can extend such a lease
  by one 30-minute window. The bridge sends it once per run handshake, which is real work starting.
- **Bridge HTTP calls never retry a 429.** `aify-service-endpoint.mjs` retries only 5xx responses, and
  only for its idempotent requests. Latent: the service never answers 429.
- **`PATCH /dispatch/runs/{id}` checks no ownership** (`update_dispatch_run`). A terminal run can no
  longer be reopened, but any caller can still change a live run's status, so a late, superseded
  sidecar can overwrite the new owner's state.
- **A steered parent run closed by a reaper leaves its steer contract open.**
  `_close_steered_contracts_for_parent_run` is called only from the PATCH settlement path
  (`service/api_core/dispatch_run_settlement.py`).
- **`comms_usage`'s personal line drops the pool's `stale` flag**, so a stale percentage can read as
  current. Advisory only; it matters for the Anthropic pool, which nothing collects.
- **Crossed messages in fast two-way exchanges.** Two agents answering each other within seconds each
  reply to a state the other has already moved past. The teams' own tie-break protocol handles it; a
  per-pair sequence number surfaced in each delivery is an idea, not a design.
- **`aify status`, a fleet status view for the terminal**, was asked to be recorded rather than
  built (2026-07-14): [docs/plans/2026-07-14-cli-status-view-and-dashboard-upgrades.md](docs/plans/2026-07-14-cli-status-view-and-dashboard-upgrades.md).
- **Operator decisions, not defects:** the service port is published on every interface, and
  `cors_origins` defaults to `*` (DECISIONS.md "The service key is opt-in").

## Looks like a defect, is by design

- **Several unsuperseded `bridge_instances` rows per agent.** A `channel-sidecar` and a
  `managed-wrapper-child` for one managed agent play different roles, and PTY siblings sharing a
  terminal must not supersede each other (`_record_bridge_registration`); rows age out once their
  heartbeat passes the 5-minute window. `_requeue_orphaned_claimed_runs` keys on the one claim bridge
  row, so siblings cannot make a dead claim look alive. The 2026-08-07 retraction is in the archive.
- **`agent_status_state.status` is written and read by nothing**; the status engine keeps only
  `in_turn`, `awaiting_input`, `turn_run_id` and the event columns there. Its stale values mislead
  anyone debugging from a table dump.

## Watch

- **A bridge started without a launcher reads `HERMES_HOME` as "I am hermes".** `detectRuntime`
  (`mcp/stdio/runtimes.js`) checks `AIFY_RUNTIME` first, which every launcher and managed start sets,
  so only a hand-configured MCP entry is affected; on this host `HERMES_HOME` is set machine-wide, so
  such a claude would register as hermes.

- **Hermes `delegate_task` blocks the parent turn today.** If a future hermes makes it asynchronous,
  the gateway turn detector's idle debounce (`AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE`, 3 ticks) would
  end a turn that is still delegating. Recheck on hermes upgrades.
- **The claude channel sidecar's parent-death guard reads the immediate `ppid`** (`ORIGINAL_PPID` in
  `mcp/stdio/claude-channel.js`). In a managed tree the immediate parent can be a transient shell that
  exits while claude lives. Seen once (2026-06-01); if consoles start dropping, walk to the real claude
  ancestor instead.
- **The claude console rules are TUI-version-dependent.** The spinner rule
  (`service/api_core/terminal_text.py`) and the one boot-prompt rule (`dev-channels-accept` in
  `service/api_core/console_prompts.py`) match claude's current TUI text. The prompt rule needs the
  dialog's question line under the cursor, so a claude UI change that rewords it stops the answer
  rather than misfiring, and the dev-channels acknowledgement then parks a new worker again.
