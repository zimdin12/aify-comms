# Consented fresh-install qualification

2026-11-13. PROVEN on isolated Linux staging: E1 exits 0 with 26 of 26 checks passed. Both installers run once in separate fresh homes and npm prefixes, with a fresh Codex PATH. The driver answers only the displayed tuple approval and policy-only preparation prompts. No approval digest, managed policy or Claude trust entry is inserted beforehand.

## Exact inputs and reproduction

The service and installed bridge are comms `05b7f2dca4096f3c45341b11d5533d93a7e85900`. Env is `2a2a702cdaa3735a748ba2c4aa38bc3ffd00490a`; wrapper is `c09fd03cb17d076acd8cd9c20ad5517efbea09d0`. The executing staging code was uncommitted at run time. `input-correspondence.json` compares every captured repository input with the staged Git blob that accompanies this record. This is a precommit run with checked byte correspondence, not a run of the resulting commit.

```bash
bash scripts/staging-e2e.sh E1 \
  --refs docs/evidence/issue49-e1-consent/refs.json \
  --out <new evidence directory> \
  --env-repo <checkout containing the exact env refs> \
  --wrapper-repo <checkout containing the exact wrapper refs>
```

Git Bash on Windows must be invoked explicitly, not through a bare `bash` that resolves to WSL. Git, Docker/Compose and at least 40 GiB free are prerequisites. The harness refuses labelled staging leftovers before building. Runtime versions remain Node 22.20.0, Codex 0.166.0 and Claude Code 2.1.316.

The existing local acquisition overrides select the staged sources. Restored canonical Git origins let the real stamp bind the bridge manifest and tuple. This qualifies local composition, not fetching the selected repositories from GitHub.

## Observed outcomes

- Noninteractive, absent-consent installation exits 78, prints one complete digest-bound approval command, exposes no key, and publishes no service registry.
- Claude installation exits 0 naturally after the actual tuple and policy-only prompts, with `chain: complete` and two answers out of two expected.
- Codex installation exits 0 naturally from its independent home and prefix, with `chain: complete` and one tuple answer out of one expected. Its actual PATH excludes Claude's install prefix.
- The sudoers entry permits only the owner's exact `prepare-policy` invocation. An unrelated sudo command is refused. Plugin installation stays with the non-root user.
- The candidate host runs, publishes definitions and starts a managed worker using the existing no-provider Codex runtime double. That is a real host/launcher/process observation, not a native provider or model qualification.
- All 16 applicable comms doctor rows and all 19 applicable aify-env doctor rows pass. The raw env strict doctor exit is 1 because the excluded `claude-login` row reports first-run setup incomplete. It is not a full doctor green. The unchanged exclusions are `usage-openai`, `claude-login` and `context-window`.
- Authentication, exact health exemptions, gateway Host admission, attacker Host refusal, both launcher checks, installed shell syntax and missing-option refusal pass.

The doctor-time config diagnostic records key names only. It observes ten names and no `projects` key. Env correctly reports no trusted roots and the existing folder-trust policy answering the dialog. No credential, identifier value or full Claude configuration was captured.

All ten run-owned resources were removed. Independent inspection found each absent, with a known-present Node image and a never-created container control in the same read-back. All four staging-label listings were empty. All 26 captured input hashes remained unchanged, and Docker commands were settled.

## Failures retained, not relabelled

`earlier-runs/first/` retains the first amended attempt. It exited 1 with 11 of 14 checks passed, then the definition CLI exited 65 before doctors. Missing snapshot origins made the bridge artifact pending, so no digest prompt appeared. Claude printed completion but its PTY remained open until the alarm; no holder was captured. Codex inherited Claude's PATH and refused the foreign-prefix env installation. Subsequent source fixes and the env owner's separate PTY witness do not identify that historical holder.

`earlier-runs/trust/` retains the next attempt at env `68dcee4`. It exited 1 with 25 of 26 checks passed. Both installs completed naturally, but the env trust-config row was unanswered. That run captured no raw configuration; its missing-projects explanation remained an assumption. Only the final run observes the key names. Each run has its own refs and cleanup read-back.

The initial independent reader also failed once on Windows's default text decoding, then on a case-sensitive missing-volume error assertion. The corrected UTF-8, case-normalized read-back passed without changing any resource. Those probe errors did not change a product result. The initial staged whitespace check refused captured JSON CRLF endings; local evidence attributes now preserve those original bytes and recognize CR at JSON line endings. Whitespace diagnostics are disabled only for raw text receipts because Node TAP also emits trailing spaces and blank assertion-diff lines. Product-source whitespace checks are unchanged. No captured payload was normalized to obtain a green check.

## Focused tests and limits

PASSES IN TESTS on the final source: 13 staging Node files, 200 tests passed with no failures, skips or cancellations; real WSL PTY tests, 4 passed; governing Python checks, 40 passed plus 2 subtests. Each command exited 0. The real-stamp and definition-environment regressions first failed at their intended assertions. A copied missing-prompt mutant failed its refusal assertion. The timeout capture test observes the actual child PID, owner and TTY. A copied value-emission mutant failed the names-only assertion while its missing and malformed controls passed.

Product size is 106 changed staging lines plus 14 already published for this issue, 120 cumulative against the approved 120-line cap. The largest modified product source is 260 lines. Tests and this evidence are excluded from that product count.

This is not a live deployment, installed-host rollout, provider login, full-suite replay, GitHub retrieval qualification, dashboard networking qualification or a release. The previously accepted full bridge suite remains red under the outer seal; its parent/environment controls and explicit publication decision are recorded separately in the wrapper-pin evidence. No issue was closed or public proof comment posted by this work.

`native-run.txt`, `refs.json`, `result.json`, doctor reports, installer outputs, config key names and independent cleanup are retained here. Full Docker/Git command receipts and the captured source capsules remain in the persistent project evidence record under `comms-evidence-fixes-20261106/e1-consented-20261113-3/`, beside both earlier attempts.
