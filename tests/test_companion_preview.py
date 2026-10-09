import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import companion_preview as preview

class PreviewTests(unittest.TestCase):
    def test_clock_translation_preserves_age(self):
        source = {'observed_at': 990, 'activity': 'idle'}
        converted = preview.device_snapshot(source, 1000, 500)
        self.assertEqual(converted['observed_at'], 490)
        self.assertEqual(source['observed_at'], 990)
        self.assertEqual(converted['activity'], 'idle')

    def test_stale_and_future_snapshots_rejected(self):
        for timestamp in (1001, -14001):
            with self.assertRaises(ValueError):
                preview.device_snapshot({'observed_at': timestamp}, 1000, 500)

    def test_cdp_origin_is_loopback_only(self):
        self.assertEqual(preview.validate_cdp_origin('http://localhost:19222'), 'http://localhost:19222')
        for origin in ('https://gateway.example', 'http://localhost:19222/path', 'http://localhost:19222?query=1'):
            with self.assertRaises(ValueError):
                preview.validate_cdp_origin(origin)
