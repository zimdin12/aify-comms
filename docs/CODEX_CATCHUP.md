# Codex catch-up

Codex is parked. In 0.9.11 Claude and hermes move to the service's tools and lose their local comms tool
process; Codex keeps its own until it is brought level (operator, 2026-10-27). This file is what that takes.

**Start when** Claude and hermes run 0.9.11 on real agents and it is proven there (operator: "when hermes and
claude are working well and in proven manner"), and Codex is unparked. Until then nothing here is built.

## What Codex runs today

One local process inside `codex-aify`: the tool bridge (`mcp/stdio/server.js`, `AIFY_MANAGED_VIA_WRAPPER=1`).
Unlike Claude's and hermes', it is also Codex's delivery: it holds the Codex app-server controller that claims
and delivers runs. Taking the process away without a replacement leaves Codex with no message delivery.

It holds these duties alone (the 0.9.11 plan's slice 1 map, read 2026-10-26):

| duty | today | after catch-up |
|---|---|---|
| claims and delivers runs | the bridge, through the app-server | a Codex delivery loop beside the launcher, the shape hermes' `hermes-managed-host.js run` has |
| liveness beat, session handle, runtime marker | the bridge | the loop |
| resident-lost at exit | the bridge | the loop, as hermes' managed host does it |
| auto-registration, binding file | the bridge | the loop |
| comms tools | the bridge's JS tools | the service's tools over Streamable HTTP, a `url` entry with header identity |
| file upload and download | `comms_share` with a `filePath`, `comms_read` of a binary | Claude's channel serves `comms_share_file` and `comms_save_file` (`shared-file-transfer.mjs`); hermes' answer is undecided; Codex takes whichever its loop can host |

## Steps

1. Build the Codex delivery loop and qualify it on a real Codex agent: cold start, incoming send and steer,
   handle change, idle and exit (the checks 0.9.11 slice 1 runs for Claude and hermes).
2. aify-wrapper: Codex's comms entry becomes `url` plus headers. Codex's binary carries `http_headers`; whether
   it sends them is unchecked, so check it on a real Codex first.
3. Re-run `install.sh --client codex`: Codex's install and skills on this host are stale since it was parked.
4. Delete what only Codex kept alive: the JS tool modules (`mcp/stdio/*-tool(s).mjs`) and `server.js`'s tool
   serving. This is the part of 0.9.11 slice 5 that waits on Codex. With them goes `artifact-tools.mjs`'s
   hand-built multipart upload; `shared-file-transfer.mjs` uploads with `FormData`. With them goes
   `from` too: only the JS `comms_send` takes it (the service's names the caller by header and ignores
   it), yet both reply hints still write `comms_send(from=...)`: `reply_contract.py` and `notify-notice.mjs`.

The 0.9.11 plan, with the slice 1 map: `docs/superpowers/plans/2026-10-26-0.9.11-one-local-comms-process.md`.
