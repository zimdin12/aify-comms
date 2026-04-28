# First Coding Agent Task

## Role

You are the first implementation agent for the dashboard/control-plane lifecycle layer in `aify-comms`.

## Objective

Implement Slice 1 from [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md): environment registry.

Do not rewrite messaging, dispatch, or the dashboard broadly. Add the smallest useful environment layer that future spawn work can build on.

Read these before editing:

- [SESSION_MODEL.md](SESSION_MODEL.md)
- [WEB_APP_DESIGN.md](WEB_APP_DESIGN.md)
- [DASHBOARD_SPEC.md](DASHBOARD_SPEC.md)

## Required Behavior

1. Add persistent environment records.
2. Add API endpoints:
   - `GET /api/v1/environments`
   - `POST /api/v1/environments/heartbeat`
3. Have the stdio bridge heartbeat its environment with:
   - environment ID
   - label
   - machine ID
   - OS/kind
   - bridge ID
   - supported runtimes/capabilities
   - current working directory roots if known
   - bridge version if available
4. Add a simple dashboard section/page listing environments.
5. Add regression tests for heartbeat upsert and API rendering.

## Suggested Data Model

Table: `environments`

- `id TEXT PRIMARY KEY`
- `label TEXT`
- `machine_id TEXT`
- `os TEXT`
- `kind TEXT`
- `bridge_id TEXT`
- `cwd_roots TEXT DEFAULT '[]'`
- `runtimes TEXT DEFAULT '[]'`
- `status TEXT DEFAULT 'online'`
- `metadata TEXT DEFAULT '{}'`
- `registered_at TEXT`
- `last_seen TEXT`

Keep JSON fields as serialized text to match existing project style.

## Endpoint Shape

`POST /api/v1/environments/heartbeat`

Request body:

```json
{
  "id": "wsl:StevenZ-L:default",
  "label": "WSL on StevenZ-L",
  "machineId": "linux:StevenZ-L",
  "os": "linux",
  "kind": "wsl",
  "bridgeId": "bridge-uuid",
  "cwdRoots": ["/mnt/c/Docker"],
  "runtimes": [
    {
      "runtime": "codex",
      "modes": ["managed-warm"],
      "capabilities": {
        "nativeResume": true,
        "bridgeResume": true,
        "cliAttach": false,
        "interrupt": true,
        "streaming": true
      }
    }
  ],
  "metadata": {}
}
```

Response:

```json
{
  "ok": true,
  "environment": { "...": "..." }
}
```

## Constraints

- Preserve all existing `aify-comms` tests.
- Do not make environment heartbeat required for existing message/dispatch flows yet.
- Do not change `comms_register` semantics in this slice.
- Do not implement actual spawn requests yet.
- Keep dashboard additions simple and functional.
- Do not make the dashboard a raw JSON/table dump. Show environment name, bridge ID, OS/kind, runtimes, roots, status, and last seen in a readable layout.
- Preserve the future distinction between environment, agent identity, and session. This slice only implements environments.

## UI Acceptance Criteria

- Environments are visible from dashboard navigation or a clearly labeled section.
- Each environment row/card shows bridge label, machine/OS, runtime capability summary, workspace roots, status, and last heartbeat.
- Long bridge IDs and roots are truncated with full value available on hover/click/copy.
- Empty state tells the user to run/connect a bridge.
- There is no spawn button wired to fake behavior yet unless clearly disabled or marked "coming next".

## Verification

Run:

```bash
python -m unittest service.tests.test_api_v2_regressions -v
node --check mcp/stdio/server.js
docker compose up -d --build
curl http://localhost:8800/health
```

If host Python lacks FastAPI, run the Python tests inside the service container after copying or rebuilding.

## Deliverable Summary Format

Return:

- files changed
- API behavior added
- dashboard behavior added
- tests run
- what remains for Slice 2
