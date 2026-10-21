"""A bounded, fail-closed mutation campaign (review section 6.3). Each arm replaces exactly one span of one file, runs
the checker that judges that file, and is KILLED only when the checker exits nonzero AND a line of its output names the
arm's expected witness. Whatever happens inside an arm, a timeout, a checker that cannot start, an exception in the
controller itself, the file is restored by byte copy in a `finally`, its sha256 is compared with the original, and the
arm's receipt is written to disk before the next arm starts.

The campaign exits 0 only when every one of these holds; anything else is a nonzero exit naming each failure:
  - the baseline checks pass before the first arm and again after the last;
  - every arm's anchor occurs exactly once;
  - every arm is killed by its named witness;
  - every arm's file is restored to its original sha256;
  - every arm's receipt exists on disk and reads back.

  python scripts/sandbox/gate/campaign.py <worktree> <out dir> <arms module>...

Each <arms module> is a Python file defining, as literals, ARMS = [(arm, file, old, new, witness, guards), ...] and/or
CHECKERS = {file: [argv...]}, the command that judges each file (a file with no entry uses CHECKERS['*']). The modules'
ARMS are concatenated and their CHECKERS merged; they are read, never executed. Commands run in the worktree."""
import ast
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

TIMEOUT_S = 300  # one checker run; a hung checker is killed, the arm fails, and the file is still restored


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load_arms(*module_paths):
    """Every module's ARMS concatenated and CHECKERS merged, as literals: read from source, never executed."""
    arms, checkers = [], {}
    for module_path in module_paths:
        for node in ast.parse(pathlib.Path(module_path).read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == 'ARMS':
                    arms += ast.literal_eval(node.value)
                elif node.targets[0].id == 'CHECKERS':
                    checkers.update(ast.literal_eval(node.value))
    ids = [arm[0] for arm in arms]
    if not arms or '*' not in checkers or len(ids) != len(set(ids)):
        raise SystemExit(f'{module_paths}: need ARMS with unique ids and CHECKERS with a "*" entry')
    return arms, checkers


def run_checker(argv, root):
    """(exit, stdout, stderr, error). A checker that cannot start or times out is an error, never a pass."""
    try:
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=TIMEOUT_S)
        return done.returncode, done.stdout, done.stderr, None
    except subprocess.TimeoutExpired as error:
        return None, error.stdout or '', error.stderr or '', f'timed out after {TIMEOUT_S} s'
    except Exception as error:  # noqa: BLE001: every failure to run is recorded, none is swallowed
        return None, '', '', f'{type(error).__name__}: {error}'


def failing(line):
    """A line that reports a failure: the gate self-check's `FAIL`, pytest's `FAILED`/`ERROR`, node's `not ok`. A witness
    named on a passing line (node prints `# Subtest: <name>` for every test) never counts as the arm's kill."""
    return line.lstrip().startswith(('FAIL', 'ERROR', 'not ok'))


def checker_for(checkers, name):
    return checkers.get(name) or checkers['*']


def run_arm(arm, root, gate, out, checkers):
    """One arm, with its file restored and its receipt written whatever happens. Returns the receipt."""
    arm_id, name, old, new, witness, guards = arm
    path = gate / name
    keep = out / f'{arm_id}.{name}.orig'
    shutil.copyfile(path, keep)
    receipt = {'arm': arm_id, 'file': name, 'guards': guards, 'witness': witness, 'sha_original': sha(path),
               'killed': False, 'problems': []}
    try:
        text = path.read_text(encoding='utf-8')
        count = text.count(old)
        if count != 1:
            receipt['problems'].append(f'anchor found {count} times')
            return receipt
        path.write_bytes(text.replace(old, new).encode('utf-8'))
        receipt['sha_mutated'] = sha(path)
        code, stdout, stderr, error = run_checker(checker_for(checkers, name), root)
        (out / f'{arm_id}.stdout.txt').write_text(stdout, encoding='utf-8')
        (out / f'{arm_id}.stderr.txt').write_text(stderr, encoding='utf-8')
        receipt['exit'] = code
        if error:
            receipt['problems'].append(f'checker error: {error}')
        named = [line for line in (stdout + stderr).splitlines() if witness in line and failing(line)]
        receipt['named'] = named[:5]
        receipt['killed'] = error is None and code != 0 and bool(named)
        if not receipt['killed'] and not error:
            receipt['problems'].append('survived: the checker passed or never named the witness')
        return receipt
    except Exception as error:  # noqa: BLE001
        receipt['problems'].append(f'controller error: {type(error).__name__}: {error}')
        return receipt
    finally:
        try:
            shutil.copyfile(keep, path)
        except Exception as error:  # noqa: BLE001
            receipt['problems'].append(f'restore failed: {type(error).__name__}: {error}')
        receipt['sha_restored'] = sha(path) if path.exists() else None
        if receipt['sha_restored'] != receipt['sha_original']:
            receipt['problems'].append('not restored to the original sha256')
        (out / f'{arm_id}.receipt.json').write_text(json.dumps(receipt, indent=1) + '\n', encoding='utf-8')


def controls(root, checkers, out, label):
    """Every distinct checker, unmutated: each must exit 0."""
    problems = []
    for index, argv in enumerate(dict.fromkeys(tuple(c) for c in checkers.values())):
        code, stdout, stderr, error = run_checker(list(argv), root)
        (out / f'{label}.{index}.txt').write_text(stdout + stderr, encoding='utf-8')
        if error or code != 0:
            problems.append(f'{label} control {" ".join(argv)}: exit {code}{f", {error}" if error else ""}')
    return problems


def campaign(root, out, *arms_modules):
    root, out = pathlib.Path(root), pathlib.Path(out)
    gate = root / 'scripts' / 'sandbox' / 'gate'
    arms, checkers = load_arms(*arms_modules)
    out.mkdir(parents=True, exist_ok=False)
    failures = controls(root, checkers, out, 'baseline')
    if failures:
        return failures, []  # a red baseline makes every arm meaningless: no arm runs
    receipts = [run_arm(arm, root, gate, out, checkers) for arm in arms]
    failures += controls(root, checkers, out, 'after')
    for arm, receipt in zip(arms, receipts):
        on_disk = out / f'{arm[0]}.receipt.json'
        try:
            if json.loads(on_disk.read_text(encoding='utf-8')) != receipt:
                failures.append(f'{arm[0]}: the receipt on disk differs from the one recorded')
        except Exception as error:  # noqa: BLE001
            failures.append(f'{arm[0]}: no readable receipt ({type(error).__name__})')
        failures += [f'{arm[0]}: {p}' for p in receipt['problems']]
    return failures, receipts


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    failures, receipts = campaign(*argv[1:])
    for r in receipts:
        print(r['arm'], r['guards'], 'KILLED' if r['killed'] else 'NOT KILLED', '|', (r.get('named') or r['problems'] or [''])[0])
    for f in failures:
        print('CAMPAIGN FAIL', f)
    print(f'{sum(r["killed"] for r in receipts)}/{len(receipts)} killed; campaign {"PASS" if not failures else "FAIL"}')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
