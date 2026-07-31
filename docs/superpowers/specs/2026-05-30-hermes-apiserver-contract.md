# hermes `api_server` platform — HTTP/SSE contract + daemon lifecycle

Status: RECON / spec. Read-only study of the installed hermes-agent. No hermes
code was modified. Drives Task A1 of
`docs/superpowers/plans/2026-05-30-hermes-apiserver-delivery.md`: building a Node
HTTP client + sidecar against the api_server platform.

**Installed version: hermes-agent v0.15.1 (2026.5.29)** at
`C:\Users\dev\AppData\Local\hermes\hermes-agent`.

Source studied (all read-only):

- `gateway/platforms/api_server.py` — the api_server platform adapter (4229 lines)
- `gateway/config.py` — `Platform.API_SERVER`, env→config enabling
- `hermes_cli/config.py` — env-var schema for `API_SERVER_*`
- `gateway/run.py` — gateway daemon / `hermes gateway run`
- `ui-tui/src/gatewayClient.ts`, `website/docs/user-guide/tui.md` — `HERMES_TUI_GATEWAY_URL`

> **CRITICAL DISTINCTION (a plan assumption that is FALSE).**
> The `api_server` platform (HTTP/SSE, default port **8642**) and the TUI/dashboard
> gateway (WebSocket RPC, default port **8765**, env `HERMES_TUI_GATEWAY_URL`) are
> **two separate transports** inside the same `hermes gateway run` daemon.
> `HERMES_TUI_GATEWAY_URL` does **NOT** point a TUI at the api_server HTTP port —
> it is a `ws://…:8765/api/ws?token=…` WebSocket URL handled by `tui_gateway`
> (documented separately in `2026-05-30-hermes-0.15.1-gateway-api.md`).
> A Node HTTP/SSE client built against this contract talks to **8642**, not 8765.

---

## TL;DR for the client implementation

- **Auth:** `Authorization: Bearer <API_SERVER_KEY>`. Missing/wrong → **401**
  `{"error":{"code":"invalid_api_key",...}}`. Compared with `hmac.compare_digest`.
- **Best fit for "one stable session per agent":** use the `/api/sessions` +
  `/api/sessions/{id}/chat/stream` family. But note: **session chat does NOT
  auto-create** — an unknown `session_id` returns **404**. The client must
  `POST /api/sessions` (with explicit `id`) once to pin the session, then chat.
- **Session chat stream SSE:** named events. Text deltas arrive on
  **`event: assistant.delta`** with `data:{"delta":"…","message_id":…}`. Terminal
  events are **`assistant.completed`** → **`run.completed`** → **`done`**
  (the literal `event: done` is the last framed event before stream close).
- **`/v1/runs` SSE** (different framing): **no `event:` line**, only
  `data: {"event":"…",...}`. Terminal event objects: `run.completed` /
  `run.failed` / `run.cancelled`, then a bare `: stream closed` SSE comment.
- **`/v1/runs` and `/v1/runs/{id}/approval` and `/stop` all EXIST** in 0.15.1.
- **Health probe:** `GET /health` (unauthenticated) → `{"status":"ok",...}`.
- **Daemon start:** `API_SERVER_ENABLED=1 API_SERVER_KEY=<secret> hermes gateway run`
  (optionally `--replace`). Port via `API_SERVER_PORT`, host via `API_SERVER_HOST`.
  There is **no** dedicated `hermes api-server` subcommand and **no CLI flag** to
  enable it — it is enabled purely via env / `config.yaml`.

---

## 1. Auth

Exact header: **`Authorization: Bearer <key>`**.

`_check_auth` (`api_server.py:843-867`):

```python
auth_header = request.headers.get("Authorization", "")
if auth_header.startswith("Bearer "):
    token = auth_header[7:].strip()
    if hmac.compare_digest(token, self._api_key):
        return None  # Auth OK
...
return web.json_response(
    {"error": {"message": "Invalid API key", "type": "invalid_request_error",
               "code": "invalid_api_key"}},
    status=401,
)
```

- Missing header, wrong scheme, or wrong token → **HTTP 401** with body
  `{"error":{"message":"Invalid API key","type":"invalid_request_error","code":"invalid_api_key"}}`.
- Constant-time compare via `hmac.compare_digest`.
- The key is read from `extra["key"]` or env `API_SERVER_KEY` (`api_server.py:696`).
- If no key is configured at all, `_check_auth` returns `None` (allows) — but
  `connect()` **refuses to start** the server without a key
  (`api_server.py:4146-4152`), so in practice auth is always enforced.
- `/health` and `/health/detailed` are the **only** endpoints that skip
  `_check_auth` (`api_server.py:1023-1046`). Everything else (incl. `/v1/models`,
  `/v1/capabilities`) calls `_check_auth` first.
- CORS preflight allows header `Authorization` (`api_server.py:504`).

---

## 2. Session chat endpoints

Routes registered (`api_server.py:4107-4108`):

- `POST /api/sessions/{session_id}/chat` → `_handle_session_chat`
- `POST /api/sessions/{session_id}/chat/stream` → `_handle_session_chat_stream`

Both **require the session to already exist** — `_get_existing_session_or_404`
is called before processing (`api_server.py:1495`, `:1539`).

### Request headers consumed

`_check_auth` reads `Authorization`. Two session headers
(`_parse_session_key_header`, `api_server.py:882-932`; and the `X-Hermes-Session-Id`
usage below):

- **`X-Hermes-Session-Id`** — continuity / short-term transcript scope. For the
  session-chat endpoints the session id comes from the **URL path**
  (`{session_id}`), and `X-Hermes-Session-Id` is **echoed back in the response
  headers** (`api_server.py:1517`, `:1643`). (On `/v1/chat/completions` and
  `/v1/responses` this header is read on input to pin continuity; on session-chat
  the path segment is authoritative.)
- **`X-Hermes-Session-Key`** — **long-term memory scope** (Honcho per-chat state),
  independent of the transcript session id. Stable per-channel identifier; passed
  to the agent as `gateway_session_key` (`api_server.py:1513`, `:976-979`).
  - Requires API-key auth; if sent with no `API_SERVER_KEY` configured → **403**
    (`api_server.py:905-916`).
  - Rejects `\r \n \0` → **400**; max length **256** chars → **400**
    (`api_server.py:920-930`).
  - Echoed back in the response headers when present (`:1518-1519`, `:1645-1646`).

**Semantics summary:** `X-Hermes-Session-Id` = *which transcript / continuity*;
`X-Hermes-Session-Key` = *which long-term-memory bucket*. They are orthogonal.

### Request body JSON keys

`_session_chat_user_message` (`api_server.py:323-334`):

- Message text field: **`message`**, or fallback **`input`** (first non-empty).
  Accepts a plain string or OpenAI multimodal content array
  (`{"type":"text","text":…}` / `image_url`). Empty → **400** `missing_message`.
- Optional system prompt: **`system_message`**, or fallback **`instructions`**
  (must be a string else **400** `invalid_system_message`)
  (`api_server.py:1504-1506`, `:1548-1550`).

History is loaded server-side from SessionDB
(`_conversation_history_for_session`, `api_server.py:1507`); the client does
**not** resend history for these endpoints.

### Non-stream response (`POST …/chat`)

`api_server.py:1520-1528`. HTTP 200, JSON:

```json
{
  "object": "hermes.session.chat.completion",
  "session_id": "<effective session id>",
  "message": {"role": "assistant", "content": "<final text>"},
  "usage": {"input_tokens": N, "output_tokens": N, "total_tokens": N}
}
```

Response headers: `X-Hermes-Session-Id: <effective>` (and `X-Hermes-Session-Key`
if one was supplied). `effective_session_id` can differ from the path id if the
agent rotated the session due to compression.

### Stream response (`POST …/chat/stream`) — SSE framing

`Content-Type: text/event-stream`, `Cache-Control: no-cache`,
`X-Accel-Buffering: no`, plus the echoed session headers (`api_server.py:1639-1647`).

Framing is **`event: <name>\ndata: <json>\n\n`** (`api_server.py:1662`).
Keepalive is a bare SSE comment `: keepalive\n\n` every 30 s of idle
(`api_server.py:1653-1655`, `CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS = 30.0`).

Every `data` payload carries `session_id`, `run_id`, `seq` (monotonic), `ts`
(epoch float) defaults (`_event_payload`, `api_server.py:1558-1565`).
`run_id`/`message_id` are minted per request as `run_<hex>` / `msg_<hex>`.

Event sequence (`_run_and_signal`, `api_server.py:1592-1629`):

| `event:` | `data:` payload (key fields) | meaning |
|---|---|---|
| `run.started` | `{user_message:{role,content}, session_id, run_id, seq, ts}` | turn began |
| `message.started` | `{message:{id,role:"assistant"}, …}` | assistant msg opened |
| **`assistant.delta`** | `{message_id, delta:"<text chunk>", …}` | **assistant TEXT delta** (the streaming token text) |
| `tool.progress` | `{message_id, tool_name, delta}` | reasoning/thinking deltas (`reasoning.available` mapped here) |
| `tool.started` | `{message_id, tool_name, preview, args}` | tool call began |
| `tool.completed` / `tool.failed` | `{message_id, tool_name, preview, args}` | tool finished |
| **`assistant.completed`** | `{session_id, message_id, content:"<full final text>", completed:true, partial:false, interrupted:false}` | final assistant text (authoritative full body) |
| **`run.completed`** | `{session_id, message_id, completed:true, messages:[…turn transcript…], usage:{…}}` | run done + usage |
| `error` | `{message:"<err>"}` | only on exception (replaces the two completed events) |
| **`done`** | `{}` | **TERMINAL framed event**, always emitted last, then the queue sentinel closes the stream |

So for the Node parser: accumulate text from **`assistant.delta`**; treat
**`done`** as the terminal marker (with `assistant.completed.content` as the
authoritative final string and `run.completed.usage` for tokens). Tool/status
events are `tool.started` / `tool.completed` / `tool.failed` / `tool.progress`.

### Unknown `X-Hermes-Session-Id` / unknown path session id → NOT auto-created

For the `/api/sessions/{id}/chat[/stream]` endpoints, the session must exist:
`_get_existing_session_or_404` returns **404**
`{"error":{"code":"session_not_found",...}}` when SessionDB has no such row
(`api_server.py:1286-1293`). It is **not** auto-created or pinned.

→ **To pin one stable session per agent:** call `POST /api/sessions` once with an
explicit `id` (e.g. `"aify-<agent>"`). `_handle_create_session`
(`api_server.py:1334-1369`):
- body `id` (or `session_id`); auto-generates `api_<ts>_<hex8>` if omitted.
- rejects `\r\n\0`, >256 chars → 400; existing id → **409** `session_exists`.
- optional `model`, `system_prompt`, `title`.
- returns **201** `{"object":"hermes.session","session":{…}}`.
Thereafter every turn targets `/api/sessions/<that id>/chat/stream`, giving a
stable transcript (and stable sandbox dir) across turns.

> NOTE: `/v1/chat/completions` and `/v1/responses` behave differently — there an
> unknown `X-Hermes-Session-Id` is auto-derived/pinned and conversation history is
> client-supplied. The session-chat family is the one with explicit lifecycle and
> server-side history, which is what aify wants for "one durable session per agent."

---

## 3. Run endpoints (all present in 0.15.1)

Routes (`api_server.py:4123-4127`):

- `POST /v1/runs` → `_handle_runs`
- `GET  /v1/runs/{run_id}` → `_handle_get_run` (poll status)
- `GET  /v1/runs/{run_id}/events` → `_handle_run_events` (SSE)
- `POST /v1/runs/{run_id}/approval` → `_handle_run_approval`
- `POST /v1/runs/{run_id}/stop` → `_handle_stop_run`

### `POST /v1/runs` — start a run

Body (`api_server.py:3577-3633`):

- **`input`** (required): string, or array of `{role,content}` messages
  (last = user message; earlier ones become history). Missing/empty → **400**.
- `instructions` (optional): ephemeral system prompt.
- `conversation_history` (optional): array of `{role,content}` — precedence over
  `previous_response_id`. Malformed → 400.
- `previous_response_id` (optional): pulls stored history from the Responses store.
- `session_id` (optional): else `stored_session_id` else falls back to the run_id.
- `model` (optional, recorded in status only).
- `X-Hermes-Session-Key` header honored as memory scope (same rules as §2).

Concurrency cap: `_MAX_CONCURRENT_RUNS = 10` → **429** `rate_limit_exceeded`
(`api_server.py:3566-3570`).

Response: **HTTP 202** `{"run_id": "run_<hex>", "status": "started"}`
(`api_server.py:3844-3848`). `X-Hermes-Session-Key` echoed if supplied.

### `GET /v1/runs/{run_id}` — poll status

`api_server.py:3850-3863`. 404 `run_not_found` if unknown. Else the status object
(`_set_run_status`, `:3493-3506`):

```json
{"object":"hermes.run","run_id":"…","status":"queued|running|waiting_for_approval|stopping|completed|failed|cancelled",
 "created_at":…, "updated_at":…, "session_id":…, "model":…, "last_event":…,
 "output":"…", "usage":{…}, "error":"…"}
```

Terminal status retained `_RUN_STATUS_TTL = 3600` s.

### `GET /v1/runs/{run_id}/events` — SSE

`api_server.py:3865-3912`. Headers `text/event-stream`, `no-cache`,
`X-Accel-Buffering: no`. Subscribe race window: polls up to ~1 s for the run to
register, else **404** `run_not_found`.

**Framing differs from session-chat:** these frames have **no `event:` line** —
just `data: <json>\n\n` (`api_server.py:3904`). The event *type* is the
`"event"` key **inside** the JSON object. Keepalive `: keepalive\n\n` every 30 s;
final SSE comment `: stream closed\n\n` then close (`:3898`, `:3902`).

Event objects (`_make_run_event_callback` `:3508-3552`, and `_run_and_close`
`:3667-3830`):

| `data.event` | key fields | meaning |
|---|---|---|
| `message.delta` | `{run_id, timestamp, delta}` | assistant TEXT delta |
| `reasoning.available` | `{run_id, timestamp, text}` | reasoning text |
| `tool.started` | `{run_id, timestamp, tool, preview}` | tool began |
| `tool.completed` | `{run_id, timestamp, tool, duration, error}` | tool finished |
| `approval.request` | `{run_id, timestamp, choices:["once","session","always","deny"], …}` | approval needed |
| `approval.responded` | `{run_id, timestamp, choice, resolved}` | approval resolved |
| **`run.completed`** | `{run_id, timestamp, output:"<final>", usage:{…}}` | TERMINAL: success |
| **`run.failed`** | `{run_id, timestamp, error}` | TERMINAL: failure |
| **`run.cancelled`** | `{run_id, timestamp}` | TERMINAL: stopped/cancelled |

After any terminal event a `None` sentinel is queued → SSE writes `: stream closed`
and ends. (`_thinking` and `subagent_progress` are intentionally NOT forwarded,
`:3550`.)

### `POST /v1/runs/{run_id}/approval`

`api_server.py:3915-4001`. Body **`choice`** ∈ `{once, session, always, deny}`
(aliases `approve|approved|allow` → `once`); optional `all`/`resolve_all` bool.
404 if run unknown; 409 `approval_not_active` / `approval_not_pending`; 400
`invalid_approval_choice`. Success → `{"object":"hermes.run.approval_response",
"run_id":…,"choice":…,"resolved":N}` and emits an `approval.responded` SSE event.

### `POST /v1/runs/{run_id}/stop`

`api_server.py:4003-4022+`. 404 `run_not_found` if neither agent nor task is
tracked. Else sets status `stopping`, calls `agent.interrupt(...)`; the run task
emits `run.cancelled` on the events stream.

---

## 4. Health / version

- **`GET /health`** (and alias `GET /v1/health`) — **unauthenticated**
  (`api_server.py:1023-1025`, route `:4092`, `:4094`):
  `{"status":"ok","platform":"hermes-agent"}`.
- **`GET /health/detailed`** — also unauthenticated (`api_server.py:1027-1046`):
  `{"status":"ok","platform":"hermes-agent","gateway_state":…,"platforms":{…},
  "active_agents":N,"exit_reason":…,"updated_at":…,"pid":…}`. Good for confirming
  the daemon owns the gateway runtime and which platforms connected.
- **`GET /v1/capabilities`** — *authenticated* machine-readable feature/endpoint
  map (`api_server.py:1069-1147`). Confirms `session_continuity_header:
  "X-Hermes-Session-Id"`, `session_key_header: "X-Hermes-Session-Key"`, and lists
  every endpoint path. Useful for version-detecting the surface, but requires the
  Bearer key.
- There is **no version-number endpoint**. Cheapest liveness probe:
  unauthenticated **`GET /health`** (a 200 with `status:ok` means the api_server
  TCP site is up). To also confirm auth + gateway health, hit
  `GET /v1/capabilities` with the Bearer key, or `GET /health/detailed`.

---

## 5. Daemon lifecycle

### Enabling the platform

`api_server` is enabled purely by env (or `config.yaml`), **not** by a CLI flag.
`hermes_cli/config.py` (`_apply_env_overrides`, lines `:1480-1505`):

```python
api_server_enabled = os.getenv("API_SERVER_ENABLED","").lower() in {"true","1","yes"}
api_server_key     = os.getenv("API_SERVER_KEY","")
...
if api_server_enabled or api_server_key:        # key alone also enables it
    config.platforms[Platform.API_SERVER].enabled = True
    extra["key"]  = api_server_key
    extra["port"] = int(API_SERVER_PORT)         # if set
    extra["host"] = API_SERVER_HOST              # if set
    extra["cors_origins"] = [...]                # if set
    extra["model_name"]   = API_SERVER_MODEL_NAME# if set
```

Env-var schema (`hermes_cli/config.py:3121-3155`): `API_SERVER_ENABLED`,
`API_SERVER_KEY` (password, **required when enabled — server refuses to start
without it**), `API_SERVER_PORT` (default **8642**), `API_SERVER_HOST`
(default `127.0.0.1`), `API_SERVER_MODEL_NAME`.

Defaults from `api_server.py:65-66`: `DEFAULT_HOST = "127.0.0.1"`,
`DEFAULT_PORT = 8642`. **Port 8642 confirmed.**

### Exact start command (fixed port + known key)

```bash
API_SERVER_ENABLED=1 \
API_SERVER_KEY=<secret> \
API_SERVER_PORT=8642 \
API_SERVER_HOST=127.0.0.1 \
hermes gateway run
```

- Add `--replace` to take over a stale gateway runtime lock
  (`gateway/run.py:18860`, `hermes_cli/gateway.py` examples). `hermes gateway run`
  is the daemon entrypoint; it starts ALL enabled platforms, of which api_server
  is one. There is no `hermes api-server` subcommand.
- Equivalent config: set the same keys in `~/.hermes/.env` (per the bundled plan
  `.plans/openai-api-server.md:213` → `API_SERVER_ENABLED=true`) or under
  `platforms.api_server` in `~/.hermes/config.yaml`.
- On Windows the detached service is spawned as
  `pythonw -m hermes_cli.main gateway run` (`hermes_cli/gateway_windows.py:305`,
  `:551`).
- Startup guards (`api_server.py:4143-4179`): refuses to start without
  `API_SERVER_KEY` (even on loopback); refuses a placeholder key on a
  network-accessible bind; **fails fast if the port is already in use** (logs
  `Port %d already in use`).

### Detecting it's already running

- TCP/HTTP probe: `GET http://127.0.0.1:8642/health` → 200 `{"status":"ok"}`.
- `connect()` itself does a `socket.connect(('127.0.0.1', port))` preflight; a
  successful connect means something already holds the port (`api_server.py:4172-4177`).
- For gateway-wide liveness independent of the HTTP port, hermes persists runtime
  status (`gateway/status.py`, surfaced via `/health/detailed`).

### `HERMES_TUI_GATEWAY_URL` — NOT the api_server port

`HERMES_TUI_GATEWAY_URL` lets a TUI attach to an already-running gateway, but over
the **tui_gateway WebSocket** transport, e.g.
`ws://localhost:8765/api/ws?token=<auth-token>`
(`website/docs/user-guide/tui.md:269-282`; consumed in
`ui-tui/src/gatewayClient.ts:32-36`). When set, the TUI skips spawning its own
gateway and becomes a thin WS client sharing state with the dashboard and any
other attached surface. This is the **same daemon** that hosts the api_server
platform, but a **different port/transport** (8765 WS RPC vs 8642 HTTP/SSE). A
Node HTTP/SSE client for aify must target the **8642 api_server** endpoints
documented here, not `HERMES_TUI_GATEWAY_URL`. (The WS/RPC surface is covered in
`2026-05-30-hermes-0.15.1-gateway-api.md`.)

---

## 6. Plan assumptions — verified vs corrected

| Plan assumption | Verdict |
|---|---|
| Default api_server port **8642** | **TRUE** (`api_server.py:66`; env default `:3138`). |
| Auth header carries `API_SERVER_KEY` | **TRUE**, as `Authorization: Bearer <key>` (not `X-API-Key`). Wrong/missing → 401. |
| `X-Hermes-Session-Id` (continuity) + `X-Hermes-Session-Key` (memory scope) header names | **TRUE** — exact names confirmed (`:901`, `:1517`, capabilities `:1120-1121`). |
| `POST /api/sessions/{id}/chat` and `/chat/stream` exist | **TRUE** (`:4107-4108`). |
| Unknown `X-Hermes-Session-Id`/path session auto-creates a session on the chat endpoints | **FALSE** — session-chat returns **404** `session_not_found`; you must `POST /api/sessions` first to pin it. (Auto-derive only happens on `/v1/chat/completions`+`/v1/responses`.) |
| `/v1/runs`, `/v1/runs/{id}/events`, `/stop`, `/approval` exist | **TRUE**, all present in 0.15.1. |
| `HERMES_TUI_GATEWAY_URL` lets a TUI attach to the api_server | **FALSE/misleading** — it is a separate **WebSocket** (`ws://…:8765/api/ws`) transport, not the 8642 HTTP api_server. Same daemon, different port. |
| A single SSE framing applies to all streams | **FALSE** — session-chat uses **named** `event:`+`data:` frames (`assistant.delta`/`done`); `/v1/runs` events use **`data:`-only** frames with the type in the JSON `"event"` key. The Node parser must handle both. |
| A `hermes api-server` CLI subcommand / flag enables it | **FALSE** — enabled only via `API_SERVER_ENABLED`/`API_SERVER_KEY` env or `config.yaml`; launched by `hermes gateway run`. |
