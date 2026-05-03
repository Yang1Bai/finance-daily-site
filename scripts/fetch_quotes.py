#!/usr/bin/env python3
"""
轻量级实时行情更新脚本
每30分钟由 GitHub Actions 触发，将最新指数/商品/加密价格写入 data/quotes.json
前端 JavaScript 加载此文件并动态更新 ticker 条。
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

try:
    import yfinance as yf
except ImportError:
    print("❌ yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

SYMBOLS = {
    "S&P 500":   "^GSPC",
    "NASDAQ":    "^IXIC",
    "DOW":       "^DJI",
    "上证指数":   "000001.SS",
    "恒生指数":   "^HSI",
    "日经225":    "^N225",
    "德国DAX":    "^GDAXI",
    "黄金 XAU":   "GC=F",
    "原油 WTI":   "CL=F",
    "BTC/USD":   "BTC-USD",
    "美元指数":   "DX-Y.NYB",
    "CNH/USD":   "CNH=X",
}

def fmt_value(v: float, symbol: str) -> str:
    """Format value based on asset type."""
    if "CNH" in symbol or "DX" in symbol:
        return f"{v:.4f}"
    if v >= 10000:
        return f"{v:,.0f}"
    if v >= 100:
        return f"{v:,.2f}"
    return f"{v:.4f}"

def main():
    print("📈 Fetching live quotes via yfinance...")
    results = []

    try:
        # Batch download
        syms = list(SYMBOLS.values())
        tickers_data = yf.download(
            syms,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )

        for name, sym in SYMBOLS.items():
            try:
                if len(syms) == 1:
                    close = tickers_data["Close"].dropna()
                else:
                    close = tickers_data[sym]["Close"].dropna()

                if len(close) < 2:
                    print(f"  ⚠️  {name}: not enough data")
                    continue

                curr = float(close.iloc[-1])
                prev = float(close.iloc[-2])
                chg = curr - prev
                chg_pct = chg / prev * 100 if prev else 0

                results.append({
                    "name":       name,
                    "symbol":     sym,
                    "value":      fmt_value(curr, sym),
                    "change":     f"{chg:+.2f}",
                    "change_pct": f"{chg_pct:+.2f}%",
                    "direction":  "up" if chg >= 0 else "down",
                })
                print(f"  ✅  {name}: {fmt_value(curr, sym)} ({chg_pct:+.2f}%)")

            except Exception as e:
                print(f"  ⚠️  {name} ({sym}): {e}")

    except Exception as e:
        print(f"❌ Batch download failed: {e}")
        sys.exit(1)

    if not results:
        print("❌ No quotes fetched")
        sys.exit(1)

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "source":  "yfinance",
        "quotes":  results,
    }

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    out_path = data_dir / "quotes.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ Saved {len(results)} quotes → data/quotes.json")
    print(f"   Updated at: {out['updated']}")


if __name__ == "__main__":
    main()
