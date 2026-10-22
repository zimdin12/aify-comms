# A custom channel without the development-channels warning (2026-10-22)

Dashboard task #3, first step: is there a route that starts Claude Code with an aify channel and no "WARNING: Loading
development channels" dialog? Claude Code 2.1.308 (npm `@anthropic-ai/claude-code@2.1.308`), in a throwaway
`node:22-bookworm` container that was created and removed. The API key is a fake string. In the `out-local-api` and
`out-envpath` runs, `ANTHROPIC_BASE_URL` points at `fake-api.mjs` inside the container, so nothing reached a real
account.

## The route

1. The channel ships as a plugin in a marketplace (`fixture/aify-local`), installed with
   `claude plugin marketplace add <dir>` and `claude plugin install aify-channel@aify-local`.
2. Managed settings allow it:
   `{"channelsEnabled": true, "allowedChannelPlugins": [{"marketplace": "aify-local", "plugin": "aify-channel"}]}`.
3. Claude starts with `claude --channels plugin:aify-channel@aify-local`, not `--dangerously-load-development-channels`.

## What was observed

| run | case | flags | result |
|---|---|---|---|
| out-local-api | allowlisted | `--channels`, plugin in `allowedChannelPlugins` (`/etc/claude-code/managed-settings.json`) | no dialog; `← aify-channel: aify channel proof event` delivered, and the session tried to answer it (401 from the fake API) |
| out-local-api | not-listed | `--channels`, `allowedChannelPlugins` names another plugin | no dialog; the server pushed its event (its log) and nothing arrived: dropped |
| out-local-api, out | dev-flag | `--dangerously-load-development-channels` | the full-screen "WARNING: Loading development channels" dialog: the positive control |
| out | allowlisted and not-listed, with the real API URL | `--channels` | "--channels waiting for your organization's policy … Messages are dropped until it loads". The fake key gets a 401 on remote managed settings, so the run can't separate the two. That is why the local-API run exists |
| out-envpath | without-env | `--channels`, no managed settings | "plugin:aify-channel@aify-local · not on the approved channels allowlist" |
| out-envpath | with-env | the allowlist only in a folder named by the undocumented `CLAUDE_CODE_MANAGED_SETTINGS_PATH` | not delivered: that variable does not substitute for the policy location in this use |

## What the binary says (static, ASSUMED until run on the operator's account)

The channel gate in 2.1.308 (function text in the linux-x64 binary):
- It blocks on `channelsEnabled` only for claude.ai **team or enterprise** logins, or, with an API key, when managed
  settings exist without it.
- It takes `allowedChannelPlugins` from managed settings whenever they set it, with no account-type check.
- So a personal Pro or Max login should register an allowlisted plugin the same way.

## On Windows (ASSUMED, from the managed-settings docs; not run)

The machine-wide `C:\Program Files\ClaudeCode\managed-settings.json` needs admin rights. The docs' user-writable
alternative is the `HKCU\SOFTWARE\Policies\ClaudeCode` registry value `Settings` (REG_SZ JSON). It is used only when
no admin source exists (HKLM, the Program Files folder, or server-managed settings). A read-only check on this host
on 2026-10-22 found none of them. Writing HKCU changes policy for every Claude session of that Windows user, so it is
the operator's decision. The allowlist also replaces Anthropic's default list, so any official channel plugin they
use must be listed too.
