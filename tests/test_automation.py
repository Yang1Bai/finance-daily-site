import importlib.util
import json
from pathlib import Path
import re
import unittest
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('podcast', ROOT/'scripts/generate_podcast.py')
podcast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(podcast)


class AutomationTests(unittest.TestCase):
    def test_podcast_rejects_old_digest(self):
        data = {'fetched_at': (datetime.now(timezone.utc)-timedelta(days=3)).isoformat(), 'date': 'old'}
        with self.assertRaises(ValueError):
            podcast.build_script(data)

    def test_podcast_uses_source_not_model_advice(self):
        data = {'fetched_at': datetime.now(timezone.utc).isoformat(), 'date': 'today',
                'news': [{'title': 'Verified title', 'date': 'today', 'source': 'SEC'}],
                'watchlist_analysis': [{'conclusion': 'BUY EVERYTHING'}]}
        script = podcast.build_script(data)
        self.assertIn('Verified title', script)
        self.assertNotIn('BUY EVERYTHING', script)

    def test_no_embedded_telegram_credentials(self):
        pattern = re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b')
        for path in (ROOT/'scripts').glob('*.py'):
            self.assertIsNone(pattern.search(path.read_text(encoding='utf-8')), path.name)
