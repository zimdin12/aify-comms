# v0.6 Phase 8 — aify-comms delegating spawns to aify-env (record)

**Finished work, reduced to this banner.** Phase 8 moved spawning out of aify-comms: first the
execution of a spawn, then (2026-09-03, proven on real hardware with no bridge running) the claiming
of it. v0.6.1 made the `aify-comms` command a verifier that starts nothing, and v0.6.3 deleted the
environment-bridge code this document traced (the spawn, environment-control, terminal-control and
managed-environment-sync loops, the managed-teardown sweeps, the terminal manager and
`mcp/stdio/env-client.mjs`). aify-env now owns processes, PTYs, spawn claiming and console streaming;
look for that behaviour there.

The one contract from this work still in force is that a launch travels as structured `argv` beside
the command string, because aify-env runs a launcher file by path and never a shell string. It is
recorded in [DECISIONS.md](../DECISIONS.md).

**The full text** (the flag design, the delegation seam, and the three defects the first real spawn
exposed) is in git at tag `v0.6.22`:

```bash
git show v0.6.22:docs/PHASE8_STATUS.md
```
