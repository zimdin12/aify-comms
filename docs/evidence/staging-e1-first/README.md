# E1 fresh-install measurement

E1 is deliberately red. It measures the current install, not a repaired or pre-approved one. The scenario and its
three wiring changes are byte-identical to the first attempt. The rerun uses the subsequently published disk-space
and leftover-resource guards.

## Reproduce

From a comms checkout with Git Bash, Docker/Compose and sibling env/wrapper checkouts containing the refs:

```bash
bash scripts/staging-e2e.sh E1 --refs docs/evidence/staging-e1-first/refs.json --out <new directory>
```

Candidate refs: comms `4bb0041d`, env `45e76f77`, wrapper `084e606`; full SHAs are in `refs.json`.
The run owns its container home, network, volumes and images. No host credential, model call or live install is used.
The two documented source overrides, `AIFY_INSTALL_ENV_SOURCE` and `AIFY_INSTALL_WRAPPER_SOURCE`, point at the staged
candidate checkouts. This proves the composed chain from those sources, not fetching them from GitHub. The work-PC
install must qualify the fetch. There is no digest pre-approval, channel-policy preparation or Claude trust fixture.

## Observed result

`resumed-run.txt` is the unedited outer output. The command exited 1 in 317 seconds, with 18 recorded checks:
15 passed and three failed. The three failures are:

- `fresh_install_claude_in_one_run (comms#49)`: exit 78. Claude assets were installed, but the managed channel policy
  was absent. The installer printed one policy-only `sudo ... prepare-policy --path /etc/claude-code/managed-settings.json`
  command and asked for a rerun as the same non-root user. Contrary to the forecast, this was not an approval-digest
  refusal. Neither the printed command nor a second Claude install was run.
- `comms_doctor_applicable`: three failed rows, `agent-state-publishers`, `spawn-delegation`, `env-bridge`, and one
  skipped row, `bridge-current`. No aify-env daemon or agent had been started in this fresh home.
- `env_doctor_applicable`: `environment` failed and 13 rows were unanswered because no daemon/bridge answered.
  `claude-trust-roots` was `unknown-roots`, not evidence of a missing trust-map defect. The scenario leaves these red.

Codex's install followed the partial Claude install in the same home and exited 0. It is not an independent
empty-home Codex installation. The subsequent `aify-env install.sh --no-prompt` exited 0 and reported a usable
credential. Both installed launchers printed their wrapper-selected permissions and exited 0 under `--check`.

| Check | Observation |
|---|---|
| Capacity guard | 132,790,456,320 bytes free, no labelled leftovers; passed before image builds |
| comms#43 | `/health` without key 200; `/openapi.json` without key 401 and with key 200; `/healthz-admin` without key 401 |
| comms#27, Host admission only | authenticated `Host: host.docker.internal:8800` 200; lookalike attacker Host 403 |
| wrapper#9, installed syntax only | `claude-aify` and `codex-aify` pass `bash -n` |
| wrapper#18, one missing-value case | wrapper installer given `--endpoint` with no value exits 78 and names the option |

The syntax loop also attempted `bash -n` on `herdr-aify`, a JavaScript command, and printed a syntax error. This is a
probe mistake, not a product defect. Its aggregate check requires two successful parses and passed for the two
requested launchers; it does not prove every command matched by `*-aify` parses as shell. The unchanged rerun keeps
that diagnostic. `--check` output and exit status alone are not an OS witness that nothing was started.

NOT_RUN: the same provider/context rows approved for E4 (`usage-openai`, `claude-login`, `context-window`), the GitHub
fetch and the dashboard-container half of comms#27. Host admission is not Docker host-gateway DNS qualification.
The run does not exercise every acceptance criterion of the referenced issues.

## Cleanup and retained evidence

The run ended with all ten owned resources removed and read back absent. An independent readback repeated exact
inspection of all ten names, with a known-present Node image and an absent container control. All ten were absent;
all captured harness input hashes still matched. `result-summary.json` retains the checks, cleanup and independent
readback. Full per-command receipts, installers and doctor reports remain in the planning record under
`e1/e1-resumed-20261113-1/`.

The earlier `first-run.txt`, with its original `refs-first.json`, is retained too. It exited 1 after 549 seconds
before any E1 step: the Docker disk filled during the host-image build. That is infrastructure failure, not a
fresh-install result. Its cleanup completed. No additional native rerun followed the resumed measurement.
