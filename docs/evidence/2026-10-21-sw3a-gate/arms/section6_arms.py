"""Mutation arms for the qualification apparatus (review section 6), run by scripts/sandbox/gate/campaign.py together
with the retained judging arms in run_arms.py (whose ARMS literal it reads in place):

  python scripts/sandbox/gate/campaign.py <worktree> <out> docs/evidence/2026-10-21-sw3a-gate/arms/run_arms.py \\
      docs/evidence/2026-10-21-sw3a-gate/arms/section6_arms.py

Each arm's witness is the test or self-check line that must name it. Read as literals, never executed."""

CHECKERS = {
    '*': ['node', 'scripts/sandbox/gate/gate.mjs', '--self-check'],
    'probe.py': ['python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'scripts/tests/test_gate_probe_acquisition.py'],
    'case-judging.mjs': ['python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'scripts/tests/test_gate_apparatus.py'],
    'attempt-dir.mjs': ['python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'scripts/tests/test_gate_apparatus.py'],
    'campaign.py': ['python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'scripts/tests/test_gate_apparatus.py'],
    'pair.mjs': ['node', '--test', 'scripts/tests/gate_pair_stdout.mjs'],
}

# (arm, file, the span replaced, its replacement, the witness that must be named, the obligation it guards)
ARMS = [
  # 6.1: env-resumed's fixture must separate fresh-busy from idle.
  ('c01', 'case-judging.mjs', '  const refused = (FIXTURE_CHECKS[id] ?? []).map((check) => check(evidence.marks)).filter(Boolean);',
   '  const refused = [];', 'test_the_retained_attempt_whose_fresh_and_idle_coincide_is_refused_by_name',
   '6.1 no fixture check: f5191387 judged the retained attempt directly'),
  ('c02', 'case-judging.mjs', '  if (!(idle.at > fresh.at && idle.seenAt > fresh.seenAt)) {', '  if (false) {',
   'test_the_retained_attempt_whose_fresh_and_idle_coincide_is_refused_by_name', '6.1 coinciding phases'),
  ('c03', 'case-judging.mjs', 'idle.at > fresh.at && idle.seenAt > fresh.seenAt', 'idle.at >= fresh.at && idle.seenAt >= fresh.seenAt',
   'test_the_retained_attempt_whose_fresh_and_idle_coincide_is_refused_by_name', '6.1 strictly after'),
  ('c04', 'case-judging.mjs', '  if (!(m.idleRequestedAt - fresh.seenAt >= FIRST_PASS_MS)) {', '  if (false) {',
   'test_a_fresh_busy_phase_shorter_than_one_claim_pass_is_refused', '6.1 a whole claim pass'),
  ('c05', 'case-judging.mjs', '  if (!fresh) return "fixture: no fresh busy record was accepted after the resume";\n', '',
   'test_the_retained_attempt_as_recorded_names_its_missing_fresh_busy_phase', '6.1 a fresh busy record'),
  ('c06', 'case-judging.mjs', '  return judges[id](evidence);', '  return { pass: true, failed: [], unknown: [] };',
   'test_CONTROL_a_separated_resume_reaches_the_judge_unchanged', '6.1 admitted evidence reaches its judge'),
  # 6.2: exclusive attempt storage.
  ('d01', 'attempt-dir.mjs', '    fs.mkdirSync(outDir);\n', '    fs.mkdirSync(outDir, { recursive: true });\n',
   'test_a_second_acquisition_of_one_name_is_refused', '6.2 recursive mkdir is not a claim (f5191387)'),
  ('d02', 'attempt-dir.mjs', '    if (error?.code === "EEXIST") throw new AttemptDirTaken', '    if (false) throw new AttemptDirTaken',
   'test_a_second_acquisition_of_one_name_is_refused', '6.2 a taken name is named'),
  # 6.3: the campaign fails closed.
  ('e01', 'campaign.py', '            shutil.copyfile(keep, path)\n', '            pass\n',
   'test_CONTROL_a_killed_arm_passes_the_campaign', '6.3 restored in finally'),
  ('e02', 'campaign.py', '    return 0 if not failures else 1', '    return 0',
   'test_the_command_line_exits_nonzero_on_any_failure', '6.3 a nonzero exit'),
  ('e03', 'campaign.py', "        receipt['killed'] = error is None and code != 0 and bool(named)", "        receipt['killed'] = code != 0",
   'test_an_arm_killed_by_another_witness_is_not_killed', '6.3 the named witness'),
  ('e04', 'campaign.py', "            receipt['problems'].append('survived: the checker passed or never named the witness')\n",
   "            pass\n", 'test_a_surviving_arm_fails_the_campaign', '6.3 a survivor fails'),
  ('e05', 'campaign.py', "    except subprocess.TimeoutExpired as error:\n        return None, error.stdout or '', error.stderr or '', f'timed out after {TIMEOUT_S} s'\n",
   '', 'test_a_hung_checker_is_killed_and_fails_its_arm', '6.3 a hung checker'),
  ('e06', 'campaign.py', "    except Exception as error:  # noqa: BLE001\n        receipt['problems'].append(f'controller error:",
   "    except ZeroDivisionError as error:  # noqa: BLE001\n        receipt['problems'].append(f'controller error:",
   'test_an_arm_that_throws_mid_mutation_is_restored_recorded_and_fails_the_campaign', '6.3 an exception mid-mutation'),
  ('e07', 'campaign.py', '        if count != 1:', '        if count > 1:',
   'test_an_anchor_that_misses_fails_the_campaign', '6.3 an anchor miss'),
  ('e08', 'campaign.py', '    if failures:\n        return failures, []', '    if False:\n        return failures, []',
   'test_a_red_baseline_runs_no_arm_and_fails', '6.3 a red baseline'),
  ('e09', 'campaign.py', "        (out / f'{arm_id}.receipt.json').write_text(json.dumps(receipt, indent=1) + '\\n', encoding='utf-8')\n",
   "        pass\n", 'test_CONTROL_a_killed_arm_passes_the_campaign', '6.3 the receipt persisted'),
  ('e10', 'campaign.py', 'if witness in line and failing(line)]', 'if witness in line]',
   'test_a_witness_named_only_on_a_passing_line_is_not_a_kill', '6.3 a kill is a failing line'),
  # Runner R1 in the gate's Pair: every data read takes stdout; each arm puts back the combined `out` at one site.
  ('s01', 'pair.mjs', '"list volumes").stdout.trim();', '"list volumes").out.trim();',
   'start: the volumes owned are the names on stdout', 'R1 pair #ownVolumes'),
  ('s02', 'pair.mjs', '    const text = result.stdout.trim();', '    const text = result.out.trim();',
   'api: status and JSON body from stdout', 'R1 pair api'),
  ('s03', 'pair.mjs', '"snapshot").stdout.trim()', '"snapshot").out.trim()',
   "snapshot: the probe's last stdout line", 'R1 pair snapshot'),
  ('s04', 'pair.mjs', '"read the timeline").stdout;', '"read the timeline").out;',
   'timeline: every stdout line is a frame', 'R1 pair timeline'),
  ('s05', 'pair.mjs', '"read the stand-in log").stdout;', '"read the stand-in log").out;',
   'standin: a last line with no newline', 'R1 pair standin'),
  ('s06', 'pair.mjs', '.lifetime.json`]).stdout.trim();', '.lifetime.json`]).out.trim();',
   "lifetime: the file's JSON", 'R1 pair lifetime'),
  ('s07', 'pair.mjs', '"find the env daemon").stdout.trim()', '"find the env daemon").out.trim()',
   'the env daemon pid', 'R1 pair #daemonPid'),
  ('s08', 'pair.mjs', '"read the relay log").stdout;', '"read the relay log").out;',
   "contact: the relay log's lines", 'R1 pair contact'),
  ('s09', 'pair.mjs', '"started at").stdout.trim());', '"started at").out.trim());',
   'restart: the start time', 'R1 pair restartComms'),
]
