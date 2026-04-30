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
assert.match(dashboardSystem, /human\/operator/);
assert.match(dashboardSystem, /Do not call comms_send back to dashboard/);
assert.match(dashboardUser, /Reply to the dashboard user in your final plain-text response/);

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
assert.match(channelSystem, /dashboard human will not normally see your final plain-text output/);
assert.match(channelSystem, /proactive status message with comms_send\(to="dashboard"/);
assert.match(channelUser, /Reply if this message asks you a question/);
assert.match(channelUser, /Human visibility: your final plain-text response is local/);
assert.match(channelUser, /Do not create broad acknowledgement loops/);
assert.match(channelUser, /send the dashboard\/human a concise status message/);

const directSystem = buildSystemPrompt("sc-coder", agentInfo, {
  from: "sc-manager",
  subject: "Review this",
  requireReply: true,
});
assert.match(directSystem, /reply to the sender/i);
assert.match(directSystem, /comms_send with inReplyTo/);

console.log("managed-message-prompts.test.js: all assertions passed");
