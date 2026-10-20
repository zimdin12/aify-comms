# Guided install: any piece installs and connects the rest (2026-10-20, plan rev 2, not built)

The operator, 2026-10-20: "i go to new pc, i tell agent to install aify-comms or aify-env and aify asks stuff if
needed and installs env and wrapper etc also. or if i ask to install aify-env and connect it with comms in xxx and
dashboard in yyy." Rev 2 answers REVISE a60461f1 (R1-R4). This page is the index; each slice has its own page under
`guided-install/`.

## Today (aify-comms 7c1f76da, aify-env 2ae603de, aify-wrapper 5a080a1)

- **Three installers, none chains another.**
  - comms `install.sh --client` copies its bridge and installs its assets (install.sh:1948-2010).
  - It installs neither aify-env nor aify-wrapper; aify-wrapper is only a pinned npm dependency whose templates
    comms renders (mcp/stdio/package.json:26).
  - aify-env's installer installs itself and asks for credentials only for services already registered (aify-env
    install.sh:62-83, scripts/install-credentials.mjs:27-35,96-125).
  - aify-wrapper's installer needs `--endpoint` and defaults the bridge to `~/.aify-comms/mcp/stdio` (aify-wrapper
    install.sh:12-22).
- **Each service writes its own registry entry.**
  - comms: `register-service-cli.mjs:42-63`. Its entry has two stdio servers, `aify-comms` (`server.js`) and
    `aify-comms-channel` (`claude-channel.js`), both in the bridge copy (:61-64).
  - The dashboard: its own `bin/register.mjs`, which since caab988 keeps every published `*CredentialRef`.
- **Every client install copies comms code.** `copy_bridge_to_native_dir` runs unconditionally (install.sh:1963).
  The function (:279-366) has no SSE exception: `--mcp-transport sse` changes only the rendered launcher
  (:497-499), and its help text (:67-69) does not make a no-copy path. No-copy is work, not current behaviour.
- **An agent-facing skill exists:** `aify-comms-install`, backed by `scripts/install-state.sh` and
  `scripts/components.sh`.

## Accepted direction (review a60461f1)

- One orchestrating chain.
- Credentials through their owner's API.
- One central registry writer.
- Raw fields preserved, never replaced by the reader's projection.
- Launchers rendered after the final registry state.

## The chain, in phases

1. **Acquire.** Install aify-env and aify-wrapper (git form; TARGET_ARCHITECTURE:35-38), then each selected
   service's approved client artifacts (S1 trust rules). The selected runtime CLI (claude, codex, hermes) is never
   installed by this chain: present means used, absent means reported pending.
2. **Connect** each selected service (S1): prepare, stage, probe, validate, publish.
3. **Render** launchers once, after the last connect.
4. **Apply assets.** Run each service's installer in assets-only mode for each selected client: skills, MCP
   registration, hooks. No registry writes and no re-entry into the chain.
5. **Report**: configured, running identity, native wake proof, and pending restarts and trust, as separate lists
   (S5). The chain starts, supersedes and relaunches nothing.

Clients are what the operator selects. PATH is inventory, not selection. herdr stays optional.

## Slices, in order

| # | page | ships |
|---|---|---|
| S1 | guided-install/S1-spec-and-connect.md | versioned spec, its trust boundary, `aify-wrapper connect` with staged publish; comms stays stdio with the copy |
| S2 | guided-install/S2-chain.md | the phased chain in aify-env's installer; handoff and assets-only modes |
| S3 | guided-install/S3-copy-consumers.md | the channel server and notify-check stop needing the bridge copy, or the copy stays declared |
| S4 | guided-install/S4-sse-no-copy.md | comms over SSE, the 5 tools mirrored, then the no-copy cutover (needs S3) |
| S5 | guided-install/S5-native-gates-and-live-test.md | the channel prompt (branch A UNKNOWN, branch B on the operator's authorization), the readiness report, task #17 |

## ASSUMED, not verified here

- The spec route, `aify-wrapper connect` and `aify-env registry check` exist nowhere yet. All three are proposed.
- That `aify-env credential set --ref` accepts a staged reference name for the dashboard's `secretsCredentialRef`.
- The dashboard's client package, bin and host-fact descriptor are PENDING from dashboard-manager.
- How all three installers behave on Linux. A container run cannot certify native Windows behaviour.
- Branch A on a personal account: UNKNOWN until a scoped native result exists.
