"""Refresh the live quote cache using the same dated source as the daily digest."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from fetch_public import SYMBOLS, fetch_quote


def main():
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for row in pool.map(fetch_quote, SYMBOLS.items()) if row]
    if not rows:
        raise RuntimeError("No verified quotes; keeping previous cache")
    target = Path(__file__).resolve().parents[1] / 'data/quotes.json'
    target.write_text(json.dumps({'updated': datetime.now(timezone.utc).isoformat(),
                                 'source': 'Yahoo Finance', 'quotes': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Updated {len(rows)} quotes, with per-symbol observation times")


if __name__ == '__main__':
    main()
