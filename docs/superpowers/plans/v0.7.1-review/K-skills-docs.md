# K: skills and operator docs against the v0.7.0 code

Read at `3f588c5c` (v0.7.0 plus one plan-doc commit; no skill or doc under review differs from the tag).
Read-only: no repo file but this one was written, nothing ran against :8800/:8802, `doctor.js` was not
imported. Ran only `mcp/stdio/tests/skill-size-ratchet.test.js` (exit 0, 6/6) and
`service/tests/test_skill_mirror_parity.py` (2 passed); `diff -r` of `.claude/skills` against
`.agents/skills` is empty for all three skills, so every skill fix below must land in both.

**Provenance.** The main skill, `.claude/commands/`, the tool descriptions and the H-A table were read
by me. The debug/install skills, the operator docs and CLAUDE/KNOWN_ISSUES/ARCHITECTURE were read by
three parallel readers; their findings are included with their evidence. I re-verified every P1 and
the ones marked (spot-checked); the others rest on the reader's file:line evidence.

**Size budget.** `ALWAYS_LOADED_LIMIT` is 16,000 characters; `aify-comms/SKILL.md` is 14,915 at a
ceiling of 14,915, and the ratchet refuses slack, so a shrinking fix lowers the ceiling in the same
commit. The SKILL.md edits below net about -800 characters (K4 -163, K5 +33, K6 +7, K7 +3, K37 -485,
K38 -237, K39 -148).

**Deployment note.** `~/.claude/skills/aify-comms/` on this host differs from the checkout
(SKILL.md, operations.md, leading-a-team.md), and this session's own channel instructions lack the
0.7.0 "Tag attributes" sentence. Agents here are still reading pre-0.7.0 text until `install.sh` is
re-run and they relaunch.

Severity: P1 = an instruction that loses or misroutes messages or leads to a destructive action;
P2 = a wrong or stale claim; P3 = wording or structure.

---

## P1

**K1. `.claude/commands/clear.md:10-12`: `/clear inbox` and `/clear agents` wipe the whole hub.**
- **Claim:** "First word = target. Second word (if number) = olderThanHours. Call comms_clear.
  `shared` and `all` clear data for every team on the service, so confirm with the user before
  calling them."
- **Code:** the command never passes `agentId`. `service/routers/maintenance.py:60-63` deletes
  `to_agent IS NOT NULL` (every direct message on the hub) for `inbox` without `agentId`, and
  `:77-80` removes every agent identity for `agents` without `agentId`. Both run without the
  confirmation the file reserves for `shared` and `all`.
- **Replace with:** "Parse arguments. First word = target; second word (if a number) =
  olderThanHours. For `inbox` and `agents`, pass `agentId` = your registered agent ID unless the user
  names every agent. Without `agentId`, `inbox` deletes every agent's messages and `agents` removes
  every identity on the service; `shared` and `all` always span every team. Confirm with the user
  before any call that is not scoped to one `agentId`."

**K2. `docs/UNINSTALL.md:17-18, 23-27`: "Stop Host Bridges" kills every live agent.** (spot-checked)
- **Claim:** `pkill -f 'aify-comms'`, and on Windows `CommandLine -match '...|aify-comms'` with
  `Stop-Process -Force`.
- **Code:** every `claude-aify` agent runs
  `claude --dangerously-load-development-channels server:aify-comms-channel`
  (`claude-aify.sh.in:823`, with `@@SERVICE_NAME@@` rendered as `aify-comms` at `install.sh:506`).
  The pattern also matches any process whose argv names the checkout or `~/.aify-comms`.
- **Replace with:** "Stop the agents first: stop managed agents from the dashboard and close resident
  `*-aify` terminals. Then stop any leftover bridge with
  `pkill -f "${AIFY_HOME:-$HOME/.aify-comms}/mcp/stdio/" || true`. On Windows, filter with
  `$_.CommandLine -match '\.aify-comms[\\/]+mcp[\\/]+stdio[\\/]'`. Never match the bare string
  `aify-comms`: every Claude agent's command line contains it."

**K3. `aify-comms-debug/references/hermes-turns.md:22-24`: the recovery leaves every managed hermes gateway dead.** (spot-checked)
- **Claim:** "Fix hermes (`hermes update`) … `install.sh` is needed only when hermes' CLI interface
  changed."
- **Code:** the gateway host starts with `--skip-build` (`mcp/stdio/hermes-gateway.mjs:198`).
  `install.hermes.md:31-36` says every Hermes update deletes the `web_dist` bundle that flag needs, and
  that `hermes update` must not run from an Administrator terminal. Following the skill leaves every
  hermes dispatch failing.
- **Replace with:** "**Recovery.** `hermes update` from a non-admin terminal, then
  `bash install.sh --client hermes` (an update deletes the web bundle `--skip-build` needs). Confirm the
  command above, then restart each worker (Sessions → Restart)."

---

## P2: the main skill, commands and tool descriptions

**K4. `aify-comms/SKILL.md:154`: the send gate is described wrongly, and its pointer promises sections that do not exist.**
- **Claim:** "Only `offline`/no-online-env targets and explicitly-disabled `stopped` agents fail."
  The pointer says operations.md "(Send Gating & Delivery) has the auto-start binding, the disable
  path, per-runtime delivery surfaces and the reply-contract reminder loop."
- **Code:**
  - `service/api_core/send_preflight.py:69,103-107` refuses every status in `NON_LIVE_AGENT_STATUSES`
    = offline, stopped, misconfigured (`service/status_engine.py:38`).
  - That includes a `resident-lost` stopped, which the skill's own operations.md:150 names.
  - A reply (`inReplyTo` or `type="response"`) is never refused
    (`routers/dispatch_messages/messages.py:149,169,181`).
  - operations.md:88-97 holds none of the four promised items.
  - This also contradicts operations.md:94 ("`stopped` … is not always the operator's doing").
- **Replace the paragraph with** (533 characters, was 696): "Sends are live-delivery gated: an
  `available` managed agent auto-starts on send (aify-env cold-starts it), so do not pre-spawn it.
  `offline`, `stopped` and `misconfigured` targets, and a managed agent with no online environment,
  are refused and nothing is stored; a reply (`inReplyTo` or `type="response"`) is stored anyway. A
  busy target that can steer takes the send into its active run; otherwise it queues as next-turn work
  (`queueIfBusy=true` forces that). Status meanings and per-runtime delivery:
  `references/operations.md`."

**K5. `aify-comms/SKILL.md:152` and `mcp/stdio/send-tools.mjs` (`COMMS_SEND_TOOL_DESCRIPTION`): `requireReply=false` does the opposite of what they say.**
- **Claim:**
  - SKILL.md: "`requireReply=false` … only drops the reply contract on `info`/`response`/`approval`."
  - Tool description: "set requireReply=false to drop the contract on `info`, `response` and
    `approval`."
- **Code:** those three types carry no contract by default
  (`api_core/reply_expectation.py:29-36`), so on them the flag does nothing. On a
  `request`/`review`/`error` it stores `require_reply=0`. The recipient is then told:
  - "No required handoff is tracked … Do not send an acknowledgement-only reply"
    (`runtimes-prompts.js:13,19-20`);
  - no same-turn line (`claude-channel-content.js:37-41`).

  The reply-capture fallback is also skipped (`api_core/dispatch_sweeps.py:77`). Yet the Work Loop
  still chases the message by type (`api_core/reply_contract.py:12-18`).
- **Replace SKILL.md:152 with:** "- Leave `requireReply` unset on `request`/`review`/`error`: `false`
  tells the recipient no reply is tracked, yet the Work Loop still chases it by type. On
  `info`/`response`/`approval` it changes nothing."
- **In the tool description:** replace "set requireReply=false to drop the contract on `info`,
  `response` and `approval` —" with "set requireReply=false only knowing that on `request`, `review`
  and `error` it tells the recipient no reply is tracked —". Three tests match the phrase
  "set requireReply=false", so keep it.

**K6. `aify-comms/SKILL.md:174`: the console pointer names the wrong file.**
- **Claim:** "Console input is recovery-only; see `references/teamwork.md`."
- **Code:** teamwork.md contains "console" 0 times; the control, `comms_send`, appears 4 times. The
  console-input rules are in `references/leading-a-team.md:182-184`.
- **Replace with:** "see `references/leading-a-team.md`."

**K7. `aify-comms/SKILL.md:195` (also `KNOWN_ISSUES.md:39-47`, `install.codex.md:257-258`, `install.hermes.md:148-149`): the OpenAI pool is collected.**
- **Claim:** "Pool collection is parked since v0.6.2, so expect `?` or `stale` rather than a number to
  route work by." KNOWN_ISSUES says "Nothing collects the usage pools".
- **Code:** `GET /usage` calls `collect_openai_pool()` itself, cached 120 s
  (`service/routers/usage.py:60-84`), from the read-only `~/.codex` mount. Only POSTed pools
  (Anthropic) and `/usage/consumption` have no caller. The "since v0.6.2" also disagrees with
  KNOWN_ISSUES' "v0.6.3".
- **Replace SKILL.md with:** "The service reads the OpenAI pool itself; nothing collects the Anthropic
  pool, so it reads `?` or `stale`."
- **Replace the KNOWN_ISSUES heading and paragraph with:** "## Only the OpenAI pool is collected".
  Say that `GET /usage` reads it (`collect_openai_pool`, cached 120 s), that Anthropic and
  consumption arrive only by `POST /usage`, whose caller was deleted with the environment bridge, and
  that on a host with no Codex token `comms_usage` says "No usage data yet (collector warming up)"
  (`usage-tool.mjs:43`) though no collector runs. The two install docs get the same correction.

**K8. `mcp/stdio/send-tools.mjs:196` (`comms_channel_send` description) and its SSE twin `service/sse/channel_tools.py:69`: a channel post is always stored.**
- **Claim:** "if any recipient is offline, stale, stopped, or lacks a live wake path, the channel
  message is not written." The SSE twin says "fail the send without storing".
- **Code:** `service/routers/channel_send.py:129-168` always stores the canonical post and every
  member's inbox copy, wakes only the members that can start, and lists the rest in `notStarted`.
  `stale` is not a status (`status_engine.py:18-20`). An agent told the post was not written re-sends
  it by DM, which duplicates it.
- **Replace with:** "Send a message to a channel. The post and every member's inbox copy are always
  stored; members that cannot start now (offline, stopped, misconfigured) are not woken and are named
  under Not started. Busy steer-capable members …" (keep the rest). Fix the channel_send.py module
  docstring (:8-9) too, which says "without storing".

**K9. `.claude/commands/search.md:10`: `/search` never searches messages.**
- **Claim:** "Call comms_search with the query. Search scope 'all' by default."
- **Code:** with no `agentId`, messages are skipped (`routers/dispatch_messages/inbox.py:312-318`).
  The tool's own description says so. An empty result then reads as "no such message".
- **Replace with:** "Call comms_search with the query and agentId = your registered agent ID (without
  it, messages are not searched). Scope 'all' by default."

**K10. `install.sh:176-183` and `README.md:4`: OpenCode and Pi are presented as supported.**
- **Claim:** `install.sh` prints "Managed OpenCode remains available through aify-env" and "Managed Pi
  remains supported". README says aify-comms is "for … Hermes, OpenCode and Oh My Pi (deprecated)".
- **Code and docs:** CLAUDE.md, operations.md:83 and `install.opencode.md:1` say OpenCode is
  unsupported and unverified, and Pi is deprecated.
- **Replace with:** "OpenCode is not supported." and "Pi is deprecated." In README: "…for Claude Code,
  Codex and Hermes agents (Oh My Pi is deprecated; OpenCode is not supported)". This is installer text,
  and the installer is held at its line ceiling, so the edit must be line-neutral.

**K11. "Set handle" names a control that does not exist.**
- **Where:** `aify-comms/SKILL.md:102`, `references/operations.md:117`, and the debug skill at
  dispatch-bridges.md:71, dispatch-launch.md:65, hermes-session.md:78 and lifecycle.md:35.
- **Code:** no dashboard file contains "Set handle". The control search "Native session handle" hits
  `service/new_dashboard/inspector-forms.mjs:54`, the field inside the agent drawer's **Edit…** button
  (`agent-drawer.mjs:70`), which PATCHes `/agents/{id}/session-handle`
  (`agent-session-actions.mjs:98-99`).
- **Replace with:** "the agent's **Edit…** → *Native session handle* field". In SKILL.md: "use
  **Edit…** (native handle) rather than re-registering". This is size-neutral.

## P2: the debug skill (reader-found)

**K12. `dispatch-delivery.md:24`: the verify SQL selects a column that does not exist.** (spot-checked)
- **Claim:** it selects and orders by `created_at` on `dispatch_runs`.
- **Code:** the `service/schema.py` dispatch_runs block has `requested_at`, not `created_at`, so the
  query fails.
- **Replace:** `created_at` with `requested_at`, in both places on the line.

**K13. `dispatch-delivery.md:59`: a blocked target does not return a not-sent notice.**
- **Claim:** "Normal `comms_send` does not queue new work behind a blocked target; it returns a
  not-sent notice."
- **Code:** steer defaults to true, which makes busy-enqueue true (`messages.py:159-164`,
  `send_preflight.py:69,128-135`), so the send steers into the run or queues behind it.
- **Replace with:** "A default `comms_send` (steer=true) still steers into or queues behind that run;
  cancel it first."

**K14. `dispatch-delivery.md:61-66`: it quotes a retired failure string and advises re-send or restart.**
- **Code:** the current text is `TURN_ENDED_WITHOUT_REPLY` (`authored_failures.py:69-74`): the RUN
  closed, not the agent, and the cause is undetermined. A restart here can kill an agent that is still
  working.
- **Replace the heading with:** "require_reply run FAILED: \"Turn ended without a reply — this RUN
  was closed\"".
- **Replace the body with:** "No reply within `stranded_reply_fail_minutes` (default 45). The worker
  may still be working: read `comms_console_tail` before re-sending or restarting. 0 disables it."

**K15. `dispatch-delivery.md:101-104`: bridge supersession runs the other way round.**
- **Claim:** it supersedes only on the full tuple.
- **Code:** registration is latest-wins per agent and machine. The exemptions are the sidecar/wrapper
  pair and a fresh same-terminal wrapper child (`bridge_registration.py:36-40,143-165`).
- **Replace with:** "A newer registration of this agent on this machine superseded it
  (latest-wins, sparing only the sidecar↔wrapper-child pair and a fresh same-terminal wrapper child).
  Find the second registrant."

**K16. `status-model.md:49-50`: the turn backstop is described wrongly.**
- **Claim:** "only an abandoned one ages out".
- **Code:** an unverifiable turn ages 30 min from its start, and a bridge-owned turn renews up to 4 h
  (`claim_gating.py:57,364-395`, `turn_liveness_policy.py:52-64`).
- **Replace with:** "a turn with no end-event ages out 30 min after it began
  (`TURN_BUSY_BACKSTOP_SECONDS`); one a live bridge owns renews, capped at 4 h
  (`TURN_LEASE_ABSOLUTE_MAX_SECONDS`)."

**K17. `hermes-session.md:74`: the marker path is wrong under Git Bash.**
- **Claim:** `${TMPDIR:-/tmp}`.
- **Code:** the marker lives in `TEMP||TMP||os.tmpdir()` (`hermes-endpoint.js:33-38`), so the skill's
  path makes the marker look missing.
- **Replace with:** `cat "${TEMP:-${TMP:-/tmp}}/aify-hermes-session-<agent>"`.

**K18. `dispatch-bridges.md:103-104`: it quotes a deleted log line.**
- **Claim:** it quotes the log line "recovered after N failure(s)".
- **Code:** that line was deleted with the environment bridge (`ac6d6e82`).
- **Replace with:** "**`fetch failed`**: a network or service interruption; check the service and
  network path."

**K19. `dashboard-console.md:26-27`: the quoted 409 text is not the real one.**
- **Code:** the real texts are "has no PTY/terminal capability" and "…not for runtime …"
  (`console_capability_gate.py:35-50`). That service message also still says "restart the bridge",
  which is stale in 0.7.
- **Replace:** the quote with those two strings.

**K20. `dispatch-launch.md:81`, `codex.md:29-31`, `pi.md:23-24`: they judge running code by `bridge-installed` alone.**
- **Code:** `bridge-installed` is disk only, and `bridge-running` skips on Windows. `bridge-current` is
  the running-code check.
- **Replace dispatch-launch step 1 with:** "`aify-comms doctor`: `bridge-installed` red → re-run
  `bash install.sh --client <runtime>`; `bridge-current` names live bridges on old code." Name
  `bridge-current` beside `bridge-installed` in the other two.

## P2: operator docs (reader-found)

**K21. `install.hermes.md:90-91`: `--safe` does not govern the gateway host.**
- **Claim:** "`--safe` (or `--no-auto`) keeps them in the visible TUI."
- **Code:** the gateway host always runs with `HERMES_YOLO_MODE=1` (`hermes-gateway.mjs:246`), and
  `--safe` only drops `--yolo` from the TUI (`hermes-aify.sh.in:563-566`).
- **Not verified:** that TUI-typed turns also run on the gateway host.
- **Replace with:** "The gateway host, which runs every delivered turn, always has
  `HERMES_YOLO_MODE=1`, so `--safe` does not restore approvals there; it only removes `--yolo` from the
  visible TUI client."

**K22. `docs/UNINSTALL.md:56-57`: the registry-key snippet does not run.**
- **Code:** it holds a raw newline inside a string, so `node -e` throws a SyntaxError and the key
  stays. Deleting the file afterwards removes every service's entry.
- **Fix:** put it on one line, ending `JSON.stringify(j,null,2)+"\n")'`.

**K23. `docs/UNINSTALL.md:243`: the verify snippet does not run.**
- **Code:** `node -e` rejects a top-level `return`.
- **Fix:** use `if(!fs.existsSync(f)){console.log("no registry on this host")}else{…}`.

**K24. `install.claude.md:64-66`, `install.codex.md:210-212`, `install.hermes.md:81-83`: the `--resume` lookup does send a key.**
- **Claim:** the lookup "works only while the service has no API key".
- **Code:** the launchers call `agent-for-handle.mjs`, which sends the key (0.7.0, `a759f4ea`).
- **Replace with:** "…looks the agent up by that handle on the service, using this host's API key;
  if the service is unreachable it falls back to …" (keep the per-runtime fallbacks).

**K25. The same three install docs, :47-48, :189-190, :66-67: `bridge-current` reads live bridges, not agents.**
- **Claim:** it names "any registered agent", and reads `unknown`.
- **Code:** it reads live bridges from `GET /bridges`, and the code is `unknown-all`
  (`doctor-predicates.js:494-497`).
- **Fix:** use README.md:116's wording.

**K26. `install.claude.md:126-127`, `install.codex.md:250-251`: managed-worker settings do not reach existing workers on save.**
- **Code:** saving affects new workers only. Existing workers need **Apply model and effort to
  existing workers** (`settings-panel.mjs:63`).
- **Fix:** say so.

**K27. `UNINSTALL.md:109`: the Codex hook change is misnamed and incomplete.**
- **Code:** the installer writes `hooks = true` under `[features]` and adds turn hooks to
  `~/.codex/hooks.json` (`install.sh:1829-1880,2015-2064`).
- **Fix:** name both, and how to remove them.

**K28. `UNINSTALL.md:41-47,205-218`: the removal lists miss three installed files.**
- **Code:** `aify-doctor.cmd` (`install.sh:2715`) and `hermes-aify.ps1` (`install.sh:859`) are not
  removed, and the Windows list also omits `aify-doctor`.
- **Fix:** add them.

**K29. `UNINSTALL.md:152-177` (Hermes): most of what the installer adds is left behind.**
- **Code:** the section misses the `plugins/aify-comms` directory and its `plugins.enabled` entry, four
  turn-hook scripts and their entries, and the hermes skills (`install.sh:444-455,1684-1790,2066-2130`).
  It also hardcodes `~/.hermes`, where the config root may be `%LOCALAPPDATA%\hermes`.
- **Fix:** list each item under the directory `hermes config path` prints.

**K30. `UNINSTALL.md:97,124`: only one of the three skills is removed.**
- **Code:** the installer copies `aify-comms`, `aify-comms-debug` and `aify-comms-install`
  (`install.sh:387-399`).
- **Fix:** remove all three, for Claude and for Codex.

**K31. `install.hermes.md:124`: the log path ignores `XDG_STATE_HOME`.**
- **Code:** the gateway log always goes to `~/.local/state/aify-comms`
  (`hermes-gateway.mjs:207`).
- **Fix:** drop the parenthetical.

**K32. `README.md:52,114`: `redeploy.sh` does not keep the SSE transport.**
- **Code:** it keeps the URL and `--env-endpoint` only (`redeploy.sh:113`), so a host installed with
  `--mcp-transport sse` is re-rendered as stdio. This is arguably a redeploy.sh bug.
- **Fix:** say so, or make redeploy carry the transport.

## P2: CLAUDE.md, KNOWN_ISSUES, ARCHITECTURE (reader-found)

**K33. `KNOWN_ISSUES.md:216-232`: it describes a Python placeholder filter and test that were deleted.**
- **Code:** both went in `b2451d86`. Only `HANDLE_PLACEHOLDERS` in `mcp/stdio/adapters/base.js:8`
  remains.
- **Fix:** rewrite as "the bridge is the only filter", and delete the sentence naming
  `test_hermes_session_discovery.py`.

**K34. `docs/ARCHITECTURE.md:101-102`: the e2e test does not drive the whole path.**
- **Code:** `test_message_to_work.py` never creates, claims or delivers a dispatch run; it covers only
  stored, readable and reply-threaded.
- **Fix:** "drives its two ends … The dispatch, claim and delivery steps between them are not in that
  test."

**K35. `CLAUDE.md:87-90`: there are six stamp-owned fields, not five.**
- **Code:** `build_dirty` is also stamp-owned (`service/config.py:20`, 0.7.0 `a50e6b69`).
- **Fix:** list all six.

**K36. `CLAUDE.md:123`: a no-evidence check reads `skipped`, not `unknown`.**
- **Claim:** "A check that gathered no evidence reports `unknown`, never ok."
- **Code:** no-evidence rows are `skipped` (`doctor.js:111`; `doctor-report.mjs:34-48` turns it into
  `ok:false, skipped:true`, excluded from `--strict`), or an `unknown-*` code.
- **Fix:** say that. Also give line 117's JSON envelope its real fields: `passed`, `failed`,
  `skipped`, `repo`, `service_url`, and `skipped?` per check.

---

## P3

**Main skill (my reads)**
- **K37. `SKILL.md:69-76` duplicates `references/operations.md:26-36` point for point (762
  characters).**
  - **Replace with:** "### Safe interruption\n\nBefore `comms_run_interrupt` (one exact run) or
    `comms_interrupt` (a managed console turn), follow `references/operations.md` (Interruption): one
    control, never a blind repeat, then verify the turn ended. A `STOP` in a message body is not an
    interrupt." (277 characters)
- **K38. `SKILL.md:93-95` restates what `register-identity.js:22-26` already prints with the warning
  (237 characters).** Delete it.
- **K39. `SKILL.md:197` repeats a prohibition (148 characters).** The `comms_listen` line repeats the
  tool's own description, and so does `operations.md:167`. Delete both; the tool text carries it.
- **K40. `SKILL.md:100`: "replaces any live instance … including a managed worker" holds only for an
  operator shell.** From inside an agent session the intent is `start` and a live instance refuses it
  with exit 75 (`aify-wrapper/lib/agent-lease.mjs:86-92`). Append: "(from an operator shell; a launch
  from inside an agent session is refused)", and pay for it with K37.
- **K41. teamwork.md uses `[REWORK]` (:71, :86) while it (:181), SKILL.md:173 and
  building-software.md:33 use `REVISE`.** Use `[REVISE]` throughout.
- **K42. leading-a-team.md says "peek before you probe" twice (:135, :181).** Delete :181; this pays
  for other bytes.
- **K43. `operations.md:75`: the `../../../../docs/…` links resolve in the checkout but not in the
  installed copy.** `~/.claude/skills/aify-comms/references` resolves to `~/docs`. Replace with
  "`docs/OPERATING_MODES.md` and `docs/ARCHITECTURE.md` in the aify-comms checkout".
- **K44. `teamwork.md:197` and `SKILL.md:128`:** "Do NOT just print the answer" and "never answer an
  acknowledgement with another acknowledgement" can be put positively, as the tool description already
  does ("leave it unanswered").

**Tool descriptions and bridge text (my reads)**
- **K45. `environment-tools.mjs:110` (`comms_spawn` runtime) and `registration-tool.mjs:91` still
  offer "opencode, or pi".** Replace with "claude-code, codex or hermes (pi deprecated)".
- **K46. `notify-notice.mjs:109` tells the agent "Reply via comms_send(… type="response" …)" under
  every notice, info included.** That contradicts SKILL.md:128. Prefix it: "When one owes a reply,
  reply via …".
- **K47. `inbox-tools.mjs:88`: in remote mode the truncation note is only "(Showing 20 of N)".**
  - Results are newest first (`inbox.py:108`), so a `peek=true` scan repeats the newest 20 and never
    surfaces older unread messages.
  - The local mode's note, by contrast, says "Use limit param for more". Add that hint to the remote
    note, or expose the route's new `offset`.
- **K48. `lifecycle-tools.mjs:188` (`comms_clear`) says "messages are auto-marked read".** A plain
  (non-peek) `comms_inbox` read marks them, and nothing else does. Replace with "reading them marks
  them read".

**Debug and install skills (reader-found)**
- **K49.** `dispatch-launch.md:44-46`: "30-60s" contradicts "for minutes". Replace with "leave a worker
  alone while its console still streams" (`managed_workers.py:214-220`).
- **K50.** `status-symptoms.md:12`: the sidecar marks turns busy with heartbeat `turnBusy`, not
  `/turn-start` (`claude-channel.js:204-210`).
- **K51.** `dispatch-launch.md:53`, `pi.md:18`, `codex.md:27`: there is no "Sessions -> Actions ->
  Reset". Use "Dashboard **Reset**" (`session-console.mjs:93`).
- **K52.** Debug `SKILL.md:44-45`: rewrite the prohibition positively: "After one
  stop/restart/interrupt, re-read ownership before any second."
- **K53.** Debug `SKILL.md:27`: the `claude-needs-channel` entry is in dispatch-delivery.md:87, not
  dispatch-bridges.md.
- **K54.** Debug `SKILL.md:8`: "Each entry gives a symptom, its cause, and the fix" is untrue for
  status-model and lifecycle. Delete it.
- **K55.** Duplicates and dead sentences. Fold them; they pay for the P2 fixes:
  - dispatch-delivery.md:134-136 repeats dispatch-launch.md:34-36;
  - status-symptoms.md:55-56 repeats dashboard-console.md:8-12;
  - dispatch-bridges.md:89-90 changes no decision;
  - dashboard-console.md:60-64 is a developer rebuild procedure without `stamp.sh`;
  - status-symptoms.md:95-96 is an open experiment and belongs in KNOWN_ISSUES.
- **K56.** `lifecycle.md:65` "relaunch it on 0.6.8+" is a stale version gate. `lifecycle.md:86`: the
  ordering guard fires only for steer=false (`send_preflight.py:143-156`).
- **K57.** `pi.md:41-50` describes an `omp-aify` wrapper that `install.sh:176-178` refuses to install.
- **K58.** `aify-comms-install/SKILL.md:73` "can reap managed workers" → "reaps its managed workers",
  matching the debug skill.

**Operator and architecture docs (reader-found)**
- **K59.** `UNINSTALL.md:6,8,137-150,179-193`: retitle the OpenCode and OMP sections as legacy
  cleanup. `:126-128` removes the shared `~/.local/bin/aify-comms` and state directory; say "only when
  removing the last client". The file never mentions the API key stored under `~/.aify/credentials`
  by `aify-env credential set` (`install.sh:2525`).
- **K60.** `TARGET_ARCHITECTURE.md:197,201`: aify-wrapper has four bins, not five, and `aify-env` has
  more subcommands than `doctor` and `tui`. `README.md:231-232`: the version badge compares the
  running build, not the checkout.
- **K61.** `ARCHITECTURE.md:30-31`: `bridge-current` reads the build from every liveness beat,
  sidecars included (`164ac617`), not only at registration. `:50` names a CLAUDE.md heading that has
  since changed. `:178` "nowhere else" ignores the four copies `bump-version.sh` writes.
- **K62.** `CLAUDE.md:79-80`: the lockfile's version is held by `test_repository_build_contract.py`,
  not the two tests named. `CLAUDE.md:216`: the SKILL.md limit is 16,000 characters
  (`ALWAYS_LOADED_LIMIT`), not 16 KB.

---

## The Claude Code comparison's ADOPT items (H-A)

The plan disposes of them as: H-A1, A2, A4, A5, A6 and A7 adopted; A3 and A8 backlog; A9 rejected.

| item | verdict | where it landed (v0.7.0) |
|---|---|---|
| **H-A1** one trust rule on every surface | **Landed** | `TRUST_RULE`, `mcp/stdio/tool-response-format.mjs:199-204`, used in `SAFETY_HEADER`, which inbox, listen, channel read and the notify notice prepend. Managed wake: `runtimes-prompts.js:39`. Channel: `claude-channel.js:189` (in the session instructions, not repeated on each event). SSE twin: `service/sse/rendering.py:23-25`. Skill: `SKILL.md:127`. |
| **H-A2** say what the sender knows until the reply | **Landed, partly** | `awaitingReplyNote`, `tool-response-format.mjs:170-174`, appended to the `comms_send` ack at `send-tools.mjs:112`. Skill: `SKILL.md:146`. **Not landed:** the channel-send ack the scan named (the scan's `send-tools.mjs:235`), and the SSE `comms_send` ack (no "arrives as a new message" anywhere in `service/`). |
| H-A3 verified `fromOperator` | Backlog (as planned) | `runtimes-prompts.js:9` still keys on `from === "dashboard"`. |
| **H-A4** notify hook: peek, reach the model, fence | **Landed** | peek: `notify-notice.mjs:27` (`&peek=1`, paged by `collectUnseen`, :42-62). Model channel: `hookOutput`, `:117-121` (`hookSpecificOutput.additionalContext` on Claude). Fence plus `SAFETY_HEADER`: `:95,:108`. Codex's hook JSON is still unverified, as the code comment at :114-116 says. |
| **H-A5** quote subjects on JS wake and read paths | **Landed** | `quoteUntrustedSubject(…, 240)` at `claude-channel-content.js:48,51`, `runtimes-prompts.js:73,108`, `tool-response-format.mjs:126,141` and `notify-notice.mjs:90`. |
| **H-A6** channel instructions name the tag attributes, plus the trust framing | **Landed** | `claude-channel.js:188-189`. Not live on this host: this session's channel instructions lack the sentence, so the installed bridge predates it. |
| **H-A7** `comms_agents`: id is the address, mark your own row | **Landed, partly** | Output header "Address an agent by the id at the start of its line." and the ` (you)` suffix: `agent-reporting-tools.mjs:52-59,65-71`. **Not landed:** the description (`:39`) is unchanged, so it still does not say that `offline`/`stopped` rows refuse sends or that `available` cold-starts; and the SSE twin has neither change. |
| H-A8 smaller worker tool set | Backlog (as planned) | 37 tools registered (`register-tools.mjs`). |
| H-A9 one "final text is not the reply" | Rejected (as planned) | `runtimes-prompts.js` still states it in several places. |

---

## Checked and sound

- **Tool map.** SKILL.md's Tool Map names exactly the 37 tools `register-tools.mjs` registers, no more
  and no fewer. The parameters the skill shows exist:
  - `comms_register`: `agentId`, `role`, `cwd`;
  - `comms_spawn`: `from`, `agentId`, `role`, `runtime`, `workspace`, `initialMessage`;
  - `comms_compact`: `mode="handoff"`, `newAgentId`;
  - `comms_console_tail`: default 40 lines;
  - `comms_unshare` and `comms_channel_delete`: the sharer or creator only.
- **`comms_inbox`.** Unread by default, limit 20. Viewing marks read, `mode=headers` included, and
  `peek=true` does none of the three writes (`inbox_read_receipts.py`). A non-peek read closes only
  `claimed`/`running` runs, never queued ones. `messageId` overrides the filter. SSE twin in step.
- **Reply contract.** `comms_send(type="response", inReplyTo=…)` is the reply, and the dashboard reply
  goes `to="dashboard"`. Default reply types are request/review/error. The dashboard is a store-only
  recipient. `queueIfBusy=true` forces the queue. The same-turn reply rule matches
  `claude-channel-content.js:37-41` and the channel instructions.
- **`clientNonce`.** Minted once per tool call (`send-tools.mjs:77-83`). The skill makes no claim about
  it, which is correct: an agent never passes one.
- **`comms_envs` and `comms_register`.** The `comms_envs` bracket, `advertised:` and `spawn UNPROVEN`
  wording match `environment-tools.mjs:48-72`. The `comms_register` identity warning matches
  `register-identity.js`. `POST /agents` exists.
- **Statuses.** The nine statuses in operations.md match `VALID_STATUSES`
  (`status_engine.py:18-20`), and "no idle/stale" holds.
- **`managed_reply_capture_fallback`.** Defaults to true (`settings_spec.py:136`).
- **Bounces.** The `[NOT DELIVERED] … up-but-deaf` bounce leading-a-team.md describes is real
  (`reconcilers/undeliverable_queued_runs.py:148,254`).
- **aify-env and the verifier.** `aify-comms` is a verifier only: `install.sh:1440-1474` handles
  `doctor`, `--check`, `--version` and `--help`, and exits 2 on anything else. operations.md:101-105
  agrees. No skill tells an agent to start or restart aify-env or to run a bare `aify-comms`; every
  mention hands that to the operator.
- **Wrapper flags.** `--safe`/`--no-auto` and the bypass flags match `claude-aify.sh.in:230-281`.
  `--resume` recovery goes through `agent-for-handle.mjs` in all three launchers.
- **Pi wrapper.** install.sh refuses the Pi wrapper install (`:170-179`), as operations.md:68 says.
- **Readers' verified items.**
  - Every doctor check id in CLAUDE.md, the README and the debug skill exists (21, one for one).
  - `MINIMUM_AIFY_ENV_VERSION` is 0.6.2, and `SPAWN_ORPHAN_GRACE_SECONDS` is 180.
  - All 10 layer-rule tests in ARCHITECTURE.md exist and assert their rule.
  - Every install.sh flag the docs name exists.
  - The bump script writes the four version files named.
  - The 1000-line gate and its empty allowlist are as described.
  - All the install skill's scripts and flags exist.
  - Every SKILL.md → references pointer in the debug skill resolves to what it promises.
- **Unverified (named, not assumed).**
  - Suite counts in CLAUDE.md:160-164 (not run).
  - Whether TUI-typed hermes turns run on the gateway host (K21).
  - Codex's handling of the notify hook's JSON.
  - Codex `approval_mode` semantics (codex.md:8-9).
  - The `hermes_state.SessionDB` one-liner (hermes-session.md:75-76).
  - Whether the ACP and app-server native fallbacks are reachable in 0.7.
