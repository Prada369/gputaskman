from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from gputaskman.fan_feed import read_fan_feed


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux Xe thermal watcher handoff')
class FanFeedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'thermal.jsonl'
        self.row = dict(at=datetime.fromtimestamp(1000, timezone.utc).isoformat(), boot_id='boot-a',
                        gpu_fan_rpm=1700, cpu_fan_rpm=3000, sources={'gpu':'/sys/class/hwmon/hwmon8'})

    def write(self, row=None, suffix=''):
        self.path.write_text(json.dumps(self.row if row is None else row) + '\n' + suffix)
        self.path.chmod(0o600)

    def read(self, **kwargs):
        options = dict(path=self.path, now=1002, boot_id='boot-a', resolve_pci=lambda _path:'0000:04:00.0')
        options.update(kwargs)
        return read_fan_feed('0000:04:00.0', **options)

    def test_fresh_sample_selects_gpu_not_cpu(self):
        self.write()
        self.assertEqual(self.read()['rpm'], 1700)
        self.assertIn('fan1_input', self.read()['source'])

    def test_zero_is_not_replaced_with_previous_speed(self):
        self.row['gpu_fan_rpm'] = 0
        self.write()
        self.assertEqual(self.read()['rpm'], 0)

    def test_old_future_or_different_boot_are_rejected(self):
        self.write()
        self.assertIsNone(self.read(now=1007))
        self.assertIsNone(self.read(now=999))
        self.assertIsNone(self.read(boot_id='different'))

    def test_different_pci_and_bad_sensor_path_are_rejected(self):
        self.write()
        self.assertIsNone(self.read(resolve_pci=lambda _path:'0000:05:00.0'))
        self.row['sources']['gpu']='/tmp/sensor'
        self.write()
        self.assertIsNone(self.read())

    def test_untrusted_or_nonregular_files_are_rejected(self):
        self.write();self.path.chmod(0o666)
        self.assertIsNone(self.read())
        self.path.unlink();os.symlink('missing',self.path)
        self.assertIsNone(self.read())
        self.path.unlink();os.mkfifo(self.path)
        self.assertIsNone(self.read())

    def test_partial_last_append_keeps_latest_complete_sample(self):
        self.write(suffix='{"at":')
        self.assertEqual(self.read()['rpm'], 1700)

    def test_native_reader_is_not_called_for_a_valid_shared_sample(self):
        import tempfile
        from gputaskman import drm
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            hwmon = Path(tmp)/'hwmon/hwmon0';hwmon.mkdir(parents=True)
            (hwmon/'name').write_text('xe')
            card = drm.DrmCard(key=1,pdev='0000:04:00.0',sysfs=tmp,driver='xe',vendor_id=0x8086,
                device_id=0xe222,name='Intel',intel=True,vram_total=32<<30,integrated=False)
            provider=Mock(query_error='',pmu_error='');provider.memory.return_value=(None,None)
            original=drm._int
            def read(path):
                self.assertFalse(str(path).endswith('/fan1_input'), 'duplicate tachometer read')
                return original(path)
            with patch('gputaskman.fan_feed.read_fan_feed',return_value={'rpm':1700,'source':'shared GPU tachometer'}), \
                 patch.object(drm,'_int',side_effect=read), \
                 patch.object(drm,'_xe_device',return_value=provider), \
                 patch.object(drm,'_intel_pmu_rates',return_value=(None,None)), \
                 patch.object(drm,'_xe_extra_telemetry'):
                result=drm.read_device(card,0)
            self.assertEqual(result.fan_rpm,1700)
            self.assertEqual(result.metric_sources['fan_rpm'],'shared GPU tachometer')


if __name__ == '__main__': unittest.main()
