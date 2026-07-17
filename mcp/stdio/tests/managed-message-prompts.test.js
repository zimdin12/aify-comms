import assert from "node:assert/strict";

const { buildSystemPrompt, buildUserPrompt } = await import("../runtimes.js");

const agentInfo = { role: "coder", instructions: "Own frontend polish." };

const dashboardSystem = buildSystemPrompt("sc-coder", agentInfo, {
  from: "dashboard",
  subject: "Can you check this?",
  requireReply: true,
});
const dashboardUser = buildUserPrompt({
  from: "dashboard",
  type: "request",
  subject: "Can you check this?",
  body: "What is broken?",
  requireReply: true,
});
// Reply contract reworked 2026-05-29 (commit 9889b5e "feat(reply-contract):
// direct agents to reply via comms_send"). The contract is now INVERTED from
// the pre-2026-05 wording this test originally pinned: the team/chat reply is
// the comms_send tool call, and final plain text is the agent's own working
// output (NOT the delivered reply). These assertions pin the current contract.
assert.match(dashboardSystem, /human\/operator/);
assert.match(dashboardSystem, /Reply with comms_send\(type="response", to="dashboard"\) so it threads into dashboard chat/);
assert.match(dashboardSystem, /Your final plain text is your own working output, not the team\/chat reply/);
assert.match(dashboardSystem, /not a lockstep protocol/);
assert.match(dashboardSystem, /treat it as a small contract/);
assert.match(dashboardSystem, /Managed visibility rule/);
assert.match(dashboardSystem, /The team-visible answer is the comms_send reply you send/);
assert.match(dashboardSystem, /comms_send\(to="sc-coder", type="request", queueIfBusy=true/);
assert.doesNotMatch(dashboardSystem, /comms_send\(from="sc-coder", to="dashboard"/);
assert.match(dashboardUser, /Reply to the dashboard user with comms_send\(type="response", to="dashboard"\)/);
assert.match(dashboardUser, /Do not end silently/);
assert.match(dashboardUser, /Answer the sender with comms_send/);
assert.match(dashboardUser, /Parallel coordination is allowed/);
assert.match(dashboardUser, /Self-continuation is allowed/);

const channelSystem = buildSystemPrompt("sc-coder", agentInfo, {
  from: "sc-manager",
  subject: "#sand-castle: Who can verify the dashboard?",
  requireReply: false,
});
const channelUser = buildUserPrompt({
  from: "sc-manager",
  type: "request",
  subject: "#sand-castle: Who can verify the dashboard?",
  body: "@sc-coder please verify the chat polish.",
  requireReply: false,
});
assert.match(channelSystem, /channel\/group message/);
assert.match(channelSystem, /Reply in the channel only when you are named/);
assert.match(channelSystem, /managed background run/);
assert.match(channelSystem, /reply with comms_send\(type="response", to="sc-manager"\)/);
assert.match(channelSystem, /Final plain text is your working output, not the reply/);
assert.match(channelUser, /reply with comms_send\(type="response", to="sc-manager"\)/);
assert.match(channelUser, /Reply delivery: send your answer as a comms_send tool call/);
assert.match(channelUser, /Do not create broad acknowledgement loops/);

const directSystem = buildSystemPrompt("sc-coder", agentInfo, {
  from: "sc-manager",
  subject: "Review this",
  requireReply: true,
});
assert.match(directSystem, /Before you finish, send the reply with comms_send\(type="response", to="sc-manager"\)/);
assert.match(directSystem, /that tool call is the team reply and closes the run/);
assert.match(directSystem, /Your final plain text is your own working output, not the reply/);

const nonReplyResponseRun = {
  from: "sc-manager",
  type: "response",
  subject: "Ack",
  body: "Round-trip confirmed; nothing owed.",
  messageId: "msg-response-1",
  requireReply: false,
};
const nonReplyResponseSystem = buildSystemPrompt("sc-coder", agentInfo, nonReplyResponseRun);
const nonReplyResponseUser = buildUserPrompt(nonReplyResponseRun);
for (const prompt of [nonReplyResponseSystem, nonReplyResponseUser]) {
  assert.match(prompt, /Do not send an acknowledgement-only reply/);
  assert.doesNotMatch(prompt, /Do not end silently/);
  assert.doesNotMatch(prompt, /Answer the sender with comms_send/);
}

console.log("managed-message-prompts.test.js: all assertions passed");
