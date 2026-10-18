"""Disagreement classes (status-switch plan S2b): a cause is a predicate over both sides' recorded inputs.

Admission counts a disagreement under the ONE class it satisfies. None, or more than one, is unresolved, and a cause
read against env facts nobody published is no attribution. The triage key (S1) cannot stand in for a class: the
first test puts two different causes under one key.
"""
import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch

from service.api_core.shadow_classes import classify, env_inputs
from service.status_engine import StatusInputs
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body, row, wire, AT


def stopped_record():
    return dict(row(), state='available', stateCause='startable', busy=False,
                process=dict(state='none', verified='unknown', pid=None), turn=None)


def comms(**flags):
    value = StatusInputs(mode='managed', alive=False, in_turn=False, awaiting_input=False, worker_present=False,
                         env_reachable=True, disabled=False, bridge_stale=False, has_live_session=False)
    return dict(mode=value.mode, **{k: v for k, v in vars(replace(value, **flags)).items() if k != 'mode'})


class ClassifyTest(unittest.TestCase):
    def test_two_causes_under_one_triage_key_land_in_different_classes(self):
        env = dict(env_inputs(stopped_record()))
        # Same S1 key: (refresh, managed, comms `stopped`, env `available`). Different causes.
        self.assertEqual(classify(comms(disabled=True), 'stopped', env, 'available'), 'comms-only-stop')
        self.assertEqual(classify(comms(disabled=False), 'stopped', env, 'available'), 'unresolved:unmatched')

    def test_an_operator_stop_aify_env_also_knows_is_not_a_b2_gap(self):
        env = dict(env_inputs(stopped_record()), stateCause='operator-stop')
        self.assertEqual(classify(comms(disabled=True), 'stopped', env, 'stopped'), 'unresolved:unmatched')

    def test_a_disagreement_two_classes_explain_is_unresolved_not_chosen(self):
        env = dict(env_inputs(row()))  # turn open
        both = comms(mode='resident', background_work=True, in_turn=False)
        self.assertEqual(classify(both, 'shell', env, 'working'), 'unresolved:ambiguous')
        self.assertEqual(classify(dict(both, background_work=False), 'shell', env, 'working'), 'turn-open-differs')
        self.assertEqual(classify(dict(both, in_turn=True), 'shell', env, 'working'), 'background-shell-unpublished')

    def test_without_env_facts_nothing_is_attributed(self):
        self.assertEqual(classify(comms(disabled=True), 'stopped', {}, 'available'), 'unresolved:unmatched')

    def test_the_env_projection_carries_only_closed_facts(self):
        self.assertEqual(dict(env_inputs(row())), {
            'stateCause': 'turn-open', 'busy': True, 'process': 'running', 'verified': 'yes',
            'turnOpen': True, 'awaitingInput': False})
        self.assertEqual(dict(env_inputs(stopped_record()))['turnOpen'], None)


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

    def test_a_comms_only_stop_is_counted_under_its_class_and_persisted(self):
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        from service.api_core import shadow_windows
        asyncio.run(AgentStateShadowStore().apply(wire(body(agents=[stopped_record()])), AT))
        owner = shadow_windows.ShadowWindows(self.recorder)
        asyncio.run(owner.start(shadow_windows.Versions(comms='c1'), AT))
        stopped = StatusInputs(mode='managed', alive=False, in_turn=False, awaiting_input=False, worker_present=False,
                               env_reachable=True, disabled=True, bridge_stale=False, has_live_session=False)
        self.shadow.observe(self.shadow.bind(), {'id': 'alpha', 'machine_id': ' Host '}, stopped, 'stopped', 'refresh')
        report = self.recorder.report()['byPurpose']['status word']
        self.assertEqual(report['counts']['partialDisagreed'], 1, 'the comparison completed and disagreed')
        self.assertEqual(report['disagreementsByClass'], {'comms-only-stop': 1})
        asyncio.run(owner.tick(shadow_windows.Versions(comms='c1'), '2026-10-07T09:00:30Z'))
        [window] = asyncio.run(shadow_windows.recent_windows(1))
        self.assertEqual(window['counts']['status word']['refresh']['class:comms-only-stop'], 1)
