# The operator's rulings on stop and on the dead aify-env keys (2026-10-22)

`ask-user-question-records.json` is extracted from the session transcript
(`~/.claude/projects/C--Docker-aify-comms/651b895f-a564-4d3a-8e0b-27f8429b1dd0.jsonl`). It holds the two
AskUserQuestion calls and their answers, each with its transcript line number and the sha256 of that whole line, so the
extract can be checked against the transcript.

- **Stop.** Asked 2026-10-21T19:17:16Z, answered 2026-10-22T13:03:22Z: "Yes, failed + inbox (Recommended)". The
  question carried the full restatement: marked FAILED, not delivered after a restart, kept in the agent's inbox, and
  a stored notice for its sender. Amendment A9 implements it.
- **Dead keys.** The first answer (13:03:22Z) was a question back: "if it never happens again then no command is
  needed. if it is going to be common then we need a solution. what do you think ?". Asked again at
  2026-10-22T13:11:05Z, answered at 13:40:58Z: "Cleanup now, auto-rule after (Recommended)". That covers a one-time
  cleanup of the 10 dead keys (backed up first), with an automatic "a key that stopped reporting yields to a live one"
  rule after the switch.

- **No forced aify-env restart for the switch.** Asked 2026-10-22T13:57:06Z (line 642399), answered 13:58:00Z (line
  642407): "Wait for my next restart (Recommended)". SW5 deploys only after the operator's next ordinary restart, once
  the live keys announce `reportEveryMs`. "Restart once at switch time" and "Assume a 60 s default" were offered and
  not chosen.

The operator's first words on the first two reached this session relayed by dashboard-manager. The rulings themselves are the
answers above, given in this session.
