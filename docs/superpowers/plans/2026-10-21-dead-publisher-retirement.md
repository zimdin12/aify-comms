# Retiring a dead publisher's key (design rev 3, 0.9.8 pre-switch)

Asked for by the SW1 verdict (1792531860902-a0541994). Revised for `REVISE_DESIGN_D48B57B0_OWNED_WORK_COMPLETENESS`,
then `REVISE_DESIGN_5F9F95B0_REMAINING_R1_UNREPORTED_MEMBER` (1792535719570-c42c5dd4). Design only: nothing is built,
swept or cleaned up. The wired switch stays on HOLD.

## Decision: defer the automatic proof; an operator-attested verb now; containment as a named later slice

**The problem.** 10 dead single-use `herdr-<invocation>` keys hold agents unknown forever (appendix A). SW2 knows an
agent only when every holder is fresh and nothing on its machine is unreadable (`operational_view._busy`), and
`readInstanceContext` refuses a used invocation, so no successor ever publishes under one of them. **The truth table
stays as it is.**

**Why it is deferred.** An automatic proof must show that every unit of owned work ended, including a descendant that
never reported. That is an OS fact only under containment, and nothing in place today provides it (read on Windows,
next section). A record of intents and pins cannot tell "the agent ended unreported" from "the agent lives
unreported". So **automatic retirement is deferred until a containment slice exists**. The rev 2 ledger is withdrawn
as a completeness claim.

## What exists today (Windows read from source; Linux ASSUMED)

- **aify-env (`ccf9a7e`): no Job Object, cgroup or subreaper.**
  - `killTreePlan` (`lib/kill-tree.mjs`) is `taskkill /PID /T /F` on Windows, a parent-pid walk, and `kill(-pid)`
    (the process group) on POSIX.
  - `Runner.stop` also uses node-pty's ConPTY console-list kill, which reaches the agent because it is attached to
    that console.
  - Neither one is a container: a walk loses a descendant whose parent died, a process can leave a console, and
    `setsid` leaves a group.
- **herdr (upstream `0.9.3` Windows source, `~/projects/herdr-diagnosis-20261005`):**
  - A pane's processes come from `session_processes`, a Toolhelp parent-pid walk; shutdown signals that list.
  - A Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` exists only in `StatusCommandGuard`, for status commands.
    It is created suspended and assigned before resume.
  - The installed `0.9.5` binary imports the same job APIs (string scan); that does not show any per-pane use.
  - The source copy has no Unix file, so herdr on Linux is **ASSUMED** unread.
- **Linux aify-env:** process groups only, by the same source. Nothing is verified on a live Linux host from here.

## Refusal rules (closing the remaining R1)

1. **Member completeness unknown refuses**, whether the root is alive, dead or reused. Without containment it is
   always unknown, so the automatic path never accepts.
2. **A recorded `ended` needs the same completeness.** It is never a bypass.
3. The accepted rules stay:
   - the legacy keys stay unproven;
   - the lifetime join stays: a row's lifetime must be in the body, and its pid one of that lifetime's pins;
   - the canonical body facts exclude probe answers;
   - the retained cursor is the fence. A lower generation is `stale`; an equal one is `conflict` or `duplicate`.
     No higher generation is ever invented, and nothing is forgotten or deleted.

## Near term: an operator-attested retirement verb

- **Command.** `aify-env publisher retire <full key>`. It runs only on Steven's explicit command, for one exact key per
  call. It is executed by the residents-role holder (`ResidentsRole`, `enter()`/`live()`), so there is one writer.
  It never touches `default`, the residents key, a key whose descriptor process is alive, or its own key. **It kills
  nothing.**
- **Named pid check first.** Before asking for confirmation, it prints that key's facts, each with its probe answer
  (alive with creation time / gone / reused / unknown):
  - the descriptor and `claimed.json` pid;
  - the owner pipe;
  - every `owned-processes.json` entry, for example pid 29316 for f17d5dd4;
  - every pid in comms' last-known rows for that key, read from comms.

  Steven confirms by typing the key. An unknown answer is shown as unknown, never as gone.
- **Body.** `retired` at saved generation + 1 (the `stale`/`conflict`/`duplicate` rules above), with
  `incarnationId` `"operator:<key>"` and `reason` `"operator-attested"`. `inputs` carries the confirmed key and the
  printed facts' digest. **It is an attestation, not a proof**, and the reason says so on the wire.
- **comms.** `RETIREMENT_REASONS` (`service/api_core/agent_state_shadow.py`) gains `operator-attested`. It is applied
  like B5's `_retire`, and the cursor is kept. comms also refuses a key that is still fresh; that check uses Steven's
  pending receipt window (decision 2 below).
- **Copies.** comms `RETIREMENT_REASONS`; env, the verb plus a pure body builder beside `AgentStatePublisher.retire`;
  the fixtures `service/tests/data/g6_agent_state_wire.mjs` and `test_agent_state_receiver.py`. aify-wrapper has no
  copy. The env change merges only after the comms and wrapper suites pass with `AIFY_ENV_REPO` set to the candidate.

## Later slice: containment, so that "empty" is an OS fact

- **Windows (verifiable here):** a per-lifetime Job Object created by the aify launcher before the agent starts. It
  is the root's own `KILL_ON_JOB_CLOSE` job, with no breakaway, and the launcher creates the agent suspended and
  assigns it before resume, as `StatusCommandGuard` does. The root holds the only handle.
  - Root dead means the job closed, which means every member was killed, reported or not.
  - The root's pin records the job attestation (`IsProcessInJob`, limit flags from `QueryInformationJobObject`)
    before any child. A live root is judged by `JobObjectBasicProcessIdList` being empty.
  - Open, unread: whether herdr's pane spawn and node-pty's ConPTY permit it.
- **Linux (ASSUMED, not verifiable here):** a per-lifetime cgroup v2, created before exec. Completeness is
  `cgroup.events` showing `populated 0`. A subreaper with a pidfd is weaker, because orphans reparent to init once
  the subreaper dies.

## Witnesses (planned, each watched RED before its fix)

| # | specimen | expected |
|---|---|---|
| W1 | root R pinned and dead; agent A alive, never reported; ledger readable; no comms row shows A | automatic: refused (completeness unknown) |
| W2 | a member pin write fails after a valid prefix | refused |
| W3 | `ended` recorded, membership unknown | refused |
| W4 | old-ended-P / new-live-P (L1 P@t1 ended, L2 P@t2 alive) | refused |
| W5 | unrelated reuse of P, membership complete under containment | proven (containment slice) |
| W6 | all ended, membership independently complete: a kill-on-close job attested at start, root dead | proven (containment slice) |
| W7 | the verb without an exact key, or on `default`, the residents key, a live descriptor, or its own key | refused, nothing posted |
| W8 | the verb on a stale dead key after confirmation | `operator-attested` applied; a later old-generation frame `stale` |
| W9 | the verb with the probe answering null | facts printed as unknown; the confirmation still required |

## Steven's decisions (open)

1. Adopt this route: automatic deferred, the attested verb now, containment as a later slice.
2. The receipt window comms' freshness refusal uses (01M5D9WR).
3. Which legacy keys to retire with the verb, after its pid check, including f17d5dd4 and its recorded pid 29316.

## Appendix A: observed population (not a cleanup manifest)

These are observations from the 2026-10-20T21:31Z comms start log and a read-only file listing. No pid was re-probed.
**It authorizes nothing.** Each retirement is a separate command by Steven, after its own pid check.
- live: `herdr-c0912a9d-4247-48a0-b1ad-a29fbd22e54d` (pid 84272), and `residents-1212b94bbe48`
- dead, unproven:
  - `herdr-f17d5dd4-b166-4709-8a7f-74fd3aeadfa5`: its `owned-processes.json` names pid 29316, `claude-aify`, owner 73960
  - `herdr-9821d407-95ef-4bbd-8c22-2b0a8a16eb6e`, `herdr-78cfcc61-d9a3-49ec-967a-0759ecbcf347`,
    `herdr-069e3559-fa62-4e79-a01e-b9940b28acd2`, `herdr-b68089e7-0bf4-4598-935e-e32805fda441`,
    `herdr-20ecafd8-463a-4739-b0ae-6cfeecf8b8c5`, `herdr-9add1a1b-e8a6-4055-b840-ec5907c50137`,
    `herdr-70d0e9be-74d4-4218-8bc7-3f00eca1fee7`, `herdr-ff4ad8b8-be99-4bfb-b960-e17e80174455`,
    `herdr-0d278add-c0ec-401b-aa94-7a4f9295ebb8`: their `owned-processes.json` files are 3 bytes, which proves nothing
    either way
