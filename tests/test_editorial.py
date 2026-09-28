import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from editorial import apply_editorial


class EditorialTests(unittest.TestCase):
    def test_source_link_preserved_and_prices_untouched(self):
        data = {'fetched_at':'2026-09-28T12:00:00+00:00', 'news':[{'url':'https://example.org/a','title':'Original'}], 'indices':[{'value':'100'}]}
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'data/editorial'; folder.mkdir(parents=True)
            (folder/'2026-09-28.json').write_text(json.dumps({'date':'2026-09-28','news':[{'url':'https://example.org/a','title':'中文标题'},{'url':'https://fake.org','title':'Unsupported'}]}),encoding='utf-8')
            result=apply_editorial(data,tmp)
        self.assertEqual(result['news'][0]['title'],'中文标题')
        self.assertEqual(result['news'][0]['title_original'],'Original')
        self.assertEqual(len(result['news']),1)
        self.assertEqual(result['indices'],data['indices'])
        self.assertEqual(data['news'][0]['title'],'Original')
