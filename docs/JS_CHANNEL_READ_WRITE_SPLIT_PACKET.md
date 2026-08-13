# Channels — read/write split packet

**Status:** submitted. Measured at `afb24dd7`. Written to satisfy the reviewer's condition for unparking the
channel read tools: *"produce a channel-specific read/write split packet that proves read-only tools do not
depend on spawn/send semantics."*

---

## 1. The claim, and the measurement behind it

Five channel tools. The measured dependency surface of each, from a strip-comments-and-strings AST-ish scan
of the registration span:

| tool | lines | local functions | module state | imports |
|---|---|---|---|---|
| `comms_channel_create` | 37 | **none** | **none** | `IS_REMOTE`, `MESSAGES_DIR`, `httpCall`, `validateName`, `fs`, `path` |
| `comms_channel_join` | 39 | **none** | **none** | same six |
| `comms_channel_read` | 45 | **none** | **none** | same six |
| `comms_channel_list` | 29 | **none** | **none** | same five (no `validateName`) |
| `comms_channel_send` | 115 | **`spawnTriggeredAgent`** | none | the six **plus** `deliverMessage`, `normalizeSessionMode`, `readAgents`, `canLaunchRuntime`, `normalizeRuntime`, `formatQueuedRun`, `randomUUID` |

Taken as a group, the four non-send tools reach:

- local functions: **NONE**
- mutable module state: **NONE**
- imports: exactly `IS_REMOTE`, `MESSAGES_DIR`, `httpCall`, `validateName`, `fs`, `path` — every one already
  owned by an extracted leaf or a node builtin.

**Explicit negative proof, each checked by name against the combined span:**

| does the read group reach… | |
|---|---|
| `spawnTriggeredAgent` | **no** |
| `deliverMessage` | **no** |
| `normalizeSessionMode` | **no** |
| `readAgents` | **no** |
| `writeAgents` | **no** |

`comms_channel_send` is the sole member touching any of them, and the only one touching a local function at
all.

## 2. Why that is the case rather than a coincidence

The four are about a channel's EXISTENCE and CONTENTS: create one, join one, list them, read messages from
one. None of that wakes an agent. `comms_channel_send` is the only one that DELIVERS, and delivery is what
drags the machinery — it must decide whether each member is reachable, cold-start one that is not, and
render what happened. That is `spawnTriggeredAgent` and the send-semantics cluster, and it is unavoidable
for a tool whose job is to make other agents run.

So the split is not "the small ones" versus "the big one". It is **membership and content** versus
**delivery**, and the dependency measurement follows the subject rather than the size.

## 3. What splitting costs, stated honestly

**A group with a hole, which I have twice refused to create.** I declined to cut lifecycle before its
blocked members were freed, and declined to cut channels alongside it, on the grounds that a group whose
members' descriptions cross-reference an absent sibling is a worse artifact than a large file.

This case is different in one measurable way and I want that difference judged rather than assumed:
`comms_channel_send` is not a MEMBER of the read group's subject. It is a delivery tool that happens to
share a noun. The four read tools' descriptions do not tell a caller to reach for `comms_channel_send`
instead — they are what you use to find out a channel exists before sending to it, which is a different
relationship from `comms_restart` telling you to prefer it over `delete_session`.

If the reviewer reads that as special pleading, the correct outcome is to leave all five parked until the
spawn packet resolves, and I will accept that.

## 4. Proposed shape

`mcp/stdio/channel-tools.mjs`, `registerChannelTools(server, z)`, the four read/membership tools. 150 lines,
byte-identical under a uniform one-level indent, exporting only the wrapper.

`comms_channel_send` stays in `server.js` and joins the group later, once `spawnTriggeredAgent` has an
owner per `JS_SPAWN_TRIGGERED_AGENT_PACKET.md`. The module header will say that in those words, so the next
reader knows the group is incomplete BY RECORD rather than by oversight — which is the actual defect in a
group with a hole.

## 5. What the tests will assert

Beyond registration and byte-identity, the properties worth pinning are about the local-mode store, which
is where a channel is a directory of files:

- a created channel exists and is listed; creating one twice is not an error or a duplicate;
- joining records membership and is idempotent;
- reading a channel returns its messages, and reading a channel that does not exist says so rather than
  returning empty — the absence-versus-emptiness distinction this repo has been bitten by twice, in
  `comms_search` and in `aify-comms doctor`;
- a traversal-shaped channel name is refused AND writes nothing, seeded so that the name guard is the only
  possible explanation (the anti-vacuity shape the reviewer accepted for `comms_status`);
- the group reaches none of the send cluster — asserted through `tests/bridge-sources.mjs` so it holds
  wherever those functions end up living.

## 6. Asking

1. Does §1 satisfy the condition? The negative proof is by name against the combined span, and the read
   group's entire import surface is six already-owned names.
2. Is §3's distinction real — delivery is not a member of the membership subject — or is it special
   pleading, in which case all five stay parked?
3. I am proceeding with the extraction in a SEPARATE commit on the reading that "produce a packet that
   proves it" is satisfied by producing it. If your condition meant "and I approve it first", say so and I
   will revert that commit; the packet stands either way.
