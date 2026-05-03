#!/usr/bin/env python3
"""
Daily Investment Briefing
每日投资早报 — 每天早上 8:30 AM Toronto 推送
"""

import yfinance as yf
import requests
import json
import os
from datetime import datetime
import pytz

TORONTO_TZ = pytz.timezone("America/Toronto")

# Telegram 配置
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8518954174:AAHIWuxR4DDTtqxqjFzeUi33WxFUtnLyQQc")
TELEGRAM_CHAT_ID = "8727904480"

# 主要追踪标的
WATCHLIST = {
    "美股指数": {
        "S&P 500":  "^GSPC",
        "纳斯达克": "^IXIC",
        "道琼斯":   "^DJI",
    },
    "AI & 科技": {
        "英伟达 NVDA": "NVDA",
        "费城半导体 SOX": "^SOX",
        "QQQ":         "QQQ",
    },
    "新能源 ETF": {
        "锂电 LIT":     "LIT",
        "清洁能源 ICLN": "ICLN",
        "太阳能 TAN":   "TAN",
        "铀矿 URA":     "URA",
    },
    "A股/港股": {
        "沪深300 ETF": "510300.SS",
        "恒生ETF":     "2800.HK",
        "新能源 ETF":  "159806.SZ",
    },
    "大宗商品": {
        "黄金":   "GC=F",
        "白银":   "SI=F",
        "铜":     "HG=F",
        "原油":   "CL=F",
        "天然气": "NG=F",
    },
    "新能源金属": {
        "钯金":   "PA=F",
        "铂金":   "PL=F",
    },
    "加密": {
        "BTC-USD": "BTC-USD",
        "ETH-USD": "ETH-USD",
    },
}

def fmt_change(pct: float) -> str:
    if pct >= 2:
        arrow = "🚀 +"
    elif pct >= 0:
        arrow = "🟢 +"
    elif pct >= -2:
        arrow = "🔴 "
    else:
        arrow = "💥 "
    return f"{arrow}{pct:.2f}%"

def trend_label(pct_5d: float, pct_30d: float) -> str:
    """简单趋势判断"""
    if pct_30d > 10 and pct_5d > 2:
        return "📈 强势上行"
    elif pct_30d > 5 and pct_5d > 0:
        return "↗️ 温和上涨"
    elif pct_30d < -10 and pct_5d < -2:
        return "📉 持续下行"
    elif pct_30d < -5 and pct_5d < 0:
        return "↘️ 弱势调整"
    elif abs(pct_30d) < 3:
        return "➡️ 横盘整理"
    elif pct_5d > 0 and pct_30d < 0:
        return "⚡ 超跌反弹"
    elif pct_5d < 0 and pct_30d > 0:
        return "⚠️ 短线回调"
    else:
        return "〰️ 震荡"

def fetch_quotes() -> dict:
    results = {}
    for category, items in WATCHLIST.items():
        results[category] = {}
        for name, ticker in items.items():
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="1mo")
                if len(hist) < 2:
                    results[category][name] = {"error": "no data"}
                    continue

                last  = hist["Close"].iloc[-1]
                prev  = hist["Close"].iloc[-2]
                d30   = hist["Close"].iloc[0]

                pct_1d  = (last - prev) / prev * 100
                pct_5d  = (last - hist["Close"].iloc[-6]) / hist["Close"].iloc[-6] * 100 if len(hist) >= 6 else None
                pct_30d = (last - d30) / d30 * 100

                results[category][name] = {
                    "price":   last,
                    "pct_1d":  pct_1d,
                    "pct_5d":  pct_5d,
                    "pct_30d": pct_30d,
                }
            except Exception as e:
                results[category][name] = {"error": str(e)[:60]}
    return results


def fmt_price(price: float) -> str:
    if price > 10000:
        return f"{price:,.0f}"
    elif price > 100:
        return f"{price:,.2f}"
    elif price > 1:
        return f"{price:.3f}"
    else:
        return f"{price:.5f}"


def build_message(quotes: dict) -> str:
    now = datetime.now(TORONTO_TZ)
    date_str = now.strftime("%Y-%m-%d %a")

    lines = [
        f"📊 *每日投资早报* — {date_str}",
        "",
    ]

    for category, items in quotes.items():
        lines.append(f"━━━ *{category}* ━━━")
        for name, data in items.items():
            if "error" in data:
                lines.append(f"  {name}: —")
                continue

            price_str = fmt_price(data["price"])
            change_str = fmt_change(data["pct_1d"])

            pct_5d  = data.get("pct_5d")
            pct_30d = data.get("pct_30d")

            # 主行：价格 + 日涨跌
            line = f"  *{name}*: {price_str}  {change_str}"
            lines.append(line)

            # 副行：5日/月涨跌 + 趋势
            if pct_5d is not None and pct_30d is not None:
                sign5  = "+" if pct_5d  >= 0 else ""
                sign30 = "+" if pct_30d >= 0 else ""
                trend  = trend_label(pct_5d, pct_30d)
                lines.append(
                    f"    5日:{sign5}{pct_5d:.1f}%  月:{sign30}{pct_30d:.1f}%  {trend}"
                )
        lines.append("")

    # 市场情绪
    changes_1d = [
        d["pct_1d"]
        for cat in quotes.values()
        for d in cat.values()
        if "pct_1d" in d
    ]
    if changes_1d:
        avg = sum(changes_1d) / len(changes_1d)
        pos = sum(1 for x in changes_1d if x > 0)
        neg = sum(1 for x in changes_1d if x < 0)
        if avg > 1.5:
            mood = "🌞 全面上涨，市场情绪亢奋，注意追高风险"
        elif avg > 0.3:
            mood = "🌤 整体偏多，风险资产强劲，可适量布局"
        elif avg > -0.3:
            mood = "😐 市场分化，观望为主，等待方向选择"
        elif avg > -1.5:
            mood = "☁️ 整体偏弱，短线谨慎，关注支撑位"
        else:
            mood = "⛈ 普跌行情，控制仓位，避免左侧接刀"

        lines.append(f"*市场情绪：* {mood}")
        lines.append(f"今日 {pos} 涨 / {neg} 跌（共 {len(changes_1d)} 标的）")
        lines.append("")

    lines.append("_数据来源：Yahoo Finance · 趋势仅供参考，非投资建议_")
    return "\n".join(lines)


SUBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "investment-subscribers.json")


def load_subscribers() -> list:
    """返回所有活跃订阅者的 chat_id 列表"""
    try:
        with open(SUBS_FILE) as f:
            data = json.load(f)
        return [uid for uid, s in data["subscribers"].items() if s.get("active")]
    except Exception:
        return [TELEGRAM_CHAT_ID]  # fallback


def save_broadcast_stat(sent: int, total: int):
    try:
        with open(SUBS_FILE) as f:
            data = json.load(f)
        stats = data.setdefault("stats", {})
        stats["total_broadcasts"] = stats.get("total_broadcasts", 0) + 1
        stats["last_broadcast"] = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d %H:%M")
        history = stats.setdefault("broadcast_history", [])
        history.append({
            "date": stats["last_broadcast"],
            "sent": sent,
            "total": total,
        })
        if len(history) > 30:
            stats["broadcast_history"] = history[-30:]
        with open(SUBS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Stat save failed: {e}")


def broadcast(message: str):
    """广播给所有活跃订阅者"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    subscribers = load_subscribers()
    sent = 0
    for chat_id in subscribers:
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }, timeout=15)
            if resp.ok:
                sent += 1
            else:
                print(f"Failed {chat_id}: {resp.status_code}")
        except Exception as e:
            print(f"Error {chat_id}: {e}")
    print(f"✅ Broadcast: {sent}/{len(subscribers)} delivered")
    save_broadcast_stat(sent, len(subscribers))


def main():
    print(f"Fetching quotes at {datetime.now(TORONTO_TZ).isoformat()}...")
    quotes = fetch_quotes()
    message = build_message(quotes)
    print(message)
    print("\n--- Broadcasting ---")
    broadcast(message)


if __name__ == "__main__":
    main()
