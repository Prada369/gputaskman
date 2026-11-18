"""Fan control checks use fake sensor files; these never write to hardware."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gputaskman.widget_controls import fan_command

if sys.platform.startswith('linux'):
    loader = importlib.machinery.SourceFileLoader('vram_target', str(Path(__file__).resolve().parents[1] / 'tools/gputaskman-vram-fan-target'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    helper = importlib.util.module_from_spec(spec)
    loader.exec_module(helper)


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux Xe fan helper')
class TargetTests(unittest.TestCase):
    def test_temperature_and_package_safety_raise_pwm_immediately(self):
        self.assertEqual(helper.pwm_demand(80, 60, 80, 60, 120), 255)
        self.assertEqual(helper.pwm_demand(60, 91, 80, 60, 120), 255)
        self.assertEqual(helper.pwm_demand(60, 60, 80, 60, 255), 250)

    def test_complete_original_curve_is_restored_including_commit_point(self):
        with tempfile.TemporaryDirectory() as directory:
            hwmon = Path(directory)
            (hwmon / 'pwm1_enable').write_text('1')
            for i, duty in enumerate([120, 180, 220, 255], 1):
                (hwmon / f'pwm1_auto_point{i}_temp').write_text(str(30000 + i * 10000))
                (hwmon / f'pwm1_auto_point{i}_pwm').write_text(str(duty))
            saved = helper.snapshot_curve(hwmon)
            for _temp, _value, pwm, _duty in saved:
                pwm.write_text('200')
            helper.restore(hwmon, 1, saved)
            self.assertEqual([int(pwm.read_text()) for _, _, pwm, _ in saved], [120, 180, 220, 255])
            helper.restore(hwmon, 2, [])
            self.assertEqual((hwmon / 'pwm1_enable').read_text(), '2')

    def test_widget_disconnect_stops_the_controller(self):
        with patch.object(helper.select, 'select', return_value=([0], [], [])), \
             patch.object(helper.os, 'read', return_value=b''):
            self.assertIsNone(helper.Lease().poll(0, 80, 60))

    def test_live_target_updates_and_lease_timeout(self):
        lease = helper.Lease()
        with patch.object(helper.select, 'select', return_value=([0], [], [])), \
             patch.object(helper.os, 'read', return_value=b'target 78 65\n'):
            self.assertEqual(lease.poll(0, 80, 60), (78, 65))
        with patch.object(helper.select, 'select', return_value=([], [], [])), \
             patch.object(helper.time, 'monotonic', return_value=lease.last + 9):
            with self.assertRaisesRegex(ValueError, 'stopped responding'):
                lease.poll(0, 78, 65)

    def test_guard_reduces_power_only_when_hot_at_full_fan_and_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            freq = root/'tile0/gt0/freq0'; freq.mkdir(parents=True)
            hwmon = root/'hwmon';hwmon.mkdir()
            for name,value in [('rpn_freq',400),('rp0_freq',2400),('min_freq',400),('max_freq',2400)]:
                (freq/name).write_text(str(value))
            for name,value in [('vendor','0x8086'),('device','0xe222'),('subsystem_vendor','0x1849'),('subsystem_device','0x6027')]:
                (root/name).write_text(value)
            power = hwmon/'power1_cap';power.write_text('200000000')
            args = SimpleNamespace(clock_min=400,clock_max=2000,power_profile=None,power_w=160,guard_power_floor_w=120)
            tuning = helper.TuningSession(root, hwmon, args)
            tuning.apply()
            self.assertEqual(tuning.guard(85,80,200,20),160)
            self.assertEqual(tuning.guard(85,80,255,24),155)
            self.assertEqual(tuning.guard(70,80,150,30),155)
            self.assertEqual(tuning.guard(70,80,150,46),160)
            tuning.restore()
            self.assertEqual(int(power.read_text()),200000000)
            self.assertEqual(int((freq/'max_freq').read_text()),2400)

    def test_invalid_commands_and_unsupported_devices_never_launch_helper(self):
        state = {'supported': True, 'target_supported': True, 'pci': '0000:04:00.0'}
        command = fan_command(state, 'vram-target', target=77, floor=65)
        self.assertEqual(command[1:4], ['/usr/local/bin/gputaskman-vram-fan-target', '--pci', '0000:04:00.0'])
        for target in (64, 96, float('nan'), True):
            with self.assertRaises(ValueError):
                fan_command(state, 'vram-target', target=target)
        with self.assertRaises(ValueError):
            fan_command(dict(state, target_supported=False), 'performance')


if __name__ == '__main__':
    unittest.main()
