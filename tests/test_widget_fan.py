"""Fan display distinguishes driver reports from history; never manufactures RPM."""
import unittest

from gputaskman.widget import FanReadings


class FanReadingTests(unittest.TestCase):
    def test_xe_zero_stays_raw_zero_and_history_is_explicitly_old(self):
        tracker = FanReadings()
        tracker.record('gpu-a', {'fan_rpm': 1700}, 10)
        device = {'fan_rpm': 0, 'source': 'Intel sysfs', 'driver_model': 'xe'}
        tracker.record('gpu-a', device, 12)
        text, note = tracker.present('gpu-a', device, 14)
        self.assertEqual(text, '0 reported')
        self.assertEqual(device['fan_rpm'], 0)
        self.assertIn('1,700 rpm, 4s ago (historical)', note)
        self.assertIn('does not verify that the fan stopped', note)

    def test_no_previous_sample_is_not_replaced_with_a_guess(self):
        text, note = FanReadings().present('gpu-a', {'fan_rpm': 0, 'driver_model': 'xe'}, 10)
        self.assertEqual(text, '0 reported')
        self.assertNotIn('Last non-zero', note)

    def test_recent_sample_display_is_aged_bounded_and_keeps_raw_zero(self):
        tracker = FanReadings()
        tracker.record('gpu-a', {'fan_rpm': 1700}, 10)
        raw = {'fan_rpm': 0, 'driver_model': 'xe'}
        text, note = tracker.present('gpu-a', raw, 14, keep_recent=True)
        self.assertEqual(text, '1,700 rpm · 4s old')
        self.assertEqual(raw['fan_rpm'], 0)
        self.assertIn('driver reported 0 RPM', note)
        self.assertEqual(tracker.present('gpu-a', raw, 19, keep_recent=True)[0], '0 reported')

    def test_history_from_another_gpu_is_never_used(self):
        tracker = FanReadings()
        tracker.record('gpu-a', {'fan_rpm': 1700}, 10)
        _, note = tracker.present('gpu-b', {'fan_rpm': 0, 'driver_model': 'xe'}, 12)
        self.assertNotIn('1,700', note)

    def test_missing_value_stays_unknown_not_last_known(self):
        tracker = FanReadings()
        tracker.record('gpu-a', {'fan_rpm': 1700}, 10)
        text, note = tracker.present('gpu-a', {'fan_rpm': None, 'driver_model': 'xe'}, 12)
        self.assertEqual(text, 'n/a')
        self.assertEqual(note, '')

    def test_other_driver_zero_and_nonzero_are_not_reinterpreted(self):
        tracker = FanReadings()
        self.assertEqual(tracker.present('amd', {'fan_rpm': 0, 'source': 'AMD sysfs'}, 10), ('0 rpm', ''))
        self.assertEqual(tracker.present('intel', {'fan_rpm': 1700, 'driver_model': 'xe'}, 10), ('1,700 rpm', ''))


if __name__ == '__main__':
    unittest.main()
