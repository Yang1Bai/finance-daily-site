#!/usr/bin/env python3
"""
Signal Tracker — AI Watchlist 历史信号绩效追踪器
Reads historical JSON files from data/, builds a running ledger of
BUY/SELL/HOLD signals, evaluates outcomes, and writes signal_ledger.json.

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
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────
ROOT_DIR   = Path(__file__).resolve().parent.parent
DATA_DIR   = ROOT_DIR / "data"
OUTPUT_FILE = DATA_DIR / "signal_ledger.json"

STOP_LOSS_PCT  = -0.07   # -7%
TAKE_PROFIT_PCT = 0.15   # +15%
MAX_HOLD_DAYS  = 10      # trading days before expiry

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

# SPY data for vs-SPY benchmark (we'll compute from the data files)
SPY_TICKER = "SPY"


def load_data_files() -> list[tuple[str, dict]]:
    """Load all dated JSON files from data/, sorted by date."""
    files = []
    skip = {"latest.json", "index.json", "signal_ledger.json",
            "quotes.json", "ai-portfolio.json",
            "investment-subscribers.json", "telegram-offset.json"}
    for fname in sorted(os.listdir(DATA_DIR)):
        if fname in skip or not fname.endswith(".json"):
            continue
        # Must look like a date file YYYY-MM-DD.json
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
    """Parse a price value that might be a float, int, or string like '875.40' or '63,000'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def extract_signals(files: list[tuple[str, dict]]) -> list[dict]:
    """
    Extract all watchlist_analysis entries from all data files.
    Returns list of dicts: date, ticker, signal, price.
    """
    signals = []
    for date_str, data in files:
        wa = data.get("watchlist_analysis", [])
        if not wa:
            continue
        if isinstance(wa, dict):
            wa = [wa]
        for item in wa:
            ticker = item.get("ticker", "").strip()
            signal = item.get("signal", "HOLD").strip().upper()
            price  = parse_price(item.get("price"))
            if not ticker or price is None or price <= 0:
                continue
            signals.append({
                "date":   date_str,
                "ticker": ticker,
                "signal": signal,
                "price":  price,
            })
    return signals


def build_price_series(files: list[tuple[str, dict]]) -> dict[str, dict[str, float]]:
    """
    Build a price lookup: {ticker: {date_str: price}}.
    Used to evaluate exit conditions on subsequent days.
    """
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


def count_trading_days(start: str, end: str, all_dates: list[str]) -> int:
    """Count how many data-file dates fall between start (exclusive) and end (inclusive)."""
    return sum(1 for d in all_dates if start < d <= end)


def build_ledger(files: list[tuple[str, dict]]) -> dict:
    """
    Core logic: build open/closed positions ledger and compute performance.
    """
    all_dates = [d for d, _ in files]
    signals   = extract_signals(files)
    price_series = build_price_series(files)

    # Track first BUY signal per ticker (de-duplicate: only one entry per ticker;
    # a new entry is added when an existing position is closed)
    open_positions: dict[str, dict] = {}   # ticker → position info
    closed_positions: list[dict]    = []

    def close_position(ticker: str, close_date: str, close_reason: str, entry_price: float, close_price: float):
        pos = open_positions.pop(ticker)
        pnl_pct = round((close_price - entry_price) / entry_price * 100, 2)
        closed_positions.append({
            "ticker":        ticker,
            "signal":        "BUY",
            "date_issued":   pos["date_issued"],
            "price_issued":  entry_price,
            "date_closed":   close_date,
            "close_reason":  close_reason,
            "pnl_pct":       pnl_pct,
        })

    for date_str, data in files:
        wa = data.get("watchlist_analysis", [])
        if not wa:
            continue
        if isinstance(wa, dict):
            wa = [wa]

        prices_today = {}
        for item in wa:
            ticker = item.get("ticker", "").strip()
            price  = parse_price(item.get("price"))
            signal = item.get("signal", "HOLD").strip().upper()
            if not ticker or price is None or price <= 0:
                continue
            prices_today[ticker] = {"price": price, "signal": signal}

        # 1. Evaluate existing open positions against today's price
        for ticker in list(open_positions.keys()):
            if ticker not in prices_today:
                continue
            pos   = open_positions[ticker]
            entry = pos["price_issued"]
            curr  = prices_today[ticker]["price"]
            pnl   = (curr - entry) / entry

            days_held = count_trading_days(pos["date_issued"], date_str, all_dates)

            # Check stop loss and take profit
            if pnl <= STOP_LOSS_PCT:
                close_position(ticker, date_str, "stop_loss", entry, curr)
            elif pnl >= TAKE_PROFIT_PCT:
                close_position(ticker, date_str, "take_profit", entry, curr)
            elif days_held >= MAX_HOLD_DAYS:
                close_position(ticker, date_str, "expired", entry, curr)
            else:
                # Update days_held and current_pnl in open position
                pos["days_held"]       = days_held
                pos["current_pnl_pct"] = round(pnl * 100, 2)
                pos["current_price"]   = curr

        # 2. Open new BUY positions
        for ticker, info in prices_today.items():
            if info["signal"] == "BUY" and ticker not in open_positions:
                entry  = info["price"]
                sl     = round(entry * (1 + STOP_LOSS_PCT), 2)
                tp     = round(entry * (1 + TAKE_PROFIT_PCT), 2)
                open_positions[ticker] = {
                    "ticker":          ticker,
                    "signal":          "BUY",
                    "date_issued":     date_str,
                    "price_issued":    entry,
                    "stop_loss":       sl,
                    "take_profit":     tp,
                    "days_held":       0,
                    "current_price":   entry,
                    "current_pnl_pct": 0.0,
                }

    # Convert open_positions dict to list (with fresh days_held computation)
    today_str = datetime.date.today().isoformat()
    open_list = []
    for ticker, pos in open_positions.items():
        days_held = count_trading_days(pos["date_issued"], today_str, all_dates)
        open_list.append({
            "ticker":          ticker,
            "signal":          "BUY",
            "date_issued":     pos["date_issued"],
            "price_issued":    pos["price_issued"],
            "stop_loss":       pos["stop_loss"],
            "take_profit":     pos["take_profit"],
            "days_held":       days_held,
            "current_pnl_pct": pos.get("current_pnl_pct", 0.0),
        })

    # ─── Performance stats ───────────────────────────────────────────────
    total = len(closed_positions)
    wins   = [p for p in closed_positions if p["pnl_pct"] > 0]
    losses = [p for p in closed_positions if p["pnl_pct"] <= 0]
    expired = [p for p in closed_positions if p["close_reason"] == "expired"]

    win_count    = len([p for p in closed_positions if p["close_reason"] == "take_profit"])
    loss_count   = len([p for p in closed_positions if p["close_reason"] == "stop_loss"])
    expired_count = len(expired)

    win_rate = round(win_count / total, 4) if total > 0 else 0.0

    gain_pnls = [p["pnl_pct"] for p in closed_positions if p["pnl_pct"] > 0]
    loss_pnls = [p["pnl_pct"] for p in closed_positions if p["pnl_pct"] <= 0]

    avg_gain = round(sum(gain_pnls) / len(gain_pnls), 2) if gain_pnls else 0.0
    avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
    expected_value = round(win_rate * avg_gain + (1 - win_rate) * avg_loss, 2) if total > 0 else 0.0

    # vs SPY: compute SPY 30-day return from price_series
    vs_spy_30d = _compute_spy_benchmark(price_series, all_dates)

    performance = {
        "total_signals":    total + len(open_list),
        "wins":             win_count,
        "losses":           loss_count,
        "expired":          expired_count,
        "open":             len(open_list),
        "win_rate":         win_rate,
        "avg_gain_pct":     avg_gain,
        "avg_loss_pct":     avg_loss,
        "expected_value_pct": expected_value,
        "vs_spy_30d":       vs_spy_30d,
    }

    return {
        "generated_at":    datetime.datetime.utcnow().isoformat() + "Z",
        "performance":     performance,
        "open_positions":  open_list,
        "closed_positions": list(reversed(closed_positions)),  # most recent first
    }


def _compute_spy_benchmark(price_series: dict, all_dates: list[str]) -> float:
    """Compute SPY 30-day return as a benchmark comparison."""
    spy_prices = price_series.get(SPY_TICKER, {})
    if len(spy_prices) < 2:
        return 0.0
    sorted_dates = sorted(spy_prices.keys())
    # Try to get ~30 calendar days ago
    if len(sorted_dates) >= 2:
        latest_date = sorted_dates[-1]
        # Find date ~30 days ago
        target_past = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=30)).isoformat()
        past_date = min(sorted_dates, key=lambda d: abs(
            (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(target_past)).days
        ))
        past_price   = spy_prices[past_date]
        latest_price = spy_prices[latest_date]
        if past_price > 0:
            return round((latest_price - past_price) / past_price * 100, 2)
    return 0.0


def compute_sector_rotation(files: list[tuple[str, dict]]) -> dict:
    """
    Compute sector rotation signal from the most recent day's sectors data.
    """
    if not files:
        return {}
    # Use the most recent file with sectors data
    for date_str, data in reversed(files):
        sectors = data.get("sectors", [])
        if not sectors:
            continue
        # Parse change_pct and sort
        parsed = []
        for s in sectors:
            raw_pct = s.get("change_pct", "0%").replace("+", "").replace("%", "").strip()
            try:
                pct = float(raw_pct)
            except ValueError:
                pct = 0.0
            parsed.append({
                "name": s.get("name", ""),
                "name_en": s.get("name_en", ""),
                "etf": s.get("etf", ""),
                "pct": pct,
                "change_pct": s.get("change_pct", "0%"),
            })
        if not parsed:
            continue
        parsed.sort(key=lambda x: x["pct"], reverse=True)
        strong = [s["name"] for s in parsed[:3]]
        weak   = [s["name"] for s in parsed[-3:]]
        top_sector = parsed[0]
        note = (
            f"今日{top_sector['name']}{top_sector['change_pct']}领涨，"
            f"建议关注{top_sector['etf']}及{top_sector['name_en']}个股"
            if top_sector["name"] else "无明显板块轮动信号"
        )
        return {
            "date":             date_str,
            "strong_sectors":   strong,
            "weak_sectors":     weak,
            "rule":             "只在强势板块开新多仓",
            "note":             note,
        }
    return {}


def main():
    print("=" * 60)
    print("📊 Signal Tracker — 信号绩效追踪器")
    print("=" * 60)

    print("📂 Loading data files...")
    files = load_data_files()
    if not files:
        print("⚠️  No data files found in data/. Exiting.")
        return
    print(f"   Loaded {len(files)} data files ({files[0][0]} → {files[-1][0]})")

    print("\n🔧 Building signal ledger...")
    ledger = build_ledger(files)

    # Add sector rotation
    sector_rotation = compute_sector_rotation(files)
    if sector_rotation:
        ledger["sector_rotation"] = sector_rotation

    # Write output
    OUTPUT_FILE.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n✅ Written: {OUTPUT_FILE}")

    perf = ledger["performance"]
    print(f"\n📈 Performance Summary:")
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

    print(f"\n📋 Open Positions ({len(ledger['open_positions'])}):")
    for pos in ledger["open_positions"]:
        print(f"   {pos['ticker']}: BUY @ ${pos['price_issued']:.2f} "
              f"({pos['date_issued']}), {pos['days_held']}d held, "
              f"P&L: {pos['current_pnl_pct']:+.1f}%")

    print(f"\n📁 Closed Positions ({len(ledger['closed_positions'])}) — last 5:")
    for pos in ledger["closed_positions"][:5]:
        print(f"   {pos['ticker']}: {pos['close_reason']} @ {pos['date_closed']}, "
              f"P&L: {pos['pnl_pct']:+.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
