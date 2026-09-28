import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from public_sources import parse_feed, collect
import fetch_public


class PublicSourceTests(unittest.TestCase):
    def test_rss_filters_stale_future_undated_and_unsafe_urls(self):
        rss = b'''<rss><channel>
        <item><title>Fresh &amp; real</title><link>https://example.org/a</link><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;source text&lt;/b&gt;</description></item>
        <item><title>Old</title><link>https://example.org/b</link><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
        <item><title>Future</title><link>https://example.org/c</link><pubDate>Mon, 28 Sep 2027 10:00:00 GMT</pubDate></item>
        <item><title>Undated</title><link>https://example.org/d</link></item>
        <item><title>Unsafe</title><link>javascript:alert(1)</link><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>'''
        rows = parse_feed(rss, 'Example', datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'Fresh & real')
        self.assertEqual(rows[0]['body'], 'source text')

    def test_atom_links_and_dates(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Research</title><link href="https://example.org/paper"/><published>2026-09-28T10:00:00Z</published><summary>Abstract</summary></entry></feed>'''
        rows = parse_feed(atom, 'Example', datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        self.assertEqual(rows[0]['url'], 'https://example.org/paper')

    def test_source_outage_is_visible(self):
        with patch('public_sources.download', side_effect=OSError('offline')):
            rows, status = collect([('Example', 'https://example.org')])
        self.assertEqual(rows, [])
        self.assertEqual(status[0]['status'], 'unavailable')

    def test_total_outage_does_not_publish(self):
        with patch.object(fetch_public, 'collect', return_value=([], [])):
            if hasattr(fetch_public, 'fetch_quote'):
                with patch.object(fetch_public, 'fetch_quote', return_value=None):
                    with self.assertRaises(RuntimeError):
                        fetch_public.fetch_public_data()
            else:
                with self.assertRaises(RuntimeError):
                    fetch_public.fetch_public_data(datetime.now(timezone.utc))
