#!/usr/bin/env python3
"""
每日播客素材包 - 生成详细文字资料推送到 Telegram
在 fetch_content.py 之后运行
"""

import json, os, sys, datetime, requests
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
DATA     = ROOT / "data"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8518954174:AAHIWuxR4DDTtqxqjFzeUi33WxFUtnLyQQc")
TG_CHAT  = "8727904480"


def tg_send(text: str, parse_mode: str = "HTML"):
    """Send one Telegram message (≤4096 chars)"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TG_CHAT,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }, timeout=15)
    return resp.ok


def tg_send_long(text: str):
    """Split and send long text in chunks"""
    LIMIT = 4000
    chunks = []
    while text:
        if len(text) <= LIMIT:
            chunks.append(text)
            break
        # Find last newline before limit
        cut = text.rfind("\n", 0, LIMIT)
        if cut == -1:
            cut = LIMIT
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    for i, chunk in enumerate(chunks):
        ok = tg_send(chunk)
        if not ok:
            print(f"  ⚠️  Chunk {i+1}/{len(chunks)} failed")


def load_data() -> dict:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for path in [DATA / f"{today}.json", DATA / "latest.json"]:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    print("❌ No data file found"); sys.exit(1)


def fmt(data: dict) -> list[str]:
    """Return list of message strings (one per section)"""
    msgs = []
    date = data.get("date", datetime.datetime.now().strftime("%Y年%-m月%-d日"))

    # ── Message 1: Header + Summary ──────────────────────────────────────────
    s = data.get("summary", {})
    sentiment_map = {"bullish": "📈 看多", "bearish": "📉 看空", "neutral": "➡️ 中性"}
    sentiment = sentiment_map.get(s.get("sentiment", "neutral"), "➡️ 中性")
    vix = s.get("vix", "N/A")
    headline = s.get("headline", "")
    context  = s.get("context", "")
    kp_lines = "\n".join(f"  • {p}" for p in s.get("key_points", []))

    msg1 = f"🎙️ <b>金融日报播客素材包</b>\n📅 {date}\n{'─'*30}\n\n"
    msg1 += f"<b>【今日大势】</b> {sentiment}  VIX {vix}\n"
    msg1 += f'<b>\u201c{headline}\u201d</b>\n\n'
    if kp_lines:
        msg1 += f"今日要点:\n{kp_lines}\n\n"
    if context:
        msg1 += f"背景:{context}"
    msgs.append(msg1)

    # ── Message 2: Top Indices ───────────────────────────────────────────────
    indices = data.get("indices", [])
    if indices:
        lines = [f"<b>【主要指数】</b>"]
        for i in indices[:12]:
            d = i.get("direction", "neutral")
            arrow = "▲" if d == "up" else ("▼" if d == "down" else "-")
            lines.append(f"  {arrow} {i['name']}  {i.get('value','')}  {i.get('change_pct','')}")
        msgs.append("\n".join(lines))

    # ── Message 3: News ──────────────────────────────────────────────────────
    news = data.get("news", [])
    if news:
        lines = ["<b>【市场要闻】</b>"]
        imp_icon = {"breaking": "🔥", "major": "⚡", "normal": "📌"}
        for n in news:
            icon = imp_icon.get(n.get("importance", "normal"), "📌")
            lines.append(f"\n{icon} <b>{n['title']}</b>")
            if n.get("body"):
                lines.append(f"  {n['body']}")
            if n.get("tldr"):
                lines.append(f"  💡 {n['tldr']}")
        msgs.append("\n".join(lines))

    # ── Message 4: AI Watchlist ──────────────────────────────────────────────
    wl = data.get("watchlist_analysis", [])
    if wl:
        lines = ["<b>【AI决策仪表盘】</b>"]
        sig_icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
        for w in wl:
            icon = sig_icon.get(w.get("signal", "HOLD"), "⚪")
            lines.append(f"\n{icon} <b>{w['name']} ({w['ticker']})</b>  信号:{w.get('signal','')}  评分:{w.get('score','')}/100")
            if w.get("conclusion"):
                lines.append(f"  结论:{w['conclusion']}")
            if w.get("key_driver"):
                lines.append(f"  驱动:{w['key_driver']}")
            up, dn = w.get("target_up",""), w.get("target_down","")
            if up or dn:
                lines.append(f"  目标:↑{up}  ↓{dn}  风险:{w.get('risk_level','')}")
            if w.get("action"):
                lines.append(f"  操作:{w['action']}")
        msgs.append("\n".join(lines))

    # ── Message 5: Bull/Bear Debate ──────────────────────────────────────────
    debate = data.get("market_debate", {})
    if debate:
        lines = [f"<b>【多空辩论】</b> {debate.get('subject','')}"]
        lines.append("\n🐂 <b>多方看涨:</b>")
        for b in debate.get("bull_case", []):
            lines.append(f"  • {b['point']}")
        lines.append("\n🐻 <b>空方看跌:</b>")
        for b in debate.get("bear_case", []):
            lines.append(f"  • {b['point']}")
        lean_map = {"bullish": "📈 偏多", "bearish": "📉 偏空", "neutral": "➡️ 中性"}
        lean = lean_map.get(debate.get("verdict_lean", "neutral"), "➡️ 中性")
        lines.append(f"\n⚖️ <b>裁判:</b>{lean}\n{debate.get('verdict','')}")
        msgs.append("\n".join(lines))

    # ── Message 6: Macro ─────────────────────────────────────────────────────
    macro = data.get("macro", [])
    if macro:
        lines = ["<b>【宏观指标】</b>"]
        dir_icon = {"up": "↑", "down": "↓", "neutral": "→"}
        for m in macro:
            arrow = dir_icon.get(m.get("direction", "neutral"), "→")
            lines.append(f"\n{arrow} <b>{m['indicator']}</b>  {m.get('value','')}(前值 {m.get('prev','')})")
            if m.get("description"):
                lines.append(f"   {m['description']}")
        msgs.append("\n".join(lines))

    # ── Message 7: Earnings ──────────────────────────────────────────────────
    earnings = data.get("earnings", [])
    if earnings:
        lines = ["<b>【财报追踪】</b>"]
        for e in earnings:
            beat = e.get("beat")
            badge = "✅ BEAT" if beat else ("❌ MISS" if beat is False else "⏳ 即将发布")
            lines.append(f"\n{badge}  <b>{e.get('company','')} ({e.get('ticker','')})</b>")
            if beat is not None:
                lines.append(f"  EPS: 实际 ${e.get('eps_actual','')}  预期 ${e.get('eps_est','')}  营收 {e.get('revenue','')}")
            else:
                lines.append(f"  发布日期:{e.get('report_date','TBD')}  预期EPS ${e.get('eps_est','')}")
            if e.get("highlight"):
                lines.append(f"  亮点:{e['highlight']}")
        msgs.append("\n".join(lines))

    # ── Message 8: Sectors ───────────────────────────────────────────────────
    sectors = data.get("sectors", [])
    if sectors:
        lines = ["<b>【板块表现】</b>"]
        for sec in sorted(sectors, key=lambda x: float(x.get("change_pct","0%").replace("%","").replace("+","")) if x.get("change_pct","").replace("%","").replace("+","").replace("-","").replace(".","").isdigit() else 0, reverse=True):
            d = sec.get("direction","neutral")
            arrow = "▲" if d == "up" else ("▼" if d == "down" else "-")
            lines.append(f"  {arrow} {sec['name']} ({sec.get('etf','')})  {sec.get('change_pct','')}")
        msgs.append("\n".join(lines))

    # ── Message 9: Central Banks ─────────────────────────────────────────────
    banks = data.get("central_banks", [])
    if banks:
        lines = ["<b>【央行动态】</b>"]
        bias_icon = {"hawkish": "🦅 鹰派", "dovish": "🕊️ 鸽派", "neutral": "➡️ 中性"}
        for b in banks:
            bias = bias_icon.get(b.get("bias","neutral"), "➡️ 中性")
            lines.append(f"\n🏦 <b>{b['bank']}</b>  {bias}  利率:{b.get('rate','')}")
            lines.append(f"  {b.get('action','')}")
            if b.get("note"):
                lines.append(f"  {b['note']}")
            if b.get("next_meeting"):
                lines.append(f"  下次会议:{b['next_meeting']}")
        msgs.append("\n".join(lines))

    # ── Message 10: Calendar ─────────────────────────────────────────────────
    cal = data.get("calendar", [])
    if cal:
        lines = ["<b>【本周日程】</b>"]
        impact_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        for c in cal:
            icon = impact_icon.get(c.get("impact","low"), "⚪")
            lines.append(f"{icon} {c.get('date','')} {c.get('time_et','')}  <b>{c['event']}</b>")
            prev, fore = c.get("previous",""), c.get("forecast","")
            if fore or prev:
                lines.append(f"   预期:{fore}  前值:{prev}")
        msgs.append("\n".join(lines))

    # ── Message 11: Market Replay ────────────────────────────────────────────
    replay = data.get("market_replay", {})
    if replay:
        lines = ["<b>【大盘复盘】</b>"]
        us = replay.get("us", {})
        cn = replay.get("cn", {})
        if us.get("summary"):
            lines.append(f"\n🇺🇸 <b>美股</b>:{us['summary']}")
            lines.append(f"  涨家数 ↑{us.get('advance','')}  跌家数 ↓{us.get('decline','')}")
            if us.get("hot_stock"):
                lines.append(f"  领涨:{us['hot_stock']}")
        if cn.get("summary"):
            lines.append(f"\n🇨🇳 <b>A股</b>:{cn['summary']}")
            lines.append(f"  {cn.get('sh_index','')} {cn.get('sh_change','')}")
            if cn.get("net_inflow"):
                lines.append(f"  北向资金:{cn['net_inflow']}")
            if cn.get("hot_stock"):
                lines.append(f"  领涨:{cn['hot_stock']}")
        msgs.append("\n".join(lines))

    # ── Footer ───────────────────────────────────────────────────────────────
    msgs.append(
        f"{'─'*30}\n"
        f"以上为今日《金融日报》完整素材\n"
        f"🌐 网页版:https://yang1bai.github.io/finance-daily-site/\n"
        f"🎙️ 播客已自动生成,网页顶部可播放"
    )

    return msgs


def main():
    print("=" * 50)
    print("📨 金融日报 - 播客素材推送")
    print("=" * 50)

    data = load_data()
    messages = fmt(data)

    print(f"📊 Date: {data.get('date','')}")
    print(f"📝 Sending {len(messages)} messages...")

    for i, msg in enumerate(messages):
        ok = tg_send(msg)
        status = "✅" if ok else "❌"
        print(f"  {status} Message {i+1}/{len(messages)} ({len(msg)} chars)")

    print("🎉 Done!")


if __name__ == "__main__":
    main()
