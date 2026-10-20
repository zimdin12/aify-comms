# S3: the bridge copy's other consumers, before any no-copy cutover

Part of [the guided-install plan](../2026-10-20-guided-install.md). Answers R3's slice-order half.

## What still runs from `~/.aify-comms` besides the MCP bridge

| consumer | evidence | S3 decides |
|---|---|---|
| `aify-comms-channel` (`claude-channel.js`) | register-service-cli.mjs:61-64 | the channel's packaging moves to an aify-wrapper plugin. The comms run claim and wake stay comms code, reached over HTTP from that plugin, with the design on its own page before code |
| `notify-check.js` (PostToolUse unread notice) | comms install.sh `install_claude_hook` and the plugin manifest | moved into aify-wrapper as a generic unread-notice hook reading the service over HTTP, or dropped if channel wakes cover it (measured, not assumed) |
| turn hooks | aify-wrapper's `bin/aify-turn-start.mjs` already | none: they are aify-wrapper's |

## Rule

- Until every row above runs without the copy, the copy stays a declared artifact of comms' spec (S1), acquired in
  phase 1.
- S4's cutover is gated on this slice. Channel trust-policy work (S5, branch A or B) is separate and is not needed
  for S3.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| channel without the copy | a resident with `~/.aify-comms` renamed away still receives a channel wake through the aify-wrapper plugin | the wake needs the copy |
| notify decided | either a notice arrives with the copy removed, or the measured case for dropping it is recorded and the hook is removed | the hook still points into the copy |
