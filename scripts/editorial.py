"""Apply dated human/assistant edits only to source items in today's digest."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path


def apply_editorial(data, root):
    stamp = datetime.fromisoformat(data['fetched_at'].replace('Z', '+00:00'))
    day = stamp.date().isoformat()
    path = Path(root) / 'data/editorial' / f'{day}.json'
    if not path.exists():
        return data
    edits = json.loads(path.read_text(encoding='utf-8'))
    if edits.get('date') != day:
        raise ValueError('Editorial date does not match source digest')
    result = copy.deepcopy(data)
    count = 0
    for section in ('news', 'papers'):
        by_url = {row['url']: row for row in result.get(section, [])}
        for edit in edits.get(section, []):
            row = by_url.get(edit.get('url'))
            if row is None:
                # A newer public feed may have dropped this item; do not reintroduce it.
                continue
            for key in ('title', 'body' if section == 'news' else 'summary'):
                value = edit.get(key)
                if value is None:
                    continue
                if not isinstance(value, str) or not value.strip() or len(value) > 2000 or '<' in value:
                    raise ValueError(f'Invalid editorial field: {section}.{key}')
                row.setdefault(key + '_original', row.get(key, ''))
                row[key] = value
            row['editorial_date'] = day
            count += 1
    result['editorial'] = {'date': day, 'items_applied': count, 'editor': 'Codex', 'mode': 'source_grounded_chinese'}
    return result
