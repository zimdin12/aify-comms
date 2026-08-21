# Hermes ACP wire-spike notes — 2026-05-23

Captured live against `hermes acp` (Hermes 0.14.0, provider openai-codex / gpt-5.5).

## Confirmed facts

- **Framing:** newline-delimited JSON. One JSON-RPC message per line on stdout/stdin. No LSP-style `Content-Length` headers.
- **JSON-RPC version:** 2.0.
- **Method names on the wire:** slash-separated (e.g. `session/new`, `session/prompt`, `session/update`, `session/cancel`, `session/close`, `initialize`).
- **Field names on the wire:** **camelCase**, NOT snake_case as the Python `acp/meta.py` source suggests. The Python schema uses `populate_by_name` aliases — the spec is camelCase over the wire.
  - `protocolVersion`, `clientCapabilities`, `clientInfo`, `agentCapabilities`, `agentInfo`, `authMethods`, `sessionId`, `mcpServers`, `stopReason`, `cachedReadTokens`, `inputTokens`, `outputTokens`, `thoughtTokens`, `totalTokens`, `readTextFile`, `writeTextFile`, `availableModels`, `currentModelId`, `availableModes`, `currentModeId`.
- **`session/update` discriminator:** `sessionUpdate` field (camelCase key) whose VALUE is snake_case:
  - `available_commands_update` — emitted right after `session/new`, lists slash commands.
  - `usage_update` — token usage progress: `{ size, used, sessionUpdate: "usage_update" }`. Emits before turn starts and after assistant chunks.
  - `agent_message_chunk` — assistant text streaming. `content: { type: "text", text: "P" }` per chunk.
  - (Spec also defines `agent_thought_chunk`, `tool_call`, `tool_call_update`, `plan`, `agent_plan_update`, `current_mode_update`, `user_message_chunk` — not seen in this minimal turn but the protocol module must handle them.)
- **`session/prompt` response shape:** `{ jsonrpc, id, result: { stopReason, usage: { cachedReadTokens, inputTokens, outputTokens, thoughtTokens, totalTokens } } }`. Stop reasons we saw: `end_turn`. Spec: also `refusal`, `cancelled`, `max_turn_requests`, `max_tokens`.
- **`session/new` response shape:** `{ result: { sessionId, models: { availableModels, currentModelId }, modes: { availableModes, currentModeId } } }`. Extra surfaces (models/modes) beyond the minimal `sessionId` — we ignore them today, may surface for `session/set_model` / `session/set_mode` later.
- **`initialize` response shape:** `{ result: { protocolVersion, agentInfo: { name, version }, agentCapabilities: { loadSession, promptCapabilities: { image }, sessionCapabilities: { fork, list, resume } }, authMethods: [{ id, name, description, args?, type? }] } }`.
- **`--accept-hooks` flag matters:** without it, the adapter prompts via TTY for shell-hook approval and a managed bridge would hang. Add to launcher.
- **Auth:** when the user's hermes setup is already configured (provider credentials present), `initialize` succeeds without calling `authenticate`. The bridge can skip `authenticate` for the happy path.

## Wire samples

**initialize request (camelCase):**
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},"clientInfo":{"name":"aify-comms-bridge","version":"4.0.0"}}}
```

**session/new request:**
```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"C:\\Docker\\aify-comms","mcpServers":[]}}
```

**session/prompt request:**
```json
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"b2b83419-...","prompt":[{"type":"text","text":"Say only the word: PONG"}]}}
```

**session/update notification (agent_message_chunk):**
```json
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"...","update":{"content":{"text":"P","type":"text"},"sessionUpdate":"agent_message_chunk"}}}
```

**session/prompt response:**
```json
{"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn","usage":{"cachedReadTokens":2560,"inputTokens":16605,"outputTokens":19,"thoughtTokens":11,"totalTokens":16624}}}
```

## Adjustments to the plan

The plan as written used snake_case field names. **The implementation must emit camelCase on the wire.** Concrete changes:

1. `encodeRequest` / `encodeNotification` payload construction in `hermes-acp-protocol.js`: build params with camelCase keys (`sessionId`, `mcpServers`, `protocolVersion`, `clientCapabilities`, `clientInfo`).
2. `formatSessionUpdateAsTerminalFrame`: switch on `update.sessionUpdate` (camelCase key, snake_case value).
3. Response parsing: read `result.sessionId`, `result.stopReason`, `result.usage.*` (all camelCase).
4. Add `usage_update` handler — emit as a dim `[~used/size tokens]` footer-style frame, or drop silently. (Decision: drop for now; we can surface via `terminalSink` status if operator asks.)
5. Bridge → hermes callback responses (`fs/read_text_file`): result key is `content` (snake_case-equivalent on the result side — confirm in spec). The acp schema.py defines `ReadTextFileResponse(content: str)` so the wire key is just `content`.

Everything else in the plan stands: framing is newline-delimited JSON; method names are slash-separated; lifecycle (initialize → session/new → session/prompt loop) matches.

## Open follow-up

- `tool_call` / `tool_call_update` variants not directly observed — verify on a turn that invokes a tool. (Will exercise via T5 client-callback test path.)
- `session/cancel` response shape unverified — assume null result based on spec.
- `session/request_permission` not seen because openai-codex provider is configured without per-tool gating; in stricter modes hermes will call back into the bridge. Plan auto-approves regardless.
