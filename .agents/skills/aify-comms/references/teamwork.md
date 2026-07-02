# aify-comms Teamwork Reference

Load this file when coordinating an autonomous team, assigning/reviewing lanes, compacting/rebriefing agents, or diagnosing why the work loop lost momentum.

## Roles

- `manager`: owns priority, routing, deadlines, blockers, and dashboard-facing status. Splits work into owner-specific contracts and follows up on overdue contracts.
- `architect` / tech lead: owns design boundaries, review quality, integration order, and rework decisions. Converts broad goals into bounded implementation lanes.
- `coder`: implements bounded chunks, self-checks, reports exact evidence, and asks for review when the slice is ready.
- `tester` / reviewer: verifies behavior, regressions, and risks. Reports evidence and blockers, not vague confidence.
- `operator`: manages environments, sessions, runtime settings, compaction, and recovery.
- `driver`/owner: owns the integrated product end-to-end and the seams between lanes; personally exercises the whole experience before "done." Distinct from per-lane ownership — dashboard-status and integration-order are not the same as owning that the product works. Usually the manager/lead also drives. (See `references/building-software.md`.)

Roles are operating modes, not rigid permissions. The current owner of a contract should act; others should avoid broad unsolicited acknowledgements.

## Work Contract Shape

A good request says:

1. Owner: who should act.
2. Scope: the exact file/task/lane.
3. Completion condition: what proves done.
4. Evidence expected: tests, git status, screenshots, logs, or specific reads.
5. Reply target: who must receive `[REVIEW]`, `[HOLD]`, `[BLOCKED]`, or final status.
6. Next wake rule: whether the recipient should self-continue, ask review, or stop.

Keep one contract per message when possible. If a message bundles unrelated topics, answer the blocker and propose a split.

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
- `[COMMIT]`: commit result with hash and verification.

Do not use labels as theater. The body must include evidence or the exact ask.

## Right-size the message — you decide how much the recipient needs

You have the context; judge how much the recipient actually needs to act correctly, and
send exactly that. There is no word limit and no virtue in terseness for its own sake —
sometimes the right message is a full spec with evidence, sometimes it's one line. **The
test is always: does this give the recipient something they need and don't already have?**

Both directions are failures:

- **Under-communicating borks the work.** If the recipient needs a decision, the evidence,
  a blocker, the exact file/criteria, or a real heads-up to act correctly, send it IN FULL.
  Never strip out information they need just to "be brief." A missing detail costs a
  clarify round-trip (or a wrong build) far more than a few extra sentences.
- **Over-communicating stalls the work.** The most common live pattern is the
  **confirmation loop ("violent agreement")**: after a decision is settled, agents keep
  sending long messages that re-state the whole agreed plan and re-affirm alignment —
  "Agreed — …", "Fully aligned, nothing to add", "Locked, standing by". Those carry ~zero
  new information; they just add round-trips and burn context. (A real thread ran ~1300
  messages between two agents on ONE experiment this way — almost all mutual
  re-confirmation, with the one genuinely-useful heads-up buried inside.)

Judgment rules (apply the test above, don't apply them mechanically):

- **Send the DELTA — what's new or what they need — not a re-summary of what they already
  have.** New decision, result, blocker, or heads-up: yes. Re-pasting the agreed plan the
  recipient wrote half of: no. If you have nothing new *and* they need nothing, that's a
  cue to ACT, not to write.
- **Point at bulky content; don't paste it. Reference if co-located, share if not.** When
  you'd otherwise inline a large brief / diff / spec / log, send a pointer instead — the
  content then lives in ONE place and the recipient pulls only the part it needs, instead
  of the whole payload being copied into every recipient's context (and the DB + envelope)
  on every send. If the recipient shares your workspace, cite the **path** (`tmp/brief.md`,
  `src/engine/World.h:118`); if they're in a different folder/env, `comms_share` it once and send
  the one-line pointer (an artifact is also an immutable snapshot — safer for "this exact
  state" than a live file). **But keep the decision, the ask, and the delta INLINE** —
  reference the detail, not the intent; a bare "see tmp/x.md" just forces a blind read.
  Small content (a number, a one-line status, the actual decision) stays inline — a pointer
  isn't worth a tool round-trip for a sentence.
- **You don't owe a message just to acknowledge.** If a reply is required to CLOSE A RUN
  (see "Reply on the surface you received"), send that one and make it terminal — an
  explicit stop marker ("Proceeding, no reply needed", `[APPROVE]`, `[COMMIT]`) — then the
  recipient treats it as the end and does NOT write back "sounds good". Don't stack extra
  confirmations on top of the one that closes the loop. **An acknowledgment never earns a
  reply: a "thanks" ENDS the thread.** (Real teams have burned full agent turns exchanging
  "Re: standing down — thanks" → "Re: standing down" — 129 characters, two context re-reads.)
- **Post a settled decision once, on a channel — then point at it instead of re-pasting.**
  When a contract/criteria/target is locked, `comms_channel_send` it once as the shared
  durable record; the re-confirmation loop is usually a symptom of there being no single
  agreed record everyone can re-read.
- **Two agents re-confirming alignment is a smell.** Once aligned, the owner ACTS and
  reports the RESULT; the reviewer waits for that result. Neither needs another "we're
  aligned."
- **Address the agent who can act — don't broadcast outside the team/project.** Send a
  decision/request to the specific owner(s) on THIS project. Broadcasting a team decision
  to agents on a different repo/team just burns their context and earns a "wrong project,
  I can't run that" bounce (it has happened). If you genuinely need many owners, name each
  and what you want from them; if it's a shared record, put it on the team channel.
- **Same text to 3+ teammates = a channel post, not N DMs.** Post ONCE to the team channel
  and name who must act; DM only that person. Prefixing DMs with "#channel:" is channel
  emulation at N× the cost — every copy wakes a full agent turn, and recipients who weren't
  the owner burn further turns declining ("this landed in my inbox but per the review matrix
  it's not mine"). A real team's ×4 review broadcasts turned 8 interactions into 26
  deliveries plus 4 pure-misroute turns; the one team that posted matrix/READY/verdict to
  the channel once each ran the same task shape with zero acks, zero misroutes.
- **Delegate within your lane; route across lanes; keep the team aware via the record.**
  Inside your own lane, your runtime's native delegation (claude-code subagents, hermes
  `delegate_task`, codex multi-agent) is the cheap tool for fan-out research/edits/
  verification. Work belonging to another ROLE goes to that teammate — a manager with a
  coder on the team routes coding to the coder, never spawns a shadow one. And shared
  awareness comes from the durable record (a channel post of the settled decision), not
  from CCing everyone in DMs — teammates who need the context should be able to find it,
  without each getting a wake-turn.

## Source-of-truth docs

Every team needs a small set of durable documents that outlive any one session: the plan,
settled decisions/contracts, and task state. **One agent owns them** — usually the
manager/driver (a dedicated docs role on bigger teams). If they don't exist, the owner
creates them at kickoff; "we'll remember" is not a plan.

- **Reference the doc instead of re-explaining.** When a teammate asks something the doc
  answers, point at the section. If the doc couldn't answer it, that's a doc bug — the
  owner fixes it when the answer lands.
- **Updating the doc IS closing the loop.** When a decision settles, the owner writes it
  into the doc (and drops one channel pointer); that beats N confirmation DMs.
- **Right-size for agent browsing.** Split any doc growing past a few hundred lines —
  same rule as code files: agents can't find things in a 1000-line monolith. Too thin is
  the other failure: a doc that can't answer real questions just sends readers back to DMs.

## Manager Discipline

- **A `[NOT DELIVERED] … up-but-deaf` bounce means the target's WORKER IS DEAD — resending
  cannot revive it.** Send-to-wake needs a live claim path; a dead worker has none. Don't
  retry the send: Restart that agent's session (dashboard Sessions → Restart, or ask the
  operator), verify it's back (console shows the runtime banner), THEN resend the lane
  once. If several teammates bounce at once, say so in one message to the operator — it's
  usually one shared cause (bridge restart, runtime update), not N separate failures.
- **Stuck? Peek before you re-spawn or remind.** When an agent looks stalled or owes an overdue reply, read what it is actually doing first — `comms_console_tail(agentId="...")` for a managed agent, or a focused `[STATUS]` probe for a resident — BEFORE you re-spawn it or fire a reminder. The console reveals mid-build vs waiting-at-a-prompt vs looping vs errored; reach for it as the reflex, not the filesystem.
- **Right-size the rigor.** Scale review depth and teammate count to task complexity and risk. Do not run the full multi-reviewer gauntlet on trivial/low-risk work — more agents and more review rounds are a COST, not a virtue; spend them where they buy something. (See `references/building-software.md`.)
- **Scope the context you hand down.** When you delegate, give each agent only the inputs that subtask needs — the specific file, the one prior result, the exact decision — not the whole thread. Broadcasting full history burns the delegate's context and tokens for no benefit, and a focused brief gets a sharper answer. If two agents don't need each other's output, don't cross-pollinate it; if one does, name the exact artifact (`comms_share` + a one-line pointer) rather than pasting it. Put shared DECISIONS everyone needs (frozen contracts, API shapes, integration order) on a team CHANNEL via `comms_channel_send`, not scattered across DMs — DMs are for owned 1:1 handoffs. (Context-scoping discipline — cf. the "Conductor" access-list idea, arXiv:2512.04388.)
- Check `comms_contracts` and `comms_agent_info` before assuming who is idle or stuck.
- If an agent is active but not working and owes a contract, send a focused status probe or rebrief.
- **Peek before you probe (managed agents).** When status alone is ambiguous — an agent shows `working` but owes an overdue reply, or you're sweeping the team on a heartbeat/monitoring loop — read what it is actually doing with `comms_console_tail(agentId="...")` (read-only, last 40 lines by default). The console reveals whether it's mid-build, waiting at a prompt, looping, or errored — detail that status can't convey. This is often faster and cheaper than a status round-trip.
- To recover a managed agent stuck at a prompt, `comms_console_input(agentId="...", text="...")` types into its console (empty text just sends Enter to unstick). Audited; use sparingly — prefer `comms_send` for normal work.
- **Console tools are managed-only.** Resident agents have no aify-owned console, so `comms_console_tail`/`comms_console_input` report "no live console." For a resident agent your levers are `comms_send` (ask for a `[STATUS]` with evidence) and the dashboard; **Switch to managed** if you need a console to peek into.
- If a worker replies with repeated vague status, demand `[REVIEW]` or `[HOLD]` with evidence.
- If context is noisy, use handoff compact/rebrief: `comms_compact(from="you", targetAgentId="other", mode="handoff")` compacts **another** managed agent by spawning a fresh managed backing seeded with a handoff packet (recent messages + your instructions). It is NOT the runtime's native `/compact`, and it needs a managed backing — a resident-only agent can't be compacted this way. Keep the same agent ID unless intentionally splitting identity (`newAgentId`). To trigger a managed PTY runtime's own in-place `/compact` (claude-code/codex/hermes), type it via `comms_console_input(agentId="...", text="/compact")` while the agent is at its prompt; for a resident agent, `comms_send` a request asking it to `/compact` itself.
- Avoid long dashboard updates. Tell the human what changed, what was verified, what remains blocked, and the next owner.

## Worker Discipline

- Do not answer with "on it" repeatedly. Start work, send a short ack only when useful, then return evidence.
- **A reply-overdue reminder asks you to CLOSE the contract, not to write a progress essay.**
  If the owed reply is ready, send it. If work is genuinely still in flight, ONE line —
  status + ETA — not a 2KB unrequested report. (Observed: a reminder fired mid-work
  triggered a 2,066-char status essay nobody asked for.)
- Self-continue only for a known next chunk. Do not create infinite self-wake loops.
- If you cannot safely proceed, send `[HOLD]` with exact evidence checked and the narrow decision needed.
- For long output, share an artifact and send a short summary.
- Automated tests for real behavior are part of "done," not optional; architect for testability (e.g. an app-factory seam). An `[APPROVE]` should be backed by tests passing. (See `references/building-software.md`.)

## Review Discipline

- Distinguish CODE REVIEW (read the diff on disk) from BEHAVIORAL VERIFICATION (run it / measure it). For any user-facing, render-, feel-, or integration-affecting change, behavioral verification is REQUIRED — code review alone misses render/feel/integration bugs. State which you did.
- Keep rework narrow and actionable.
- If a review finds no issue, say what was checked and what risk remains.
- Do not approve broad "done" claims without evidence.
- **End every review with an explicit verdict, not prose.** Reply `inReplyTo` the work request with a clear `APPROVE` or `REVISE` as the first line (then the evidence/rework). `APPROVE` is the signal that closes the loop and lets the manager ship; `REVISE` must list the specific, checkable changes needed. A workflow keeps cycling (implement → review → revise) until a reviewer returns `APPROVE` — that token is the completion contract, so never leave a review ambiguous about which it is. (Explicit accept/revise termination — cf. "TRINITY" Verifier ACCEPT, arXiv:2512.04695.)

## Dashboard User

The dashboard user is the human/operator. Dashboard chat rides the aify-comms transport, so a dashboard-managed run replies the same way as any aify-comms message: `comms_send(type="response", inReplyTo="<message id>", to="dashboard")`. That threads into dashboard chat and closes the run. Your final plain text is your own working output, not the chat reply.

When the human asks "what happened", inspect messages/runs/contracts first. Do not summarize from memory if the system has data.

## Reply on the surface you received

**Default rule: reply on the same surface the request arrived on, unless explicitly directed otherwise.**

- Request arrived as a `<channel source="aify-comms-channel" ...>` event (someone called `comms_send` to you) → reply with `comms_send(type="response", inReplyTo="<message id>", ...)`. Do NOT just print the answer as terminal output; the sender is not watching your terminal — they're waiting for the threaded reply via the bridge.
- Request typed directly into your CLI (operator at your keyboard) → reply in the CLI / final plain text. Do not `comms_send` back to the operator unless they specifically asked for a dashboard update.
- A dashboard-managed delivered run with `inReplyTo` in the metadata → reply with `comms_send(type="response", inReplyTo="<message id>", to="dashboard")`; that closes the run and threads into chat. (Only if `managed_reply_capture_fallback` is enabled does an unanswered run auto-mirror its summary as a fallback — don't rely on it.)
- Same rule for agent-to-agent: A sends `comms_send` to B → B replies with `comms_send(type="response", inReplyTo=A's-message-id, to="A")`. Don't expect the sender to read your stdout.

The principle: every channel of communication has its own thread. Replying on a different surface breaks threading, hides the answer from the sender, and creates duplicate context. If a message asks for action that produces output to multiple surfaces (e.g. "commit and tell me the hash"), the primary reply still goes back where the request came from; supplementary notifications go via `comms_send` to whoever else needs them.

When in doubt: where did the question arrive? Reply there.
