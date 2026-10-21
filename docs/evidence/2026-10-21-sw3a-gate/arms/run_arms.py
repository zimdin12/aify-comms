"""Mutation arms for the gate's judging (SW3a). Each arm replaces exactly one source span, runs the gate's
--self-check, keeps its full stdout/stderr, exit and the witnesses that went FAIL, then restores the file by byte copy
and checks its sha256. An arm is KILLED only if the self-check exits nonzero AND a FAIL line names the guard's case."""
import hashlib, json, os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
GATE = ROOT / 'scripts' / 'sandbox' / 'gate'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
ARMS = [
  ('a01', 'judges.mjs', '  if (claim.claimedAt < accepted.at) failed.push(`${label}: claimed before comms accepted the publication`);\n', '', 'claimed a second before the acceptance, seen after it'),
  ('a02', 'judges.mjs', '  if (accepted.seenAt != null && claim.seenAt < accepted.seenAt) failed.push(`${label}: the watcher saw the claim first`);\n', '', 'seen claimed first in the same second'),
  ('a03', 'judges.mjs', '  if (claim.claimedAt - accepted.at > FIRST_PASS_MS) failed.push(`${label}: not claimed on the first pass after acceptance`);\n', '', 'claimed after the first pass'),
  ('a04', 'judges.mjs', '  if (polls(ev.standin, window.from, window.to) < MIN_POLLS) failed.push(`${label}: the stand-in was not seen polling`);\n', '', 'no claimer polling'),
  ('a05', 'judges.mjs', '  if (received(ev.standin, runId)) failed.push(`${label}: the stand-in received ${runId}`);\n', '', 'stale-second-holder: received fails'),
  ('a06', 'judges.mjs', 'const lastKnownShown = (word) => Boolean(word?.lastKnown) || /last[ -]?known/i.test(String(word?.statusNote ?? ""));', 'const lastKnownShown = (word) => true;', 'no last-known note'),
  ('a07', 'judges.mjs', '    if (controlsFor(ev.timeline, m.runId).length) failed.push("it became a control, not a delivery");\n', '', 'became a control'),
  ('a08', 'judges.mjs', '    if (!(m.sentAt - m.secondAppliedAt > RECEIPT_SILENCE_MS)) failed.push("fixture: the second holder was not stale");\n', '', 'second holder fresh'),
  ('a09', 'judges.mjs', '    else if (spawns.some((s) => s.initial_message !== "")) failed.push("a spawn request carried an initial message");\n', '', 'an initial message'),
  ('a10', 'judges.mjs', '    if (m.spawnsAfterSend > 0) failed.push("the send to the stopped agent started it");\n', '', 'the send started it'),
  ('a11', 'judges.mjs', 'export const polls = (standin, from, to) => standin.filter((e) => e.event === "polled" && e.t >= from && e.t <= to).length;', 'export const polls = (standin, from, to) => 99;', 'no claimer polling'),
  ('a12', 'judges.mjs', '  const seen = timeline.find((s) => { const r = runOf(s, runId); return r && r.status !== "queued"; });', '  const seen = timeline.find((s) => { const r = runOf(s, runId); return r; });', 'claimed'),
  ('a13', 'cases.mjs', 'expected: "delivered at once",', 'expected: "delivered promptly",', "its expected answer is page 2's words"),
  ('a14', 'judges.mjs', '    if (m.wordAfterSnapshot?.status === "unknown") failed.push("still unknown after the complete snapshot");\n', '', 'unknown after the snapshot'),
  ('a15', 'judges.mjs', '    if (m.word?.status !== "working") failed.push(`the word read ${m.word?.status}, not working`);\n', '', 'promotion skipped'),
]

def selfcheck():
    done = subprocess.run(['node', str(GATE / 'gate.mjs'), '--self-check'], cwd=ROOT, capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout, done.stderr

OUT.mkdir(parents=True, exist_ok=True)
base = selfcheck()
(OUT / 'baseline.stdout.txt').write_text(base[1]); (OUT / 'baseline.stderr.txt').write_text(base[2])
summary = {'baseline_exit': base[0], 'arms': []}
for arm_id, name, old, new, expect in ARMS:
    path = GATE / name
    keep = OUT / f'{arm_id}.{name}.orig'
    shutil.copyfile(path, keep)
    original = sha(path)
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        summary['arms'].append({'arm': arm_id, 'error': f'anchor found {text.count(old)} times'}); continue
    path.write_bytes(text.replace(old, new).encode('utf-8'))
    mutated = sha(path)
    code, out, err = selfcheck()
    shutil.copyfile(keep, path)
    restored = sha(path)
    fails = [l for l in out.splitlines() if l.startswith('FAIL')]
    named = [l for l in fails if expect in l]
    (OUT / f'{arm_id}.stdout.txt').write_text(out); (OUT / f'{arm_id}.stderr.txt').write_text(err)
    entry = {'arm': arm_id, 'file': name, 'expect': expect, 'exit': code, 'fails': fails, 'named': named,
             'killed': code != 0 and bool(named), 'sha_original': original, 'sha_mutated': mutated,
             'sha_restored': restored, 'restored_ok': restored == original}
    (OUT / f'{arm_id}.meta.json').write_text(json.dumps(entry, indent=1))
    summary['arms'].append(entry)
after = selfcheck()
(OUT / 'after.stdout.txt').write_text(after[1])
summary['after_exit'] = after[0]
(OUT / 'summary.json').write_text(json.dumps(summary, indent=1))
for a in summary['arms']:
    print(a['arm'], 'KILLED' if a.get('killed') else 'SURVIVED', a.get('exit'), 'restored' if a.get('restored_ok') else 'NOT RESTORED', '|', (a.get('named') or a.get('fails') or [a.get('error')])[:1])
print('baseline exit', base[0], 'after exit', after[0])
