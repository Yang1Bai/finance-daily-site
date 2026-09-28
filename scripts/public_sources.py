"""Small, key-free RSS/Atom reader. Dates and source URLs remain inspectable."""
import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime


def download(url):
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (daily-source-reader; github.com/Yang1Bai)"})
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read(4_000_000)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def plain(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value or ""))).strip()


def parse_feed(raw, source, now=None, days=7):
    now = now or datetime.now(timezone.utc)
    rows = []
    for item in ET.fromstring(raw).iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        fields = {}
        link = ""
        for child in item:
            key = child.tag.split("}")[-1]
            fields[key] = "".join(child.itertext()).strip()
            if key == "link" and child.attrib.get("rel", "alternate") == "alternate":
                link = child.attrib.get("href") or fields[key]
        stamp = fields.get("pubDate") or fields.get("published") or fields.get("date") or fields.get("updated")
        try:
            try:
                published = parsedate_to_datetime(stamp)
            except (ValueError, TypeError):
                published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, AttributeError):
            continue
        if not now - timedelta(days=days) <= published <= now + timedelta(hours=1):
            continue
        if not link.startswith(("https://", "http://")) or not fields.get("title"):
            continue
        rows.append({"title": plain(fields["title"]), "url": link,
                     "body": plain(fields.get("description") or fields.get("summary") or fields.get("content"))[:700],
                     "date": published.date().isoformat(), "published_at": published.isoformat(),
                     "source": source, "tags": [source], "importance": "normal"})
    return rows


def collect(feeds, days=7):
    items, status = [], []
    for name, url in feeds:
        try:
            rows = parse_feed(download(url), name, days=days)
            items.extend(rows)
            status.append({"source": name, "url": url, "status": "ok", "items": len(rows)})
            print(f"[source] {name}: {len(rows)} recent items", flush=True)
        except Exception as exc:
            status.append({"source": name, "url": url, "status": "unavailable", "error": type(exc).__name__})
            print(f"::warning::{name}: {type(exc).__name__}", flush=True)
    unique = {item["url"]: item for item in items}
    return sorted(unique.values(), key=lambda x: x["published_at"], reverse=True), status
