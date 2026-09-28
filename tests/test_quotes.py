import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from fetch_public import quote_from_chart
from fetch_content import inject_into_html, render_news


class QuoteTests(unittest.TestCase):
    def test_verified_price_and_timestamp(self):
        now = datetime.now(timezone.utc)
        payload = {'chart': {'result': [{'meta': {'regularMarketPrice': 110, 'chartPreviousClose': 100, 'regularMarketTime': now.timestamp()}}]}}
        row = quote_from_chart(payload, 'Example', 'EX', now)
        self.assertEqual(row['change_pct'], '+10.00%')
        payload['chart']['result'][0]['meta']['regularMarketTime'] = (now - timedelta(days=10)).timestamp()
        with self.assertRaises(ValueError):
            quote_from_chart(payload, 'Example', 'EX', now)

    def test_missing_section_never_displays_old_advice(self):
        html = '<html><head></head><!-- WATCHLIST_ANALYSIS:START -->old advice<!-- WATCHLIST_ANALYSIS:END --></html>'
        result = inject_into_html(html, {'content_mode': 'public'})
        self.assertNotIn('old advice', result)

    def test_feed_text_is_escaped(self):
        result = render_news([{'title': '<script>alert(1)</script>', 'body': '<img src=x>', 'url': 'https://example.org/?a=1&b=2'}])
        self.assertNotIn('<script>', result)
        self.assertNotIn('<img', result)
