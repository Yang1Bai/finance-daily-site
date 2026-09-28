"""Observable market prices and official releases, without generated advice."""
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from public_sources import collect, download

SYMBOLS = {"S&P 500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI", "上证指数": "000001.SS",
           "恒生指数": "^HSI", "日经225": "^N225", "德国DAX": "^GDAXI", "黄金期货": "GC=F",
           "原油 WTI": "CL=F", "BTC/USD": "BTC-USD", "美元指数": "DX-Y.NYB", "USD/CNH": "CNH=X"}
FEEDS = [("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
         ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
         ("ECB", "https://www.ecb.europa.eu/rss/press.html")]


def quote_from_chart(payload, name, symbol, now=None):
    now = now or datetime.now(timezone.utc)
    meta = payload["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    previous = float(meta.get("chartPreviousClose") or meta["previousClose"])
    stamp = datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)
    if not all(math.isfinite(x) and x > 0 for x in (price, previous)):
        raise ValueError("Invalid price")
    if not now - timedelta(days=7) <= stamp <= now + timedelta(minutes=5):
        raise ValueError("Stale or future quote")
    delta = price - previous
    return {"name": name, "symbol": symbol, "value": f"{price:,.4f}" if "CNH" in symbol else f"{price:,.2f}",
            "change": f"{delta:+.2f}", "change_pct": f"{delta / previous * 100:+.2f}%",
            "direction": "up" if delta > 0 else "down" if delta < 0 else "neutral",
            "as_of": stamp.isoformat(), "currency": meta.get("currency"), "source": "Yahoo Finance",
            "url": f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/"}


def fetch_quote(pair):
    name, symbol = pair
    for host in ("query1", "query2"):
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?interval=1d&range=1d"
            return quote_from_chart(json.loads(download(url)), name, symbol)
        except Exception as exc:
            print(f"[quotes] {symbol} {host}: {type(exc).__name__}", flush=True)
    return None


def fetch_public_data():
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(fetch_quote, SYMBOLS.items()))
    indices = [row for row in rows if row]
    news, sources = collect(FEEDS, days=7)
    if not indices and not news:
        raise RuntimeError("No fresh prices or releases; preserving the last published dashboard")
    missing = [name for (name, _), row in zip(SYMBOLS.items(), rows) if row is None]
    for name in missing:
        print(f"::warning::Quote unavailable: {name}")
    return {"date": f"{now.year}年{now.month}月{now.day}日", "content_mode": "public",
            "fetched_at": now.isoformat(), "sources": sources, "missing_quotes": missing,
            "indices": indices, "news": news[:15],
            "summary": {"headline": "公开来源行情与机构公告", "sentiment": "neutral", "vix": "N/A",
                        "context": "价格来自 Yahoo Finance，可能延迟；每个标的显示实际报价时间。公告保留原文，不生成交易建议。",
                        "key_points": [f"已获取 {len(indices)} 个标的", f"近七日 {len(news)} 条机构公告",
                                       "未核验栏目显示暂无更新"]}}
