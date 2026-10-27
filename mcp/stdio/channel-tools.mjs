// Channels: whether one exists, who is in it, and what it contains.
//
// Six MCP tools — `comms_channel_create`, `comms_channel_join`, `comms_channel_leave`,
// `comms_channel_read`, `comms_channel_list`, `comms_channel_delete`. v0.5.4 layer 2 of the
// server.js decomposition. (This line said "Four" and listed four while the module held five, then
// six: a count written once and never re-measured. Everything below that says "the four" means the
// original four, and is left alone rather than silently renumbered.)
//
// `comms_channel_send` is not here: it lives with `comms_send` in `send-tools.mjs`, because the two share
// the send path. A reader who wonders where sending went should find this paragraph, not a gap.
//
// WHY THE SPLIT FALLS HERE AND NOT ON SIZE. These four are about a channel's EXISTENCE and CONTENTS, and
// none of them wakes an agent. Measured, the four together reach zero local functions, zero mutable module
// state, and exactly six imported names. `comms_channel_send` reaches thirteen. The dependency boundary
// follows the subject — membership and content versus delivery — which is why it was worth cutting at all.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.


import { httpCall } from "./aify-service-endpoint.mjs";
import { validateName } from "./safe-name.mjs";
import { SAFETY_HEADER } from "./tool-response-format.mjs";

// Registers the four channel membership/read tools. A function rather than a module-scope side effect, so a
// fake server can capture the registrations and a test can call the handlers without an MCP transport.
// `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The four bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerChannelTools(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 9. comms_channel_create -- Create a channel (group chat)
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_channel_create",
    "Create a channel (group chat). You join it automatically.",
    {
      name: z.string().describe("Channel name (e.g. 'backend-team', 'code-review')"),
      from: z.string().describe("Your agent ID"),
      description: z.string().optional().describe("Channel description"),
    },
    async ({ name, from, description }) => {
      try { validateName(name, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      await httpCall("POST", "/channels", { name, createdBy: from, description });
      return { content: [{ type: "text", text: `Channel #${name} created. You're a member.` }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 10. comms_channel_join -- Join a channel
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_channel_join",
    "Join a channel yourself, or add another agent to a channel.",
    {
      channel: z.string().describe("Channel name to join"),
      from: z.string().describe("Your agent ID"),
      agentId: z.string().optional().describe("Agent to add (omit to join yourself)"),
    },
    async ({ channel, from, agentId }) => {
      const target = agentId || from;
      try { validateName(channel, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      const r = await httpCall("POST", `/channels/${encodeURIComponent(channel)}/join`, { agentId: target });
      const action = target === from ? "Joined" : `Added ${target} to`;
      return { content: [{ type: "text", text: `${action} #${channel}. Members: ${r.members.join(", ")}` }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 11b. comms_channel_leave -- Stop receiving a channel WITHOUT destroying it
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_channel_leave",
    "Leave a channel: removes only your membership. The channel and its history stay for every other member.",
    {
      channel: z.string().describe("Channel name to leave"),
      from: z.string().describe("Your agent ID"),
    },
    // NO third-party removal, deliberately, and this is where it differs from comms_channel_join.
    // `POST /channels/{name}/leave` deletes whatever membership it is handed and never checks that
    // the caller owns it, so an `agentId` parameter here would let any agent silently remove any
    // other from a channel. Leaving is about yourself; removing somebody else is an operator action.
    // Keeping the parameter off also leaves the two transports exactly in step.
    async ({ channel, from }) => {
      const target = from;
      try { validateName(channel, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      const r = await httpCall("POST", `/channels/${encodeURIComponent(channel)}/leave`, { agentId: target });
      if (!r.changed) {
        return { content: [{ type: "text", text: `${target} is not a member of #${channel}; nothing to leave.` }] };
      }
      return { content: [{ type: "text", text: `Left #${channel}. Remaining members: ${r.members.join(", ") || "none"}` }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 12. comms_channel_read -- Read channel messages
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_channel_read",
    "Read recent messages from a channel.",
    {
      channel: z.string().describe("Channel name"),
      limit: z.number().optional().describe("Number of messages (default: 20, newest first)"),
    },
    async ({ channel, limit }) => {
      try { validateName(channel, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      const maxN = limit || 20;
      let ch;

      ch = await httpCall("GET", `/channels/${encodeURIComponent(channel)}?limit=${maxN}`);

      if (!ch.messages.length) {
        return {
          content: [{ type: "text", text: `#${channel} -- no messages yet. Members: ${ch.members.join(", ")}` }],
        };
      }

      const header = `#${channel} -- ${ch.totalMessages} messages, ${ch.members.length} members (${ch.members.join(", ")})`;
      const lines = ch.messages.map((m) => {
        // UTC, not the reader's locale: the service renders the same text for every agent (0.9.11).
        const time = m.timestamp ? new Date(m.timestamp).toISOString().replace(/\.\d{3}Z$/, "Z") : "?";
        const safeBody = "```\n" + (m.body || "").replace(/```/g, "'''") + "\n```";
        return `[${time}] ${m.from}: ${safeBody}`;
      });
      return {
        content: [{ type: "text", text: `${SAFETY_HEADER}\n\n${header}\n\n${lines.join("\n\n")}` }],
      };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 13. comms_channel_list -- List all channels
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_channel_list",
    "List all channels.",
    {},
    async () => {
      const r = await httpCall("GET", "/channels");
      if (!r.channels.length) return { content: [{ type: "text", text: "No channels." }] };
      const lines = r.channels.map((c) =>
        `#${c.name} -- ${c.description || "(no description)"} | ${c.members.length} members, ${c.messageCount} messages`
      );
      return { content: [{ type: "text", text: lines.join("\n") }] };
    }
  );
  // ═══════════════════════════════════════════════════════════════════════════════
  // 14. comms_channel_delete -- Delete a channel you created, with its messages
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_channel_delete",
    // The warning below lived HERE as a code comment, where no agent could read it, while the SSE
    // transport carried it in its description. Same endpoint, same destruction, and which warning an
    // agent received depended only on how it happened to be connected.
    "DESTRUCTIVE. Deletes a channel YOU created, its membership and EVERY message posted to it, shared history for every member. There is no undo. To stop receiving a channel, LEAVE it: deleting ends it for everybody. Only the creator may delete it.",
    {
      channel: z.string().describe("Channel name to delete"),
      from: z.string().describe("Your agent ID — must be the channel's creator"),
    },
    async ({ channel, from }) => {
      // THE MOST DESTRUCTIVE DELETE AN AGENT CAN REACH: channel, membership and every message ever
      // posted to it — shared history for every member, not just the caller's. Added 2026-08-18
      // together with the endpoint's ownership check; the tool without the check would have opened
      // the hole rather than closed it.
      try {
        await httpCall("DELETE", `/channels/${encodeURIComponent(channel)}?requestedBy=${encodeURIComponent(from || "")}`);
        return { content: [{ type: "text", text: `Deleted channel #${channel} and its messages.` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Failed to delete channel: ${e.message}` }], isError: true };
      }
    }
  );
}
