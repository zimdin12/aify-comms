# Communication Guide

`aify-comms` should make AI agents behave like a focused working team, not like a message queue full of disconnected summaries.

## Desired Behavior

Agents should:

- answer messages that ask for work, review, debugging, approval, or status
- treat dashboard direct messages as coming from the human/operator and answer them with `comms_send(to="dashboard", type="response", ...)`
- keep each message focused on one ask, one result, or one blocker
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

## Context Discipline

The bridge injects only recent direct-message context. Agents must treat that context as background, not as a command to continue every old topic.

Rules:

- Use only context relevant to the new message.
- Do not revive unrelated old topics.
- If the sender asks "what did we discuss?", check the direct conversation/inbox before answering.
- If the answer depends on a file, test run, dashboard state, or another agent, inspect that source or say it has not been checked.
- If a message bundles unrelated work, handle the immediate blocker first and suggest splitting the rest.

## Reply Discipline

For delivered managed runs, answer the current sender with `comms_send(type="response", inReplyTo=...)`. The bridge captures final plain-text output for run summaries, diagnostics, and fallback repair, but chat delivery should depend on tool calls.

For dashboard-origin direct messages, send `comms_send(to="dashboard", type="response", ...)`. Dashboard is a store-only human recipient, so this writes chat without trying to wake a runtime.

For later asynchronous updates that were triggered by another agent, the manager should send `comms_send(to="dashboard", type="info" or "response", ...)` when the update completes a dashboard promise. The backend may still store manager/operator summaries as fallback repair, but the intended path is explicit tool delivery.

In managed background runs, final plain text is run-level output. Do not assume the user or teammate sees it in chat unless `comms_send` succeeds or fallback repair mirrors it.

For `info`, reply with a short acknowledgement only when it affects coordination or the sender likely needs confirmation.

For channel messages, avoid automatic loops. Reply when you are named, responsible, asked a question, or have useful evidence. Use direct messages for owner-specific follow-up. Managers should ask named agents or owners for evidence instead of sending broad "everyone answer" prompts.

Agents may send multiple messages in a row when it helps coordination, for example an acknowledgement followed by a result, or a blocker followed by a fix. Do not split one coherent answer into chat spam.

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
