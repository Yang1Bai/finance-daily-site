#!/usr/bin/env python3
"""
Signal Tracker — AI Watchlist 历史信号绩效追踪器
Reads historical JSON files from data/, builds a running ledger of
BUY/SELL/HOLD signals, evaluates outcomes with REAL yfinance prices,
and writes signal_ledger.json.

Signal lifecycle:
  - Open:    BUY first issued, still within 10 trading days, no exit triggered
  - Win:     price rose +15% from entry  → WIN
  - Loss:    price dropped -7% from entry → LOSS
  - Expired: still open after 10 trading days → closed at last known price

Run: python scripts/signal_tracker.py
"""

import json
import os
import datetime
import warnings
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).resolve().parent.parent
DATA_DIR    = ROOT_DIR / "data"
OUTPUT_FILE = DATA_DIR / "signal_ledger.json"

STOP_LOSS_PCT   = -0.07   # -7%
TAKE_PROFIT_PCT =  0.15   # +15%
MAX_HOLD_DAYS   = 10      # trading days before expiry

# Bucket map for sector correlation guard
BUCKET_MAP = {
    "NVDA":      "tech",
    "AMD":       "tech",
    "QQQ":       "tech",
    "SPY":       "index",
    "510300.SS": "china",
    "GC=F":      "gold",
    "CL=F":      "oil",
    "BTC-USD":   "crypto",
    "NOK":       "telecom",
    "TSLA":      "ev",
}

SPY_TICKER = "SPY"


# ─── Data File Loading ────────────────────────────────────────────────────────

def load_data_files() -> list[tuple[str, dict]]:
    """Load all dated JSON files from data/, sorted by date."""
    files = []
    skip = {"latest.json", "index.json", "signal_ledger.json",
            "quotes.json", "ai-portfolio.json",
            "investment-subscribers.json", "telegram-offset.json"}
    for fname in sorted(os.listdir(DATA_DIR)):
        if fname in skip or not fname.endswith(".json"):
            continue
        stem = fname.replace(".json", "")
        try:
            datetime.date.fromisoformat(stem)
        except ValueError:
            continue
        try:
            with open(DATA_DIR / fname, encoding="utf-8") as f:
                data = json.load(f)
            files.append((stem, data))
        except Exception:
            pass
    return files


def parse_price(raw) -> float | None:
    """Parse a price value that might be float, int, or string like '875.40' or '63,000'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def get_all_tickers(files: list[tuple[str, dict]]) -> list[str]:
    """Return sorted list of unique tickers across all data files."""
    tickers: set[str] = set()
    for _, data in files:
        wa = data.get("watchlist_analysis", [])
        if not wa:
            continue
        if isinstance(wa, dict):
            wa = [wa]
        for item in wa:
            t = item.get("ticker", "").strip()
            if t:
                tickers.add(t)
    return sorted(tickers)


# ─── yfinance Real Prices ─────────────────────────────────────────────────────

def get_real_prices(tickers: list[str], start_date: str, end_date: str) -> dict[str, dict[str, float]]:
    """
    Fetch real historical closing prices from yfinance.

    Returns: {ticker: {date_str: close_price}}
    Skips tickers that fail; caller falls back to AI prices for those.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️  yfinance not installed — falling back to AI prices for all tickers")
        return {}

    # Pad start by 7 days so we can back-fill weekends near the boundary
    start_dt  = datetime.date.fromisoformat(start_date)
    fetch_start = (start_dt - datetime.timedelta(days=7)).isoformat()
    # Pad end by 2 days (today might be a partial/weekend day)
    end_dt    = datetime.date.fromisoformat(end_date)
    fetch_end = (end_dt + datetime.timedelta(days=2)).isoformat()

    price_data: dict[str, dict[str, float]] = {}

    for ticker in tickers:
        try:
            print(f"   📡 Fetching {ticker} ({fetch_start} → {fetch_end})…")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                hist = yf.Ticker(ticker).history(
                    start=fetch_start, end=fetch_end, auto_adjust=True
                )
            if hist.empty:
                print(f"   ⚠️  No data for {ticker} — will use AI prices")
                continue

            ticker_prices: dict[str, float] = {}
            for idx, row in hist.iterrows():
                # idx may carry timezone info; strip it for a plain date string
                date_str = idx.strftime("%Y-%m-%d")
                close_val = row.get("Close", None)
                if close_val is None:
                    close_val = row.iloc[0]
                try:
                    close_f = float(close_val)
                except (TypeError, ValueError):
                    continue
                import math
                if math.isnan(close_f) or close_f <= 0:
                    continue
                ticker_prices[date_str] = round(close_f, 4)

            if not ticker_prices:
                print(f"   ⚠️  All rows invalid for {ticker} — will use AI prices")
                continue

            price_data[ticker] = ticker_prices
            sorted_dates = sorted(ticker_prices)
            print(f"   ✅ {ticker}: {len(ticker_prices)} trading days "
                  f"({sorted_dates[0]} → {sorted_dates[-1]})")

        except Exception as exc:
            print(f"   ⚠️  Failed to fetch {ticker}: {exc} — will use AI prices")

    return price_data


def get_nearest_price(ticker_prices: dict[str, float], target_date: str,
                      direction: str = "backward") -> float | None:
    """
    Return the price on target_date, or the nearest available trading day.

    direction:
      "forward"  → next available date at or after target
      "backward" → last available date at or before target  (default)
      "nearest"  → whichever side is closer
    """
    if target_date in ticker_prices:
        return ticker_prices[target_date]

    sorted_dates = sorted(ticker_prices)
    if not sorted_dates:
        return None

    if direction == "forward":
        future = [d for d in sorted_dates if d >= target_date]
        return ticker_prices[future[0]] if future else ticker_prices[sorted_dates[-1]]

    if direction == "backward":
        past = [d for d in sorted_dates if d <= target_date]
        return ticker_prices[past[-1]] if past else ticker_prices[sorted_dates[0]]

    # "nearest"
    return ticker_prices[min(sorted_dates, key=lambda d: abs(
        (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(target_date)).days
    ))]


# ─── AI Price Fallback ────────────────────────────────────────────────────────

def build_ai_price_series(files: list[tuple[str, dict]]) -> dict[str, dict[str, float]]:
    """Build AI-price lookup from JSON files: {ticker: {date_str: price}}."""
    price_series: dict[str, dict[str, float]] = {}
    for date_str, data in files:
        wa = data.get("watchlist_analysis", [])
        if not wa:
            continue
        if isinstance(wa, dict):
            wa = [wa]
        for item in wa:
            ticker = item.get("ticker", "").strip()
            price  = parse_price(item.get("price"))
            if not ticker or price is None or price <= 0:
                continue
            price_series.setdefault(ticker, {})[date_str] = price
    return price_series


# ─── Ledger Building ──────────────────────────────────────────────────────────

def count_trading_days(start: str, end: str, all_dates: list[str]) -> int:
    """Count data-file dates in (start, end] range."""
    return sum(1 for d in all_dates if start < d <= end)


def build_ledger(files: list[tuple[str, dict]],
                 real_prices: dict[str, dict[str, float]],
                 ai_prices:   dict[str, dict[str, float]]) -> dict:
    """
    Core logic: build open/closed positions ledger and compute performance.
    Uses real yfinance prices where available; falls back to AI prices otherwise.
    """
    all_dates = [d for d, _ in files]

    def effective_price(ticker: str, date_str: str) -> tuple[float | None, str]:
        """Return (price, source) — prefer real yfinance, fall back to AI."""
        if ticker in real_prices:
            p = get_nearest_price(real_prices[ticker], date_str, direction="backward")
            if p:
                return p, "yfinance"
        ai = ai_prices.get(ticker, {})
        p  = ai.get(date_str)
        return p, "ai_fallback"

    open_positions: dict[str, dict] = {}
    closed_positions: list[dict]    = []

    def close_position(ticker: str, close_date: str, close_reason: str,
                       entry_price: float, close_price: float, source: str):
        pos     = open_positions.pop(ticker)
        pnl_pct = round((close_price - entry_price) / entry_price * 100, 2)
        closed_positions.append({
            "ticker":        ticker,
            "signal":        "BUY",
            "date_issued":   pos["date_issued"],
            "price_issued":  entry_price,
            "date_closed":   close_date,
            "close_reason":  close_reason,
            "pnl_pct":       pnl_pct,
            "price_source":  source,
        })

    # ── Process every data-file date chronologically ──
    for date_str, data in files:
        wa = data.get("watchlist_analysis", [])
        if not wa:
            continue
        if isinstance(wa, dict):
            wa = [wa]

        today_info: dict[str, dict] = {}
        for item in wa:
            ticker = item.get("ticker", "").strip()
            signal = item.get("signal", "HOLD").strip().upper()
            if not ticker:
                continue
            price, src = effective_price(ticker, date_str)
            if price is None or price <= 0:
                continue
            today_info[ticker] = {"price": price, "signal": signal, "source": src}

        # 1. Evaluate open positions against today's real price
        for ticker in list(open_positions.keys()):
            if ticker not in today_info:
                continue
            pos   = open_positions[ticker]
            entry = pos["price_issued"]
            curr  = today_info[ticker]["price"]
            src   = today_info[ticker]["source"]
            pnl   = (curr - entry) / entry
            days  = count_trading_days(pos["date_issued"], date_str, all_dates)

            if pnl <= STOP_LOSS_PCT:
                close_position(ticker, date_str, "stop_loss", entry, curr, src)
            elif pnl >= TAKE_PROFIT_PCT:
                close_position(ticker, date_str, "take_profit", entry, curr, src)
            elif days >= MAX_HOLD_DAYS:
                close_position(ticker, date_str, "expired", entry, curr, src)
            else:
                pos["days_held"]       = days
                pos["current_pnl_pct"] = round(pnl * 100, 2)
                pos["current_price"]   = curr

        # 2. Open new BUY positions
        for ticker, info in today_info.items():
            if info["signal"] == "BUY" and ticker not in open_positions:
                entry = info["price"]
                open_positions[ticker] = {
                    "ticker":          ticker,
                    "signal":          "BUY",
                    "date_issued":     date_str,
                    "price_issued":    round(entry, 4),
                    "stop_loss":       round(entry * (1 + STOP_LOSS_PCT), 4),
                    "take_profit":     round(entry * (1 + TAKE_PROFIT_PCT), 4),
                    "days_held":       0,
                    "current_price":   round(entry, 4),
                    "current_pnl_pct": 0.0,
                    "price_source":    info["source"],
                }

    # ── Finalise open positions with latest real prices ──
    today_str = datetime.date.today().isoformat()
    open_list: list[dict] = []
    for ticker, pos in open_positions.items():
        days = count_trading_days(pos["date_issued"], today_str, all_dates)
        curr, src = effective_price(ticker, today_str)
        if curr is None or curr <= 0:
            curr = pos["current_price"]
        cur_pnl = round((curr - pos["price_issued"]) / pos["price_issued"] * 100, 2)
        open_list.append({
            "ticker":          ticker,
            "signal":          "BUY",
            "date_issued":     pos["date_issued"],
            "price_issued":    pos["price_issued"],
            "stop_loss":       pos["stop_loss"],
            "take_profit":     pos["take_profit"],
            "days_held":       days,
            "current_price":   round(curr, 4),
            "current_pnl_pct": cur_pnl,
            "price_source":    src,
        })

    # ── Performance stats ──
    total         = len(closed_positions)
    win_count     = sum(1 for p in closed_positions if p["close_reason"] == "take_profit")
    loss_count    = sum(1 for p in closed_positions if p["close_reason"] == "stop_loss")
    expired_count = sum(1 for p in closed_positions if p["close_reason"] == "expired")

    win_rate = round(win_count / total, 4) if total > 0 else 0.0

    gain_pnls = [p["pnl_pct"] for p in closed_positions if p["pnl_pct"] > 0]
    loss_pnls = [p["pnl_pct"] for p in closed_positions if p["pnl_pct"] <= 0]

    avg_gain = round(sum(gain_pnls) / len(gain_pnls), 2) if gain_pnls else 0.0
    avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
    expected_value = round(win_rate * avg_gain + (1 - win_rate) * avg_loss, 2) if total > 0 else 0.0

    # vs SPY 30-day benchmark using real prices
    vs_spy_30d = _compute_spy_benchmark_real(real_prices, today_str)

    # Identify tickers that fell back to AI prices
    ai_fallback_tickers = sorted(
        t for t in get_all_tickers(files)
        if t not in real_prices or not real_prices.get(t)
    )

    performance = {
        "total_signals":      total + len(open_list),
        "wins":               win_count,
        "losses":             loss_count,
        "expired":            expired_count,
        "open":               len(open_list),
        "win_rate":           win_rate,
        "avg_gain_pct":       avg_gain,
        "avg_loss_pct":       avg_loss,
        "expected_value_pct": expected_value,
        "vs_spy_30d":         vs_spy_30d,
        "price_source":       "yfinance_real",
        "ai_fallback_tickers": ai_fallback_tickers,
    }

    return {
        "generated_at":     datetime.datetime.utcnow().isoformat() + "Z",
        "performance":      performance,
        "open_positions":   open_list,
        "closed_positions": list(reversed(closed_positions)),  # most-recent first
    }


def _compute_spy_benchmark_real(real_prices: dict, today_str: str) -> float:
    """Compute SPY 30-day return using real yfinance data."""
    spy = real_prices.get(SPY_TICKER, {})
    if not spy:
        return 0.0
    target_past = (datetime.date.fromisoformat(today_str) - datetime.timedelta(days=30)).isoformat()
    past_price  = get_nearest_price(spy, target_past,  direction="nearest")
    now_price   = get_nearest_price(spy, today_str,    direction="backward")
    if past_price and now_price and past_price > 0:
        return round((now_price - past_price) / past_price * 100, 2)
    return 0.0


# ─── Sector Rotation ──────────────────────────────────────────────────────────

def compute_sector_rotation(files: list[tuple[str, dict]]) -> dict:
    """Compute sector rotation signal from the most recent day's sectors data."""
    if not files:
        return {}
    for date_str, data in reversed(files):
        sectors = data.get("sectors", [])
        if not sectors:
            continue
        parsed = []
        for s in sectors:
            raw = s.get("change_pct", "0%").replace("+", "").replace("%", "").strip()
            try:
                pct = float(raw)
            except ValueError:
                pct = 0.0
            parsed.append({
                "name":       s.get("name", ""),
                "name_en":    s.get("name_en", ""),
                "etf":        s.get("etf", ""),
                "pct":        pct,
                "change_pct": s.get("change_pct", "0%"),
            })
        if not parsed:
            continue
        parsed.sort(key=lambda x: x["pct"], reverse=True)
        top = parsed[0]
        note = (
            f"今日{top['name']}{top['change_pct']}领涨，"
            f"建议关注{top['etf']}及{top['name_en']}个股"
            if top["name"] else "无明显板块轮动信号"
        )
        return {
            "date":           date_str,
            "strong_sectors": [s["name"] for s in parsed[:3]],
            "weak_sectors":   [s["name"] for s in parsed[-3:]],
            "rule":           "只在强势板块开新多仓",
            "note":           note,
        }
    return {}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("📊 Signal Tracker — 信号绩效追踪器 (yfinance Real Prices)")
    print("=" * 60)

    print("📂 Loading data files…")
    files = load_data_files()
    if not files:
        print("⚠️  No data files found in data/. Exiting.")
        return
    print(f"   Loaded {len(files)} data files ({files[0][0]} → {files[-1][0]})")

    all_dates  = [d for d, _ in files]
    start_date = all_dates[0]
    end_date   = datetime.date.today().isoformat()

    tickers = get_all_tickers(files)
    print(f"\n📡 Tickers found: {', '.join(tickers)}")

    print("\n💾 Loading AI prices as fallback…")
    ai_prices = build_ai_price_series(files)

    print(f"\n🌐 Fetching real prices from yfinance ({start_date} → {end_date})…")
    real_prices = get_real_prices(tickers, start_date, end_date)

    fallback_set = set(t for t in tickers if t not in real_prices or not real_prices.get(t))
    if fallback_set:
        print(f"\n⚠️  AI-price fallback for: {', '.join(sorted(fallback_set))}")
    print(f"\n✅ Real data: {len(real_prices)}/{len(tickers)} tickers")

    print("\n🔧 Building signal ledger with real prices…")
    ledger = build_ledger(files, real_prices, ai_prices)

    rotation = compute_sector_rotation(files)
    if rotation:
        ledger["sector_rotation"] = rotation

    OUTPUT_FILE.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n✅ Written: {OUTPUT_FILE}")

    perf = ledger["performance"]
    print(f"\n📈 Performance Summary (REAL PRICES):")
    print(f"   Total signals:  {perf['total_signals']}")
    print(f"   Wins:           {perf['wins']}")
    print(f"   Losses:         {perf['losses']}")
    print(f"   Expired:        {perf['expired']}")
    print(f"   Open:           {perf['open']}")
    print(f"   Win rate:       {perf['win_rate']*100:.1f}%")
    print(f"   Avg gain:       +{perf['avg_gain_pct']:.2f}%")
    print(f"   Avg loss:       {perf['avg_loss_pct']:.2f}%")
    print(f"   Expected value: {perf['expected_value_pct']:+.2f}%/signal")
    print(f"   vs SPY 30d:     {perf['vs_spy_30d']:+.2f}%")
    if perf.get("ai_fallback_tickers"):
        print(f"   AI fallback:    {', '.join(perf['ai_fallback_tickers'])}")

    print(f"\n📋 Open Positions ({len(ledger['open_positions'])}):")
    for pos in ledger["open_positions"]:
        print(f"   {pos['ticker']}: BUY @ ${pos['price_issued']:.4f} "
              f"({pos['date_issued']}), {pos['days_held']}d, "
              f"now ${pos['current_price']:.4f}, "
              f"P&L: {pos['current_pnl_pct']:+.1f}% [{pos['price_source']}]")

    print(f"\n📁 Closed Positions ({len(ledger['closed_positions'])}) — last 5:")
    for pos in ledger["closed_positions"][:5]:
        print(f"   {pos['ticker']}: {pos['close_reason']} @ {pos['date_closed']}, "
              f"P&L: {pos['pnl_pct']:+.1f}% [{pos['price_source']}]")
    print("=" * 60)


if __name__ == "__main__":
    main()
