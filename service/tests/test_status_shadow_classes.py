"""Disagreement classes (status-switch plan S2b): a cause is a predicate over both sides' recorded inputs.

Admission counts a disagreement under the ONE class it satisfies. None, or more than one, is unresolved, and a cause
read against env facts nobody published is no attribution.

TWO KINDS OF WITNESS, kept apart. `SyntheticClassifierTest` exercises the classifier's mechanics on hand-made tuples
and claims nothing about real histories. `ConstructibleHistoryTest` claims what the classes do with histories the two
derivations can actually produce: every comms word there is `derive()` of the same StatusInputs, and every env word
and cause is aify-env's own `deriveAgentState` of the stated facts, run from the sibling checkout.
"""
import asyncio
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from service.api_core.partial_status_shadow import candidate_word, project_inputs
from service.api_core.shadow_classes import classify, env_inputs
from service.status_engine import StatusInputs, derive
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body, row, wire, AT


def comms_side(**flags):
    """Comms' projected inputs exactly as the recorder stores them, and the word derive() gives the same inputs."""
    defaults = dict(mode='managed', alive=False, in_turn=False, awaiting_input=False, worker_present=False,
                    env_reachable=True, disabled=False, bridge_stale=False, has_live_session=False)
    inputs = StatusInputs(**{**defaults, **flags})
    return dict(project_inputs(inputs)), derive(inputs)


def env_repo():
    named = os.environ.get('AIFY_ENV_REPO')
    repo = Path(__file__).resolve().parents[2]
    candidates = [Path(named)] if named else [repo.parent / 'aify-env', Path.home() / 'projects/aify-env']
    return next((p for p in candidates if (p / 'lib/agent-state.mjs').is_file()), None)


def env_side(facts, turn_open):
    """aify-env's word and cause for `facts`, from its own deriveAgentState, as a validated record's projection."""
    source, node = env_repo(), shutil.which('node')
    assert source and node, 'the aify-env checkout (AIFY_ENV_REPO) and node are needed for a constructible history'
    href = (source / 'lib/agent-state.mjs').as_uri()
    script = (f'const m = await import({json.dumps(href)});'
              'process.stdout.write(JSON.stringify(m.deriveAgentState(JSON.parse(process.argv[1]))));')
    derived = json.loads(subprocess.run([node, '--input-type=module', '-e', script, json.dumps(facts)],
                                        capture_output=True, text=True, check=True, timeout=20).stdout)
    record = dict(row(), state=derived['state'], stateCause=derived['cause'], busy=facts['busy'],
                  process=dict(state=facts['process'], verified=facts['verified'], pid=4242 if facts['process'] == 'running' else None),
                  turn=None if turn_open is None else dict(row()['turn'], open=turn_open, awaitingInput=facts['awaitingInput']))
    return dict(env_inputs(record)), candidate_word(record['state'])


def env_facts(**changes):
    base = dict(stoppedByOperator=False, definition='valid', mode='resident', process='running', verified='yes',
                startingInWindow=False, conflict=False, busy=False, awaitingInput=False, screen=None, backgroundShells=0)
    return {**base, **changes}


class SyntheticClassifierTest(unittest.TestCase):
    """Mechanics only, on hand-made tuples: no claim that these pairs occur."""

    def test_the_helper_keeps_the_mode_it_was_given(self):
        self.assertEqual(comms_side(mode='resident')[0]['mode'], 'resident')

    def test_zero_one_and_several_matches(self):
        env = {'stateCause': 'turn-open', 'turnOpen': True}
        self.assertEqual(classify({'background_work': True, 'in_turn': False}, 'shell', env, 'working'), 'unresolved:ambiguous')
        self.assertEqual(classify({'background_work': False, 'in_turn': False}, 'shell', env, 'working'), 'turn-open-differs')
        self.assertEqual(classify({'background_work': True, 'in_turn': True}, 'shell', env, 'working'), 'background-shell-unpublished')
        self.assertEqual(classify({'in_turn': True}, 'working', env, 'working'), 'unresolved:unmatched')

    def test_without_env_facts_nothing_is_attributed(self):
        self.assertEqual(classify({'disabled': True}, 'stopped', {}, 'available'), 'unresolved:unmatched')

    def test_the_env_projection_carries_only_closed_facts(self):
        self.assertEqual(dict(env_inputs(row())), {
            'stateCause': 'turn-open', 'busy': True, 'process': 'running', 'verified': 'yes',
            'turnOpen': True, 'awaitingInput': False})
        self.assertIsNone(dict(env_inputs(dict(row(), turn=None)))['turnOpen'])


class ConstructibleHistoryTest(unittest.TestCase):
    """Histories both derivations produce. Each asserts its full S1 key before what the class makes of it."""

    def key(self, source, comms, env):
        return (source, comms[0]['mode'], comms[1], env[1])

    def test_two_causes_under_one_triage_key_land_in_different_classes(self):
        shell = comms_side(mode='resident', alive=True, has_live_session=True, background_work=True)
        turn_open = env_side(env_facts(busy=True), turn_open=True)
        screen_working = env_side(env_facts(screen={'state': 'working', 'fresh': True}), turn_open=False)
        self.assertEqual(self.key('refresh', shell, turn_open), ('refresh', 'resident', 'shell', 'working'))
        self.assertEqual(self.key('refresh', shell, screen_working), ('refresh', 'resident', 'shell', 'working'))
        self.assertEqual((turn_open[0]['stateCause'], screen_working[0]['stateCause']), ('turn-open', 'screen'))
        # Same key. With the turn open, the background-shell and turn classes both explain it: unresolved.
        # With only the screen working, the background shell alone does.
        self.assertEqual(classify(shell[0], shell[1], *turn_open), 'unresolved:ambiguous')
        self.assertEqual(classify(shell[0], shell[1], *screen_working), 'background-shell-unpublished')

    def test_a_resident_nobody_can_wake_is_q3_and_one_whose_bridge_died_is_not(self):
        no_handle = comms_side(mode='resident', alive=True, worker_present=True, has_live_session=True,
                               wake_handle_absent=True)
        bridge_died = comms_side(mode='resident', bridge_stale=True)
        running = env_side(env_facts(), turn_open=False)
        self.assertEqual(self.key('refresh', no_handle, running), ('refresh', 'resident', 'offline', 'online'))
        self.assertEqual(self.key('refresh', bridge_died, running), ('refresh', 'resident', 'offline', 'online'))
        self.assertEqual(classify(no_handle[0], no_handle[1], *running), 'no-wake-handle')
        self.assertEqual(classify(bridge_died[0], bridge_died[1], *running), 'unresolved:unmatched')
        # The authoritative builder also sets config_defect for it, and words the same fact `misconfigured`.
        worded = comms_side(mode='resident', alive=True, worker_present=True, has_live_session=True,
                            wake_handle_absent=True, config_defect='no usable wake handle (wakeMode=hermes-missing-handle)')
        self.assertEqual(worded[1], 'misconfigured')
        self.assertEqual(classify(worded[0], worded[1], *running), 'no-wake-handle')

    def test_a_wait_one_side_missed_is_b6_and_a_blocked_screen_is_not(self):
        held = comms_side(mode='resident', alive=True, worker_present=True, has_live_session=True, in_turn=True)
        waiting = env_side(env_facts(busy=True, awaitingInput=True), turn_open=True)
        self.assertEqual(self.key('refresh', held, waiting), ('refresh', 'resident', 'working', 'blocked'))
        self.assertEqual(classify(held[0], held[1], *waiting), 'awaiting-input-differs')
        # The reverse: comms heard the wait and aify-env did not.
        asked = comms_side(mode='resident', alive=True, worker_present=True, has_live_session=True, in_turn=True,
                           awaiting_input=True)
        running = env_side(env_facts(busy=True), turn_open=True)
        self.assertEqual(self.key('refresh', asked, running), ('refresh', 'resident', 'blocked', 'working'))
        self.assertEqual(classify(asked[0], asked[1], *running), 'awaiting-input-differs')
        # Same word pair, another cause: aify-env sees a blocked screen and both agree no input is awaited.
        turn = comms_side(alive=True, worker_present=True, in_turn=True)
        screen = env_side(env_facts(mode='managed', busy=True, screen={'state': 'blocked', 'fresh': True}), turn_open=True)
        self.assertEqual(self.key('refresh', turn, screen), ('refresh', 'managed', 'working', 'blocked'))
        self.assertEqual(screen[0]['stateCause'], 'screen')
        self.assertEqual(classify(turn[0], turn[1], *screen), 'unresolved:unmatched')

    def test_a_wait_on_a_turn_only_one_side_holds_is_the_turn_difference(self):
        idle = comms_side(mode='resident', alive=True, worker_present=True, has_live_session=True)
        waiting = env_side(env_facts(busy=True, awaitingInput=True), turn_open=True)
        self.assertEqual(self.key('refresh', idle, waiting), ('refresh', 'resident', 'online', 'blocked'))
        self.assertEqual(classify(idle[0], idle[1], *waiting), 'turn-open-differs')
        asked = comms_side(mode='resident', alive=True, worker_present=True, has_live_session=True, in_turn=True,
                           awaiting_input=True)
        closed = env_side(env_facts(), turn_open=False)
        self.assertEqual(self.key('refresh', asked, closed), ('refresh', 'resident', 'blocked', 'online'))
        self.assertEqual(classify(asked[0], asked[1], *closed), 'turn-open-differs')

    def test_a_stop_only_comms_knows_is_b2_and_one_aify_env_knows_is_not(self):
        stopped = comms_side(disabled=True)
        unheard = env_side(env_facts(mode='managed', process='none', verified='unknown'), turn_open=None)
        heard = env_side(env_facts(mode='managed', process='none', verified='unknown', stoppedByOperator=True), turn_open=None)
        self.assertEqual(self.key('refresh', stopped, unheard), ('refresh', 'managed', 'stopped', 'available'))
        self.assertEqual(classify(stopped[0], stopped[1], *unheard), 'comms-only-stop')
        self.assertEqual(heard[1], 'stopped', 'aify-env reports the stop itself: an agreement, not a class')

    def test_a_shell_on_a_managed_screen_is_not_a_background_shell(self):
        screen_shell = comms_side(alive=True, worker_present=True, host_activity='shell', host_activity_fresh=True)
        idle = env_side(env_facts(mode='managed'), turn_open=False)
        self.assertEqual(self.key('refresh', screen_shell, idle), ('refresh', 'managed', 'shell', 'online'))
        self.assertEqual(classify(screen_shell[0], screen_shell[1], *idle), 'unresolved:unmatched')

    def test_a_turn_comms_holds_open_and_aify_env_closed_is_b6(self):
        working = comms_side(alive=True, worker_present=True, in_turn=True)
        idle = env_side(env_facts(mode='managed'), turn_open=False)
        self.assertEqual(self.key('refresh', working, idle), ('refresh', 'managed', 'working', 'online'))
        self.assertEqual(classify(working[0], working[1], *idle), 'turn-open-differs')


class ClassesThroughThePublicationTest(FastApiTestCase):
    """The real path: a validated publication's record facts reach the class, and the window counts it."""

    def setUp(self):
        super().setUp()
        from service.api_core import partial_status_shadow as shadow
        self.shadow = shadow
        self.recorder = shadow.Recorder()
        for p in (patch.object(shadow, 'mirror', shadow.Mirror()), patch.object(shadow, 'recorder', self.recorder),
                  patch.object(shadow, 'clock', return_value=AT)):
            p.start(); self.addCleanup(p.stop)

    def stopped_vs(self, record):
        """Apply `record` through the receiver, then observe a derived comms stop against it."""
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        self.assertTrue(asyncio.run(AgentStateShadowStore().apply(wire(body(agents=[record])), AT)).applied)
        stopped = StatusInputs(mode='managed', alive=False, in_turn=False, awaiting_input=False, worker_present=False,
                               env_reachable=True, disabled=True, bridge_stale=False, has_live_session=False)
        self.shadow.observe(self.shadow.bind(), {'id': 'alpha', 'machine_id': ' Host '}, stopped, derive(stopped), 'refresh')
        return self.recorder.report()['byPurpose']['status word']

    def test_a_record_that_heard_the_stop_but_reports_another_word_is_not_a_b2_gap(self):
        # The receiver admits state and cause independently; an honest aify-env never pairs these two.
        contradictory = dict(row(), state='available', stateCause='operator-stop', busy=False,
                             process=dict(state='none', verified='unknown', pid=None), turn=None)
        report = self.stopped_vs(contradictory)
        self.assertEqual(report['counts']['partialDisagreed'], 1, 'the comparison completed and disagreed')
        self.assertEqual(report['disagreementsByClass'], {'unresolved:unmatched': 1})

    def test_a_comms_only_stop_is_counted_under_its_class_and_persisted(self):
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        from service.api_core import shadow_windows
        unheard = dict(row(), state='available', stateCause='startable', busy=False,
                       process=dict(state='none', verified='unknown', pid=None), turn=None)
        asyncio.run(AgentStateShadowStore().apply(wire(body(agents=[unheard])), AT))
        owner = shadow_windows.ShadowWindows(self.recorder)
        asyncio.run(owner.start(shadow_windows.Versions(comms='c1'), AT))
        stopped = StatusInputs(mode='managed', alive=False, in_turn=False, awaiting_input=False, worker_present=False,
                               env_reachable=True, disabled=True, bridge_stale=False, has_live_session=False)
        self.shadow.observe(self.shadow.bind(), {'id': 'alpha', 'machine_id': ' Host '}, stopped, derive(stopped), 'refresh')
        report = self.recorder.report()['byPurpose']['status word']
        self.assertEqual(report['counts']['partialDisagreed'], 1, 'the comparison completed and disagreed')
        self.assertEqual(report['disagreementsByClass'], {'comms-only-stop': 1})
        asyncio.run(owner.tick(shadow_windows.Versions(comms='c1'), '2026-10-07T09:00:30Z'))
        [window] = asyncio.run(shadow_windows.recent_windows(1))
        self.assertEqual(window['counts']['status word']['refresh']['class:comms-only-stop'], 1)
