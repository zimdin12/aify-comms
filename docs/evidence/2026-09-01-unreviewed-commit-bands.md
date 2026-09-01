# The unreviewed objects, named — 2026-09-01

The dev's v0.6 verdict holds the tag until the unreviewed commits are reviewed in four bands,
**or** an operator accepts the risk in a statement that NAMES the objects being accepted. This
file is that list. It exists so the second option is possible at all: "accept 111 commits" is
not a risk acceptance, it is a wish.

## The count has grown, which is the finding

The roadmap recorded **65** unreviewed commits (48 before the cutover band, plus its 17).
Measured today against the same base object `b1eda9a1`, the range is **111**.

The gap is widening faster than review closes it. That is not an argument for reviewing harder;
it is an argument that the tag decision cannot wait for the backlog to be emptied, because the
backlog is a moving target. Either a band closes, or its objects are named and accepted.

## Bands

A commit lands in the FIRST band whose paths it touches, so the riskiest classification wins
rather than the last one checked. Counts are commits and file-touches.

| band | commits | file-touches |
|---|---:|---:|
| A. Ownership, reconcile, status safety | 18 | 132 |
| B. SQL, schema, listing, performance gates | 14 | 75 |
| C. Dashboard controls and payload boundaries | 48 | 245 |
| D. Docs, config, doctor, skills closure | 19 | 28 |
| E. Unclassified by the four bands | 12 | 24 |
| **total** | **111** | |

Two objects in this range already carry an explicit verdict and are marked below.
Everything else is unreviewed source, whatever its deployment history:
*deployment evidence does not retroactively supply source review.*

## A. Ownership, reconcile, status safety

| sha | date | subject |
|---|---|---|
| `f4c60cc2` | 2026-08-29 | fix(ownership): the environment bridge can take its own agents back |
| `09fc4f55` | 2026-08-29 | fix(reconcile): the one repair step that healed without ever saying so |
| `d1fcaac9` | 2026-08-29 | fix(reconcile): 53 log lines that reported nothing while looking like they did |
| `43e74666` | 2026-08-29 | fix(sessions): one owner for the ended-session statuses, and nine SQL strings stop guessing |
| `6647bcdb` | 2026-08-29 | refactor(terminal-status): sixteen hand-typed live-terminal filters become two named fragments |
| `df5ae18e` | 2026-08-29 | fix(terminals): the event page says when it is a page, and the cap has one owner |
| `0d0c94d7` | 2026-08-29 | fix(terminal-history): output chatter no longer evicts the lifecycle trail |
| `a74f6620` | 2026-08-29 | fix(status): a comment named the flag that closes a hole, and it was the wrong flag |
| `02045701` | 2026-08-29 | fix(environments): an advertisement disarmed the guard that keeps two bridges apart |
| `83bca59e` | 2026-08-30 | fix(environments): the id is built once, in the tier whose table it keys |
| `50a61dbe` | 2026-08-30 | fix(environments): a heartbeat no longer blanks what it did not mention |
| `badab14c` | 2026-08-30 | fix(environments): a heartbeat erased four more fields it never mentioned |
| `2c6698d7` | 2026-08-30 | feat(environments): the cutover — exactly one tier describes a host |
| `0ca5db8f` | 2026-08-30 | fix(environments): two regressions the cutover caused, both found by running it |
| `5a1c8b64` | 2026-08-30 | fix(v0.6): the anti-strand ceiling measured the wrong clock, so a beating latch never aged out |
| `23d1b2da` | 2026-08-31 | fix(v0.6): a prefix said WHICH keys are bridge-owned and never who may write them |
| `91b41f60` | 2026-08-31 | fix(v0.6): two client-supplied names agreeing is not evidence, and GET is not safe here |
| `b66a5268` | 2026-08-31 | fix(v0.6): the rebinding fix guarded one branch of three, so omitting a header walked around it |

## B. SQL, schema, listing, performance gates

| sha | date | subject |
|---|---|---|
| `3b16482e` | 2026-08-29 | perf(status): batch the turn-state read, and put a ratchet under the number |
| `be53a024` | 2026-08-29 | fix(cleanup): an orphan is a REMOVED agent, not an identity that was never one |
| `faebb118` | 2026-08-29 | perf(runs): the default listing walks an index instead of sorting 21,778 rows |
| `8d7968c5` | 2026-08-29 | fix(sessions): the resident->managed switch could never infer an environment, for 78 days |
| `399558b7` | 2026-08-29 | fix(terminals): the thirteenth hand-typed status filter, and the one the SQL sweep could not see |
| `732db52a` | 2026-08-30 | fix(spawn): the host said it could not start the runtime, and nothing read it |
| `cb8d6713` | 2026-08-30 | fix(dispatch): the cold-start refusal now carries the host's own reason |
| `6381d23d` | 2026-08-30 | fix(v0.6): only a VERIFIABLE renewal may extend a turn, and an acceptance is not a URL |
| `cf00b991` | 2026-08-31 | fix(v0.6): two guards asked one question and the weaker one was the front door |
| `5b764123` | 2026-08-31 | fix(v0.6): "a browser cannot omit both headers" was my claim, and it is false |
| `0d24f122` | 2026-08-31 | fix(v0.6): the status clamp aged against a clock the latch keeps winding |
| `3bb4b546` | 2026-08-31 | fix(v0.6): my tests recomputed the clamp, so they could not see the query that forgot the column |
| `402d0c37` | 2026-08-31 | fix(v0.6): sharing a policy function is not sharing its answer |
| `01c84994` | 2026-08-31 | fix(v0.6): the same function and the same verdict, on two different clocks |

## C. Dashboard controls and payload boundaries

| sha | date | subject |
|---|---|---|
| `d4938c13` | 2026-08-29 | fix(chat): the note now loads on the healthy path, where it never did |
| `301fae62` | 2026-08-29 | feat(doctor): an open fleet listing that hands out live gateway tokens |
| `1d4283db` | 2026-08-29 | fix(dashboard): the badge that means "this failed" looked like the one that means "hello" |
| `d65feb64` | 2026-08-29 | fix(dashboard): the agent drawer says how long since the agent was last heard from |
| `edc26d54` | 2026-08-29 | fix(bridge): the deliverability classifier stops reading two fields that cannot arrive |
| `198e4ce3` | 2026-08-29 | fix(dashboard): two display sentinels were being read as data, and one was posted to the server |
| `e87581a5` | 2026-08-29 | fix(dashboard): the payload gate could not see two thirds of the reads it claimed to cover |
| `194d7b23` | 2026-08-29 | fix(bridge): two branches gated on a mechanism the service retired in May |
| `60d6cf3b` | 2026-08-29 | fix(dashboard): the spawn panel promised the operator a state that cannot exist |
| `6e590bee` | 2026-08-29 | fix(gates): one fact, four copies, three of them wrong by 1,321 lines |
| `20c04b62` | 2026-08-29 | fix(terminals): a re-attached delegated console repeats its output and said nothing about it |
| `93106331` | 2026-08-29 | fix(doctor): the first of three remedies had no cost stated, and it is the expensive one |
| `d14e164a` | 2026-08-29 | fix(doctor): the delegation remedy named the trap instead of the switch |
| `2686aab2` | 2026-08-29 | fix(dashboard): the red confirm button was on the two safest destructive controls |
| `c286498a` | 2026-08-29 | fix(api): two counts that were not counting the rows their responses named |
| `30f8a9cf` | 2026-08-29 | refactor(doctor): the file two lines from the gate, and the table that said it was nine |
| `12c19568` | 2026-08-29 | fix(docs): four agent-facing texts promised an opt-out the Work Loop does not honour |
| `492580a2` | 2026-08-30 | fix(runtimes): two of five advertised themselves launchable without probing a file |
| `c4a8f60f` | 2026-08-30 | test(identity): one host, one machine id, proven by running both builders |
| `b3c02bf1` | 2026-08-30 | fix(bridge): one list of the env names that carry the service key |
| `655c65b3` | 2026-08-30 | fix(install): setting an API key would have 401'd the entire fleet |
| `d6c3646a` | 2026-08-30 | fix(security): a page you visit could drive the whole fleet, and still can't |
| `abd5d554` | 2026-08-30 | test(identity): the two tiers agree on `kind` too, which is what the id is built from |
| `6c11c312` | 2026-08-30 | fix(deps): bump the aify-wrapper pin, so the keyEnv reader is actually here |
| `b445388e` | 2026-08-30 | docs(install): point an agent at the repo and it can install this |
| `54054d6a` | 2026-08-30 | fix(install): install every skill in the tree, and stop arguing against the key |
| `ec67d24b` | 2026-08-30 | docs(plan): rows 5 and 6 measured, with the instrument controlled |
| `9b920070` | 2026-08-30 | docs(plan): row 7 researched -- what Anthropic's cross-session messaging offers us |
| `9a2cfdca` | 2026-08-30 | fix(v0.6): the key never reached hermes or the bridge, and two verbs were armed on one transport only |
| `6442faf5` | 2026-08-30 | fix(v0.6): the bridge stood down on a flag that meant "aify-env has a target", not "we were told" |
| `0534c17e` | 2026-08-31 | feat(v0.6): the key this service uses now reaches the tier that needs it |
| `1de5703e` | 2026-08-31 | fix(v0.6): an ordinary reinstall would have deleted the credential reference |
| `54e76284` | 2026-08-31 | chore(v0.6): bump the aify-wrapper pin, and PROVE the bump took |
| `60d6aa61` | 2026-08-31 | feat(v0.6.1): "last seen 4m ago" stops being a number frozen at render |
| `ea18156b` | 2026-08-31 | fix(v0.6.1): "start clean" never once started clean for a hermes agent |
| `c8aa5090` | 2026-08-31 | feat(doctor): the agents were unreachable in the one way nothing measured |
| `3d6070e6` | 2026-08-31 | fix(install): every hermes install since yesterday registered no MCP server |
| `e755e43d` | 2026-08-31 | test(hermes): the adapter tests were writing markers into the operator's live TEMP |
| `71bc5fa5` | 2026-08-31 | feat(doctor): one conversation, two agents, and nothing that could say so |
| `a4011365` | 2026-08-31 | feat(doctor): what is holding hermes' files, and an honest "I do not know why" |
| `ce5e137d` | 2026-08-31 | fix(doctor): two checks built to refuse false greens were producing them |
| `7b29f2d8` | 2026-09-01 | feat(env): name the processes whose agent was removed, and refuse to claim more than that |
| `fc15f75b` | 2026-09-01 | fix(env): four claims the report made that its own code did not support |
| `34c3f1a1` | 2026-09-01 | fix(env): the malformed-row bucket only caught primitives, so junk was classified as processes |
| `14739a02` | 2026-09-01 | fix(terminal): the keepalive tests slept and counted timer fires, so 2 runs in 6 failed **[APPROVED 2026-09-01 (keepalive flake fix)]** |
| `3b7eb8e6` | 2026-09-01 | fix(env): a fractional pid was a process, and an unreadable listing was a clean fleet |
| `a1af7b4d` | 2026-09-01 | fix(env): a default parameter was turning "nobody looked" into "nothing was there" |
| `f7e76404` | 2026-09-01 | fix(env): the decision guard checked a JavaScript kind and called it a contract **[APPROVED 2026-09-01 (report-only decision contract)]** |

## D. Docs, config, doctor, skills closure

| sha | date | subject |
|---|---|---|
| `928edbe4` | 2026-08-29 | fix(gate+docs): the fixture proves itself, and revision 7 removes a seam revision 6 added |
| `8c0d5116` | 2026-08-29 | docs: re-measure the size table, which was one day stale and I was about to quote it |
| `193e57bb` | 2026-08-29 | docs: re-measure every count in CLAUDE.md, both copies, from one run |
| `fc2d1f98` | 2026-08-29 | docs: two of the nineteen decisions that "need you" were already done |
| `cf5cae62` | 2026-08-29 | docs: the closing re-measure, and one more copy of the reply-flag rule |
| `6035d5a3` | 2026-08-30 | test: gate the heartbeat's cost and the install skill's flags |
| `cb7614f2` | 2026-08-30 | docs: v0.6 verified on the deployed system, with the counts re-measured |
| `04aa445e` | 2026-08-30 | docs: the v0.6.1 scope, written down before it is worked |
| `f34e8399` | 2026-08-30 | docs(plan): the security audit, and the claim it corrects |
| `5f4de51d` | 2026-08-30 | docs(plan): row 3 triaged by executing the registrar, not grepping it |
| `ffdc9c02` | 2026-08-30 | docs(plan): rows 1+2 measured, and hermes' dashboard actually read |
| `0dbb02b4` | 2026-08-31 | fix(v0.6): the installer read the FIRST key in .env and Compose reads the LAST |
| `11787197` | 2026-08-31 | fix(v0.6): I fixed a silent-failure bug by writing a data-loss bug into the fix |
| `3422388d` | 2026-08-31 | docs(v0.6): the launcher credential is closed, and nothing was failing |
| `d1dd9610` | 2026-08-31 | docs(v0.6): credential carrier progress — the two aify-env pieces that are in |
| `2c600062` | 2026-08-31 | docs: repair a line a heredoc broke across two of them |
| `6d89649e` | 2026-08-31 | docs(v0.6): the credential carrier is complete, both tiers |
| `02b9669c` | 2026-08-31 | docs(evidence): the frozen populations, and the false green that was not one |
| `1d0d2669` | 2026-09-01 | docs: the layout table was stale by three numbers at once, which it predicts |

## E. Unclassified by the four bands

| sha | date | subject |
|---|---|---|
| `4339b8ef` | 2026-08-29 | perf(stats): three unread counts in one pass instead of three |
| `7bf5d3be` | 2026-08-29 | test(sql): one owner for reading the SQL a module issues, f-strings included |
| `fb4b51a6` | 2026-08-29 | test(schema): every column a statement names is one the schema declares |
| `7b6b5c0a` | 2026-08-29 | test(schema): a declared cascade is enforced, not merely declared |
| `d4d8c7db` | 2026-08-29 | perf(messages): /messages/recent sorted 33,619 rows to return a page of 80 |
| `022e1904` | 2026-08-29 | test(skills): the tool gate checks parameters, not just names |
| `e44e3cd4` | 2026-08-29 | fix(config): three knobs an operator can set and the service never reads |
| `34c30772` | 2026-08-29 | test(rename): the gate that claims completeness was measuring a hand-typed set |
| `2210a7dd` | 2026-08-29 | fix(sse): the two transports agreed on tool names and nobody compared what the tools take |
| `1faaaa62` | 2026-08-29 | test(clock): a freeze that had never once reached the clock the code under test reads |
| `a0dadd67` | 2026-08-30 | test(environments): re-declare the split proof for the label preservation |
| `fa7f75c4` | 2026-08-31 | test(status): a one-second boundary, not a race, was reddening the anchor gate |

## What this file is not

It is not a review. Banding says where the risk lives, not whether any of it is sound. Band A
carries the ownership and status-safety changes and is where a defect costs the most, which is
why the dev ranked it first; band C is the largest by count and the widest by file-touches.

It is also not stable. Every commit pushed after this file was written extends the range it
describes, so the counts above are a reading, not a property. Re-measure before quoting them.
