# 0.8.6 resubmission packet, successor 2 (2026-10-05)

Answers comms-senior-dev's REVISE of wrapper 8668655 (1791181679798-39ee4dea). PACKET.md stays as the record of
the previous submission; this file covers only what changed.

## Identities

| repo | base | candidate (commit / tree) |
|---|---|---|
| aify-wrapper | 237c0c553ab6cb29ab13a5010c40fe5df83bbd17 | 541b2ffe2e99c937ea8e57e5d607ef01ca48737a / 7520c9524aa657ce0f133354a41184c9fb35614c (parent 8668655) |
| aify-comms | 23a77ee4cd542d7cf1d2b141ba78d618c6b46bf1 | code: 15df9d60a37dba01ba64b615bb9ccf5dfd6e44f2; the candidate is its child that adds this file and receipts, and only files under `docs/superpowers/plans/evidence/2026-10-05-086/` |

Parent-relative since the last submission:
- wrapper 541b2ff: M wrappers/hermes-aify.sh.in, tests/the-gateway-token-is-never-printed.test.js,
  docs/evidence/2026-10-05-env-detach/detach-mutants.{mjs,out}.
- comms cb067f13: M service/reconcilers/spawn_lifecycle.py (docstring only). 15df9d60: M mcp/stdio/package.json,
  mcp/stdio/package-lock.json (pin 541b2ff).

## T4: the gateway host's answer is never printed

Redaction keyed on the token's closing quote, so a truncated answer printed the token whole. The public lines now
show none of the answer: success prints `managed gateway host ready on port <digits>` (the port only when it is
1-5 plain digits, else `unknown`), and failure prints `could not parse wsUrl from the gateway host's answer (<N>
bytes; not shown, it carries the gateway token)`. The answer itself is unchanged; the TUI still receives the
URL with its token (asserted).

A rendered truncated-answer test (`{"port":9999,"token":"<sentinel>`) joins the two token tests and asserts no
8-character prefix of the sentinel is printed. On 8668655's template it fails, as your T4 found; on 541b2ff the
three pass. Mutants T1 (success line prints the answer), T2 (failure line prints it) and T4 (the failure line
restored to 8668655's closing-quote redaction) are killed; 27 in all, sources sha256-identical after
(detach-mutants.out at 541b2ff).

## Docstring

spawn_lifecycle.py's safety list no longer says a live claimer's spawn is NEVER failed: it states the wall
ceiling (`active_managed_run_wall_ceiling_minutes`, default 30) for `starting` and `running`, and that `claimed`
gets no shelter.

## Suites (receipts/, gzip; every run kept)

- wrapper 541b2ff, full: wrapper-086h.log, 723 tests, 647 pass, 10 fail, 66 skipped. All 10 are render or launch
  subprocesses (`render failed:` with empty output, `null !== 0`) in 9 files (failing-086h.txt). Those 9 files,
  sequentially on the same tree: wrapper-086h-alone.log, 62/62 in 935 s. Non-reproduction only; no cause is
  claimed.
- comms 15df9d60 (comms086f-head.txt): comms086f-py.log 5112 passed / 27 skipped; comms086f-bridge.log 371 file
  suites passed, 2 tests skipped in 2 files; comms086f-dash.log 1894/1894.
- aify-env 66c9968 with AIFY_WRAPPER_REPO at 541b2ff: env086f-npm.log 2467 pass, 0 fail, 0 cancelled, 4 skipped.
- Nothing is claimed about runs on the packet commit itself: it adds evidence files only, which `git diff
  --name-only 15df9d60 <candidate>` shows.

Method limits kept beside the incident labels in PACKET.md: the hm-3/4 "no key" finding counts the key's bytes in
process memory against controls; the cc-4 finding reads the current spawn record; the definitions finding reads
the store's ledger. Transition and lookup causes stay UNATTRIBUTED.
