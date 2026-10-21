"""Mutation arms for the gate's judging (SW3a successor). Each arm replaces exactly one source span, runs its checker
(the gate's --self-check, or for the collector probe.py its own test) and the review replay
(../review-replay/replay.mjs), keeps their full stdout/stderr and exits and the witnesses that went FAIL, then restores
the file by byte copy and checks its sha256. An arm is KILLED only if the checker exits nonzero AND a FAIL line names
the arm's expected witness. The replay's exit is recorded beside it.

  python docs/evidence/2026-10-21-sw3a-gate/arms/run_arms.py <worktree> <out dir>"""
import hashlib, json, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
GATE = ROOT / 'scripts' / 'sandbox' / 'gate'
REPLAY = ROOT / 'docs' / 'evidence' / '2026-10-21-sw3a-gate' / 'review-replay' / 'replay.mjs'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
# (arm, file, the span replaced, its replacement, the witness that must go FAIL, the review item it guards)
ARMS = [
  ('a01', 'judges.mjs', '  if (claim.claimedAt < accepted.at) failed.push(`${label}: claimed before comms accepted the publication`);\n', '',
   'claimed a second before the acceptance, seen after it', 'ordering'),
  ('a02', 'judges.mjs', '  if (accepted.seenAt != null && claim.seenAt < accepted.seenAt) failed.push(`${label}: the watcher saw the claim first`);\n', '',
   'seen claimed first in the same second', 'ordering'),
  ('a03', 'judges.mjs', '  if (claim.claimedAt - accepted.at > FIRST_PASS_MS) failed.push(`${label}: not claimed on the first pass after acceptance`);\n', '',
   'claimed after the first pass', 'first pass'),
  ('a04', 'judges.mjs', '  if (polls(ev.standin, window.from, window.to) < MIN_POLLS) failed.push(`${label}: the stand-in was not seen polling`);\n', '',
   'no claimer polling', 'claimer control'),
  ('a05', 'judges.mjs', '  && e.runId === runId && e.t <= to);', '  && e.runId === runId);',
   'review R1: a hold, idle at 21 s, then a claim and receipt at 22 s passes', 'R1'),
  ('a06', 'judges.mjs', '  const claimed = byEnd(claim.claimedAt, window.to,', '  const claimed = byEnd(claim.claimedAt, Infinity,',
   'review R1: a hold, idle at 21 s, then a claim and receipt at 22 s passes', 'R1'),
  ('a07', 'judges.mjs', '  if (receivedBy(ev.standin, runId, window.to)) failed.push(`${label}: the stand-in received ${runId} inside the window`);\n', '',
   'received inside the hold', 'R1'),
  ('a08', 'judges.mjs', '  if (steered.includes("inside")) failed.push(', '  if (false) failed.push(',
   'steered inside the window', 'R6'),
  ('a09', 'judges.mjs', '    else claimedOnFirstPassAfter(ev.timeline, m.queueRunId, m.idleAccepted, failed, "the queueIfBusy send");',
   '    else claimedOnFirstPassAfter(ev.timeline, m.queueRunId, m.freshAccepted, failed, "the queueIfBusy send");',
   'review R2: the queueIfBusy send claimed while busy, before idle', 'R2'),
  ('a10', 'judges.mjs', '    if (!(m.freshAccepted?.at >= m.resumedAt - 1000)) failed.push("no fresh publication was accepted after the resume");\n', '',
   'no fresh publication after the resume', 'R2'),
  ('a11', 'judges.mjs', '        if (m.idleAccepted?.at == null || claim.claimedAt - m.idleAccepted.at > FIRST_PASS_MS) {', '        if (false) {',
   'the steerable send late', 'R2'),
  ('a12', 'judges.mjs', '        if (claim.claimedAt < m.freshAccepted?.at) failed.push("the steerable send drained before a fresh publication");\n', '',
   'the steerable send a second before the fresh publication, seen after it', 'R2'),
  ('a13', 'judges.mjs', '    else if (m.preRecords.length) failed.push', '    else if (false) failed.push',
   'review R3: an available row with no lifetime before the send', 'R3'),
  ('a14', 'judges.mjs', '  if (!Number.isInteger(m.announcedMs)) return failed.push("fixture: aify-env announced no interval, so no silence is derived");',
   '  if (!Number.isInteger(m.announcedMs)) return;', 'aify-env announced nothing', 'R5'),
  ('a15', 'judges.mjs', 'export const MISSED_REPORTS = 3;', 'export const MISSED_REPORTS = 1;', 'three intervals not passed', 'R5'),
  ('a16', 'judges.mjs', '  if (applied(contact).some((e) => e.t > from && e.t <= to)) failed.push("fixture: a report reached comms during the silence");\n', '',
   'a report reached comms during the silence', 'R4'),
  ('a17', 'judges.mjs', '  else if (!lastEnv || m.sentAt - lastEnv.t >= MISSED_REPORTS * envMs) failed.push("fixture: aify-env\'s holder was not in contact");\n', '',
   'aify-env out of contact', 'R5'),
  ('a18', 'judges.mjs', '  if (m.envBusyAtSend !== busy) failed.push(`fixture: aify-env\'s holder was ${busy ? "not busy" : "not idle"}`);\n', '',
   'aify-env not busy', 'R6'),
  ('a19', 'judges.mjs', '    if (run && run.steer_if_busy !== 1) failed.push("fixture: the send was not steerable");\n', '',
   'the send not steerable', 'R6'),
  ('a20', 'judges.mjs', '  if (!m.seedRunId) failed.push("fixture: no queued work was seeded before the stop");', '  if (!m.seedRunId) {}',
   'review R7: no queued work seeded', 'R7'),
  ('a21', 'judges.mjs', '    if (m.seedRunId) claimedOnFirstPassAfter(ev.timeline, m.seedRunId, m.restartAccepted, failed, "the seeded run");\n', '',
   'review R7: the named seed never drained', 'R7'),
  ('a22', 'judges.mjs', '    if (m.wordAfterFirstTurn?.status !== "online") failed.push(`after the first turn the word read ${m.wordAfterFirstTurn?.status}`);\n', '',
   'restart leaves unknown after the first turn', 'R7'),
  ('a23', 'judges.mjs', '  const note = word?.lastKnown;',
   '  const note = word?.lastKnown ?? (/last[ -]?known/i.test(String(word?.statusNote ?? "")) ? prior : null);',
   'review R8: negative prose in statusNote', 'R8'),
  ('a24', 'judges.mjs', '  if (note.status !== prior.status) failed.push(`lastKnown.status ${note.status}, not the prior ${prior.status}`);\n', '',
   'lastKnown names another word', 'R8'),
  ('a25', 'judges.mjs', '  if (ms(note.appliedAt) !== ms(prior.appliedAt)) failed.push(`lastKnown.appliedAt ${note.appliedAt}, not ${prior.appliedAt}`);\n', '',
   'lastKnown names another time', 'R8'),
  ('a26', 'cases.mjs', 'expected: "after three announced intervals of publication silence:', 'expected: "after three intervals of publication silence:',
   "env-paused: its expected answer is page 2's or its amendment's words", 'amendment words'),
  ('a27', 'cases.mjs', 'expected: "delivered at once",', 'expected: "delivered promptly",',
   "legacy-busy-env-idle: its expected answer is page 2's or its amendment's words", 'page 2 words'),
  # Review F1: a whole-second stamp against a millisecond window end.
  ('f01', 'judges.mjs', '  if (stampMs === null || stampMs > to) return "outside";', '  if (stampMs === null || stampMs + SECOND_MS > to) return "outside";',
   "claimed in the hold's last second, seen claimed before it ends", 'F1 no blanket grace'),
  ('f02', 'judges.mjs', '  if (stampMs + SECOND_MS - 1 <= to) return "inside";\n', '',
   'claimed in an earlier second, seen late, with no receipt', 'F1 bucket wholly before'),
  ('f03', 'judges.mjs', '  if (seenBy <= to) return "inside";\n', '',
   "claimed in the hold's last second, seen claimed before it ends", 'F1 seen by the end'),
  ('f04', 'judges.mjs', '  if (absentAfter !== null && absentAfter > to) return "outside";\n', '',
   "review F1: released in the hold's last second, read queued after it ends", 'F1 absent after the end'),
  ('f05', 'judges.mjs', '  return "unknown";\n}', '  return "inside";\n}',
   "a claim straddling the hold's end with no millisecond evidence", 'F1 unknown, not a breach'),
  ('f06', 'judges.mjs', '  if (claimed === "unknown") unknown.push(', '  if (false) unknown.push(',
   "a claim straddling the hold's end with no millisecond evidence", 'F1 unknown, not a pass'),
  ('f07', 'judges.mjs', '({ pass: failed.length === 0 && unknown.length === 0,', '({ pass: failed.length === 0,',
   "a claim straddling the hold's end with no millisecond evidence", 'F1 unknown, not a pass'),
  ('f08', 'judges.mjs', 'seenBy: earliest(claim.seenAt, delivered)', 'seenBy: earliest(delivered)',
   "claimed in the hold's last second, seen claimed before it ends", 'F1 watcher sight'),
  ('f09', 'judges.mjs', 'absentAfter: claim.queuedThrough })', 'absentAfter: null })',
   "review F1: released in the hold's last second, read queued after it ends", 'F1 watcher order'),
  ('f10', 'judges.mjs', '{ seenBy: earliest(ev.timeline[index]?.t, received),', '{ seenBy: earliest(received),',
   "steered in the window's last second, seen before it ends", 'F1 controls'),
  ('f11', 'judges.mjs', '      absentAfter: absentThrough(ev.timeline, index, "controls", (s) => !(s.controls ?? []).some(same)) });',
   '      absentAfter: null });', "steered in the window's last second, read absent after it ends", 'F1 controls'),
  ('f12', 'judges.mjs', '  else if (steered.includes("unknown")) unknown.push(', '  else if (false) unknown.push(',
   "a steer straddling the window's end with no millisecond evidence", 'F1 controls'),
  # Review F1.a/F1.b: absence only from a successful read acquired after the end.
  ('g01', 'judges.mjs', '    const bound = timeline[j + 1]?.previousSeenFrom ?? frame.acquiredFrom;', '    const bound = frame.t;',
   'review F1.a: a queued frame emitted after the end but read before it', 'F1.a emission is not acquisition'),
  ('g02', 'judges.mjs', '    if (frame.error || !Array.isArray(frame[inventory]) || !absent(frame)) continue;', '    if (!absent(frame)) continue;',
   'review F1.b: an error read after the end is not an empty inventory', 'F1.b a failed read is not absence'),
  ('g03', 'judges.mjs', '    const bound = timeline[j + 1]?.previousSeenFrom ?? frame.acquiredFrom;', '    const bound = frame.acquiredFrom;',
   'a read still queued after the end, recorded on the next frame', 'F1.a the last read of a state'),
  ('g04', 'judges.mjs', '    const bound = timeline[j + 1]?.previousSeenFrom ?? frame.acquiredFrom;', '    const bound = timeline[j + 1]?.previousSeenFrom;',
   "steered in the window's last second, read absent after it ends", 'F1.a a frame\'s own acquisition'),
  # The collector: its stamps are what the judges' lower bounds stand on. Judged by its own test, not the self-check.
  ('p01', 'probe.py', '        acquired = now_ms()\n        try:\n', '        acquired = now_ms() + 250\n        try:\n',
   'test_a_frame_is_acquired_before_its_read_and_emitted_after_it', 'F1.a stamped before the read'),
  ('p02', 'probe.py', "'previousSeenFrom': last_from,", "'previousSeenFrom': acquired,",
   'test_previous_seen_from_is_the_last_read_of_the_previous_content', 'F1.a the previous read'),
  ('p03', 'probe.py', '        last_from = acquired\n', '',
   'test_previous_seen_from_is_the_last_read_of_the_previous_content', 'F1.a the previous read'),
  ('p04', 'probe.py', "            seen = {'error': f'{type(error).__name__}: {error}'}",
   "            seen = {'error': f'{type(error).__name__}: {error}', 'runs': [], 'controls': []}",
   'test_an_error_read_is_its_own_frame_with_no_inventory', 'F1.b an error has no inventory'),
]

def run_checked(command):
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout, done.stderr

node = lambda args: run_checked(['node', *args])
gate_selfcheck = lambda: node([str(GATE / 'gate.mjs'), '--self-check'])
probe_test = lambda: run_checked([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                                  str(ROOT / 'scripts' / 'tests' / 'test_gate_probe_acquisition.py')])
replay = lambda: node([str(REPLAY)])
# A judge or case arm is judged by the gate's self-check (FAIL lines); a collector arm by its own test (FAILED lines).
selfcheck = lambda name='judges.mjs': probe_test() if name == 'probe.py' else gate_selfcheck()

OUT.mkdir(parents=True, exist_ok=True)
base, base_replay = selfcheck(), replay()
(OUT / 'baseline.stdout.txt').write_text(base[1]); (OUT / 'baseline.stderr.txt').write_text(base[2])
(OUT / 'baseline.replay.txt').write_text(base_replay[1] + base_replay[2])
summary = {'baseline_exit': base[0], 'baseline_replay_exit': base_replay[0], 'arms': []}
for arm_id, name, old, new, expect, guards in ARMS:
    path = GATE / name
    keep = OUT / f'{arm_id}.{name}.orig'
    shutil.copyfile(path, keep)
    original = sha(path)
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        summary['arms'].append({'arm': arm_id, 'error': f'anchor found {text.count(old)} times'}); continue
    path.write_bytes(text.replace(old, new).encode('utf-8'))
    mutated = sha(path)
    code, out, err = selfcheck(name)
    rcode, rout, rerr = replay()
    shutil.copyfile(keep, path)
    restored = sha(path)
    fails = [l for l in out.splitlines() if l.startswith('FAIL')]
    named = [l for l in fails if expect in l]
    (OUT / f'{arm_id}.stdout.txt').write_text(out); (OUT / f'{arm_id}.stderr.txt').write_text(err)
    (OUT / f'{arm_id}.replay.txt').write_text(rout + rerr)
    entry = {'arm': arm_id, 'file': name, 'guards': guards, 'expect': expect, 'exit': code, 'fails': fails, 'named': named,
             'killed': code != 0 and bool(named), 'replay_exit': rcode, 'sha_original': original, 'sha_mutated': mutated,
             'sha_restored': restored, 'restored_ok': restored == original}
    (OUT / f'{arm_id}.meta.json').write_text(json.dumps(entry, indent=1) + '\n')
    summary['arms'].append(entry)
after, after_replay = selfcheck(), replay()
(OUT / 'after.stdout.txt').write_text(after[1])
summary['after_exit'], summary['after_replay_exit'] = after[0], after_replay[0]
(OUT / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
for a in summary['arms']:
    print(a['arm'], a.get('guards'), 'KILLED' if a.get('killed') else 'SURVIVED', a.get('exit'), 'replay', a.get('replay_exit'),
          'restored' if a.get('restored_ok') else 'NOT RESTORED', '|', (a.get('named') or a.get('fails') or [a.get('error')])[:1])
print('baseline exit', base[0], 'replay', base_replay[0], '| after exit', after[0], 'replay', after_replay[0])
