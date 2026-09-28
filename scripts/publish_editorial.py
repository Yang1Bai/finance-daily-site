"""Re-render the latest digest with data/editorial/YYYY-MM-DD.json, without API calls."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
path = root / 'data/latest.json'
data = json.loads(path.read_text(encoding='utf-8'))
stamp = datetime.fromisoformat(data['fetched_at'].replace('Z', '+00:00'))
if stamp.date() != datetime.now(timezone.utc).date():
    raise SystemExit('Refresh public sources first; refusing to publish old data as today')
env = dict(os.environ, CONTENT_MODE='public', CONTENT_INPUT=str(path))
subprocess.run([sys.executable, str(root/'scripts/fetch_content.py')], env=env, check=True)
