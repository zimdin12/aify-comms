# E3 first run

E3 starts one managed Codex agent through comms and aify-env with the real codex-cli 0.166.0 (no runtime double, no
login, no model call) on comms 6956c49e, env 45e76f77, wrapper 084e606 (`refs.json`). Run it from the comms checkout
with Git Bash:

```bash
bash scripts/staging-e2e.sh E3 --refs docs/evidence/staging-e3-first/refs.json --out <new directory>
```

`run.txt` is the raw output of the first run: exit 0 after 327 s, 18 recorded checks, `PASS E3`, cleanup PASS.

| Issue | Checks |
|---|---|
| wrapper#14 | `app_server_requires_capability_token`: the launch's app-server runs with `--ws-auth capability-token` and a verifier equal to SHA-256 of the token in its environment. `unauthenticated_upgrade_refused`: no token 401, a wrong token 401. `launch_token_accepted`: the launch's token 101, and `initialize` answers. `token_in_no_argv_or_record`. `e3_stop_ended_app_server_and_launcher`: both were alive before the comms stop and absent after |
| comms allowlist | `comms_allowlist_names_both_carriers`: the installed `~/.codex/config.toml` lists both variable names in `env_vars` and holds no token |

`fixtures/codex-auth-probe.mjs` reads the token from the app-server's own `/proc` environ (same user, the exposure
aify-wrapper's contract names) and prints only facts about it, never the token.

Limits:
- The argv scan has no in-run positive control. The verifier check shows the probe read the real token.
- Nothing publishes the agent's `runtimeConfig` without a model turn (the bridge writes the runtime marker), so "not in
  the comms record" passes trivially today. Whether the record names the token variable only is NOT_RUN.
- NOT_RUN: the comms MCP child connecting with the forwarded token, and resident wake through it. Codex starts MCP
  children only on a turn; a premise probe saw none after app-server start, `thread/start` or `thread/list`. Both are
  checked on the live switch with a logged-in Codex agent.

An independent run (`staging-e3/lead-independent-run-1`) recorded the same 18 checks passing and the same two NOT_RUN
rows. It failed only `inputsUnchanged`: it ran from this checkout while uncommitted E1 edits to `main.mjs`,
`session.mjs` and `e4.mjs` landed mid-run. A clean re-run from a separate checkout (`lead-independent-run-2`) stopped
at service start ("staging service never reached internally observed health") because the Docker disk was full, which
is not an E3 result.
