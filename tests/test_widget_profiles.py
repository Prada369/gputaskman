import tempfile
from pathlib import Path
import unittest

from gputaskman.widget_profiles import ProfileStore, SessionHistory, WorkloadGate, presets, validate_profile


class ProfileTests(unittest.TestCase):
    state = dict(pci='0000:04:00.0', clock_low=400, clock_high=2400, power_w=200,
                 profiles=['base', 'power_saving'])

    def test_presets_stay_within_hardware_range_and_custom_is_gpu_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory)/'profiles.json')
            for profile in presets(self.state).values():
                validate_profile(profile, self.state)
            chosen = presets(self.state)['Efficiency']
            store.save(self.state['pci'], chosen, self.state)
            restored = ProfileStore(store.path)
            self.assertEqual(restored.get(self.state['pci'], self.state)['power_w'], 160)
            self.assertIsNone(restored.get('0000:05:00.0', self.state))
            with self.assertRaises(ValueError):
                validate_profile(dict(chosen, power_w=201), self.state)

    def test_workload_gate_needs_load_and_restores_after_idle(self):
        gate = WorkloadGate()
        resident = {'processes': [{'vram_bytes': 10000000000, 'gpu_pct': None}]}
        self.assertFalse(gate.active(resident, 10))
        self.assertTrue(gate.active({'device': {'util_gpu': 50}}, 12))
        self.assertTrue(gate.active(resident, 20))
        self.assertFalse(gate.active(resident, 28))

    def test_unknown_values_never_enter_history_as_zero(self):
        history = SessionHistory()
        history.record({'taken': 10, 'device': {'power_w': None, 'sm_clock': 0}})
        self.assertNotIn('power_w', history.summary())
        self.assertEqual(history.summary()['sm_clock']['mean'], 0)


if __name__ == '__main__':
    unittest.main()
