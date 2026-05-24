# aify-comms Teamwork Reference

Load this file when coordinating an autonomous team, assigning/reviewing lanes, compacting/rebriefing agents, or diagnosing why the work loop lost momentum.

## Roles

- `manager`: owns priority, routing, deadlines, blockers, and dashboard-facing status. Splits work into owner-specific contracts and follows up on overdue contracts.
- `architect` / tech lead: owns design boundaries, review quality, integration order, and rework decisions. Converts broad goals into bounded implementation lanes.
- `coder`: implements bounded chunks, self-checks, reports exact evidence, and asks for review when the slice is ready.
- `tester` / reviewer: verifies behavior, regressions, and risks. Reports evidence and blockers, not vague confidence.
- `operator`: manages environments, sessions, runtime settings, compaction, and recovery.

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
5. Manager reports only meaningful decisions/progress to dashboard.

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

## Manager Discipline

- Check `comms_contracts` and `comms_agent_info` before assuming who is idle or stuck.
- If an agent is active but not working and owes a contract, send a focused status probe or rebrief.
- If a worker replies with repeated vague status, demand `[REVIEW]` or `[HOLD]` with evidence.
- If context is noisy, use handoff compact/rebrief. Keep the same agent ID unless intentionally splitting identity.
- Avoid long dashboard updates. Tell the human what changed, what was verified, what remains blocked, and the next owner.

## Worker Discipline

- Do not answer with "on it" repeatedly. Start work, send a short ack only when useful, then return evidence.
- Self-continue only for a known next chunk. Do not create infinite self-wake loops.
- If you cannot safely proceed, send `[HOLD]` with exact evidence checked and the narrow decision needed.
- For long output, share an artifact and send a short summary.

## Review Discipline

- Verify on disk before approval.
- Keep rework narrow and actionable.
- If a review finds no issue, say what was checked and what risk remains.
- Do not approve broad "done" claims without evidence.

## Dashboard User

The dashboard user is the human/operator. A dashboard-managed run should answer the current dashboard message in final plain text. Use `comms_send(to="dashboard", ...)` only for later proactive updates outside that delivered run.

When the human asks "what happened", inspect messages/runs/contracts first. Do not summarize from memory if the system has data.

## Reply on the surface you received

**Default rule: reply on the same surface the request arrived on, unless explicitly directed otherwise.**

- Request arrived as a `<channel source="aify-comms-channel" ...>` event (someone called `comms_send` to you) → reply with `comms_send(type="response", inReplyTo="<message id>", ...)`. Do NOT just print the answer as terminal output; the sender is not watching your terminal — they're waiting for the threaded reply via the bridge.
- Request typed directly into your CLI (operator at your keyboard) → reply in the CLI / final plain text. Do not `comms_send` back to the operator unless they specifically asked for a dashboard update.
- A dashboard-managed delivered run with `inReplyTo` in the metadata → final plain text closes the run and the bridge threads it back. No `comms_send` needed for the primary reply.
- Same rule for agent-to-agent: A sends `comms_send` to B → B replies with `comms_send(type="response", inReplyTo=A's-message-id, to="A")`. Don't expect the sender to read your stdout.

The principle: every channel of communication has its own thread. Replying on a different surface breaks threading, hides the answer from the sender, and creates duplicate context. If a message asks for action that produces output to multiple surfaces (e.g. "commit and tell me the hash"), the primary reply still goes back where the request came from; supplementary notifications go via `comms_send` to whoever else needs them.

When in doubt: where did the question arrive? Reply there.
