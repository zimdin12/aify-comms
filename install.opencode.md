# OpenCode: not supported

OpenCode is not a supported runtime. There is nothing to install for it:

- `bash install.sh --client opencode` exits 1 by design and writes no OpenCode config.
- The service would launch a managed OpenCode agent as `opencode-aify`, and no such launcher ships:
  [aify-wrapper](https://github.com/zimdin12/aify-wrapper) carries templates for claude, codex, hermes
  and pi only. aify-env runs only launchers that carry the harness marker, so it has nothing to start
  for OpenCode and does not offer it.
- The OpenCode adapter and controller code stay in the repo, unverified since aify-env became the host
  tier.

An OpenCode session can still call the aify-comms MCP tools if you add the server to its config by
hand, but it cannot be woken by a message. For agents that receive messages, use Claude Code
([install.claude.md](install.claude.md)), Codex ([install.codex.md](install.codex.md)) or Hermes
([install.hermes.md](install.hermes.md)), with aify-env and its `install.sh` on each agent host.
