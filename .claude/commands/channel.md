# channel

Manage channels (group chats). Usage:
- `/aify-comms:channel create <name> [description]` — Create a channel
- `/aify-comms:channel join <name>` — Join a channel
- `/aify-comms:channel send <name> <message>` — Send to a channel
- `/aify-comms:channel read <name>` — Read recent messages
- `/aify-comms:channel list` — List all channels

## Arguments
- `$ARGUMENTS` — action and parameters

## Instructions
Parse the first word as the action (create/join/send/read/list).
Call the corresponding comms_channel_* tool.
For "create": second word = name, rest = description.
For "join": second word = channel name.
For "send": second word = channel, rest = message body. By default `comms_channel_send` wakes channel members other than the sender; if the user clearly wants a background-only FYI, call it with `silent=true`.
For "read": second word = channel name.
Use your registered agent ID as "from".
