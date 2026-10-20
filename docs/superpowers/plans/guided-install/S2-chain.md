# S2: the phased chain, entry-point handoff, assets-only application

Part of [the guided-install plan](../2026-10-20-guided-install.md). Answers R3's ordering half.

## Where the chain lives

The chain runs in aify-env's installer:

```
install.sh --connect <service>=<url> ... --client <runtime> ... [--no-prompt]
```

It runs the index's five phases in order:
1. **Acquire** is idempotent:
   - aify-env and aify-wrapper with `npm install -g`, git form;
   - each selected service's approved artifacts, through that service's installer in `--acquire-only` mode (comms:
     today's bridge copy).
2. **Connect** each service (S1).
3. **Render** once.
4. **Apply assets** through each service's installer in `--assets-only --client <runtime>` mode, which writes no
   registry state and never hands off.
5. **Report.**

## Handoff without recursion

- comms' and the dashboard's installers, run on a host where aify-env is missing or older than the chain, install
  aify-env (phase 1 only) and then exec its chain with their own `--connect`.
- The chain calls the service installers only in `--acquire-only` and `--assets-only` mode. Those two modes never
  hand off, so the chain is entered once.
- A service installer run with neither mode and with the chain present hands off. Its old full path stays for
  hosts that choose it, until S4.

## Inputs, never invented

- Selected clients come from flags or the guide's questions. PATH is inventory.
- A selected client whose CLI is absent is reported **pending**; its assets are skipped. Under `--no-prompt` the run
  exits non-zero, naming it.
- Profile roots (hermes) come from the existing resolver. Operator config and existing trust are kept.
- Secrets come on stdin or a named file, never in argv. A required secret or approval that is missing fails under
  `--no-prompt`.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| render after connect | in a node+git container with nothing preseeded (no bridge files, no credentials, no runtime CLI), `install.sh --connect aify-comms=<fixture> --client claude --no-prompt` acquires, connects and renders, then exits non-zero naming `claude: pending (CLI absent)` | launchers render before connect (`aify-wrapper-check` reports the fingerprint stale), or the run exits 0 |
| no recursion | running comms' installer as the entry point enters the chain once (counted) and assets-only writes no registry bytes | the chain is entered twice, or the registry hash changes in phase 4 |
| PATH is not selection | `codex` on PATH but not selected: no codex assets are applied | they are |
