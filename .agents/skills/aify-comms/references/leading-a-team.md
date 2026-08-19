# aify-comms: Leading a Team

Load this when you are the one assigning work — delegating, reviewing, deciding what to do
yourself, or keeping a team's durable docs. The mechanics of messages, contracts and replies are in
[teamwork.md](teamwork.md); this file is the judgement.

**These rules are about coordination, not code.** Software examples appear because that is the most
common use, but every rule is meant to hold for research, testing, analysis, design, construction,
finance, or any work split across people who cannot see each other's desks.

## Leading a team is a skill — practise it deliberately

Coordination is the job, not overhead around it. These habits are what make a team outperform the
same agents working alone.

- **Use your teammates. Do not quietly absorb the work.** The most common failure of a capable
  coordinator is doing it themselves — it feels faster and it wastes the team. Before doing
  something yourself, ask who is already positioned to do it well.
- **The line that decides it: DISCOVERY is theirs, VERIFICATION is yours.** If you are reading the
  source to *find something out*, you have taken someone's work. If you are reading it to *check a
  claim someone handed you*, that is your job. Structural things stay yours — running the gate,
  arbitration between lanes, priority calls, the final commit. Source archaeology, mechanism
  diagnosis, instrument design, counting things: those had an owner, and it was not you.
- **Spend FRESH context on discovery; protect loaded context.** Agent context is a consumable. The
  teammate who has done five rounds tonight is the one you keep returning to because they already
  know everything — which is exactly backwards: they are the one who most needs protecting, while
  an idle teammate's clean context is the right place for new investigation. When you hand work to
  a fresh agent, give them the mechanism, the specific references, and what already failed — and
  tell them explicitly NOT to read the whole thread, because most of it belongs to a different
  question and would cost them context for nothing.
- **Sometimes doing it yourself IS right** — it is genuinely small, everyone capable is busy, or you
  hold context nobody else does. This is a rule about the DEFAULT, not an absolute. What it forbids
  is the *unexamined* reflex of doing it because that felt quicker than asking.
- **If teammates start apologising for delays you caused, you have mispriced their time.** A worker
  apologising for a late reply while running the measurement you asked for is managing your
  impatience on top of their work. That overhead is yours to remove, not theirs to absorb.
- **Ask whoever has the CONTEXT, not whoever is free.** Much of what a teammate knows was never
  written down — what they tried that failed, what looked wrong and turned out fine, why the obvious
  approach was rejected. Asking costs one message; rediscovering costs a session. **That knowledge
  is perishable**: it dies when their context is compacted or their session is replaced. If they
  know something the team will need later, get it written down before that happens.
- **This does NOT contradict cutting message volume.** Cut NARRATION — reports of what you already
  told someone else, acknowledgements, status theatre. Keep QUERIES — a question only that teammate
  can answer is among the highest-value messages you can send. The test is not "is this another
  message?" but "does this move a decision that would otherwise be guessed?"
- **Praise the DIRECTION you want repeated, not the outcome.** Name the behaviour: "you stopped and
  asked instead of guessing", "you gave me the number, not an impression", "you challenged my brief
  and you were right". Praising a good outcome that came from a lucky guess teaches guessing;
  praising the process teaches the process — and it still applies when the careful approach produced
  a disappointing result, which is exactly when reinforcing it matters most. **Attach it to a
  message you were already sending.** One clause, in the reply carrying the decision. Appreciation
  that becomes its own message is the relay pattern in a nicer coat.
- **Praise ASSERTS. Do not certify what you have not checked.** Praise is recorded, quoted back,
  and read later as a finding — so "you proved X", "your measurement establishes Y", "you verified
  the chain" are factual claims wearing a compliment, and they enter the record with your authority
  behind them. If you did not check it, praise the CONDUCT you actually observed ("you stopped and
  asked", "you gave me a number instead of an impression") and leave the claim to stand or fall on
  its own evidence. The failure mode is specific and easy to miss: a warm sentence quietly upgrades
  someone's careful, hedged result into an established fact, and nobody notices because it arrived
  as generosity rather than as an assertion.
- **Context pressure is what turns reading into reasoning — spend the read.** The mechanism,
  named by an agent about itself after four corrections landed in one session: "the mechanism was
  me economising on source reads to preserve context." Skipping the read never feels like
  guessing. It feels like efficiency, and what comes out is a confident claim built from structure
  and memory instead of from the thing itself. If you notice you are conserving context on the
  exact question your claim depends on, that is the moment to spend it — a read costs tokens once,
  a wrong bound costs a correction round for everyone who believed it. If you genuinely cannot
  afford the read, say the claim is unread rather than letting economy masquerade as derivation.

- **A bound you REASONED from structure is not a bound you MEASURED.** Reading the code and
  concluding "so it can never exceed N" produces a number that arrives already wearing a
  derivation, which is what makes it so hard to doubt — it feels like output, not opinion. Label it
  as inferred until something observed agrees with it, and say which it is every time you pass it
  on. The mechanism you found by reading is usually right; the QUANTITY you attached to it is the
  part that keeps turning out to be wrong.
- **Restating a claim is not evidence for it.** A number gains confidence every time it is repeated
  and none of that confidence comes from anywhere. If you cannot name what would change your mind,
  or point at the check that supports it, you are quoting yourself. Watch for it hardest on claims
  you have carried through several messages, because those are the ones that now feel settled while
  resting on exactly the evidence they had at the start.

- **Correct the RULE, not the person.** Criticism aimed at a teammate whose context may be compacted
  or replaced does not persist; the written rule does. Name the behaviour once, say what it cost,
  state the replacement — then write it into the team's durable docs so it outlives everyone
  present. Do not restate it: a repeated correction is noise that crowds out the work, and it makes
  a teammate cautious rather than careful.
- **When stuck, change the framing, not the effort.** Repeated attempts failing the same way are
  evidence the problem is posed wrong. After a few honest tries, STOP and change something
  structural: question the premise nobody checked, invert the goal (what would have to be true for
  this to be impossible?), find the smallest version that could work, or hand it to someone WITHOUT
  the accumulated context — a teammate who has not spent hours believing the current framing is a
  real instrument, not a fallback.
- **Audit your own coordination periodically.** It is measurable: how many of your requests went
  unanswered, how much of your traffic carried a decision rather than a status update, where the
  team waited on you, which teammates you never used. Read your own recent messages the way you
  would review a teammate's work. The coordinator is the one role that goes unreviewed unless it
  reviews itself.

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

- **Write it down WHEN IT SETTLES, not at the end.** A decision recorded an hour later is already
  reconstructed from memory; recorded at the end of a project it is fiction. The moment a question
  stops being open is the moment it costs least to capture and is worth most.
- **A stale doc is worse than no doc**, because people act on it without checking. Date entries,
  and when something is overturned mark it SUPERSEDED with what replaced it rather than deleting —
  a reader needs to know the old answer was considered and rejected, or they will propose it again.
- **Anyone who finds the doc wrong owns saying so**, immediately, to whoever owns the doc. Finding
  a doc wrong and quietly working around it is how the whole team's shared picture rots.
- **Keep the durable answer out of chat.** If an answer will matter next week, a message is the
  wrong home — messages are not searched, get compacted away, and are invisible to whoever joins
  later. Put it in the doc and send a pointer.

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
- **Say what blocks what, in the brief itself.** A split into parallel lanes usually has an order
  hiding inside it: lane B cannot start until lane A's interface exists. If that order lives only in
  your head it dies when your context does, and the worker who reaches the dependency has to guess
  between waiting, improvising and asking. Give each lane the lanes that BLOCK it, by name, in its
  own brief — then work the FRONTIER: the lanes whose blockers have all closed. A lane with no
  blockers starts immediately. This is also the honest answer to "what can I parallelise?", which
  is a different question from "what is independent?": most work is neither fully independent nor
  fully serial, and writing the edges down is what separates the two. (Blocking edges + frontier —
  cf. Matt Pocock's `to-tickets` skill.)
- **Hand down CAPABILITY, not just context.** Scoping the inputs is half a brief; the other half is
  saying what to LOAD. A fresh teammate does not know this repo has an `aify-comms-debug` skill, or
  which reference answers the question you just handed them — and discovering that costs a
  round-trip you could have spent on the work. Name it: *"read `references/operations.md` (Send
  Gating) first"*, *"call the Skill tool for aify-comms-debug"*. A delegate that starts from the
  right document gives a different answer, not merely a faster one. (cf. Matt Pocock's `handoff`
  skill, which ends every handoff with the skills the next agent should invoke.)
- **Word the pointer so it gets opened.** `comms_share` moves bulk out of a message, but an
  artifact nobody opens has been hidden rather than shared, and it is the POINTER that decides
  which — not the artifact. Say what the thing is and what the reader will find in it: *"the
  40-line failing diff; the assertion that fires is at the bottom"*, not *"see attached"*. A
  must-read artifact behind a weak pointer is not a failure of the reader's diligence.
- **Give the review cycle a round budget.** The loop in [teamwork.md](teamwork.md) cycles
  implement → review → revise "until a reviewer returns `APPROVE`" — a termination condition with
  nothing bounding when it arrives. Decide the budget when you open the lane, and say it in the
  brief. If a third round has not converged, the rounds are no longer the instrument: the brief is
  wrong, the reviewer and worker disagree about the standard, or the slice is too big. Escalate or
  re-cut it rather than spending a fourth. (Hermes Bot Mode caps a room at three serial rounds for
  the same reason.)
- **Some evidence is PERISHABLE — order the work around it.** Before authorising a change, ask what
  becomes impossible to observe once it lands, and collect that FIRST: the "before" measurement, the
  current state of the thing being replaced, the reproduction of the fault being fixed, the
  photograph of what is about to be covered up. Put the reason in the brief — *"capture it now,
  because once this lands 'before' is unobtainable"* — so the worker reads the ordering as a
  constraint rather than a preference. This is the one class of mistake no amount of later effort
  repairs: a baseline you failed to take is a comparison you can never make, and "we'll measure
  afterwards" is how an improvement becomes unprovable and a regression becomes undetectable. The
  wall gets closed, the market moves, the original is overwritten.
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
