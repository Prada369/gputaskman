"""Persisted per-device alarms and fan commands, without hardware writes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gputaskman.widget_controls import Preferences, alarm_state, fan_command, fan_state, tuning_command, tuning_state, validate_setting, vram_panel_label


class AlarmTests(unittest.TestCase):
    def test_panel_bar_preserves_unknown_zero_and_out_of_range_readings(self):
        self.assertEqual(vram_panel_label(None, 100), 'VRAM unavailable')
        self.assertEqual(vram_panel_label(5, 0), 'VRAM unavailable')
        self.assertEqual(vram_panel_label(0, 100), 'VRAM ▱▱▱▱▱▱▱▱ 0%')
        self.assertEqual(vram_panel_label(50, 100), 'VRAM ▰▰▰▰▱▱▱▱ 50%')
        self.assertEqual(vram_panel_label(120, 100), 'VRAM ▰▰▰▰▰▰▰▰ 120%')
        self.assertTrue(vram_panel_label(20, 100, estimated=True).startswith('VRAM~'))

    def test_alarm_unknown_never_becomes_a_low_reading(self):
        setting = validate_setting('fan_rpm', {'alarm_enabled': True, 'threshold': 500, 'direction': 'below'})
        self.assertEqual(alarm_state(None, setting), 'unknown')
        self.assertEqual(alarm_state(float('nan'), setting), 'unknown')
        self.assertEqual(alarm_state(0, setting), 'alarm')
        self.assertEqual(alarm_state(501, setting), 'normal')

    def test_high_alarm_includes_threshold_and_can_be_disabled(self):
        setting = validate_setting('temp', {'alarm_enabled': True, 'threshold': 85})
        self.assertEqual(alarm_state(84, setting), 'normal')
        self.assertEqual(alarm_state(85, setting), 'alarm')
        setting['alarm_enabled'] = False
        self.assertEqual(alarm_state(100, setting), 'off')

    def test_invalid_colors_and_thresholds_cannot_enter_stylesheets(self):
        for setting in ({'color': 'red; background: red'}, {'color': '#zzzzzz'},
                        {'alarm_color': None}, {'threshold': float('inf')}, {'threshold': -1},
                        {'threshold': True}, {'direction': 'elsewhere'}, {'alarm_enabled': 'false'}):
            with self.subTest(setting=setting), self.assertRaises((ValueError, TypeError)):
                validate_setting('temp', setting)

    def test_preferences_survive_restart_and_are_per_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested/widget.json'
            prefs = Preferences(path)
            prefs.set('intel-a', 'temp', {'color': '#123456', 'alarm_enabled': True, 'threshold': 82})
            reloaded = Preferences(path)
            self.assertEqual(reloaded.get('intel-a', 'temp')['color'], '#123456')
            self.assertTrue(reloaded.get('intel-a', 'temp')['alarm_enabled'])
            self.assertFalse(reloaded.get('intel-b', 'temp')['alarm_enabled'])
            self.assertIsNone(reloaded.get('intel-b', 'temp')['color'])

    def test_failed_save_preserves_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs = Preferences(Path(directory) / 'widget.json')
            prefs.set('a', 'temp', {'threshold': 80})
            with patch('gputaskman.widget_controls.os.replace', side_effect=OSError('no write')):
                with self.assertRaises(OSError):
                    prefs.set('a', 'temp', {'threshold': 99})
            self.assertEqual(prefs.get('a', 'temp')['threshold'], 80)
            self.assertEqual(Preferences(prefs.path).get('a', 'temp')['threshold'], 80)


class TuningTests(unittest.TestCase):
    state = dict(supported=True, pci='0000:04:00.0', clock_low=400, clock_high=2400,
                 clock_min=400, clock_max=2400, power_w=200, profiles=['base','power_saving'])

    def test_selected_gpu_and_supported_values_only(self):
        command = tuning_command(self.state, clock_min=400, clock_max=2000, power_w=160, profile='power_saving')
        self.assertEqual(command[:6], ['/usr/bin/pkexec','/usr/bin/env','ARC_GPU_BDF=0000:04:00.0',
                                      '/usr/local/bin/xe-gpu-tune','set','--clk-max'])
        self.assertIn('160', command)
        self.assertIn('power_saving', command)

    def test_invalid_tuning_cannot_build_commands(self):
        for values in ({'clock_min': 300, 'clock_max': 2400}, {'clock_min': 2300, 'clock_max': 2000},
                       {'clock_min': 400, 'clock_max': 2500}, {'clock_min': 400.5, 'clock_max': 2400},
                       {'clock_min': 400, 'clock_max': 2400, 'power_w': 201},
                       {'clock_min': 400, 'clock_max': 2400, 'profile': 'invalid'}):
            with self.subTest(values=values), self.assertRaises(ValueError): tuning_command(self.state, **values)

    def test_lowering_ceiling_below_old_floor_lowers_floor_first(self):
        state = dict(self.state, clock_min=2000)
        command = tuning_command(state, clock_min=400, clock_max=1500)
        self.assertLess(command.index('--clk-min'), command.index('--clk-max'))

    def test_power_only_change_does_not_rewrite_clock_requests_or_profile(self):
        state = dict(self.state, profile='base')
        command = tuning_command(state, clock_min=400, clock_max=2400, power_w=190, profile='base')
        self.assertEqual(command[5:], ['--power-w', '190'])

    def test_unchanged_settings_need_no_privileged_command(self):
        state = dict(self.state, profile='base')
        self.assertEqual(tuning_command(state, clock_min=400, clock_max=2400,
                                       power_w=200, profile='base'), [])

    def test_unchanged_invalid_power_still_cannot_bypass_validation(self):
        state = dict(self.state, power_w=250)
        with self.assertRaises(ValueError):
            tuning_command(state, clock_min=400, clock_max=2400, power_w=250, power_ceiling=200)

    def test_read_only_tuning_discovery_reads_only_matched_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); freq = root / self.state['pci'] / 'tile0/gt0/freq0'
            freq.mkdir(parents=True)
            for key, value in {'rpn_freq':400,'rp0_freq':2400,'min_freq':400,'max_freq':2400}.items():
                (freq/key).write_text(str(value))
            (freq/'power_profile').write_text('[base] power_saving\n')
            hwmon = root / self.state['pci'] / 'hwmon/hwmon8';hwmon.mkdir(parents=True)
            (hwmon/'name').write_text('xe\n');(hwmon/'power1_cap').write_text('200000000\n')
            state = tuning_state(self.state['pci'], sysfs=root)
            self.assertTrue(state['supported'])
            self.assertEqual((state['clock_min'],state['clock_max'],state['power_w']), (400,2400,200))
            self.assertEqual(state['profile'], 'base')
            self.assertFalse(tuning_state('0000:05:00.0', sysfs=root)['supported'])


class FanTests(unittest.TestCase):
    state = {'supported': True, 'pci': '0000:04:00.0'}

    def test_unsupported_driver_never_builds_a_write_command(self):
        with self.assertRaises(ValueError):
            fan_command({'supported': False}, 'custom', 50)

    def test_command_always_targets_explicit_selected_pci(self):
        command = fan_command(self.state, 'automatic')
        self.assertEqual(command, ['/usr/bin/pkexec', '/usr/bin/env', 'ARC_GPU_BDF=0000:04:00.0',
                                   '/usr/local/bin/xe-fan-curve', 'auto'])
        with self.assertRaises(ValueError):
            fan_command({'supported': True, 'pci': '0000:04:00.0; invalid'}, 'full')

    def test_custom_floor_keeps_monotonic_thermal_ramp_and_full_speed_at_85(self):
        for percent in (30, 50, 100):
            command = fan_command(self.state, 'custom', percent)
            points = [tuple(map(int, pair.split(':'))) for pair in command[command.index('set')+1:]]
            self.assertEqual(points[-1], (85, 255))
            self.assertEqual([p[1] for p in points], sorted(p[1] for p in points))
            self.assertEqual(points[0][1], round(percent*255/100))
        for percent in (0, 29, 101, float('nan')):
            with self.assertRaises(ValueError): fan_command(self.state, 'custom', percent)

    def test_read_only_driver_is_distinct_from_adjustable_driver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwmon = root / self.state['pci'] / 'hwmon/hwmon8'
            hwmon.mkdir(parents=True)
            (hwmon/'name').write_text('xe\n')
            (hwmon/'fan1_input').write_text('1200\n')
            result = fan_state(self.state['pci'], sysfs=root)
            self.assertFalse(result['supported'])
            self.assertIn('fan RPM', result['reason'])
            (hwmon/'pwm1_enable').write_text('2\n')
            for index in range(1, 5):
                (hwmon/f'pwm1_auto_point{index}_pwm').write_text('128\n')
            result = fan_state(self.state['pci'], sysfs=root)
            self.assertTrue(result['supported'])
            self.assertEqual(result['mode_name'], 'Automatic')


if __name__ == '__main__':
    unittest.main()
