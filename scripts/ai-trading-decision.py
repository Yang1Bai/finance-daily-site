#!/usr/bin/env python3
"""
AI 多模型操盘决策脚本
每个交易日收盘后运行，让各模型基于最新市场数据做出交易决策并更新组合
"""

import json
import os
import sys
import time
from datetime import datetime
import pytz

TORONTO_TZ = pytz.timezone("America/Toronto")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_FILE = os.path.join(BASE_DIR, "..", "data", "ai-portfolio.json")
LATEST_FILE    = os.path.join(BASE_DIR, "..", "data", "latest.json")

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

# ── 支持的 ticker 列表（供模型选择）────────────────────────────────────────
ALLOWED_TICKERS = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "QQQ", "SPY", "IWM", "XLK", "XLE", "XLF",
    "GLD", "SLV", "TLT", "BTC-USD", "ETH-USD",
    "TSM", "BABA", "JD", "AMD", "INTC", "AVGO",
]

def fetch_prices(tickers):
    prices = {}
    for t in set(tickers):
        try:
            h = yf.Ticker(t).history(period="2d")
            if len(h): prices[t] = round(float(h["Close"].iloc[-1]), 2)
        except: pass
    return prices

def build_market_context():
    """从 latest.json 提取市场摘要"""
    try:
        with open(LATEST_FILE) as f:
            d = json.load(f)
    except:
        return "市场数据暂不可用"

    parts = []
    s = d.get("summary", {})
    if s.get("headline"):
        parts.append(f"大势：{s['headline']}。{s.get('context','')[:200]}")

    macro = d.get("macro", [])
    if macro:
        m_lines = [f"{m.get('label','')} {m.get('value','')} ({m.get('detail','')})" for m in macro[:5]]
        parts.append("宏观：" + " | ".join(m_lines))

    news = d.get("news", [])
    if news:
        n_lines = [n.get("headline","") for n in news[:4] if n.get("headline")]
        parts.append("要闻：" + " | ".join(n_lines))

    watchlist = d.get("watchlist", [])
    if watchlist:
        w_lines = []
        for w in watchlist[:5]:
            w_lines.append(f"{w.get('ticker','')} {w.get('action','')} score={w.get('score','')} {w.get('thesis','')[:60]}")
        parts.append("AI分析：" + "\n  ".join(w_lines))

    return "\n\n".join(parts)

def format_portfolio_state(portfolio, prices):
    """格式化当前持仓状态给模型看"""
    holdings = portfolio.get("holdings", [])
    lines = []
    total = 0
    cash = 0
    for h in holdings:
        t = h["ticker"]
        s = h["shares"]
        if t == "CASH":
            cash = h["avg_cost"]
            total += cash
            lines.append(f"  CASH: ${cash:,.2f}")
        else:
            p = prices.get(t, h["avg_cost"])
            v = s * p
            total += v
            lines.append(f"  {t}: {s}股 × ${p:.2f} = ${v:,.2f}")
    lines.append(f"  组合总值: ${total:,.2f}  现金: ${cash:,.2f}")
    return "\n".join(lines), total, cash

def ask_claude(prompt, api_key):
    import urllib.request
    payload = json.dumps({
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    return resp["content"][0]["text"]

def ask_gpt(prompt, api_key):
    import urllib.request
    payload = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"]

def parse_json_response(text):
    """从模型输出中提取 JSON"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except:
        pass
    # 提取 ```json ... ``` 块
    import re
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    # 找第一个 { ... }
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except: pass
    return None

def make_decision_prompt(model_name, portfolio, prices, market_ctx, today, start_capital):
    state_str, total_val, cash = format_portfolio_state(portfolio, prices)
    pnl = total_val - start_capital
    pnl_pct = pnl / start_capital * 100

    price_lines = "\n".join([f"- {t}: ${p:.2f}" for t, p in sorted(prices.items()) if t in ALLOWED_TICKERS])

    return f"""你是 {model_name}，正在参加一场 AI 模型操盘大赛。
今日日期：{today}。你的初始资金是 $10,000（比赛开始于 2026-05-13）。

当前组合状态：
{state_str}
当前盈亏：${pnl:+.2f} ({pnl_pct:+.2f}%)

当前可用价格：
{price_lines}

今日市场信息：
{market_ctx}

可操作标的：{', '.join(ALLOWED_TICKERS)}

请做出今日交易决策。规则：
1. 只能买卖 ALLOWED_TICKERS 中的标的
2. 不能超额（现金不能为负）
3. 每笔交易 shares 必须是正整数
4. 最多持有 6 个非现金标的
5. 如果无操作，返回空 trades 数组

输出严格 JSON（不要任何解释文字）：
{{
  "trades": [
    {{"action": "BUY or SELL", "ticker": "XXX", "shares": N, "price": P, "reason": "简短理由"}}
  ],
  "note": "今日策略一句话总结（中文，50字内）"
}}"""

def apply_trades(portfolio, trades, prices, cash):
    """执行交易，返回更新后的 holdings 和 cash"""
    holdings_map = {}
    for h in portfolio.get("holdings", []):
        if h["ticker"] != "CASH":
            holdings_map[h["ticker"]] = {"shares": h["shares"], "avg_cost": h["avg_cost"]}

    valid_trades = []
    for t in trades:
        action = t.get("action", "").upper()
        ticker = t.get("ticker", "").upper()
        shares = int(t.get("shares", 0))
        price = prices.get(ticker, t.get("price", 0))
        if not ticker or shares <= 0 or ticker not in ALLOWED_TICKERS:
            continue
        cost = shares * price
        if action == "BUY":
            if cost > cash + 0.01:
                print(f"  ⚠️ {ticker} BUY {shares}股 跳过（现金不足 ${cash:.2f} < ${cost:.2f}）")
                continue
            cash -= cost
            if ticker in holdings_map:
                old = holdings_map[ticker]
                total_shares = old["shares"] + shares
                holdings_map[ticker] = {
                    "shares": total_shares,
                    "avg_cost": round((old["shares"] * old["avg_cost"] + shares * price) / total_shares, 4)
                }
            else:
                holdings_map[ticker] = {"shares": shares, "avg_cost": price}
            t["price"] = price
            valid_trades.append(t)
            print(f"  ✅ BUY {ticker} {shares}股 @ ${price:.2f}")
        elif action == "SELL":
            if ticker not in holdings_map or holdings_map[ticker]["shares"] < shares:
                avail = holdings_map.get(ticker, {}).get("shares", 0)
                print(f"  ⚠️ {ticker} SELL {shares}股 跳过（持仓不足 {avail}股）")
                continue
            cash += shares * price
            holdings_map[ticker]["shares"] -= shares
            if holdings_map[ticker]["shares"] == 0:
                del holdings_map[ticker]
            t["price"] = price
            valid_trades.append(t)
            print(f"  ✅ SELL {ticker} {shares}股 @ ${price:.2f}")

    # 重建 holdings
    new_holdings = [
        {"ticker": t, "shares": v["shares"], "avg_cost": round(v["avg_cost"], 4)}
        for t, v in holdings_map.items()
    ]
    new_holdings.append({"ticker": "CASH", "shares": 1, "avg_cost": round(cash, 4)})
    return new_holdings, round(cash, 4), valid_trades

def update_portfolio(portfolio, prices, spy_price, start_capital, trades, note, today):
    """计算当日组合价值并追加到 history"""
    holdings = portfolio.get("holdings", [])
    cash = 0
    positions = {}
    total = 0

    for h in holdings:
        t = h["ticker"]
        if t == "CASH":
            cash = h["avg_cost"]
            total += cash
        else:
            p = prices.get(t, h["avg_cost"])
            v = round(h["shares"] * p, 2)
            positions[t] = {"shares": h["shares"], "price": p, "value": v}
            total += v

    portfolio_value = round(total, 2)
    baseline_value  = round(spy_price * 13.5296, 2) if spy_price else None

    # 更新或追加当天记录
    history = portfolio.get("history", [])
    record = {
        "date": today,
        "portfolio_value": portfolio_value,
        "baseline_value": baseline_value,
        "cash": cash,
        "positions": positions,
        "trades": trades,
        "note": note
    }
    if history and history[-1]["date"] == today:
        history[-1] = record
    else:
        history.append(record)

    portfolio["history"] = history
    return portfolio, portfolio_value

def main():
    now   = datetime.now(TORONTO_TZ)
    today = now.strftime("%Y-%m-%d")
    print(f"\n🤖 AI 操盘决策 — {today}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key    = os.environ.get("OPENAI_API_KEY", "")

    with open(PORTFOLIO_FILE) as f:
        data = json.load(f)

    contest    = data["contest"]
    portfolios = data["portfolios"]
    start_cap  = contest["start_capital"]

    # 收集需要的 tickers
    needed = set(["SPY"])
    for pf in portfolios.values():
        for h in pf.get("holdings", []):
            if h["ticker"] != "CASH":
                needed.add(h["ticker"])
    for t in ALLOWED_TICKERS:
        needed.add(t)

    print(f"📊 获取 {len(needed)} 个标的价格…")
    prices = fetch_prices(list(needed))
    spy_price = prices.get("SPY")
    print(f"  SPY: ${spy_price:.2f}" if spy_price else "  SPY 价格获取失败")

    market_ctx = build_market_context()

    model_configs = {
        "claude-sonnet-4-5": {"fn": ask_claude, "key": anthropic_key, "label": "Claude Sonnet 4.5"},
        "gpt-4o":            {"fn": ask_gpt,    "key": openai_key,    "label": "GPT-4o"},
    }

    for model_id, cfg in model_configs.items():
        if model_id not in portfolios:
            continue
        print(f"\n{'─'*50}")
        print(f"🤖 {cfg['label']} 决策中…")

        pf = portfolios[model_id]
        prompt = make_decision_prompt(cfg["label"], pf, prices, market_ctx, today, start_cap)

        decision = None
        if cfg["key"]:
            try:
                raw = cfg["fn"](prompt, cfg["key"])
                decision = parse_json_response(raw)
                if decision:
                    print(f"  决策解析成功")
                else:
                    print(f"  ⚠️ JSON 解析失败，原始输出: {raw[:200]}")
            except Exception as e:
                print(f"  ❌ API 调用失败: {e}")
        else:
            print(f"  ⚠️ 无 API Key，跳过决策")

        if decision:
            raw_trades = decision.get("trades", [])
            note = decision.get("note", "")
            # 计算当前现金
            cash = next((h["avg_cost"] for h in pf["holdings"] if h["ticker"] == "CASH"), 0)
            new_holdings, new_cash, valid_trades = apply_trades(pf, raw_trades, prices, cash)
            pf["holdings"] = new_holdings
        else:
            valid_trades = []
            note = pf["history"][-1].get("note", "") if pf.get("history") else ""

        # 更新组合价值
        pf, pv = update_portfolio(pf, prices, spy_price, start_cap, valid_trades, note, today)
        portfolios[model_id] = pf

        baseline = round(spy_price * 13.5296, 2) if spy_price else 10000
        alpha = pv - baseline
        print(f"  组合价值: ${pv:,.2f}  基线: ${baseline:,.2f}  超额: ${alpha:+,.2f}")

    data["portfolios"] = portfolios

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ ai-portfolio.json 已更新")

if __name__ == "__main__":
    main()
