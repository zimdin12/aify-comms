# Agent Guide: Building Services on aify-container

This guide is for AI agents tasked with building a service on this template.

## Overview

This template provides a FastAPI orchestrator that manages Docker sub-containers on demand. Your job is to:
1. Define what containers to run (models, databases, etc.)
2. Add API endpoints that use those containers
3. Register MCP tools so other AI agents can use the service
4. Update integrations (Claude Code, OpenClaw, Open WebUI)

## Platform Patterns

When building on `aify-container`, prefer these patterns so services stay consistent across runtimes and transports:

- Put orchestration state in the service, not in a specific client.
- Keep stdio and SSE tool surfaces as aligned as practical.
- Model runtime differences with capabilities, not with hidden special cases.
- If a feature is unsupported for a runtime, return a clear capability error instead of silently falling back.
- Separate message delivery from active execution. A message bus and a dispatch system are different layers.
- Prefer one small installer or handoff doc per client runtime so another agent can install the service without reverse-engineering the repo.
- If you support "resident sessions" and "managed workers", document them explicitly and make the default wake-up path obvious.
- Do not teach agents to depend on polling loops when a resident wake-up or trigger path exists; reserve waiting loops for intentional inbox-driven workers.

## File Map: What to Modify

| File | What Goes Here | Priority |
|------|---------------|----------|
| `config/service.example.json` | Container definitions (images, commands, GPU, volumes) | First |
| `service/routers/api.py` | Domain-specific REST endpoints | Core |
| `mcp/sse_server.py` | MCP tools (SSE transport, runs in container) | Core |
| `mcp/stdio/server.js` | MCP tools (stdio transport, mirrors SSE) | Core |
| `service/requirements.txt` | Python dependencies | As needed |
| `Dockerfile` | System packages (apt-get) | As needed |
| `.env.example` | Service-specific env vars | As needed |
| `integrations/claude-code/SKILL.md` | Skill definition with all tools | Important |
| `integrations/openclaw/index.ts` | Plugin tools + hooks | Important |
| `integrations/open-webui/tool.py` | Tool methods for chat UI | Important |
| `integrations/open-webui/prompt.md` | System prompt for chat models | Nice to have |

## Step-by-Step

### Step 1: Define Containers

Edit `config/service.example.json`. Each container needs at minimum:
- `image` - Docker image
- `internal_port` - Port the process listens on
- `command` - How to start it (if not using image default)

Optional but recommended:
- `gpu` - Device IDs and memory fraction
- `volumes` - Named volumes for persistence
- `idle_timeout_seconds` - When to auto-stop (0 = never)
- `auto_start` - Start on orchestrator startup
- `group` - Logical grouping name
- `health_check.endpoint` - Path to poll for readiness

Example for a llama.cpp container:
```json
"qwen": {
  "image": "ghcr.io/ggerganov/llama.cpp:server-cuda",
  "command": ["--model", "/models/qwen3.5.gguf", "--port", "8080", "--gpu-layers", "99"],
  "volumes": { "llm-models": "/models" },
  "gpu": { "device_ids": ["0"], "memory_fraction": 0.6 },
  "idle_timeout_seconds": 300,
  "group": "inference"
}
```

To share a container (avoid duplicates):
```json
"openmemory-llm": {
  "image": "ghcr.io/ggerganov/llama.cpp:server-cuda",
  "shared_with": "qwen"
}
```

### Step 2: Add API Endpoints

Edit `service/routers/api.py`. Access containers via the manager:

```python
from fastapi import APIRouter, Request, HTTPException
import httpx

router = APIRouter(tags=["api"])

@router.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions using the qwen container."""
    manager = request.app.state.container_manager
    state = manager.states.get("qwen")

    if not state or state.status.value != "running":
        # Start it
        await manager.start_container("qwen")
        state = manager.states["qwen"]

    url = f"{state.internal_url}/v1/chat/completions"
    body = await request.json()

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=body)
        return resp.json()
```

Or use the built-in proxy: clients can directly call `/route/qwen/v1/chat/completions`.

### Step 3: Register MCP Tools

Edit `mcp/sse_server.py`. Container management tools are already built-in. Add domain-specific tools:

```python
@mcp_server.tool()
async def chat(message: str, model: str = "qwen") -> str:
    """Send a chat message to a specific LLM container."""
    manager = _get_manager()
    if not manager:
        return "Container manager not available"

    # Ensure container is running
    await manager.start_container(model)
    url = manager.resolve_url(model)

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{url}/v1/chat/completions", json={
            "messages": [{"role": "user", "content": message}]
        })
        data = resp.json()
        return data["choices"][0]["message"]["content"]
```

Mirror in `mcp/stdio/server.js`:
```javascript
server.tool(
  "chat",
  "Send a chat message to an LLM container",
  {
    message: z.string().describe("The message to send"),
    model: z.string().optional().default("qwen").describe("Container name"),
  },
  async ({ message, model }) => {
    try {
      const result = await apiCall("POST", "/api/v1/chat/completions", {
        model, messages: [{ role: "user", content: message }]
      });
      return ok(result);
    } catch (e) { return err(e); }
  }
);
```

### Step 4: Update Integrations

**Claude Code** (`integrations/claude-code/SKILL.md`):
- Add your new tools to the `tools:` list in the frontmatter
- Document each tool with usage examples
- Update triggers for when the skill should activate

**OpenClaw** (`integrations/openclaw/index.ts`):
- Add tool handlers that call your REST endpoints
- Implement `before_agent_start` hook for auto-context injection
- Implement `agent_end` hook for auto-processing

**Open WebUI** (`integrations/open-webui/tool.py`):
- Add async methods - each becomes a tool in the chat UI
- Use `httpx` (not `aiohttp`) - it's available in Open WebUI's environment

### Step 5: Add Dependencies

- Python packages: `service/requirements.txt`
- System packages: add to `Dockerfile` in the `apt-get install` line
- Node packages: `mcp/stdio/package.json`

### Step 6: Persistence

- Use `/data` directory for the orchestrator's own persistent data
- Sub-containers use named volumes (defined in their config)
- Access path via `from service.config import get_config; get_config().data_dir`

### Step 7: Test

```bash
docker compose up -d --build
bash scripts/test-endpoints.sh

# Test a specific container:
curl -X POST http://localhost:8800/api/v1/containers/qwen/start
curl http://localhost:8800/route/qwen/health
curl http://localhost:8800/api/v1/gpu
```

## Architecture

```
Clients (Claude Code, OpenClaw, Open WebUI, curl)
         |
         v
+------------------------------+
|  FastAPI Orchestrator (:8800) |
|  /health /info /docs         |
|  /api/v1/* (your endpoints)  |
|  /route/{name}/* (proxy)     |
|  /mcp/sse (MCP server)       |
|                              |
|  ContainerManager            |
|   - DockerClient (SDK)       |
|   - GPUAllocator             |
|   - IdleReaper (background)  |
|   - HealthMonitor (bg)       |
+------|----------|------------+
       |          |
  docker.sock     |
       |          |
  +----v---+ +----v----+ +-------+
  | embed  | | qwen    | | glm4  |
  | GPU:0  | | GPU:0   | | GPU:1 |
  | 0.2    | | 0.6     | | 0.5   |
  +--------+ +---------+ +-------+
   (always)   (on-demand)  (on-demand)
 ```

## Agent-Team Pattern

For services meant to be used by multiple coding agents, the strongest reusable shape is:

- `message` tools for conversation and handoffs
- `channel` tools for shared team threads
- `artifact` tools for shared outputs
- `dispatch` tools for active execution requests
- `run` tools for status and control of active work

That pattern scales better than overloading one tool to do everything, and it keeps marketplace installs easier to explain.

## Checklist

- [ ] Container definitions in service.example.json
- [ ] API endpoints in service/routers/api.py
- [ ] MCP tools in mcp/sse_server.py (SSE)
- [ ] MCP tools in mcp/stdio/server.js (stdio)
- [ ] Claude Code SKILL.md updated
- [ ] OpenClaw plugin updated
- [ ] Open WebUI tool updated
- [ ] Python deps in requirements.txt
- [ ] System deps in Dockerfile (if any)
- [ ] .env.example updated (if new vars)
- [ ] `docker compose up --build` works
- [ ] All endpoints respond correctly
- [ ] Sub-containers start and respond via /route/
