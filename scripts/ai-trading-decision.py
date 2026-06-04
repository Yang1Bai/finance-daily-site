#!/usr/bin/env python3
"""
AI 多模型操盘决策脚本 v2
每个交易日收盘后运行，让各模型基于最新市场数据做出交易决策并更新组合。

v2 改进：
- 新增 compute_technicals(): RSI-14、20日动量、50MA 技术信号
- Prompt 加入技术信号 + alpha 反馈，减少情绪化防御交易
- 最多 2 笔/天，同标的持有 <3 天不建议卖出
- 新增纯规则策略 rule_based 作对照
- --dry-run 模式不写文件
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta
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

# 防御性资产（在牛市里多持有这些会落后 SPY）
DEFENSIVE_TICKERS = {"GLD", "SLV", "TLT", "BTC-USD", "ETH-USD"}


# ── 技术指标计算 ──────────────────────────────────────────────────────────

def _calc_rsi(closes, period=14):
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def compute_technicals(tickers):
    """
    返回 dict: {ticker: {rsi_14, momentum_20d, above_50ma, trend}}
    trend: "强势" / "中性" / "弱势"
    失败时对应 ticker 返回 None
    """
    result = {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="3mo")
            if len(hist) < 51:
                result[t] = None
                continue
            closes = hist["Close"]
            rsi    = _calc_rsi(closes)
            mom20  = float((closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21] * 100)
            ma50   = float(closes.rolling(50).mean().iloc[-1])
            above  = closes.iloc[-1] > ma50

            if rsi > 55 and mom20 > 3 and above:
                trend = "强势"
            elif rsi < 45 and mom20 < -3 and not above:
                trend = "弱势"
            else:
                trend = "中性"

            result[t] = {
                "rsi_14":      round(rsi, 1),
                "momentum_20d": round(mom20, 2),
                "above_50ma":  above,
                "trend":       trend,
            }
        except Exception as e:
            result[t] = None
    return result


def fmt_tech_table(technicals, prices):
    """格式化技术信号表，供 prompt 使用"""
    arrow = {"强势": "↑", "中性": "→", "弱势": "↓"}
    lines = []
    for t in ALLOWED_TICKERS:
        info = technicals.get(t)
        if not info:
            continue
        p = prices.get(t, 0)
        sign = "+" if info["momentum_20d"] >= 0 else ""
        ma_flag = "▲50MA" if info["above_50ma"] else "▼50MA"
        lines.append(
            f"  {t:<10} RSI={info['rsi_14']:>5.1f}  动量20d={sign}{info['momentum_20d']:>6.2f}%  "
            f"{ma_flag}  [{info['trend']} {arrow[info['trend']]}]"
        )
    return "\n".join(lines)


# ── 价格 & 市场上下文 ─────────────────────────────────────────────────────

def fetch_prices(tickers):
    prices = {}
    for t in set(tickers):
        try:
            h = yf.Ticker(t).history(period="2d")
            if len(h): prices[t] = round(float(h["Close"].iloc[-1]), 2)
        except: pass
    return prices


def build_market_context():
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
        m_lines = [f"{m.get('label','')} {m.get('value','')} ({m.get('detail','')})" for m in macro[:4]]
        parts.append("宏观：" + " | ".join(m_lines))

    news = d.get("news", [])
    if news:
        n_lines = [n.get("headline","") for n in news[:3] if n.get("headline")]
        parts.append("要闻：" + " | ".join(n_lines))

    return "\n\n".join(parts)


# ── 组合状态格式化 ────────────────────────────────────────────────────────

def format_portfolio_state(portfolio, prices, today):
    holdings = portfolio.get("holdings", [])
    lines = []
    total = 0
    cash  = 0
    defensive_val = 0

    for h in holdings:
        t  = h["ticker"]
        s  = h["shares"]
        bd = h.get("buy_date", "")
        if t == "CASH":
            cash   = h["avg_cost"]
            total += cash
            lines.append(f"  CASH: ${cash:,.2f}")
        else:
            p  = prices.get(t, h["avg_cost"])
            v  = s * p
            total += v
            if t in DEFENSIVE_TICKERS:
                defensive_val += v
            # 持仓天数
            days_held = ""
            if bd:
                try:
                    d0 = datetime.strptime(bd, "%Y-%m-%d").date()
                    d1 = datetime.strptime(today, "%Y-%m-%d").date()
                    days_held = f"  (持仓{(d1-d0).days}天)"
                except: pass
            lines.append(f"  {t}: {s}股 × ${p:.2f} = ${v:,.2f}{days_held}")

    defensive_pct = defensive_val / total * 100 if total > 0 else 0
    lines.append(f"  组合总值: ${total:,.2f}  现金: ${cash:,.2f}  防御仓位占比: {defensive_pct:.0f}%")
    return "\n".join(lines), total, cash, defensive_pct


# ── API 调用 ──────────────────────────────────────────────────────────────

def ask_claude(prompt, api_key):
    import urllib.request
    payload = json.dumps({
        "model": "claude-opus-4-5",
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
        "temperature": 0.2,
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
    try: return json.loads(text)
    except: pass
    import re
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except: pass
    return None


# ── Prompt 构建 ───────────────────────────────────────────────────────────

def make_decision_prompt(model_name, portfolio, prices, technicals, market_ctx,
                          today, start_capital, spy_value):
    state_str, total_val, cash, def_pct = format_portfolio_state(portfolio, prices, today)
    pnl      = total_val - start_capital
    pnl_pct  = pnl / start_capital * 100
    alpha    = total_val - spy_value
    alpha_pct = alpha / spy_value * 100

    spy_ret   = (spy_value - start_capital) / start_capital * 100
    tech_table = fmt_tech_table(technicals, prices)

    # 防御仓位警告
    def_warning = ""
    if def_pct > 40:
        def_warning = f"\n⚠️ 你当前防御仓位（GLD/TLT/SLV等）占比 {def_pct:.0f}%，在 SPY 上涨 {spy_ret:+.2f}% 的牛市中这会持续落后！考虑换回成长股。"

    alpha_label = "落后" if alpha < 0 else "领先"
    alpha_warn = f"你{alpha_label}基准 SPY ${abs(alpha):.2f}（{abs(alpha_pct):.2f}%）"
    if alpha < -100:
        alpha_warn += f"——差距已经较大，需要更主动的进攻性仓位！"
    elif alpha < 0:
        alpha_warn += f"——适度增加成长股敞口有助于追回差距。"
    else:
        alpha_warn += f"——继续保持，不要过度防御。"

    # 找持仓不足3天的标的
    short_hold = []
    for h in portfolio.get("holdings", []):
        if h["ticker"] == "CASH": continue
        bd = h.get("buy_date", "")
        if bd:
            try:
                d0 = datetime.strptime(bd, "%Y-%m-%d").date()
                d1 = datetime.strptime(today, "%Y-%m-%d").date()
                if (d1 - d0).days < 3:
                    short_hold.append(h["ticker"])
            except: pass

    short_hold_note = ""
    if short_hold:
        short_hold_note = f"\n⚠️ 以下标的持仓不足3天，非止损情况下不建议卖出：{', '.join(short_hold)}"

    return f"""你是 {model_name}，正在参加 AI 操盘大赛（目标：跑赢 SPY 指数基准）。
今日日期：{today}。初始资金：$10,000（比赛开始 2026-05-13）。

━━━ 当前组合状态 ━━━
{state_str}
绝对盈亏：${pnl:+.2f}（{pnl_pct:+.2f}%）
相对基准：{alpha_warn}
SPY 基准今日价值：${spy_value:,.2f}（基准收益 {spy_ret:+.2f}%）{def_warning}{short_hold_note}

━━━ 技术信号（价格数据，比新闻更客观）━━━
{tech_table}

━━━ 今日市场背景 ━━━
{market_ctx}

━━━ 交易规则 ━━━
1. 只能买卖 ALLOWED_TICKERS 中的标的，shares 必须是正整数
2. 现金不能为负
3. 最多持有 6 个非现金标的
4. **本次最多执行 2 笔交易**（买卖各算1笔）
5. 持仓不足 3 天的标的，非止损不卖出
6. 技术信号"强势↑"的标的优先配置，"弱势↓"的考虑减仓
7. 目标是跑赢 SPY（当前 {spy_ret:+.2f}%）——适度进攻比防御更有效
8. 防御仓位（GLD/TLT/SLV等）在牛市中是拖累，非对冲需要不要超过总仓位20%
9. 无需操作时返回空 trades

输出**严格 JSON**（不加任何解释文字）：
{{
  "trades": [
    {{"action": "BUY or SELL", "ticker": "XXX", "shares": N, "price": P, "reason": "基于技术信号的简短理由（中文）"}}
  ],
  "note": "今日策略一句话总结（中文，50字内）"
}}"""


# ── 纯规则策略 ────────────────────────────────────────────────────────────

def rule_based_decision(prices, technicals, portfolio, today):
    """
    纯规则趋势跟随：
    - 卖出所有"弱势"且持仓>=3天的标的
    - 用现金买入最多2个"强势"标的（按动量排序，平均分配）
    """
    holdings_map = {h["ticker"]: h for h in portfolio.get("holdings", [])
                    if h["ticker"] != "CASH"}
    cash = next((h["avg_cost"] for h in portfolio.get("holdings", [])
                  if h["ticker"] == "CASH"), 0)

    trades = []

    # 卖出弱势标的
    sell_list = []
    for t, h in holdings_map.items():
        info = technicals.get(t)
        if not info:
            continue
        if info["trend"] == "弱势":
            bd = h.get("buy_date", "")
            days = 0
            if bd:
                try:
                    d0 = datetime.strptime(bd, "%Y-%m-%d").date()
                    d1 = datetime.strptime(today, "%Y-%m-%d").date()
                    days = (d1 - d0).days
                except: pass
            if days >= 3:
                sell_list.append(t)

    for t in sell_list[:1]:  # 最多卖1个
        h     = holdings_map[t]
        price = prices.get(t, h["avg_cost"])
        trades.append({"action": "SELL", "ticker": t, "shares": h["shares"],
                        "price": price, "reason": f"技术弱势({technicals[t]['trend']})"})
        cash += h["shares"] * price

    # 已持仓标的
    held = set(holdings_map.keys())

    # 买入强势标的
    strong = sorted(
        [(t, info) for t, info in technicals.items()
         if info and info["trend"] == "强势" and t not in held and t in ALLOWED_TICKERS
         and t not in DEFENSIVE_TICKERS],
        key=lambda x: x[1]["momentum_20d"], reverse=True
    )

    buy_count = max(0, 2 - len(trades))
    per_pos   = cash * 0.45  # 每个仓位最多用45%现金
    for t, info in strong[:buy_count]:
        price = prices.get(t)
        if not price or price <= 0:
            continue
        shares = int(per_pos / price)
        if shares < 1:
            continue
        cost = shares * price
        if cost > cash + 0.01:
            continue
        trades.append({"action": "BUY", "ticker": t, "shares": shares,
                        "price": price, "reason": f"技术强势+动量{info['momentum_20d']:+.1f}%"})
        cash -= cost

    strong_names = [t for t, _ in strong[:3]]
    note = f"规则策略：持有强势{strong_names}，剔除弱势" if strong_names else "规则策略：无强势标的，持币观望"
    return trades, note


# ── 交易执行 ─────────────────────────────────────────────────────────────

def apply_trades(portfolio, trades, prices, cash, today):
    holdings_map = {}
    for h in portfolio.get("holdings", []):
        if h["ticker"] != "CASH":
            holdings_map[h["ticker"]] = {
                "shares":   h["shares"],
                "avg_cost": h["avg_cost"],
                "buy_date": h.get("buy_date", today),
            }

    valid_trades = []
    for t in trades:
        action = t.get("action", "").upper()
        ticker = t.get("ticker", "").upper()
        shares = int(t.get("shares", 0))
        price  = prices.get(ticker, t.get("price", 0))
        if not ticker or shares <= 0 or ticker not in ALLOWED_TICKERS:
            continue
        cost = shares * price
        if action == "BUY":
            if cost > cash + 0.01:
                print(f"  ⚠️ {ticker} BUY {shares}股 跳过（现金不足 ${cash:.2f} < ${cost:.2f}）")
                continue
            cash -= cost
            if ticker in holdings_map:
                old  = holdings_map[ticker]
                tot  = old["shares"] + shares
                holdings_map[ticker] = {
                    "shares":   tot,
                    "avg_cost": round((old["shares"] * old["avg_cost"] + shares * price) / tot, 4),
                    "buy_date": old["buy_date"],  # 保留原始买入日期
                }
            else:
                holdings_map[ticker] = {"shares": shares, "avg_cost": price, "buy_date": today}
            t["price"] = price
            valid_trades.append(t)
            print(f"  ✅ BUY  {ticker} {shares}股 @ ${price:.2f}")
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

    new_holdings = [
        {"ticker": t, "shares": v["shares"], "avg_cost": round(v["avg_cost"], 4),
         "buy_date": v["buy_date"]}
        for t, v in holdings_map.items()
    ]
    new_holdings.append({"ticker": "CASH", "shares": 1, "avg_cost": round(cash, 4)})
    return new_holdings, round(cash, 4), valid_trades


def update_portfolio(portfolio, prices, spy_price, start_capital, trades, note, today):
    holdings = portfolio.get("holdings", [])
    cash = 0
    positions = {}
    total = 0

    for h in holdings:
        t = h["ticker"]
        if t == "CASH":
            cash   = h["avg_cost"]
            total += cash
        else:
            p  = prices.get(t, h["avg_cost"])
            v  = round(h["shares"] * p, 2)
            positions[t] = {"shares": h["shares"], "price": p, "value": v}
            total += v

    portfolio_value = round(total, 2)
    baseline_value  = round(spy_price * 13.5296, 2) if spy_price else None

    history = portfolio.get("history", [])
    record  = {
        "date":            today,
        "portfolio_value": portfolio_value,
        "baseline_value":  baseline_value,
        "cash":            cash,
        "positions":       positions,
        "trades":          trades,
        "note":            note,
    }
    if history and history[-1]["date"] == today:
        history[-1] = record
    else:
        history.append(record)

    portfolio["history"] = history
    return portfolio, portfolio_value


# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="打印决策但不写入文件")
    args = parser.parse_args()

    now   = datetime.now(TORONTO_TZ)
    today = now.strftime("%Y-%m-%d")
    print(f"\n🤖 AI 操盘决策 v2 — {today}" + (" [DRY RUN]" if args.dry_run else ""))

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key    = os.environ.get("OPENAI_API_KEY", "")

    with open(PORTFOLIO_FILE) as f:
        data = json.load(f)

    contest    = data["contest"]
    portfolios = data["portfolios"]
    start_cap  = contest["start_capital"]

    # 初始化 rule_based 组合（如不存在）
    if "rule_based" not in portfolios:
        portfolios["rule_based"] = {
            "display_name": "规则策略",
            "emoji": "📐",
            "holdings": [{"ticker": "CASH", "shares": 1, "avg_cost": 10000.0}],
            "history":  []
        }
        print("  ℹ️ 初始化 rule_based 组合 $10,000")

    # 收集需要的 tickers
    needed = set(["SPY"])
    for pf in portfolios.values():
        for h in pf.get("holdings", []):
            if h["ticker"] != "CASH":
                needed.add(h["ticker"])
    for t in ALLOWED_TICKERS:
        needed.add(t)

    print(f"\n📊 获取价格（{len(needed)} 个标的）…")
    prices = fetch_prices(list(needed))
    spy_price = prices.get("SPY")
    spy_value = round(spy_price * 13.5296, 2) if spy_price else start_cap
    print(f"  SPY: ${spy_price:.2f} → 基准价值 ${spy_value:,.2f}" if spy_price else "  SPY 获取失败")

    print(f"\n📈 计算技术信号…")
    technicals = compute_technicals(ALLOWED_TICKERS)
    strong_cnt = sum(1 for v in technicals.values() if v and v["trend"] == "强势")
    weak_cnt   = sum(1 for v in technicals.values() if v and v["trend"] == "弱势")
    print(f"  强势: {strong_cnt}  弱势: {weak_cnt}  中性: {len(technicals)-strong_cnt-weak_cnt}")

    market_ctx = build_market_context()

    # ── AI 模型决策 ────────────────────────────────────────────────────
    model_configs = {
        "claude-sonnet-4-5": {"fn": ask_claude, "key": anthropic_key, "label": "Claude Opus 4.5"},
        "gpt-4o":            {"fn": ask_gpt,    "key": openai_key,    "label": "GPT-4o"},
    }

    for model_id, cfg in model_configs.items():
        if model_id not in portfolios:
            continue
        print(f"\n{'─'*55}")
        print(f"🤖 {cfg['label']} 决策中…")

        pf     = portfolios[model_id]
        prompt = make_decision_prompt(
            cfg["label"], pf, prices, technicals, market_ctx,
            today, start_cap, spy_value
        )

        decision = None
        if cfg["key"] and not args.dry_run:
            try:
                raw      = cfg["fn"](prompt, cfg["key"])
                decision = parse_json_response(raw)
                if decision:
                    print(f"  决策解析成功")
                else:
                    print(f"  ⚠️ JSON 解析失败，原始: {raw[:200]}")
            except Exception as e:
                print(f"  ❌ API 调用失败: {e}")
        elif args.dry_run:
            print(f"  [DRY-RUN] prompt 长度={len(prompt)} 字符，不调用 API")

        if decision:
            # 强制限制：最多 2 笔交易
            raw_trades = decision.get("trades", [])[:2]
            note = decision.get("note", "")
            cash = next((h["avg_cost"] for h in pf["holdings"] if h["ticker"] == "CASH"), 0)
            new_holdings, new_cash, valid_trades = apply_trades(pf, raw_trades, prices, cash, today)
            pf["holdings"] = new_holdings
        else:
            valid_trades = []
            note = pf["history"][-1].get("note", "") if pf.get("history") else ""

        pf, pv = update_portfolio(pf, prices, spy_price, start_cap, valid_trades, note, today)
        portfolios[model_id] = pf

        alpha = pv - spy_value
        print(f"  组合: ${pv:,.2f}  基准: ${spy_value:,.2f}  超额: ${alpha:+,.2f}")

    # ── 纯规则策略 ────────────────────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f"📐 Rule-Based 策略决策…")
    rb_pf = portfolios["rule_based"]
    rb_trades_raw, rb_note = rule_based_decision(prices, technicals, rb_pf, today)
    rb_cash = next((h["avg_cost"] for h in rb_pf["holdings"] if h["ticker"] == "CASH"), 0)

    if not args.dry_run:
        rb_holdings, rb_cash2, rb_valid = apply_trades(rb_pf, rb_trades_raw, prices, rb_cash, today)
        rb_pf["holdings"] = rb_holdings
    else:
        rb_valid = rb_trades_raw
        print(f"  [DRY-RUN] 拟执行交易: {rb_trades_raw}")

    rb_pf, rb_pv = update_portfolio(rb_pf, prices, spy_price, start_cap, rb_valid, rb_note, today)
    portfolios["rule_based"] = rb_pf
    rb_alpha = rb_pv - spy_value
    print(f"  组合: ${rb_pv:,.2f}  基准: ${spy_value:,.2f}  超额: ${rb_alpha:+,.2f}")
    print(f"  策略: {rb_note}")

    # ── 排名汇总 ─────────────────────────────────────────────────────
    print(f"\n{'═'*55}")
    print(f"📊 今日排名（基准 SPY=${spy_value:,.2f}）")
    rankings = []
    for pid, pf in portfolios.items():
        if not pf.get("history"): continue
        pv = pf["history"][-1].get("portfolio_value", start_cap)
        rankings.append((pid, pv, pv - spy_value))
    rankings.sort(key=lambda x: x[1], reverse=True)
    for rank, (pid, pv, alpha) in enumerate(rankings, 1):
        print(f"  #{rank} {pid:<25} ${pv:,.2f}  vs SPY: {alpha:+,.2f}")

    # ── 写入文件 ─────────────────────────────────────────────────────
    data["portfolios"] = portfolios

    if not args.dry_run:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n✅ ai-portfolio.json 已更新")
    else:
        print(f"\n[DRY-RUN] 未写入文件")


if __name__ == "__main__":
    main()
