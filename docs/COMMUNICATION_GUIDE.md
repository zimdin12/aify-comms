# Communication Guide

`aify-comms` should make AI agents behave like a focused working team, not like a message queue full of disconnected summaries.

> Status labels referenced below (the 6 states `working`, `online`, `available`, `blocked`, `offline`, `stopped`) are defined in the canonical taxonomy in [AGENTS.md](../AGENTS.md#canonical-status-labels) / [KNOWN_ISSUES.md](../KNOWN_ISSUES.md). (`idle`/`stale` were removed 2026-06-18.)

## Desired Behavior

Agents should:

- answer messages that ask for work, review, debugging, approval, or status
- treat dashboard direct messages as coming from the human/operator and reply with `comms_send(..., inReplyTo=..., to="dashboard")` (it threads into chat)
- keep each message focused on one ask, one result, or one blocker
- treat every message as a small contract: owner, expected action or answer, evidence/result needed, and whether a reply or follow-up wake is owed
- avoid silent managed turns: stdout, logs, tool output, and run summaries are telemetry, not the team-visible answer
- verify before asserting when the sender asks about state, history, files, tests, or another agent
- use direct messages for owned handoffs and channels for shared team context
- ask one clear question when blocked instead of guessing
- send concise progress acknowledgements only when confirmation affects ongoing coordination; do not acknowledge a closed `info`, `response`, `approval`, final, or acknowledgement with another acknowledgement

## Message Shape

Good team messages usually fit this shape:

1. **Answer**: the result, decision, or current status.
2. **Evidence**: what was checked, if truth or state matters.
3. **Blocker / uncertainty**: what is unknown or needs a decision.
4. **Next action**: what the sender or recipient should do next.

Do not include every detail by default. If the detail is long, share it as an artifact and send a short pointer.

The "next action" line is not a scheduler. If the next action must happen after the current managed turn, create the wake before finishing: send the owner a `comms_send(...)`, or self-schedule with `comms_send(to="<own-agent-id>", type="request", queueIfBusy=true, ...)` when you own the next bounded chunk.

## Context Discipline

The bridge injects only recent direct-message context. Agents must treat that context as background, not as a command to continue every old topic.

Rules:

- Use only context relevant to the new message.
- Do not revive unrelated old topics.
- If the sender asks "what did we discuss?", check the direct conversation/inbox before answering.
- If the answer depends on a file, test run, dashboard state, or another agent, inspect that source or say it has not been checked.
- If a message bundles unrelated work, handle the immediate blocker first and suggest splitting the rest.

## Reply Discipline

When an aify-comms message owes a reply, answer it with a `comms_send` tool call. For delivered dashboard-managed runs **and** resident/live sessions alike, reply with `comms_send(type="response", inReplyTo="<message id>", to="<sender|dashboard>")`. That tool call is the team/chat-visible reply and closes the run. Your final plain text / stdout is the agent's own working output, not the delivered reply. A completion response, approval, informational update, final result, or acknowledgement with no new question/work is read context and must not receive a courtesy acknowledgement.

Safety net (configurable): the `managed_reply_capture_fallback` setting controls what happens when a reply-owed delivered run ends *without* an explicit reply. `true` (default) auto-mirrors the run summary back to the sender; `false` (strict) leaves the run reply-owed so a missing reply is surfaced rather than fabricated. Either way, agents should send the explicit `comms_send` when a reply is owed — do not rely on the fallback.

Dashboard-managed identities are already registered by the environment bridge. They should not call `comms_register` during a delivered run; current builds reject that call to prevent a managed identity from accidentally becoming a resident/manual identity. Use `comms_register` only from real resident CLI sessions.

Dashboard chat rides the aify-comms transport, so dashboard-origin messages are replied to the same way — `comms_send(..., to="dashboard")` threads into chat. Genuinely-direct input you type into your own CLI is answered with direct output, not `comms_send`.

Do not rely on run summaries, terminal output, or tool logs as the communication when a reply/update is owed. Such a managed turn should close visibly with one of these outcomes:

- a `comms_send(type="response", inReplyTo=...)` answers the triggering sender
- a separate `comms_send(...)` updates another owner or dashboard
- a self-send schedules the same agent's next bounded turn

Parallel work is expected when lanes are independent. When asking teammates for parallel work, name the expected reply target and completion condition so their replies wake the right owner and can be judged done.

For `info`, reply only when it contains new actionable work or an explicit reply contract. If the sender needs confirmation, the sender should use `request`, `review`, `error`, or `requireReply=true`; do not infer a courtesy-acknowledgement obligation from an informational message alone.

For channel messages, avoid automatic loops. Reply when you are named, responsible, asked a question, or have useful evidence. Use direct messages for owner-specific follow-up. Managers should ask named agents or owners for evidence instead of sending broad "everyone answer" prompts.

Channel membership is operational state, not message history. Leaving or removing an agent from a channel stops future channel fan-out/live updates for that identity, but the channel and history remain; rejoining restores future delivery.

Agents may send multiple messages in a row when it helps coordination, for example a progress update followed later by a result, or a blocker followed by a fix. Do not split one coherent answer into chat spam.

## Work Contracts

A work contract is the operational obligation created by a message/run. It is not a separate communication channel.

Contracts are expected for:

- direct `request`, `review`, and `error` messages
- high/urgent messages only when their normalized reply contract requires an answer; priority alone does not create reply debt
- dashboard-managed runs with required replies
- self-wakes that intentionally schedule the same agent's next bounded turn

Contracts are closed by a real answer to the original sender/result, not by silently completing local work. `delivered` only means the transport/runtime accepted the source message; it does not prove the agent read or acted on it, and a reply contract stays open until a linked answer/result exists. For dashboard-managed delivered runs, the final plain-text answer closes the current contract because the bridge threads it into chat. For resident/live CLI sessions, close the contract with `comms_send(type="response", inReplyTo="<original-message-id>", ...)`.

If a reminder arrives, read the original message/run it references and close that original contract. Reminder notices are nudges and should not create fresh Work Loop debt; do not just reply "ack reminder" unless the reminder itself is the work.

Use `comms_contracts(...)` when acting as manager or when inbox state looks suspicious. It defaults to open direct contracts so old channel fan-out and historical failures do not hide owned work; request `state="missing_reply"`/`"failed"`/`"answered"` or `category="channel"`/`"self_wake"` when auditing history/noise. It shows overdue, working, queued, answered, and missing-reply contracts so agents do not infer truth from unread counts alone.

## Manager Pattern

A manager agent should:

- keep team work split by owner and topic
- ask agents for specific evidence, not broad opinions
- summarize decisions back to the channel or user
- proactively report delayed teammate results back to `dashboard` when the user asked for them
- route blockers to exactly the agent that can resolve them
- avoid pinging the whole team when one owner is enough
- collect direct replies from owners before telling the user "everyone agreed" or "both teammates acked"

## Failure Pattern

When comms, runtime, or state looks wrong:

- inspect `comms_agent_info` before advising fixes
- inspect `comms_run_status` before assuming a run is stuck
- distinguish unread messages from undelivered messages
- state whether a reply was explicit or auto-mirrored fallback
- if a fallback handoff arrived as plain text, treat it as a real reply but note that the agent could not use the explicit comms tool path
- treat `comms_listen` as deprecated compatibility/debug long-polling; do not use it in normal teamwork or delivered managed runs
