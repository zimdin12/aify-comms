# Legacy aify-container - System Prompt Addition

This file is legacy reference material for an older container-service integration. It is not the current `aify-comms` dashboard/control-plane prompt. Current agent communication guidance lives in `.agents/skills/aify-comms/SKILL.md`, `.claude/skills/aify-comms/SKILL.md`, and `docs/COMMUNICATION_GUIDE.md`.

Add this block to your Open WebUI model's system prompt.

---

## Legacy Available Service: aify-container

You have access to a containerized service that manages on-demand sub-containers (LLMs, databases, etc.).

### Tools:
- `service_info` - Service metadata, endpoints, container list
- `list_containers` - All containers with status, GPU, groups
- `start_container(name)` - Start a container by name
- `stop_container(name)` - Stop a container
- `gpu_status` - GPU allocation across containers

### Usage:
1. Call `list_containers` to see available containers and their status
2. Call `start_container("name")` to start one you need
3. Use the proxy URL pattern to access container APIs: `http://SERVICE_URL/route/{name}/{path}`
4. Containers auto-stop after idle timeout

### Proxy examples:
```
POST /route/qwen/v1/chat/completions   -> Chat with Qwen model
POST /route/embed/embedding            -> Generate embeddings
GET  /route/qdrant/collections         -> List Qdrant collections
```

### Container sharing:
Some containers show `shared_with` in their status. This means they use another container's service instead of running their own (saves GPU memory).

---
