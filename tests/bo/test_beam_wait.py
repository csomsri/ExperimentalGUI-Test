import ast
import math
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class BeamWaitTests(unittest.TestCase):
    def page(self):
        source = Path('python/app/pid_ga_control_tab.py')
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PIDGAControlTab')
        names = {'_clear_candidate_wait', '_wait_for_candidate_beam', '_start_automatic_candidate', '_ga_sequence_tick'}
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = dict(math=math, time=time, PIDCandidate=object,
                  ControlMode=SimpleNamespace(GA_TUNING='GA'))
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), ns)
        page = SimpleNamespace(_ga_auto_active=True, _ga_sequence_state="PREPARING", ga_tuner=object(),
            auto_ga_arm_check=Mock(), arm_output_check=Mock(), output_callback=Mock(),
            context=Mock(), _actuator_index=0, _ga_sequence_timer=Mock(),
            ga_status_label=Mock(), _bo_event=Mock(), _abort_automatic_ga=Mock(),
            _present_candidate=Mock(side_effect=RuntimeError('start boundary')),
            _beam_window_snapshot=Mock(return_value=dict(valid=False, fresh=False, ready=False, value_nA=math.nan)))
        page.context.power_states = [True]
        page.context.enable_states = [True]
        for name in names:
            setattr(page, name, ns[name].__get__(page))
        page._clear_candidate_wait()
        return page

    def test_recovery_retains_candidate_and_starts_once(self):
        page = self.page()
        candidate = SimpleNamespace(index=0, generation=0)
        with patch.object(time, 'monotonic', return_value=0):
            page._start_automatic_candidate(candidate)
        self.assertIs(page._ga_wait_candidate, candidate)
        page._present_candidate.assert_not_called()
        page._beam_window_snapshot.return_value = dict(valid=True, ready=True, fresh=True, value_nA=11)
        page._present_candidate.side_effect = None
        page._reset_ga_live_response = Mock(side_effect=RuntimeError('start boundary'))
        with patch.object(time, 'monotonic', return_value=3):
            with self.assertRaisesRegex(RuntimeError, 'start boundary'):
                page._ga_sequence_tick()
            page._ga_sequence_tick()
        page._present_candidate.assert_called_once_with(candidate)
        self.assertIsNone(page._ga_wait_candidate)

    def test_deadline_and_invalid_contract(self):
        for snapshot in [dict(valid=True, ready=True, fresh=False, value_nA=11),
                         dict(valid=True, ready=False, fresh=True, value_nA=11),
                         dict(valid=True, ready=True, fresh=True, value_nA=math.inf)]:
            page = self.page()
            page._beam_window_snapshot.return_value = snapshot
            with patch.object(time, 'monotonic', return_value=0):
                page._start_automatic_candidate(SimpleNamespace(index=0, generation=0))
            with patch.object(time, 'monotonic', return_value=3):
                page._ga_sequence_tick()
            self.assertEqual(page._ga_wait_deadline, 10)
            page._beam_window_snapshot.return_value = dict(valid=True, ready=True, fresh=True, value_nA=11)
            with patch.object(time, 'monotonic', return_value=10):
                page._ga_sequence_tick()
            page._abort_automatic_ga.assert_called_once()
            page._present_candidate.assert_not_called()

    def test_authorization_loss_and_stale_tick(self):
        for fault in ['auto', 'output', 'callback', 'power', 'enable', 'owner']:
            page = self.page()
            with patch.object(time, 'monotonic', return_value=0):
                page._start_automatic_candidate(SimpleNamespace(index=0, generation=0))
            if fault == 'auto': page.auto_ga_arm_check.isChecked.return_value = False
            if fault == 'output': page.arm_output_check.isChecked.return_value = False
            if fault == 'callback': page.output_callback = None
            if fault == 'power': page.context.power_states = [False]
            if fault == 'enable': page.context.enable_states = [False]
            if fault == 'owner': page.context.can_write.return_value = False
            page._ga_sequence_tick()
            page._abort_automatic_ga.assert_called_once()
            page._ga_auto_active = False
            page._ga_sequence_tick()
            page._present_candidate.assert_not_called()


if __name__ == '__main__':
    unittest.main()

