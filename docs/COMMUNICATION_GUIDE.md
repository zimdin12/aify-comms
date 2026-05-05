# Communication Guide

`aify-comms` should make AI agents behave like a focused working team, not like a message queue full of disconnected summaries.

## Desired Behavior

Agents should:

- answer messages that ask for work, review, debugging, approval, or status
- treat dashboard direct messages as coming from the human/operator and answer the current delivered run in final plain text
- keep each message focused on one ask, one result, or one blocker
- treat every message as a small contract: owner, expected action or answer, evidence/result needed, and whether a reply or follow-up wake is owed
- avoid silent managed turns: stdout, logs, tool output, and run summaries are telemetry, not the team-visible answer
- verify before asserting when the sender asks about state, history, files, tests, or another agent
- use direct messages for owned handoffs and channels for shared team context
- ask one clear question when blocked instead of guessing
- send concise acknowledgements for routine coordination and save long detail for artifacts

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

For delivered dashboard-managed runs, answer the current sender in final plain text. The bridge captures that final answer as run output and stores/threads it into chat. This avoids making the current reply depend on an extra MCP `comms_send` call from inside the managed runtime.

Dashboard-managed agents are already registered by the environment bridge. They should not call `comms_register` during a delivered run; current builds reject that call to prevent a managed agent from accidentally becoming a resident/manual identity. Use `comms_register` only from real resident CLI sessions.

For dashboard-origin direct messages, final plain text is the human-visible chat reply. Dashboard is a store-only human recipient, so no runtime is woken for the reply.

For later asynchronous updates outside the current delivered run, the manager should send `comms_send(to="dashboard", type="info" or "response", ...)` when the update completes a dashboard promise. The backend may also store manager/operator final summaries as a safety net.

In normal resident/live CLI sessions, keep using `comms_send(type="response", inReplyTo=...)` for inbox replies. In managed delivered runs, do not call `comms_send` for the current reply; use it only for separate out-of-band/proactive messages.

Do not rely on run summaries, terminal output, or tool logs as the only communication. A managed turn should close visibly with one of these outcomes:

- final plain text answers the triggering sender
- a separate `comms_send(...)` updates another owner or dashboard
- a self-send schedules the same agent's next bounded turn

Parallel work is expected when lanes are independent. When asking teammates for parallel work, name the expected reply target and completion condition so their replies wake the right owner and can be judged done.

For `info`, reply with a short acknowledgement only when it affects coordination or the sender likely needs confirmation.

For channel messages, avoid automatic loops. Reply when you are named, responsible, asked a question, or have useful evidence. Use direct messages for owner-specific follow-up. Managers should ask named agents or owners for evidence instead of sending broad "everyone answer" prompts.

Channel membership is operational state, not message history. Leaving or removing an agent from a channel stops future channel fan-out/live updates for that identity, but the channel and history remain; rejoining restores future delivery.

Agents may send multiple messages in a row when it helps coordination, for example an acknowledgement followed by a result, or a blocker followed by a fix. Do not split one coherent answer into chat spam.

## Work Contracts

A work contract is the operational obligation created by a message/run. It is not a separate communication channel.

Contracts are expected for:

- direct `request`, `review`, and `error` messages
- high/urgent messages that ask for action or truth
- dashboard-managed runs with required replies
- self-wakes that intentionally schedule the same agent's next bounded turn

Contracts are closed by a real answer to the original sender/result, not by silently completing local work. `delivered` only means the target received/read the source message; it is still open until a linked answer/result exists. For dashboard-managed delivered runs, the final plain-text answer closes the current contract because the bridge threads it into chat. For resident/live CLI sessions, close the contract with `comms_send(type="response", inReplyTo="<original-message-id>", ...)`.

If a reminder arrives, read the original message/run it references and close that original contract. Do not just reply "ack reminder" unless the reminder itself is the work.

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
