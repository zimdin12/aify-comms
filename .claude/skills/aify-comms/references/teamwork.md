# aify-comms Teamwork Reference

Load this file when coordinating an autonomous team, assigning/reviewing lanes, compacting/rebriefing agents, or diagnosing why the work loop lost momentum.

**These rules are about coordination, not about code.** Software examples appear throughout because
that is the most common use, but every rule here is meant to hold for research, testing, analysis,
design, construction, finance, or any other work split across people who cannot see each other's
desks. Where a rule names something code-specific — a commit, a diff, a test — read it as "the
pinned version of the source", "the artifact as written", "the check that it actually works", and
apply the general form. If a rule only makes sense for code, it is written wrong.

## Roles

- `manager`: owns priority, routing, deadlines, blockers, and dashboard-facing status. Splits work into owner-specific contracts and follows up on overdue contracts.
- `architect` / tech lead: owns design boundaries, review quality, integration order, and rework decisions. Converts broad goals into bounded implementation lanes.
- `coder`: implements bounded chunks, self-checks, reports exact evidence, and asks for review when the slice is ready.
- `tester` / reviewer: verifies behaviour, regressions and risks. Reports evidence, not vague confidence.
- `operator`: manages environments, sessions, runtime settings, compaction, recovery.
- `driver`/owner: owns the integrated result end-to-end and the seams between lanes; personally exercises the whole thing before "done." Distinct from per-lane ownership — tracking status is not the same as owning that it works. Usually the lead also drives. (See `references/building-software.md`.)

Roles are operating modes, not rigid permissions. The owner of a contract acts; everyone else passes.

## Work Contract Shape

A good request says:

1. Owner: who should act.
2. Scope: the exact file/task/lane.
3. Completion condition: what proves done.
4. Evidence expected: tests, git status, screenshots, logs, or specific reads.
5. Reply target: who must receive `[REVIEW]`, `[HOLD]`, `[BLOCKED]`, or final status.
6. Next wake rule: whether the recipient should self-continue, ask review, or stop.

Keep one contract per message when possible. If a message bundles unrelated topics, answer the blocker and propose a split.

**Point at a pinned VERSION of the source, and say it outranks your message.** Whenever a task
rests on something that can be revised — a spec, plan, drawing, dataset, price list, prior result —
name the exact version the worker must work from, in whatever way that medium can be pinned: a
commit and content hash, a revision letter and issue date, a snapshot timestamp, a document
version plus page. Then say which wins: *"work from THAT text; if you find yourself doing something
it does not say, stop and amend it first — the source is the authority now, not this message."*
A message is a snapshot of one person's understanding at send time; a pinned artifact is checkable
and cannot drift under the worker. Skipping this produces the argument where both sides are certain
and neither is wrong, because they were working from different revisions. A builder working to
superseded drawings and an analyst working from last week's figures are the same failure.

**Name what is OUT of scope — especially something the worker just discovered.** A brief that
bounds the work but not the findings invites scope creep in its most tempting form: an adjacent
problem spotted mid-task, which is genuinely worth fixing and is not this job. Record it as its own
item, then say plainly not to touch it in this pass.

**Changing a task already in flight is its own message — label it as one.** Say so in the subject
(`SCOPE WIDENED MID-FLIGHT`, `HOLD`, `AMENDMENT`), state exactly what changed, and rule explicitly
on the work already done: kept, discarded, or superseded, and why. Quietly sending a bigger version
of the same brief makes the worker guess whether to restart — and a worker who guesses wrong loses
everything done so far.

**A request must carry its own reply contract.** Say who replies, what closes the task, and by
when. Measured across 27.6k messages here, requests under 1k characters go unanswered ~12% of the time
versus ~3-4% for 1-3k (excluding self-wakes and reminders, which need no reply). Not because they
are short — because a brief message is the one most likely to omit the ask, the owner and the
completion condition, so the recipient never registers that a reply is owed. This is why the rule is "smallest message that lets
the recipient act correctly", not "shortest message".

## Autonomous Loop

Default lane loop:

1. Manager/lead sends a bounded `request`.
2. Worker reads exact docs/files needed, implements, verifies, and replies with `[REVIEW]` or `[HOLD]`.
3. Lead verifies on disk and replies `[APPROVE]`, `[REWORK]`, or `[BLOCKED]`.
4. Worker fixes rework or continues to the next bounded slice.
5. Before "done," the driver INTEGRATES and behaviorally verifies the WHOLE — the end-to-end flow plus the cross-cutting concerns no single lane owns (controls/UX consistency, data across layers, auth→action→persistence, restart/recovery) — not just each approved slice. Per-slice APPROVE is not product-works.
6. Manager reports only meaningful decisions/progress to dashboard.

Agents may work in parallel when lanes are independent. Every parallel request must name the expected reply target and completion condition so results wake the correct owner.

## Message Labels

Use labels in subjects when they reduce ambiguity:

- `[PLAN]`: proposed split or approach.
- `[IMPLEMENT]`: bounded coding request.
- `[REVIEW]`: worker believes the slice is ready.
- `[APPROVE]`: reviewer accepts and may authorize commit/next slice.
- `[REWORK]`: exact change needed before acceptance.
- `[HOLD]`: grounded stop because safe progress needs a decision/evidence.
- `[BLOCKED]`: external blocker; name owner and required unblock.
- `[STATUS]`: evidence-backed status, not a promise.
- `[COMMIT]`: the result landed — the identifier it landed under (commit, revision, document version, order number) plus how it was verified.

Labels are not theatre — the body must carry evidence or the exact ask.

## Right-size the message

Send the smallest message that lets the recipient act correctly. Terseness is not the goal;
avoid both missing inputs and repeated context.

- **Send the delta** — the new decision, result, blocker, or ask; do not restate an agreed plan.
- **Route outcomes, not hops.** The cheapest message to write and the least useful to receive is
  one telling party C what you already sent party B — "adopted verbatim and sent", "relayed, thanks".
  It reads as diligence and gives the recipient nothing to do. Whoever advised you does need to
  learn the OUTCOME — adopted and done, or rejected and why, so they neither re-raise it nor assume
  silence meant no — but they do not need a receipt for each hop in between. A coordinator who
  narrates every hop turns a two-party handoff into a three-party thread and doubles the traffic
  without adding a decision. (Observed: a team that stopped relaying through a third party halved its volume with no loss of
  work in flight.)
- **The subject line is an index entry, not the message.** Keep it to one scannable line: what
  changed or what is being asked, plus the identifier. It is what a teammate scans in a list and
  what a coordinator greps months later. Never let reply prefixes accumulate — rewrite the subject
  instead of nesting `Re: … Re: … latest: …`. (Measured here: 2,021 subjects over 200 characters, worst case 1,834 — a whole report in the
  subject line, so read by nobody and searched by nobody.)
- **Keep intent inline.** Put the owner, decision, ask, and completion condition in the message. Point to a shared path or `comms_share` artifact for bulky detail.
- **PASS is a move. Make it.** When a message carries no decision, evidence or ask for you, PASS:
  read it, mark it, let the thread settle. A terminal `APPROVE`, result or thanks is where a
  thread is meant to stop. Written as an action rather than a ban, because a ban puts the banned
  behaviour in front of you. (Hermes Bot Mode: bots "reply briefly or pass"; a room settles when
  a full round stays silent.)
- **Promises need a wake.** Before ending a turn with future work, create the follow-up contract or self-wake. Written `Next:` text is not scheduling.
- **DM owners; channel shared state.** Post a settled decision once on the team channel. Send a DM only to the person who must act. The same text to several people belongs on a channel.
- **Delegate inside your lane; route across lanes.** Native subagents can help an owner; they must not shadow another teammate's role.

When unsure, ask: "Does the recipient need this and not already have it?" If not, act instead
of sending another message.

## Coordinating the work

Delegation, reinforcement, getting unstuck, and the manager's operational levers live in
[leading-a-team.md](leading-a-team.md). Load that when you are the one assigning work.

## Worker Discipline

- Do not answer with "on it" repeatedly. Start work, send a short ack only when useful, then return evidence.
- **A reply-overdue reminder asks you to CLOSE the contract, not to write a progress essay.**
  If the owed reply is ready, send it. If work is genuinely still in flight, ONE line —
  status + ETA — not a 2KB unrequested report. (Observed: a reminder fired mid-work
  triggered a 2,066-char status essay nobody asked for.)
- Self-continue only for a known next chunk. Do not create infinite self-wake loops.
- **A watchdog that polls faster than the thing can change is a spin loop.** Before re-waking to
  check on something, ask how long it could possibly take to change. Wait at least that long.
  Concrete cures overnight, a review takes minutes, a build takes as long as a build takes — polling
  every 30 seconds cannot make any of them finish sooner, it just spends your turns and your
  context. Three requirements for any self-wake:
  1. **An exit condition**, written down before the first wake — what you are waiting for, and what
     you will do when it arrives.
  2. **Back off when nothing changed.** If a wake observes no change, the next interval must be
     LONGER than the last. Same-interval polling is the loop.
  3. **A give-up count.** After N consecutive no-change wakes, stop and report or escalate. An
     agent silently watching forever looks identical to one that is working.

  (Measured here: one agent self-woke 1,248 times at a median gap of 27 seconds — 10% of them under
  4 seconds — accounting for half its entire outbound volume. Teammates on the same task doing
  comparable work sat at 1-2%. The platform did not require this; the loop was self-inflicted, and
  the tell was in the subjects, which had degenerated into `Re: Pending updates; latest: Re:
  Pending updates; latest: watchdog…`.)
- **Absence of visible output is not absence of progress.** Do not treat "no files written yet" or
  "nothing posted yet" as evidence of a stall a few minutes in — thinking, reading, measuring and
  waiting all look identical to idleness from outside. Check what the worker is actually doing
  before you chase it, and give the work a plausible amount of time first.
- If you cannot safely proceed, send `[HOLD]` with exact evidence checked and the narrow decision needed.
- For long output, share an artifact and send a short summary.
- Automated tests for real behavior are part of "done," not optional; architect for testability (e.g. an app-factory seam). An `[APPROVE]` should be backed by tests passing. (See `references/building-software.md`.)

## Review Discipline

- Distinguish INSPECTION (read the thing as written — the diff, the drawing, the spreadsheet, the protocol) from BEHAVIORAL VERIFICATION (run it, measure it, walk it, try it as the user would). **State which one you did**, because they fail differently: inspection catches what is wrong in the description, verification catches what is wrong in reality, and only the second catches "correct as specified, wrong in use". For anything user-facing, physical, render-, feel- or integration-affecting, behavioral verification is REQUIRED — a design can be right on paper and wrong in the room.
- Keep rework narrow and actionable.
- If a review finds no issue, say what was checked and what risk remains.
- **A negative result is only evidence if your method could have found the thing. Say what proved
  it could.** "Nothing found", "no matches", "no defects" mean nothing alone — first point the
  method at something you KNOW is there and watch it register. Absence measured with a proven-live
  instrument is a finding; absence from an unverified one is just silence, and the two look
  identical on the page. It cuts both ways: a filter matching too MUCH lies just as confidently.
  Applies far outside code — the record search with the wrong date range, the survey that never
  reached the population, the rig that was not plugged in. (Self-inflicted here twice in one
  session: a filter on subjects *containing* "Restart" silently matched ordinary chat messages and
  produced a confident, wrong conclusion; and a finding that "short messages get ignored" turned out
  to be mostly self-addressed notes needing no reply — the number was real, the interpretation was
  not.) **Check what your method matched, not only how much it returned.**
- Do not approve broad "done" claims without evidence.
- **End every review with an explicit verdict, not prose.** Reply `inReplyTo` the work request with a clear `APPROVE` or `REVISE` as the first line (then the evidence/rework). `APPROVE` is the signal that closes the loop and lets the manager ship; `REVISE` must list the specific, checkable changes needed. A workflow keeps cycling (implement → review → revise) until a reviewer returns `APPROVE` — that token is the completion contract, so never leave a review ambiguous about which it is. (Explicit accept/revise termination — cf. "TRINITY" Verifier ACCEPT, arXiv:2512.04695.)

## Dashboard User

The dashboard user is the human/operator. Dashboard chat rides the aify-comms transport, so a dashboard-managed run replies the same way as any aify-comms message: `comms_send(type="response", inReplyTo="<message id>", to="dashboard")`. That threads into dashboard chat and closes the run. Your final plain text is your own working output, not the chat reply.

When the human asks "what happened", inspect messages/runs/contracts first. Do not summarize from memory if the system has data.

## Reply on the surface you received

**Use one human-facing surface per interaction**, inferred from where the request arrived. An
explicit "talk in comms" / "talk in terminal" overrides that until the user changes it.

- Arrived as a `<channel source="aify-comms-channel" ...>` event → reply with
  `comms_send(type="response", inReplyTo="<message id>", ...)`. Do NOT just print the answer: the
  sender is not watching your terminal, they are waiting on the threaded reply.
- Typed directly into your CLI (operator at your keyboard) → reply in the CLI. Do not `comms_send`
  back unless they asked for a dashboard update.
- A dashboard-managed run with `inReplyTo` in its metadata → `comms_send(type="response",
  inReplyTo="<message id>", to="dashboard")`; that closes the run and threads into chat.
- Agent-to-agent is the same rule: B replies to A with `inReplyTo` = A's message id.

**Comms-observed work needs no terminal narration** (nobody is reading it), and terminal-observed
work needs no duplicate comms copy. Every channel has its own thread: replying on a different
surface breaks threading, hides the answer from the sender, and duplicates context. If a request
produces output for several surfaces, the primary reply goes back where it came from; supplementary
notifications go to whoever else needs them.
