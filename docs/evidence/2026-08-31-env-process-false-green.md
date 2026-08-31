# RETRACTED: there was no false green. `env-processes` reports the orphans correctly

FROZEN 2026-08-31T19:47:49.702591+00:00. Read-only. Nothing was killed, reaped or restarted.

Ordered by comms-senior-dev ahead of Row 1 on my report that the check read OK while eleven
orphans ran. **That report was wrong.** The `OK` came from a doctor run taken while aify-env had
ZERO processes -- a correct result at that moment -- and I carried it forward as a statement about
now. Re-run against this frozen population the check reports:

    env-processes: ok=False  code=unaccounted
    11 process(es) aify-env is running have NO live terminal: ...-p9 (pid 239888, apg-pilot-07),
    ...-p12 (pid 37196, apg-pilot-07), and nine more

It names every one, with pid and label, and surfaces the DUPLICATE custody of apg-pilot-07 without
being asked. The instrument is sound; the reading was stale. Recorded here rather than deleted,
because a retraction that leaves no trace teaches nothing.

**What remains real** is the condition the check is reporting: eleven live Claude Code processes for
agents the control plane has intentionally removed, one agent holding two of them, none of the ten
having ever bound a session handle. The check's own fix text says it plainly -- "nothing will reap"
them. That is a defect in the SYSTEM, not in the instrument, and it is the thing to fix.

## Populations, as read in one pass

- control-plane agents: **48**
- live terminals: **5**
- processes aify-env owns: **16**

### aify-env processes

| id | pid | label | service | startedAtMs |
|---|---|---|---|---|
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p1 | 60552 | sc-coder-2 | aify-comms | 1788200220959 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p2 | 32456 | apg-pilot-01 | aify-comms | 1788200413680 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p3 | 128564 | apg-pilot-02 | aify-comms | 1788200545544 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p4 | 218884 | apg-pilot-03 | aify-comms | 1788200551010 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p5 | 102616 | apg-pilot-09 | aify-comms | 1788200559992 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p6 | 262020 | apg-pilot-04 | aify-comms | 1788200712879 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p7 | 259760 | apg-pilot-05 | aify-comms | 1788200816335 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p8 | 215328 | apg-pilot-06 | aify-comms | 1788200828029 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p9 | 239888 | apg-pilot-07 | aify-comms | 1788200834127 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p10 | 19772 | apg-pilot-08 | aify-comms | 1788200849677 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p11 | 223884 | apg-pilot-10 | aify-comms | 1788200853282 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p12 | 37196 | apg-pilot-07 | aify-comms | 1788201032449 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p13 | 153788 | sc-claude | aify-comms | 1788201137686 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p14 | 187796 | sc-designer | aify-comms | 1788201155156 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p15 | 60260 | graph-senior-dev | aify-comms | 1788201513892 |
| 0e12251c-f0e0-43ed-b523-c302f5afbb0b-p16 | 47636 | comms-senior-dev | aify-comms | 1788203681082 |

### live terminals

| id | agentId | processId | status |
|---|---|---|---|
| term_1788203679963_b5cec1fb | comms-senior-dev | 47636 | attached |
| term_1788201512788_de9c155e | graph-senior-dev | 60260 | attached |
| term_1788200220590_6dea073a | sc-coder-2 | 60552 | attached |
| term_1788201154797_31842ce9 | sc-designer | 187796 | attached |
| term_1788201136797_56b8492f | sc-claude | 153788 | attached |

### does the control plane still know each labelled agent?

| label | in agent listing |
|---|---|
| apg-pilot-01 | **NO** |
| apg-pilot-02 | **NO** |
| apg-pilot-03 | **NO** |
| apg-pilot-04 | **NO** |
| apg-pilot-05 | **NO** |
| apg-pilot-06 | **NO** |
| apg-pilot-07 | **NO** |
| apg-pilot-08 | **NO** |
| apg-pilot-09 | **NO** |
| apg-pilot-10 | **NO** |
| comms-senior-dev | yes |
| graph-senior-dev | yes |
| sc-claude | yes |
| sc-coder-2 | yes |
| sc-designer | yes |
