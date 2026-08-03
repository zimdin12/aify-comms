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

**Cite the authority as an immutable version, not as this message.** When a brief rests on a
design, plan, or prior result, name the version the worker must build against — commit, path, and
content hash — and say plainly that the artifact outranks the brief: *"build against THAT text; if
you find yourself implementing something the file does not say, stop and amend the file first."* A
message is a snapshot of someone's understanding at send time; a commit is checkable and cannot
drift underneath the worker. Without it you get the failure where both sides are certain and
neither is wrong: the worker built correctly against a base that moved. The same rule is why a
worker should refuse to evaluate a change against a stale build.

**Name what is OUT of scope, especially what the worker just found.** A brief that bounds files but
not findings invites scope creep in the most tempting form — an adjacent defect discovered
mid-slice. Record it as its own item and say `DO NOT touch it in this slice`.

**Amending a dispatched contract is its own message type — label it.** If scope changes while work
is in flight, say so in the subject (e.g. `SLICE WIDENED MID-FLIGHT`, `HOLD`, `AMENDMENT`), state
exactly what changed, and rule explicitly on the work already done: kept, discarded, or superseded,
with the reason. Silently sending a bigger brief makes the worker guess whether to restart, and a
worker who guesses wrong loses the whole slice.

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

## Right-size the message

Send the smallest message that lets the recipient act correctly. Terseness is not the goal;
avoid both missing inputs and repeated context.

- **Send the delta.** Include the new decision, result, blocker, or exact ask; do not restate an agreed plan.
- **Do not relay.** The costliest message is one that tells party C what you already sent party B —
  "adopted verbatim and sent", "verified at source and relayed". It reads as diligence and leaves
  the recipient nothing to do. If A's input changed the brief, send the brief to whoever implements
  it and let the artifact record that A shaped it; a reviewer needs the OUTCOME (landed, hash,
  rejected + why), not a receipt for each hop. A manager who narrates every hop turns a two-party
  handoff into a three-party thread and doubles the traffic without adding a decision.
- **Keep intent inline.** Put the owner, decision, ask, and completion condition in the message. Point to a shared path or `comms_share` artifact for bulky detail.
- **No courtesy loop.** A terminal `APPROVE`, result, or thanks ends the thread unless it creates new work.
- **Promises need a wake.** Before ending a turn with future work, create the follow-up contract or self-wake. Written `Next:` text is not scheduling.
- **DM owners; channel shared state.** Post a settled decision once on the team channel. Send a DM only to the person who must act. The same text to several people belongs on a channel.
- **Delegate inside your lane; route across lanes.** Native subagents can help an owner; they must not shadow another teammate's role.

When unsure, ask: "Does the recipient need this and not already have it?" If not, act instead
of sending another message.

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
- **Some evidence is PERISHABLE — order the lane around it.** Before authorising a change, ask what
  becomes unobtainable once it lands, and collect that first: a pre-change performance baseline, the
  current output of a query you are about to alter, the reproduction of the bug you are about to
  fix. Say why in the brief — *"capture it NOW, because once Slice A lands 'before' is
  unobtainable"* — so the worker treats the ordering as a constraint rather than a preference. A
  baseline you can no longer take is a comparison you can never make, and "we'll measure after" is
  how a regression becomes unprovable.
- Check `comms_contracts` and `comms_agent_info` before assuming who is idle or stuck.
- **Presence is not progress.** `online` proves a live worker and `lastSeen` proves a heartbeat; neither proves work or session resumption. Measure a lane by its latest evidenced output — for example a commit, push, merge, deploy, test result, or delivered artifact — and keep those states distinct.
- If an agent is `online`/`available` and owes a contract, send a focused status probe or rebrief.
- **Peek before you probe (managed agents).** When status alone is ambiguous — an agent shows `working` but owes an overdue reply, or you're sweeping the team on a heartbeat/monitoring loop — read what it is actually doing with `comms_console_tail(agentId="...")` (read-only, last 40 lines by default). The console reveals whether it's mid-build, waiting at a prompt, looping, or errored — detail that status can't convey. This is often faster and cheaper than a status round-trip.
- To recover a managed agent stuck at a prompt, `comms_console_input(agentId="...", text="...")` types into its console (empty text just sends Enter to unstick). Audited; use sparingly — prefer `comms_send` for normal work.
- **`comms_console_input` is NOT a reliable submit, and a success response does not mean it worked.** It reports success once the bytes reach the PTY; whether the runtime acted on them is unobservable. Measured 2026-07-26 on a stuck managed-claude draft: two text writes and three bare-Enter retries ALL returned success while the draft never submitted. So: send ONE attempt, re-read with `comms_console_tail`, and if the console did not visibly change **escalate to the operator instead of retrying** — repeated Enter has been measured to change nothing, and an agent that keeps retrying burns critical path (~15 min in the observed case). The lever that reliably woke those agents was an ordinary `comms_send`; try that first.
- **Console tools are managed-only.** Resident agents have no aify-owned console, so `comms_console_tail`/`comms_console_input` report "no live console." For a resident agent your levers are `comms_send` (ask for a `[STATUS]` with evidence) and the dashboard; **Switch to managed** if you need a console to peek into.
- If a worker replies with repeated vague status, demand `[REVIEW]` or `[HOLD]` with evidence.
- If context is noisy, use handoff compact/rebrief: `comms_compact(from="you", targetAgentId="other", mode="handoff")` compacts **another** managed agent by spawning a fresh managed backing seeded with a handoff packet (recent messages + your instructions). It is NOT the runtime's native `/compact`, and it needs a managed backing — a resident-only agent can't be compacted this way. Keep the same agent ID unless intentionally splitting identity (`newAgentId`). To trigger a managed PTY runtime's own in-place `/compact` (claude-code/codex/hermes), type it via `comms_console_input(agentId="...", text="/compact")` while the agent is at its prompt; for a resident agent, `comms_send` a request asking it to `/compact` itself.
- **An identity change is a migration.** Renaming tombstones the old ID, orphans a live session until it re-registers under the new ID, and makes stale sends fail. Notify the agent and its active correspondents of the cutover, or keep the existing ID.
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
- **A negative result is only evidence if the search could have found something — say what your
  positive control was.** "No call sites" and "no matching rows" mean nothing until you show the
  query works: *"my grep found the definition, so the search is its own positive control and that
  absence is ADVERSE, not VOID."* Absence with a proven-live probe is a finding; absence from an
  unvalidated probe is just silence, and the two are indistinguishable in the output. This cuts both
  ways — a filter that matches too MUCH lies just as confidently. (Measured in this repo: a
  `subject LIKE '%Restart%'` filter silently matched ordinary chat messages whose subjects mentioned
  restarting, which produced a confident, published, and completely wrong conclusion about restart
  behaviour. The fix was one column — `from_agent='dashboard'` — but nothing in the output looked
  wrong until someone re-derived it.) Before trusting a query, check what it matched, not just how
  many rows came back.
- Do not approve broad "done" claims without evidence.
- **End every review with an explicit verdict, not prose.** Reply `inReplyTo` the work request with a clear `APPROVE` or `REVISE` as the first line (then the evidence/rework). `APPROVE` is the signal that closes the loop and lets the manager ship; `REVISE` must list the specific, checkable changes needed. A workflow keeps cycling (implement → review → revise) until a reviewer returns `APPROVE` — that token is the completion contract, so never leave a review ambiguous about which it is. (Explicit accept/revise termination — cf. "TRINITY" Verifier ACCEPT, arXiv:2512.04695.)

## Dashboard User

The dashboard user is the human/operator. Dashboard chat rides the aify-comms transport, so a dashboard-managed run replies the same way as any aify-comms message: `comms_send(type="response", inReplyTo="<message id>", to="dashboard")`. That threads into dashboard chat and closes the run. Your final plain text is your own working output, not the chat reply.

When the human asks "what happened", inspect messages/runs/contracts first. Do not summarize from memory if the system has data.

## Reply on the surface you received

**Use one human-facing surface per interaction.** Infer it from where the request arrived. An explicit "talk in comms" or "talk in terminal" overrides that choice until the user changes it; ask once only when the surface is genuinely ambiguous.

- **Terminal-observed:** use normal concise terminal interaction, including useful progress between tool calls and the final result. Do not also send the operator a dashboard/comms copy unless requested.
- **Comms-observed:** send questions, useful progress, and results through `comms_send`; omit terminal narration and duplicate final prose because nobody is reading that surface. Tool-call telemetry may still be visible there.

- Request arrived as a `<channel source="aify-comms-channel" ...>` event (someone called `comms_send` to you) → reply with `comms_send(type="response", inReplyTo="<message id>", ...)`. Do NOT just print the answer as terminal output; the sender is not watching your terminal — they're waiting for the threaded reply via the bridge.
- Request typed directly into your CLI (operator at your keyboard) → reply in the CLI / final plain text. Do not `comms_send` back to the operator unless they specifically asked for a dashboard update.
- A dashboard-managed delivered run with `inReplyTo` in the metadata → reply with `comms_send(type="response", inReplyTo="<message id>", to="dashboard")`; that closes the run and threads into chat. (Only if `managed_reply_capture_fallback` is enabled does an unanswered run auto-mirror its summary as a fallback — don't rely on it.)
- Same rule for agent-to-agent: A sends `comms_send` to B → B replies with `comms_send(type="response", inReplyTo=A's-message-id, to="A")`. Don't expect the sender to read your stdout.

The principle: every channel of communication has its own thread. Replying on a different surface breaks threading, hides the answer from the sender, and creates duplicate context. If a message asks for action that produces output to multiple surfaces (e.g. "commit and tell me the hash"), the primary reply still goes back where the request came from; supplementary notifications go via `comms_send` to whoever else needs them.

When in doubt, use the request's arrival surface; ask only if that evidence conflicts with an explicit user choice.
