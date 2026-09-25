# Skills

The repo ships three skills, each in two byte-identical copies (`.claude/skills/` for Claude Code,
`.agents/skills/` for Codex and Hermes; `test_skill_mirror_parity.py` holds them equal):

| skill | loads when | holds |
|---|---|---|
| `aify-comms` | `comms_*` tools are available | the messaging contract, the evidence ladder, sending and replying, the tool map. Slower detail sits in `references/`: `operations.md` (install, runtime policy, ownership, the status table), `teamwork.md` (message and contract mechanics), `leading-a-team.md` (assigning work), `building-software.md` (splitting implementation lanes) |
| `aify-comms-debug` | dispatch, wake mode, status or a console is broken | the failure catalogue, by symptom, in `references/` |
| `aify-comms-install` | installing, updating or connecting a host | the install flow and what to check afterwards |

The main `SKILL.md` stays small because it loads into every agent's context on every turn; anything
only some runs need goes behind a pointer. How to write and size a skill, including the size ratchet,
is in CLAUDE.md, "Writing a skill".

## Where they install

`install.sh` copies the trees out; editing them in the checkout changes nothing for the fleet until it
is re-run (the doctor's `skills-installed` row catches that):

| client | from | to |
|---|---|---|
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Codex | `.agents/skills/` | `${CODEX_HOME:-~/.codex}/skills/` |
| Hermes | `.agents/skills/` | `<hermes-home>/skills/autonomous-ai-agents/` |

```bash
bash install.sh --client claude http://192.0.2.10:8800 --with-hook
```

After an install, relaunch the `*-aify` agents that should see the new text; a running agent keeps the
skills it loaded at start.

## Naming

The skills use the `aify-comms` name because it is the product, the MCP server identity and the tool
namespace. Add a separate skill only for a clearly distinct trigger: every skill's description sits in
context and can over-trigger. Prefer a reference under `aify-comms` until a workflow proves it needs
its own skill.
