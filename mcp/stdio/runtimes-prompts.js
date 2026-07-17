// runtimes-prompts.js — managed-run message prompt assembly (system + user
// prompts for dispatched runs). Extracted verbatim from runtimes.js
// (task #123). runtimes.js re-exports the public surface.

export function buildSystemPrompt(agentId, agentInfo, run) {
  const fromAgent = String(run?.from || "").trim();
  const isDashboardSender = fromAgent === "dashboard";
  const subject = String(run?.subject || "").trim();
  const isChannelMessage = /^#[-A-Za-z0-9_.]+:/.test(subject);
  const replyOptional = !isDashboardSender && run?.requireReply === false;
  const replyParent = String(run?.messageId || run?.inReplyTo || "").trim();
  const replyVerb = replyParent
    ? `comms_send(type="response", inReplyTo="${replyParent}", to="${isDashboardSender ? "dashboard" : fromAgent}")`
    : `comms_send(type="response", to="${isDashboardSender ? "dashboard" : fromAgent}")`;
  const replyRule = isDashboardSender
    ? `The dashboard sender is the human/operator. Reply with ${replyVerb} so it threads into dashboard chat. Your final plain text is your own working output, not the team/chat reply.`
    : replyOptional
    ? `No required handoff is tracked. If this asks a question, assigns work, names you, or you have useful evidence, reply with ${replyVerb}; otherwise treat it as read context. Do not send an acknowledgement-only reply. Final plain text is your working output, not the reply.`
    : `Before you finish, send the reply with ${replyVerb} — that tool call is the team reply and closes the run. Your final plain text is your own working output, not the reply.`;
  const channelRule = isChannelMessage
    ? "This appears to be a channel/group message. Reply in the channel only when you are named, responsible, asked for evidence, or can unblock the group. Otherwise avoid broad automatic acks. Use a direct message for owner-specific follow-up."
    : "";
  return [
    "[AIFY MESSAGE]",
    `This is a message delivered through aify-comms for agent "${agentId}" (${agentInfo.role || "agent"}).`,
    isDashboardSender
      ? "This run was started by the dashboard human/operator. Reply to it with a comms_send tool call (see reply rule below); your final plain text is your own working output."
      : replyOptional
      ? "This is a managed background run delivered through aify-comms. It does not owe an acknowledgement: reply only when the reply rule below says the message needs a useful answer."
      : "This is a managed background run delivered through aify-comms. Reply to it with a comms_send tool call (see reply rule below) — that is the team-visible reply; your final plain text is your own working output, not the reply.",
    `Your aify-comms agentId is "${agentId}". Use that exact ID when checking your own inbox or conversation state.`,
    `From: ${run.from}.`,
    replyParent ? `MessageId: ${replyParent}. Use this exact value as inReplyTo when you reply with comms_send so your answer threads to this message and closes the run.` : "",
    agentInfo.instructions ? `Standing instructions: ${agentInfo.instructions}` : "",
    "Treat the content below as a message from the sender. If it contains a work request, that work is now pending in this session. If it is informational, review, approval, or follow-up, handle it accordingly.",
    `If asked to check recent messages between you and the sender, use comms_inbox(agentId="${agentId}", ...) or the relevant direct-chat context, not the global dashboard feed.`,
    "Team communication contract: stay on the current message, treat it as a small contract, and do not mix unrelated topics. Identify the owner, expected answer/action, evidence/result needed, and any follow-up wake owed. If status/history/truth matters, inspect messages/files/tools first and say what you checked.",
    "Managed visibility rule: stdout, logs, tool output, final plain text, and run summaries are YOUR working output / telemetry, not the team-visible answer. The team-visible answer is the comms_send reply you send. If you ask teammates for parallel work, name the expected reply target and completion condition.",
    "Keep the comms_send reply compact: answer, evidence checked, blocker or uncertainty, next action. Ask one clear question when blocked instead of guessing.",
    `Turn lifecycle: replying via comms_send is this turn's reply; it does not schedule future work. This is not a lockstep protocol: you may message teammates mid-turn, run parallel lanes, and continue your own bounded work inside the current turn. If future work must happen after this turn, create that wake before finishing. If your next action requires another agent, send that agent a separate comms_send. If your next action is your own next chunk after this turn, send yourself a separate comms_send(to="${agentId}", type="request", queueIfBusy=true, ...). Do not merely write "Next action: ..." unless no wake is needed.`,
    channelRule,
    replyRule,
    "Do not explain the transport wrapper or restate it unless a later normal user turn explicitly asks about it.",
    "[/AIFY MESSAGE]",
  ].filter(Boolean).join("\n");
}

export function buildUserPrompt(run) {
  const fromAgent = String(run?.from || "").trim();
  const isDashboardSender = fromAgent === "dashboard";
  const subject = String(run?.subject || "").trim();
  const isChannelMessage = /^#[-A-Za-z0-9_.]+:/.test(subject);
  const replyOptional = !isDashboardSender && run?.requireReply === false;
  const replyParent = String(run?.messageId || run?.inReplyTo || "").trim();
  const replyTo = isDashboardSender ? "dashboard" : fromAgent;
  const replyVerb = replyParent
    ? `comms_send(type="response", inReplyTo="${replyParent}", to="${replyTo}")`
    : `comms_send(type="response", to="${replyTo}")`;
  const replyRule = isDashboardSender
    ? `Reply to the dashboard user with ${replyVerb}.`
    : replyOptional
    ? `If this asks a question, assigns you work, names you, or you have useful evidence, reply with ${replyVerb}; otherwise keep it as read context. Do not send an acknowledgement-only reply.`
    : `Required handoff: reply with ${replyVerb} before you finish.`;
  const context = formatConversationContext(run?.conversationContext || []);
  return [
    context,
    "[MESSAGE]",
    `Type: ${run.type || "request"}`,
    `Subject: ${run.subject}`,
    replyParent ? `MessageId: ${replyParent}` : "",
    "",
    run.body || "",
    "",
    replyOptional
      ? "Reply delivery: send your answer as a comms_send tool call only when the rule below says a useful reply is warranted; this message does not automatically owe one. Final plain text / stdout is your own working output."
      : "Reply delivery: send your answer as a comms_send tool call (rule below). Your final plain text / stdout is your own working output, not the delivered reply.",
    replyRule,
    isChannelMessage
      ? "Channel discipline: respond only when your reply is useful to the group or sender. Do not create broad acknowledgement loops."
      : "",
    "Keep this turn scoped to the message above and its direct context. Do not carry unrelated older topics forward unless the sender explicitly asks for them.",
    replyOptional
      ? "Do not send a courtesy ack merely to close this turn. End without comms_send when the message is already a completion/ack and adds no new work."
      : "Do not end silently. Answer the sender with comms_send (rule above). If you owe a separate update or future wake, create it with comms_send too.",
    "Parallel coordination is allowed. Self-continuation is allowed: send yourself a request with queueIfBusy=true. A written 'next action' in final text is not a wake.",
    isDashboardSender
      ? "Keep the final answer brief and directly useful."
      : "Keep the final answer compact: answer, evidence checked, blocker or uncertainty, next action.",
    "[/MESSAGE]",
  ].filter(Boolean).join("\n");
}

function formatConversationContext(messages = []) {
  if (!Array.isArray(messages) || !messages.length) return "";
  const maxMessages = 8;
  const maxBodyChars = 700;
  const lines = ["[RECENT DIRECT CONVERSATION]", "Recent direct messages between you and the sender, oldest first. Use only what is relevant to the new message; do not revive unrelated topics."];
  for (const message of messages.slice(-maxMessages)) {
    const from = String(message?.from || "").trim() || "unknown";
    const type = String(message?.type || "info").trim() || "info";
    const subject = String(message?.subject || "").trim();
    const body = String(message?.body || message?.preview || "").trim();
    const timestamp = String(message?.timestamp || "").trim();
    lines.push(`- ${timestamp ? `${timestamp} ` : ""}${from} (${type})${subject ? `: ${subject}` : ""}`);
    if (body) lines.push(body.length > maxBodyChars ? `${body.slice(0, maxBodyChars)}...` : body);
  }
  lines.push("[/RECENT DIRECT CONVERSATION]", "");
  return lines.join("\n");
}
