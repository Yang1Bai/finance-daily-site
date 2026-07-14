#!/usr/bin/env python3
"""
金融日报 — Daily Financial Dashboard Content Fetcher
Uses Claude API with web_search_20250305 tool to fetch live market data
and inject it into index.html.
"""

import os
import re
import sys
import json
import time
import datetime
import traceback
from pathlib import Path

import anthropic

# ─── Optional json-repair for robustness ────────────────────────────────────
try:
    from json_repair import repair_json
    HAS_JSON_REPAIR = True
except ImportError:
    HAS_JSON_REPAIR = False

# ─── Configuration ────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).resolve().parent.parent
INDEX_HTML  = ROOT_DIR / "index.html"
DATA_DIR    = ROOT_DIR / "data"
ARCHIVE_DIR = ROOT_DIR / "archive"
FEED_FILE   = ROOT_DIR / "feed.xml"
SIGNAL_LEDGER_FILE = DATA_DIR / "signal_ledger.json"

# Watchlist bucket map for correlation guard
_BUCKET_MAP = {
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

MODEL       = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOKENS  = 16000
MAX_SEARCH  = 15

# ─── Prompts ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位服务于北美华人投资者的专业金融市场编辑。
你的核心读者是生活在北美、同时关注美股和中国市场的中文投资者。
你的任务是通过网络搜索获取最新市场数据，输出严格JSON格式的日报内容。

重要规则：
1. 只输出JSON，不要有任何其他文字、解释或markdown代码块
2. 所有正文使用中文简体；指标名称、公司代码、会议名称可保留英文
3. JSON格式必须完整正确，所有字符串用双引号
4. 搜索真实最新数据，不要编造数字；数据来源尽量注明日期
5. 视角偏重：①美股及美联储 ②A股/港股/人民币汇率 ③中美关系对市场的影响
6. 日期格式：date字段用"YYYY年M月D日"，report_date/next_meeting用"YYYY-MM-DD"
"""

USER_PROMPT_TEMPLATE = """请搜索今天（{today}）的最新全球金融市场数据，为北美华人投资者生成日报。

请按顺序搜索以下9类信息：
0. **今日大势summary**：先综合搜索今日整体市场表现，形成一句话判断 + VIX当前值
1. 全球主要股指、商品、加密货币最新价格和涨跌幅（S&P500、纳斯达克、道琼斯、上证综指、恒生指数、日经225、德国DAX、黄金现货、原油WTI、比特币、美元指数DXY、离岸人民币CNH/USD）
2. 顶级金融领袖最新观点（巴菲特、达利欧、杰米·戴蒙、鲍威尔等，最近1-2周内的言论）
3. 过去24-48小时内的重大市场新闻，重点关注：①中美贸易/关税动态 ②美联储官员讲话 ③科技巨头财报 ④A股/港股重要事件（至少6条，标注importance）
4. 最新宏观经济数据发布（CPI、NFP、PCE、GDP、PMI、失业率等）
5. 本周重要财报结果及即将发布的财报
6. 今日标普500各行业板块表现（全部11个GICS行业）
7. 主要央行最新动态（美联储、欧央行、中国人民银行、日本银行）
8. **未来7天经济日历calendar**：重要经济数据发布时间、央行会议、重要讲话（含预期值）
9. **自选股AI决策仪表盘（watchlist_analysis）**：搜索以下10个标的的最新价格、近期新闻和技术面关键点（均线、RSI、近期支撑/压力位），为每个标的生成AI分析：NVDA(英伟达)、AMD(超威半导体)、NOK(诺基亚)、QQQ(纳斯达克100 ETF)、SPY(标普500 ETF)、BTC-USD(比特币)、510300.SS(沪深300 ETF)、GC=F(黄金)、CL=F(原油)、TSLA(特斯拉)
10. **多空辩论（market_debate）**：基于当前市场环境，对标普500本周展望进行多空分析
11. **大盘复盘（market_replay）**：搜索今日美股和A股收盘数据，包括涨跌家数、热门板块、资金流向
12. **市场极端行情（market_extremes）**：搜索过去1个月美股市场两类标的：
   - top_gainers：过去1个月涨幅最大的前5支标的（知名大盘股或ETF），附上涨原因、当前价格、月涨幅
   - top_losers_with_rebound：过去1个月跌幅最大但具备触底反弹条件的前5支标的，附下跌原因、反弹逻辑、潜在催化剂、风险提示

必须输出以下JSON格式（只输出JSON，无其他内容）：

{{
  "date": "{today}",
  "summary": {{
    "headline": "一句话大势判断，15-25字，点出最核心驱动因素",
    "context": "2-3句背景说明，解释关键驱动力，以及对北美华人投资者的具体影响",
    "sentiment": "bullish|bearish|neutral",
    "vix": "VIX当前值如 '16.4'",
    "key_points": ["今日最重要的事1，15字以内", "今日最重要的事2，15字以内", "今日最重要的事3，15字以内"]
  }},
  "indices": [
    {{
      "name": "S&P 500",
      "symbol": "SPX",
      "value": "5,234.18",
      "change": "+12.5",
      "change_pct": "+0.24%",
      "direction": "up"
    }}
  ],
  "leaders": [
    {{
      "name": "巴菲特",
      "name_en": "Warren Buffett",
      "role": "伯克希尔哈撒韦CEO",
      "quote": "简短的金句或关键观点引用",
      "body": "详细描述其最新观点、发言背景和市场影响，2-3句话",
      "tags": ["#价值投资", "#美股"],
      "initials": "WB",
      "quote_date": "M月D日"
    }}
  ],
  "news": [
    {{
      "title": "新闻标题",
      "body": "新闻摘要，2-3句话说明事件、影响和市场反应",
      "tldr": "对普通人意味着什么，白话1句，15-25字，如：'持有美股ETF的投资者本周或承压，建议关注回撤机会'",
      "url": "https://原始新闻链接（如无则省略此字段）",
      "importance": "breaking",
      "tags": ["#美股", "#美联储"]
    }}
  ],
  "macro": [
    {{
      "indicator": "CPI YoY",
      "value": "3.5%",
      "prev": "3.2%",
      "direction": "up",
      "description": "通胀意外反弹，高于预期的3.4%，降息预期推迟"
    }}
  ],
  "earnings": [
    {{
      "company": "Apple",
      "ticker": "AAPL",
      "report_date": "2025-05-01",
      "eps_actual": "1.53",
      "eps_est": "1.50",
      "beat": true,
      "revenue": "$90.8B",
      "highlight": "服务收入创历史新高，宣布1100亿美元回购"
    }}
  ],
  "sectors": [
    {{
      "name": "科技",
      "name_en": "Technology",
      "etf": "XLK",
      "change_pct": "+1.82%",
      "direction": "up",
      "note": "AI芯片需求持续驱动"
    }}
  ],
  "central_banks": [
    {{
      "bank": "美联储",
      "bank_en": "Federal Reserve",
      "action": "维持利率5.25-5.50%不变",
      "next_meeting": "2025-06-18",
      "rate": "5.25-5.50%",
      "bias": "hawkish",
      "note": "鲍威尔强调需要看到更多通胀降温证据才会降息"
    }}
  ],
  "calendar": [
    {{
      "date": "周二 5/6",
      "time_et": "10:00 AM ET",
      "event": "ISM服务业PMI",
      "impact": "high|medium|low",
      "previous": "51.4",
      "forecast": "52.0"
    }}
  ],
  "watchlist_analysis": [
    {{
      "ticker": "NVDA",
      "name": "英伟达",
      "price": "当前价格如 875.40",
      "change_pct": "+2.5%",
      "direction": "up",
      "signal": "BUY",
      "score": 78,
      "conclusion": "一句话核心结论，不超过20字",
      "key_driver": "最关键驱动因素，1句",
      "target_up": "上行目标价如 950",
      "target_down": "下行风险位如 820",
      "risk_level": "medium",
      "action": "具体操作建议，1-2句",
      "tags": ["#AI", "#半导体"]
    }},
    {{
      "ticker": "AMD",
      "name": "超威半导体",
      "price": "当前价格如 165.20",
      "change_pct": "+1.8%",
      "direction": "up",
      "signal": "BUY",
      "score": 72,
      "conclusion": "一句话核心结论，不超过20字",
      "key_driver": "最关键驱动因素，1句",
      "target_up": "上行目标价如 185",
      "target_down": "下行风险位如 150",
      "risk_level": "medium",
      "action": "具体操作建议，1-2句",
      "tags": ["#AI", "#半导体"]
    }},
    {{
      "ticker": "NOK",
      "name": "诺基亚",
      "price": "当前价格如 4.50",
      "change_pct": "+0.5%",
      "direction": "up",
      "signal": "HOLD",
      "score": 55,
      "conclusion": "一句话核心结论，不超过20字",
      "key_driver": "最关键驱动因素，1句",
      "target_up": "上行目标价如 5.20",
      "target_down": "下行风险位如 4.10",
      "risk_level": "medium",
      "action": "具体操作建议，1-2句",
      "tags": ["#5G", "#电信设备"]
    }}
  ],
  "market_debate": {{
    "subject": "标普500 本周展望",
    "bull_case": [
      {{"point": "多方论点1，15-25字", "confidence": "high"}},
      {{"point": "多方论点2，15-25字", "confidence": "high"}},
      {{"point": "多方论点3，15-25字", "confidence": "medium"}}
    ],
    "bear_case": [
      {{"point": "空方论点1，15-25字", "confidence": "high"}},
      {{"point": "空方论点2，15-25字", "confidence": "high"}},
      {{"point": "空方论点3，15-25字", "confidence": "medium"}}
    ],
    "verdict": "综合裁判，50字以内，给出倾向性判断",
    "verdict_lean": "bullish"
  }},
  "market_replay": {{
    "us": {{
      "date": "日期",
      "advance": "3240",
      "decline": "1820",
      "new_high": "创52周新高家数",
      "new_low": "创52周新低家数",
      "top_sectors": ["科技", "能源"],
      "hot_stock": "今日最热标的(涨幅)",
      "volume_note": "成交量简评，1句",
      "summary": "美股今日一句话复盘，20字"
    }},
    "cn": {{
      "date": "日期",
      "sh_index": "3300.50",
      "sh_change": "+0.8%",
      "advance": "2800",
      "decline": "1500",
      "net_inflow": "+52亿",
      "hot_sector": "今日最热板块",
      "hot_stock": "A股今日领涨股(涨幅)",
      "summary": "A股今日一句话复盘，20字"
    }}
  }},
  "market_extremes": {{
    "top_gainers": [
      {{
        "ticker": "XXX",
        "name": "公司名",
        "gain_pct": "+45.2%",
        "period": "近1个月",
        "current_price": "125.30",
        "reason": "上涨核心原因，1-2句",
        "tags": ["#科技", "#AI"]
      }}
    ],
    "top_losers_with_rebound": [
      {{
        "ticker": "XXX",
        "name": "公司名",
        "loss_pct": "-35.0%",
        "period": "近1个月",
        "current_price": "45.20",
        "drop_reason": "下跌原因，1句",
        "rebound_case": "反弹逻辑，1-2句",
        "rebound_catalyst": "潜在催化剂，1句",
        "risk": "主要风险，1句",
        "tags": ["#超跌", "#反弹机会"]
      }}
    ]
  }}
}}

请确保：
- indices数组包含至少10个资产
- leaders数组包含3-5位领袖
- news数组包含至少5条新闻
- macro数组包含至少6个指标
- earnings数组包含3-8个公司
- sectors数组包含全部11个标普500行业（使用GICS标准）
- central_banks数组包含至少4家央行（Fed、ECB、PBOC、BOJ）
- calendar数组包含未来7天内至少5个重要经济事件，impact用high/medium/low区分
- summary必须填写，headline要有具体指向，不能是空泛表述
- watchlist_analysis数组包含全部10个标的（NVDA、AMD、NOK、QQQ、SPY、BTC-USD、510300.SS、GC=F、CL=F、TSLA），signal只能是BUY/SELL/HOLD，score为0-100整数，risk_level只能是low/medium/high
- market_debate包含bull_case和bear_case各3条，confidence只能是high/medium，verdict_lean只能是bullish/bearish/neutral
- market_replay包含us和cn两个对象，数据尽量真实
- market_extremes必须包含top_gainers（5支）和top_losers_with_rebound（5支）
"""

# ─── HTML rendering ───────────────────────────────────────────────────────────


def render_summary(summary: dict) -> str:
    if not summary:
        return ""
    sentiment = summary.get("sentiment", "neutral")
    sentiment_cn = {"bullish": "看多 · Bullish", "bearish": "看空 · Bearish", "neutral": "中性 · Neutral"}
    sentiment_label = sentiment_cn.get(sentiment, "中性")
    vix = summary.get("vix", "N/A")
    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    key_points = summary.get("key_points", [])
    kp_html = ""
    if key_points:
        items_html = "".join(f'<li class="kp-item"><span class="kp-arrow">▸</span>{esc(p)}</li>' for p in key_points[:3])
        kp_html = f'<ul class="key-points">{items_html}</ul>'
    sentiment_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(sentiment, "🟡")
    return f"""<div class="summary-card {sentiment}">
  <div class="summary-top">
    <span class="sentiment-pill {sentiment}">{sentiment_icon} {sentiment_label}</span>
    <span class="vix-badge">VIX <span class="vix-num">{esc(vix)}</span></span>
  </div>
  <div class="summary-headline">{esc(summary.get("headline",""))}</div>
  {kp_html}
  <details class="summary-details">
    <summary class="summary-details-toggle">深度解读 ▸</summary>
    <div class="summary-context">{esc(summary.get("context",""))}</div>
  </details>
</div>"""


def render_calendar(calendar: list) -> str:
    if not calendar:
        return "<div style=\'color:var(--muted);font-size:0.8rem;padding:10px\'>暂无本周日程数据</div>"
    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    html_parts = ["<div class=\"calendar-grid\">"]
    for item in calendar:
        impact = item.get("impact", "medium")
        prev = item.get("previous", "")
        forecast = item.get("forecast", "")
        values_parts = []
        if forecast: values_parts.append(f"预期: <span>{esc(forecast)}</span>")
        if prev:     values_parts.append(f"前值: {esc(prev)}")
        values_html = f"<div class=\"cal-values\">{' &nbsp;&middot;&nbsp; '.join(values_parts)}</div>" if values_parts else ""
        html_parts.append(f"""  <div class="cal-item {impact}">
    <div class="cal-date">{esc(item.get("date",""))}<br><span style="font-size:0.6rem;color:var(--dimmed)">{esc(item.get("time_et",""))}</span></div>
    <div>
      <div class="cal-event">{esc(item.get("event",""))}</div>
      {values_html}
    </div>
  </div>""")
    html_parts.append("</div>")
    return "\n".join(html_parts)


def render_indices(indices: list) -> str:
    html_parts = []
    for idx in indices:
        direction = idx.get("direction", "neutral")
        arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "–")
        change_pct = idx.get("change_pct", "0.00%")
        html_parts.append(f"""<div class="ticker-card {direction}">
  <div class="ticker-name">{idx.get('name','')}</div>
  <div class="ticker-value">{idx.get('value','')}</div>
  <div class="ticker-change {direction}">{arrow} {change_pct}</div>
</div>""")
    return "\n".join(html_parts)


def _avatar_class(initials: str) -> str:
    mapping = {"WB": "wb", "RD": "rd", "JD": "jd", "JP": "jp"}
    return mapping.get(initials.upper(), "default")


def render_leaders(leaders: list) -> str:
    html_parts = []
    for ldr in leaders:
        initials = ldr.get("initials", "??")
        avatar_cls = _avatar_class(initials)
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in ldr.get("tags", []))
        name_en = ldr.get("name_en", "")
        name_en_html = f' <span style="color:var(--muted);font-weight:400;font-size:0.78rem;">{name_en}</span>' if name_en else ""
        html_parts.append(f"""<div class="leader-card">
  <div class="leader-header">
    <div class="leader-avatar {avatar_cls}">{initials}</div>
    <div class="leader-meta">
      <div class="leader-name">{ldr.get('name','')}{name_en_html}</div>
      <div class="leader-role">{ldr.get('role','')}</div>
    </div>
    <div class="leader-date">{ldr.get('quote_date','')}</div>
  </div>
  <div class="leader-quote">"{ldr.get('quote','')}"</div>
  <div class="leader-body">{ldr.get('body','')}</div>
  <div class="tags">{tags_html}</div>
</div>""")
    return "\n".join(html_parts)


def render_news(news: list) -> str:
    html_parts = []
    for i, item in enumerate(news):
        importance = item.get("importance", "normal")
        title = item.get("title", "")
        url = item.get("url", "")
        title_html = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>' if url else title
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in item.get("tags", []))
        tldr = item.get("tldr", "")
        tldr_html = f'<div class="news-tldr"><span class="tldr-label">💡 新手解读</span>{tldr}</div>' if tldr else ""
        featured = " featured" if i < 3 else ""
        body_text = item.get('body','')
        body_html = f'<div class="news-body-collapse">{body_text}</div>' if body_text else ""
        imp_icons = {"breaking": "🔥", "major": "⚡", "normal": "📌"}
        imp_icon = imp_icons.get(importance, "•")
        html_parts.append(f"""<div class="news-item {importance}{featured}" onclick="toggleNews(this)">
  <div class="news-header">
    <span class="imp-dot {importance}"></span>
    <div class="news-title">{title_html}</div>
    <span class="news-expand-icon">＋</span>
  </div>
  {tldr_html}
  {body_html}
  <div class="news-footer">
    <span class="importance-badge {importance}">{imp_icon} {importance.upper()}</span>
    <div class="tags">{tags_html}</div>
  </div>
</div>""")
    return "\n".join(html_parts)


# Indicators where "up" is bad (inflation-related)
_BEARISH_UP = {"CPI","PCE","PPI","CPI YoY","PCE YoY","PPI YoY","失业率","Unemployment","通胀"}

def render_macro(macro: list) -> str:
    html_parts = ["<div class=\"macro-grid\">"]
    for m in macro:
        direction = m.get("direction", "neutral")
        indicator = m.get("indicator","")
        # For inflation-type indicators, "up" is bad
        bearish_up = any(k in indicator for k in _BEARISH_UP)
        if direction == "up":
            val_cls = "macro-value-down" if bearish_up else "macro-value-up"
            arrow_sym, arrow_cls, card_cls = "↑", "up", "dir-up"
        elif direction == "down":
            val_cls = "macro-value-up" if bearish_up else "macro-value-down"
            arrow_sym, arrow_cls, card_cls = "↓", "down", "dir-down"
        else:
            val_cls, arrow_sym, arrow_cls, card_cls = "", "→", "", "dir-neutral"
        def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        html_parts.append(f"""  <div class="macro-card {card_cls}">
    <div class="macro-indicator">{esc(indicator)}</div>
    <div class="macro-value {val_cls}">{esc(m.get('value',''))}</div>
    <div class="macro-prev"><span class="arrow {arrow_cls}">{arrow_sym}</span> 前值: {esc(m.get('prev',''))}</div>
    <div class="macro-desc">{esc(m.get('description',''))}</div>
  </div>""")
    html_parts.append("</div>")
    return "\n".join(html_parts)


def render_earnings(earnings: list) -> str:
    html_parts = ["<div class=\"earnings-grid\">"]
    for e in earnings:
        beat_val = e.get("beat")
        if beat_val is None:
            status_cls = "pending"
            badge_label = "UPCOMING"
        elif beat_val:
            status_cls = "beat"
            badge_label = "BEAT ✓"
        else:
            status_cls = "miss"
            badge_label = "MISS ✗"

        eps_actual = e.get("eps_actual", "")
        eps_est    = e.get("eps_est", "")

        if beat_val is None:
            eps_block = f"""    <div class="earnings-eps">
      <div class="eps-item">
        <div class="eps-label">EPS EST.</div>
        <div class="eps-value est">${eps_est}</div>
      </div>
      <div class="eps-item">
        <div class="eps-label">REPORT DATE</div>
        <div class="eps-value est" style="font-size:0.75rem;">{e.get('report_date','TBD')}</div>
      </div>
    </div>"""
        else:
            eps_block = f"""    <div class="earnings-eps">
      <div class="eps-item">
        <div class="eps-label">EPS ACTUAL</div>
        <div class="eps-value actual {status_cls}">${eps_actual}</div>
      </div>
      <div class="eps-item">
        <div class="eps-label">EPS EST.</div>
        <div class="eps-value est">${eps_est}</div>
      </div>
    </div>"""

        html_parts.append(f"""  <div class="earnings-card {status_cls}">
    <div class="earnings-ticker">{e.get('ticker','')}</div>
    <div class="earnings-company">{e.get('company','')}</div>
    <span class="earnings-beat-badge {status_cls}">{badge_label}</span>
{eps_block}
    <div class="earnings-revenue">收入: <span>{e.get('revenue','N/A')}</span></div>
    <div class="earnings-highlight">{e.get('highlight','')}</div>
  </div>""")
    html_parts.append("</div>")
    return "\n".join(html_parts)


def render_sectors(sectors: list) -> str:
    html_parts = ["<div class=\"sectors-list\">"]
    # Normalize bar widths: find max abs change
    pct_values = []
    for s in sectors:
        raw = s.get("change_pct", "0%").replace("+", "").replace("%", "").strip()
        try:
            pct_values.append(abs(float(raw)))
        except ValueError:
            pct_values.append(0.0)
    max_pct = max(pct_values) if pct_values else 1.0

    for s, pct_abs in zip(sectors, pct_values):
        direction = s.get("direction", "neutral")
        bar_width = min(100, int((pct_abs / max(max_pct, 0.01)) * 90) + 10)
        bar_cls = "up" if direction == "up" else "down"
        pct_label = s.get("change_pct", "0%")
        html_parts.append(f"""  <div class="sector-row">
    <div><div class="sector-name">{s.get('name','')}</div><div class="sector-name-en">{s.get('name_en','')}</div></div>
    <div class="sector-etf">{s.get('etf','')}</div>
    <div class="sector-bar-wrap"><div class="sector-bar {bar_cls}" style="width:{bar_width}%"></div></div>
    <div class="sector-pct {direction}">{pct_label}</div>
  </div>""")
    html_parts.append("</div>")
    return "\n".join(html_parts)


def render_central_banks(central_banks: list) -> str:
    html_parts = ["<div class=\"cb-grid\">"]
    for cb in central_banks:
        bias = cb.get("bias", "neutral")
        html_parts.append(f"""  <div class="cb-card">
    <div class="cb-header">
      <div>
        <div class="cb-name">{cb.get('bank','')}</div>
        <div class="cb-name-en">{cb.get('bank_en','')}</div>
      </div>
      <span class="bias-badge {bias}">{bias.capitalize()}</span>
    </div>
    <div class="cb-rate">{cb.get('rate','')}</div>
    <div class="cb-action">{cb.get('action','')}</div>
    <div class="cb-next">下次会议: <span class="font-mono" style="color:var(--neutral)">{cb.get('next_meeting','TBD')}</span></div>
    <div class="cb-note">{cb.get('note','')}</div>
  </div>""")
    html_parts.append("</div>")
    return "\n".join(html_parts)



def render_watchlist_analysis(items) -> str:
    if not items:
        return "<div style='color:var(--muted);padding:10px'>暂无数据</div>"
    if isinstance(items, dict):
        items = [items]
    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    html_parts = ['<div class="watchlist-grid">']
    for item in items:
        direction = item.get("direction", "neutral")
        signal = item.get("signal", "HOLD")
        risk = item.get("risk_level", "medium")
        score = item.get("score", 50)
        chg = item.get("change_pct", "0%")
        chg_cls = "up" if direction == "up" else ("down" if direction == "down" else "")
        tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in item.get("tags", []))
        t_up = item.get("target_up", "")
        t_dn = item.get("target_down", "")
        targets_html = ""
        if t_up or t_dn:
            targets_html = '<div class="wc-targets">'
            if t_up: targets_html += f'<span class="wc-target-up">↑ {esc(t_up)}</span>'
            if t_dn: targets_html += f'<span class="wc-target-down">↓ {esc(t_dn)}</span>'
            targets_html += '</div>'
        html_parts.append(f"""<div class="watchlist-card">
  <div class="wc-header">
    <div><div class="wc-ticker">{esc(item.get('ticker',''))}</div><div class="wc-name">{esc(item.get('name',''))}</div></div>
    <div class="wc-price"><div class="wc-price-val">{esc(item.get('price',''))}</div><div class="wc-price-chg {chg_cls}">{esc(chg)}</div></div>
  </div>
  <div class="wc-signal-row">
    <span class="signal-badge {signal}">{signal}</span>
    <span class="wc-score">评分 <span>{score}</span>/100</span>
  </div>
  <div class="wc-conclusion">{esc(item.get('conclusion',''))}</div>
  <div class="wc-driver">{esc(item.get('key_driver',''))}</div>
  {targets_html}
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><span class="risk-badge {risk}">风险:{esc(risk)}</span></div>
  <div class="wc-action">{esc(item.get('action',''))}</div>
  <div class="tags" style="margin-top:8px">{tags_html}</div>
</div>""")
    html_parts.append('</div>')
    return "\n".join(html_parts)


def render_market_debate(items) -> str:
    if not items:
        return "<div style='color:var(--muted);padding:10px'>暂无数据</div>"
    debate = items if isinstance(items, dict) else (items[0] if items else {})
    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    bull = debate.get("bull_case", [])
    bear = debate.get("bear_case", [])
    verdict_lean = debate.get("verdict_lean", "neutral")
    def pts(lst):
        rows = []
        for p in lst:
            conf = p.get("confidence", "medium")
            rows.append(f'<div class="debate-point"><span class="conf-badge {conf}">{conf}</span>{esc(p.get("point",""))}</div>')
        return "\n".join(rows)
    return f"""<div class="debate-container">
  <div class="debate-header">{esc(debate.get('subject', '标普500 本周展望'))}</div>
  <div class="debate-sides">
    <div class="debate-side bull">
      <div class="debate-side-title">🐂 多方看涨</div>
      {pts(bull)}
    </div>
    <div class="debate-side bear">
      <div class="debate-side-title">🐻 空方看跌</div>
      {pts(bear)}
    </div>
  </div>
  <div class="debate-verdict">
    <div class="verdict-label">裁判结论 <span class="verdict-lean {verdict_lean}">{verdict_lean.upper()}</span></div>
    <div class="verdict-text">{esc(debate.get('verdict', ''))}</div>
  </div>
</div>"""


def render_market_replay(items) -> str:
    if not items:
        return "<div style='color:var(--muted);padding:10px'>暂无数据</div>"
    replay = items if isinstance(items, dict) else (items[0] if items else {})
    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    us = replay.get("us", {})
    cn = replay.get("cn", {})
    def adv_pct(adv, dec):
        try:
            a, d = int(str(adv).replace(",","")), int(str(dec).replace(",",""))
            total = a + d
            return round(a / total * 100) if total else 50
        except: return 50
    def card(market, data, ap):
        hot = data.get('hot_sector') or (data.get('top_sectors', ['—'])[0] if data.get('top_sectors') else '—')
        extra_label = '北向资金' if data.get('net_inflow') else '创52周新高'
        extra_val = data.get('net_inflow') or data.get('new_high', '—')
        return f"""<div class="replay-card">
  <div class="replay-card-title">{market}</div>
  <div class="replay-summary">{esc(data.get('summary', ''))}</div>
  <div class="ad-bar-wrap">
    <span class="ad-label-up">↑{esc(data.get('advance', ''))}</span>
    <div class="ad-bar adv" style="width:{ap}px;max-width:80px"></div>
    <div class="ad-bar dec" style="width:{100 - ap}px;max-width:80px"></div>
    <span class="ad-label-dn">↓{esc(data.get('decline', ''))}</span>
  </div>
  <div class="replay-stats">
    <div class="replay-stat"><div class="replay-stat-label">热门板块</div><div class="replay-stat-val">{esc(hot)}</div></div>
    <div class="replay-stat"><div class="replay-stat-label">领涨标的</div><div class="replay-stat-val">{esc(data.get('hot_stock', '—'))}</div></div>
    <div class="replay-stat"><div class="replay-stat-label">{extra_label}</div><div class="replay-stat-val">{esc(str(extra_val))}</div></div>
  </div>
</div>"""
    return f'<div class="replay-grid">{card("🇺🇸 美股", us, adv_pct(us.get("advance", 0), us.get("decline", 0)))}{card("🇨🇳 A股", cn, adv_pct(cn.get("advance", 0), cn.get("decline", 0)))}</div>'


def render_market_extremes(items) -> str:
    if not items:
        return "<div style='color:var(--muted);padding:10px'>暂无数据</div>"
    extremes = items if isinstance(items, dict) else (items[0] if items else {})
    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    gainers = extremes.get("top_gainers", [])
    losers  = extremes.get("top_losers_with_rebound", [])

    def render_gainer(g):
        tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in g.get("tags", []))
        return f"""<div class="extreme-card gain">
  <div class="extreme-header">
    <div><div class="extreme-ticker">{esc(g.get('ticker',''))}</div><div class="extreme-name">{esc(g.get('name',''))}</div></div>
    <div class="extreme-pct gain">{esc(g.get('gain_pct',''))}</div>
  </div>
  <div class="extreme-price">{esc(g.get('current_price',''))}  <span style="color:var(--muted);font-size:0.7rem">{esc(g.get('period',''))}</span></div>
  <div class="extreme-reason">{esc(g.get('reason',''))}</div>
  <div class="tags">{tags_html}</div>
</div>"""

    def render_loser(l):
        tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in l.get("tags", []))
        return f"""<div class="extreme-card loss">
  <div class="extreme-header">
    <div><div class="extreme-ticker">{esc(l.get('ticker',''))}</div><div class="extreme-name">{esc(l.get('name',''))}</div></div>
    <div class="extreme-pct loss">{esc(l.get('loss_pct',''))}</div>
  </div>
  <div class="extreme-price">{esc(l.get('current_price',''))}  <span style="color:var(--muted);font-size:0.7rem">{esc(l.get('period',''))}</span></div>
  <div class="extreme-drop">{esc(l.get('drop_reason',''))}</div>
  <div class="extreme-rebound">🔄 {esc(l.get('rebound_case',''))}</div>
  <div class="extreme-catalyst">⚡ 催化剂: {esc(l.get('rebound_catalyst',''))}</div>
  <div class="extreme-risk">⚠️ 风险: {esc(l.get('risk',''))}</div>
  <div class="tags">{tags_html}</div>
</div>"""

    gainers_html = "\n".join(render_gainer(g) for g in gainers)
    losers_html  = "\n".join(render_loser(l) for l in losers)

    return f"""<div class="extremes-container">
  <div class="extremes-col">
    <div class="extremes-col-title">🚀 近期最强涨势</div>
    {gainers_html}
  </div>
  <div class="extremes-col">
    <div class="extremes-col-title">🎯 超跌反弹机会</div>
    {losers_html}
</div>
</div>"""


# ─── Signal Ledger & Macro Intelligence ─────────────────────────────────────

def load_signal_ledger() -> dict:
    """Load signal_ledger.json if it exists, else return empty dict."""
    if SIGNAL_LEDGER_FILE.exists():
        try:
            return json.loads(SIGNAL_LEDGER_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  Warning: Could not load signal_ledger.json: {e}")
    return {}


def compute_macro_regime(vix_str: str) -> dict:
    """Derive macro regime from VIX level."""
    try:
        vix = float(str(vix_str).replace(",", "").strip())
    except (ValueError, TypeError):
        vix = 20.0
    if vix < 18:
        return {
            "status": "RISK-ON",
            "vix_level": str(vix_str),
            "description": f"VIX {vix:.1f} 低于18，市场风险偏好，可正常开新BUY仓位",
            "allowed_actions": ["BUY", "HOLD", "SELL"],
            "color": "green",
        }
    elif vix < 25:
        return {
            "status": "CAUTION",
            "vix_level": str(vix_str),
            "description": f"VIX {vix:.1f} 在18-25区间，建议仓位减半，止损收紧至-5%",
            "allowed_actions": ["HOLD", "SELL"],
            "color": "yellow",
        }
    else:
        return {
            "status": "RISK-OFF",
            "vix_level": str(vix_str),
            "description": f"VIX {vix:.1f} 超过25，市场恐慌情绪进入防御模式，建议以现金和黄金为主",
            "allowed_actions": ["SELL"],
            "color": "red",
        }


def generate_pre_catalyst_alerts(calendar: list, today_str: str) -> list:
    """Parse calendar events and generate pre-positioning alerts."""
    import re as _re
    if not calendar:
        return []
    try:
        today = datetime.date.fromisoformat(today_str)
    except Exception:
        today = datetime.date.today()

    alerts = []
    ticker_keywords = {
        "NVDA":      ["nvidia", "英伟达", "nvda"],
        "AMD":       ["amd", "超威"],
        "TSLA":      ["tesla", "特斯拉"],
        "QQQ":       ["nasdaq", "纳斯达克"],
        "SPY":       ["s&p", "标普"],
        "GC=F":      ["黄金", "gold"],
        "CL=F":      ["原油", "oil", "wti"],
        "BTC-USD":   ["bitcoin", "比特币", "btc"],
        "510300.SS": ["沪300", "a股", "中国"],
    }
    fomc_keywords = ["fomc", "美联储", "鲍威尔", "federal reserve", "利率决议"]
    cpi_keywords  = ["cpi", "通胀", "inflation"]

    for event in calendar:
        event_name = event.get("event", "")
        date_raw   = event.get("date", "")

        event_date = None
        try:
            event_date = datetime.date.fromisoformat(date_raw)
        except Exception:
            pass
        if event_date is None:
            m = _re.search(r'(\d{1,2})/(\d{1,2})', date_raw)
            if m:
                try:
                    mo, dy = int(m.group(1)), int(m.group(2))
                    event_date = today.replace(month=mo, day=dy)
                    if event_date < today:
                        event_date = event_date.replace(year=event_date.year + 1)
                except Exception:
                    pass
        if event_date is None:
            continue

        days_until = (event_date - today).days
        if days_until < 0:
            continue

        active = 2 <= days_until <= 7
        event_lower = event_name.lower()

        alert_type = "economic"
        ticker     = "MACRO"
        strategy   = ""

        if any(kw in event_lower for kw in fomc_keywords):
            alert_type = "fomc"
            ticker     = "MACRO"
            strategy   = ("利率决议临近，风险资产承压，建议关注防御性配置" if days_until <= 14
                           else "提前了解美联储鹰派/鸽派倍号，为利率决议做方向备备")
        elif any(kw in event_lower for kw in cpi_keywords):
            alert_type = "cpi"
            ticker     = "MACRO"
            strategy   = "通胀数据发布前，遭拥大类资产，等数据展示后再入场"
        else:
            for tkr, keywords in ticker_keywords.items():
                if any(kw in event_lower for kw in keywords):
                    ticker = tkr
                    alert_type = ("earnings" if any(w in event_lower
                                  for w in ["财报", "earnings", "earning"])
                                  else "event")
                    strategy   = ("财报前期权溢价高，建议财报后看方向确认"
                                  if alert_type == "earnings" else "重要事件临近，等结果后再操作")
                    break

        if not strategy:
            strategy = "重要事件附近，建议谨慎操作新仓位"

        alerts.append({
            "ticker":                ticker,
            "event":                 event_name,
            "event_date":            event_date.isoformat(),
            "days_until":            days_until,
            "alert_type":            alert_type,
            "pre_position_strategy": strategy,
            "active":                active,
        })

    alerts.sort(key=lambda a: a["days_until"])
    return alerts[:10]


def generate_sector_rotation_signal(sectors: list) -> dict:
    """Rank sectors by change_pct and output rotation signal."""
    if not sectors:
        return {}
    parsed = []
    for s in sectors:
        raw_pct = s.get("change_pct", "0%").replace("+", "").replace("%", "").strip()
        try:
            pct = float(raw_pct)
        except ValueError:
            pct = 0.0
        parsed.append({
            "name":       s.get("name", ""),
            "name_en":    s.get("name_en", ""),
            "etf":        s.get("etf", ""),
            "change_pct": s.get("change_pct", "0%"),
            "pct":        pct,
        })
    parsed.sort(key=lambda x: x["pct"], reverse=True)
    strong = [s["name"] for s in parsed[:3] if s["name"]]
    weak   = [s["name"] for s in parsed[-3:] if s["name"]]
    top    = parsed[0] if parsed else {}
    note = (
        f"今日{top['name']}{top['change_pct']}领涨，建议关注{top['etf']}及{top.get('name_en','')}个股"
        if top.get("name") else "无明显板块轮动信号"
    )
    return {
        "strong_sectors": strong,
        "weak_sectors":   weak,
        "rule":           "只在强势板块开新多仓",
        "note":           note,
    }


def build_signal_context(ledger: dict, vix: str = "N/A") -> str:
    """Build Chinese context block for the AI prompt from the signal ledger."""
    if not ledger:
        return ""

    perf     = ledger.get("performance", {})
    open_pos = ledger.get("open_positions", [])

    regime        = compute_macro_regime(vix)
    regime_status = regime["status"]

    open_buckets: dict = {}
    for pos in open_pos:
        ticker = pos.get("ticker", "")
        bucket = _BUCKET_MAP.get(ticker, "other")
        open_buckets.setdefault(bucket, []).append(ticker)

    restriction_lines = []
    for bucket, tickers in open_buckets.items():
        all_in_bucket = [t for t, b in _BUCKET_MAP.items() if b == bucket]
        blocked = [t for t in all_in_bucket if t not in tickers]
        if blocked:
            restriction_lines.append(
                f"  - 已有 {', '.join(tickers)} BUY 仓位 (桶:{bucket}) -> 不允许新增: {', '.join(blocked)}"
            )

    open_lines = []
    for pos in open_pos:
        pnl  = pos.get("current_pnl_pct", 0.0)
        sign = "+" if pnl >= 0 else ""
        open_lines.append(
            f"  - {pos['ticker']}: BUY @ ${pos['price_issued']:.2f} "
            f"({pos['date_issued']}), P&L: {sign}{pnl:.1f}%, "
            f"持有{pos['days_held']}天, 止损位${pos['stop_loss']:.2f}"
        )

    win_rate_pct = round(perf.get("win_rate", 0) * 100)
    wins   = perf.get("wins", 0)
    losses = perf.get("losses", 0)
    ev     = perf.get("expected_value_pct", 0.0)

    lines = ["\n=== 你的历史信号记录 ==="]
    lines.append(f"胜率: {win_rate_pct}% ({wins}胜/{losses}负) 期望值: {ev:+.2f}%/次")
    if open_lines:
        lines.append("当前持仓:")
        lines.extend(open_lines)
    else:
        lines.append("当前无持仓")

    lines.append("\n规则提示:")
    lines.append(f"  - 当前 VIX: {vix} -> 宏观状态: {regime_status}")
    if regime_status == "RISK-ON":
        lines.append("  - 处于 RISK-ON (VIX<18) 状态，允许开新 BUY 信号")
    elif regime_status == "CAUTION":
        lines.append("  - 处于 CAUTION (VIX 18-25)，建议仓位减半，止损收紧至-5%")
    else:
        lines.append("  - 处于 RISK-OFF (VIX>25)，建议只 SELL，不开新多单")
    lines.append("  - 全局止损: -7%, 止盈: +15%, 最长持仓: 10个交易日")
    lines.append("  - 同一板块不超过1个BUY仓位（NVDA/AMD/QQQ 算同一科技板块）")
    if restriction_lines:
        lines.append("板块冲突限制:")
        lines.extend(restriction_lines)

    return "\n".join(lines) + "\n"


def render_macro_regime(regime) -> str:
    """Render macro_regime dict as HTML card."""
    if not regime:
        return ""
    r = regime if isinstance(regime, dict) else {}
    status = r.get("status", "UNKNOWN")
    vix    = r.get("vix_level", "N/A")
    desc   = r.get("description", "")
    color  = r.get("color", "yellow")
    color_hex = {"green": "#00e676", "yellow": "#ffb300", "red": "#ff5252"}.get(color, "#ffb300")
    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(color, "🟡")
    allowed = ", ".join(r.get("allowed_actions", []))
    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<div class="regime-card" style="border:1px solid {color_hex}33;background:rgba(0,0,0,.3);border-radius:10px;padding:12px 16px;margin-bottom:12px;">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
    <span class="regime-badge" style="background:{color_hex}22;color:{color_hex};border:1px solid {color_hex}44;border-radius:20px;padding:3px 12px;font-weight:600;font-size:.82rem;">{icon} {esc(status)} 模式</span>
    <span style="font-family:var(--mono);color:var(--muted);font-size:.72rem;">VIX {esc(vix)}</span>
    <span style="font-size:.72rem;color:var(--muted);">|允许操作: <strong style="color:{color_hex};">{esc(allowed)}</strong></span>
  </div>
  <div style="margin-top:6px;font-size:.78rem;color:var(--text);">{esc(desc)}</div>
</div>"""


def render_pre_catalyst_alerts(alerts) -> str:
    """Render pre-catalyst alerts as HTML."""
    if not alerts:
        return ""
    if isinstance(alerts, dict):
        alerts = [alerts]
    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    active_icon = {True: "🔴", False: "🟡"}
    type_icon   = {"fomc": "🏦", "earnings": "📊", "cpi": "📈", "economic": "📅", "event": "⚡"}
    items_html = []
    for a in alerts:
        active = a.get("active", False)
        days   = a.get("days_until", 0)
        atype  = a.get("alert_type", "event")
        ticker = a.get("ticker", "")
        event  = a.get("event", "")
        edate  = a.get("event_date", "")
        strat  = a.get("pre_position_strategy", "")
        status_color  = "#ff5252" if active else "#ffb300"
        ticker_badge  = (f'<span style="font-size:.68rem;color:var(--accent);'
                         f'background:rgba(0,212,170,.1);border-radius:4px;padding:1px 6px;">'
                         f'{esc(ticker)}</span>' if ticker != "MACRO" else "")
        items_html.append(f"""  <div class="catalyst-item" style="display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border);">
    <span style="font-size:1.1rem;flex-shrink:0;">{active_icon[active]} {type_icon.get(atype, "📅")}</span>
    <div style="flex:1;">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <strong style="color:var(--text);font-size:.82rem;">{esc(event)}</strong>
        <span style="font-family:var(--mono);font-size:.7rem;color:{status_color};">{days}天后 · {esc(edate)}</span>
        {ticker_badge}
      </div>
      <div style="font-size:.75rem;color:var(--muted);margin-top:3px;">{esc(strat)}</div>
    </div>
  </div>""")
    return f'<div class="pre-catalyst-list">\n{"".join(items_html)}\n</div>'


def render_sector_rotation(signal) -> str:
    """Render sector_rotation_signal as HTML."""
    if not signal:
        return ""
    r = signal if isinstance(signal, dict) else {}
    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    strong      = r.get("strong_sectors", [])
    weak        = r.get("weak_sectors", [])
    note        = r.get("note", "")
    rule        = r.get("rule", "")
    strong_html = "".join(
        f'<span style="background:#00e67622;color:#00e676;border:1px solid #00e67644;'
        f'border-radius:4px;padding:2px 8px;font-size:.75rem;">{esc(s)}</span>' for s in strong)
    weak_html   = "".join(
        f'<span style="background:#ff525222;color:#ff5252;border:1px solid #ff525244;'
        f'border-radius:4px;padding:2px 8px;font-size:.75rem;">{esc(s)}</span>' for s in weak)
    return f"""<div class="sector-rotation-card" style="background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px 16px;">
  <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px;">🔄 板块轮动信号</div>
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;">
    <div><div style="font-size:.7rem;color:var(--muted);margin-bottom:4px;">🟢 强势板块</div><div style="display:flex;gap:4px;flex-wrap:wrap;">{strong_html}</div></div>
    <div><div style="font-size:.7rem;color:var(--muted);margin-bottom:4px;">🔴 弱势板块</div><div style="display:flex;gap:4px;flex-wrap:wrap;">{weak_html}</div></div>
  </div>
  <div style="font-size:.76rem;color:var(--text);">{esc(note)}</div>
  <div style="font-size:.7rem;color:var(--muted);margin-top:4px;">规则: {esc(rule)}</div>
</div>"""


def render_signal_performance(ledger) -> str:
    """Render signal performance dashboard as HTML."""
    if not ledger:
        return "<div style='color:var(--muted);padding:10px'>暂无绩效数据</div>"
    r = ledger if isinstance(ledger, dict) else {}
    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    perf     = r.get("performance", {})
    open_pos = r.get("open_positions", [])

    win_rate = round(perf.get("win_rate", 0) * 100)
    ev       = perf.get("expected_value_pct", 0.0)
    vs_spy   = perf.get("vs_spy_30d", 0.0)
    wins     = perf.get("wins", 0)
    losses   = perf.get("losses", 0)
    total    = perf.get("total_signals", 0)

    vs_cls = "up" if vs_spy >= 0 else "down"
    ev_cls = "up" if ev >= 0 else "down"

    pos_rows = ""
    for p in open_pos:
        pnl     = p.get("current_pnl_pct", 0.0)
        pnl_cls = "up" if pnl >= 0 else "down"
        sign    = "+" if pnl >= 0 else ""
        pos_rows += f"""      <tr>
        <td style="color:var(--accent);font-family:var(--mono);">{esc(p.get('ticker',''))}</td>
        <td>${esc(str(p.get('price_issued','')))}</td>
        <td>{esc(p.get('date_issued',''))}</td>
        <td class="{pnl_cls}">{sign}{esc(str(pnl))}%</td>
        <td>{esc(str(p.get('days_held',0)))}天</td>
        <td style="font-size:.7rem;color:var(--muted);">${esc(str(p.get('stop_loss','')))}</td>
      </tr>
"""
    if open_pos:
        pos_table = f"""<table style="width:100%;font-size:.76rem;border-collapse:collapse;">
      <thead><tr style="color:var(--muted);font-size:.68rem;">
        <th style="text-align:left;padding:4px 6px;">标的</th>
        <th style="text-align:left;padding:4px 6px;">入场价</th>
        <th style="text-align:left;padding:4px 6px;">入场日</th>
        <th style="text-align:left;padding:4px 6px;">P&amp;L</th>
        <th style="text-align:left;padding:4px 6px;">持仓天</th>
        <th style="text-align:left;padding:4px 6px;">止损位</th>
      </tr></thead>
      <tbody>{pos_rows}</tbody>
    </table>"""
    else:
        pos_table = "<div style='color:var(--muted);font-size:.8rem;padding:8px;'>暂无持仓</div>"

    gen_at  = r.get("generated_at", "")[:10]
    ev_sign = "+" if ev >= 0 else ""
    vs_sign = "+" if vs_spy >= 0 else ""
    return f"""<div class="signal-perf-card" style="background:var(--card2);border:1px solid var(--border);border-radius:12px;padding:16px;">
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;">
    <div style="background:var(--card);border-radius:8px;padding:10px 14px;flex:1;min-width:100px;">
      <div style="font-size:.68rem;color:var(--muted);margin-bottom:2px;">胜率</div>
      <div style="font-size:1.3rem;font-weight:700;color:var(--accent);">{win_rate}%</div>
      <div style="font-size:.68rem;color:var(--muted);">{wins}胜 / {losses}负 / {total}总</div>
    </div>
    <div style="background:var(--card);border-radius:8px;padding:10px 14px;flex:1;min-width:100px;">
      <div style="font-size:.68rem;color:var(--muted);margin-bottom:2px;">期望值</div>
      <div style="font-size:1.3rem;font-weight:700;" class="{ev_cls}">{ev_sign}{ev:.2f}%</div>
      <div style="font-size:.68rem;color:var(--muted);">每次信号</div>
    </div>
    <div style="background:var(--card);border-radius:8px;padding:10px 14px;flex:1;min-width:100px;">
      <div style="font-size:.68rem;color:var(--muted);margin-bottom:2px;">vs SPY 30天</div>
      <div style="font-size:1.3rem;font-weight:700;" class="{vs_cls}">{vs_sign}{vs_spy:.1f}%</div>
      <div style="font-size:.68rem;color:var(--muted);">相对收益</div>
    </div>
  </div>
  <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:6px;">📂 当前持仓 ({len(open_pos)})</div>
  {pos_table}
  <div style="font-size:.65rem;color:var(--dimmed);margin-top:8px;">数据更新: {esc(gen_at)} | 止损-7% | 止盈+15% | 最长10交易日</div>
</div>"""


# ─── Anchor injection ─────────────────────────────────────────────────────────

ANCHOR_RENDERERS = {
    "SUMMARY":            render_summary,
    "INDICES":            render_indices,
    "LEADERS":            render_leaders,
    "NEWS":               render_news,
    "MACRO":              render_macro,
    "EARNINGS":           render_earnings,
    "SECTORS":            render_sectors,
    "CENTRAL_BANKS":      render_central_banks,
    "CALENDAR":           render_calendar,
    "WATCHLIST_ANALYSIS": render_watchlist_analysis,
    "MARKET_DEBATE":      render_market_debate,
    "MARKET_REPLAY":      render_market_replay,
    "MARKET_EXTREMES":    render_market_extremes,
    "MACRO_REGIME":        render_macro_regime,
    "PRE_CATALYST_ALERTS": render_pre_catalyst_alerts,
    "SECTOR_ROTATION":    render_sector_rotation,
    "SIGNAL_PERFORMANCE": render_signal_performance,
}


def inject_into_html(html: str, data: dict) -> str:
    section_map = {
        "SUMMARY":            "summary",
        "INDICES":            "indices",
        "LEADERS":            "leaders",
        "NEWS":               "news",
        "MACRO":              "macro",
        "EARNINGS":           "earnings",
        "SECTORS":            "sectors",
        "CENTRAL_BANKS":      "central_banks",
        "CALENDAR":           "calendar",
        "WATCHLIST_ANALYSIS": "watchlist_analysis",
        "MARKET_DEBATE":      "market_debate",
        "MARKET_REPLAY":      "market_replay",
        "MARKET_EXTREMES":    "market_extremes",
        "MACRO_REGIME":        "macro_regime",
        "PRE_CATALYST_ALERTS": "pre_catalyst_alerts",
        "SECTOR_ROTATION":    "sector_rotation_signal",
        "SIGNAL_PERFORMANCE": "_signal_ledger",
    }
    for anchor, key in section_map.items():
        items = data.get(key, [])
        if not items:
            print(f"  ⚠️  No data for section {anchor}, skipping injection")
            continue
        renderer = ANCHOR_RENDERERS[anchor]
        new_content = renderer(items)
        pattern = rf"(<!-- {anchor}:START -->).*?(<!-- {anchor}:END -->)"
        replacement = rf"\1\n{new_content}\n\2"
        html, n = re.subn(pattern, replacement, html, flags=re.DOTALL)
        if n == 0:
            print(f"  ⚠️  Anchor {anchor}:START/END not found in HTML")
        else:
            print(f"  ✅  Injected {len(items)} items into {anchor}")
    # Update meta last-updated
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    if 'name="last-updated"' in html:
        html = re.sub(r'(<meta name="last-updated" content=")[^"]*(")', rf'\g<1>{now_iso}\2', html)
    else:
        html = html.replace("</head>", f'  <meta name="last-updated" content="{now_iso}">\n</head>')
    return html


# ─── Archive generation ───────────────────────────────────────────────────────

def generate_archive_page(html: str, date_str: str, filename: Path) -> None:
    """Save a static snapshot of the day's content."""
    archive_html = html.replace(
        "<title>金融日报 | Financial Daily</title>",
        f"<title>金融日报 {date_str} | Financial Daily</title>"
    ).replace(
        'href="archive/index.html"',
        'href="../index.html"'
    ).replace(
        'href="feed.xml"',
        'href="../feed.xml"'
    )
    filename.write_text(archive_html, encoding="utf-8")
    print(f"  📄 Archive snapshot: {filename.name}")


def rebuild_archive_index(data_dir: Path, archive_dir: Path) -> None:
    """Rebuild archive/index.html listing all past snapshots."""
    index_json = data_dir / "index.json"
    if not index_json.exists():
        return
    try:
        entries = json.loads(index_json.read_text(encoding="utf-8"))
    except Exception:
        entries = []

    rows = ""
    for entry in sorted(entries, key=lambda x: x.get("date_iso", ""), reverse=True):
        date_iso = entry.get("date_iso", "")
        date_cn  = entry.get("date_cn", date_iso)
        rows += f"""    <tr>
      <td><a href="{date_iso}.html">{date_cn}</a></td>
      <td>{entry.get('indices_count', '—')}</td>
      <td>{entry.get('news_count', '—')}</td>
      <td><a href="../data/{date_iso}.json" style="color:var(--muted);font-size:0.75rem;">JSON</a></td>
    </tr>\n"""

    archive_index = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>归档 | Financial Daily Archive</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{ --bg:#070b14; --card:#0d1526; --border:#1a2a4a; --accent:#00d4aa; --text:#e2e8f0; --muted:#8899aa; --mono:'JetBrains Mono',monospace; --sans:'Inter',system-ui,sans-serif; }}
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ font-family:var(--sans); background:var(--bg); color:var(--text); padding:40px 20px; }}
    .container {{ max-width:800px; margin:0 auto; }}
    h1 {{ font-size:1.6rem; margin-bottom:6px; background:linear-gradient(135deg,var(--accent),#4fc3f7); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }}
    p.sub {{ color:var(--muted); font-size:0.8rem; margin-bottom:28px; }}
    a {{ color:var(--accent); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    table {{ width:100%; border-collapse:collapse; }}
    th {{ text-align:left; font-size:0.65rem; font-family:var(--mono); color:var(--muted); letter-spacing:0.08em; text-transform:uppercase; padding:8px 12px; border-bottom:1px solid var(--border); }}
    td {{ padding:10px 12px; font-size:0.82rem; border-bottom:1px solid rgba(26,42,74,0.5); }}
    tr:hover td {{ background:var(--card); }}
    .back {{ display:inline-block; margin-bottom:24px; font-size:0.8rem; color:var(--muted); border:1px solid var(--border); padding:5px 14px; border-radius:6px; }}
    .back:hover {{ color:var(--accent); border-color:var(--accent); text-decoration:none; }}
  </style>
</head>
<body>
  <div class="container">
    <a class="back" href="../index.html">← 返回首页</a>
    <h1>历史归档</h1>
    <p class="sub">Financial Daily — 每日金融市场数据存档</p>
    <table>
      <thead>
        <tr>
          <th>日期</th>
          <th>指数</th>
          <th>新闻</th>
          <th>数据</th>
        </tr>
      </thead>
      <tbody>
{rows}      </tbody>
    </table>
  </div>
</body>
</html>
"""
    (archive_dir / "index.html").write_text(archive_index, encoding="utf-8")
    print("  📋 Rebuilt archive/index.html")


def update_data_index(data_dir: Path, entry: dict) -> None:
    index_json = data_dir / "index.json"
    try:
        entries = json.loads(index_json.read_text(encoding="utf-8")) if index_json.exists() else []
    except Exception:
        entries = []
    # Upsert by date_iso
    existing = {e.get("date_iso"): i for i, e in enumerate(entries)}
    date_iso = entry.get("date_iso", "")
    if date_iso in existing:
        entries[existing[date_iso]] = entry
    else:
        entries.append(entry)
    index_json.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── RSS Feed ─────────────────────────────────────────────────────────────────

def generate_rss(data: dict, feed_file: Path) -> None:
    date_cn   = data.get("date", "")
    pub_date  = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    items_xml = ""
    for article in data.get("news", []):
        title = article.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        desc  = article.get("body", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        link  = article.get("url", "")
        items_xml += f"""  <item>
    <title>{title}</title>
    <description>{desc}</description>
    <link>{link}</link>
    <pubDate>{pub_date}</pubDate>
  </item>
"""
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>金融日报 | Financial Daily</title>
    <description>每日全球金融市场摘要，由 Claude AI + Web Search 自动生成</description>
    <link>https://your-username.github.io/finance-daily-site/</link>
    <language>zh-CN</language>
    <lastBuildDate>{pub_date}</lastBuildDate>
    <item>
      <title>金融日报 {date_cn}</title>
      <description>今日金融市场摘要已更新</description>
      <pubDate>{pub_date}</pubDate>
    </item>
{items_xml}  </channel>
</rss>
"""
    feed_file.write_text(rss, encoding="utf-8")
    print("  📡 Updated feed.xml")


# ─── Claude API call ──────────────────────────────────────────────────────────

def fetch_data_from_claude() -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today = datetime.datetime.now().strftime("%Y年%-m月%-d日")
    user_prompt = USER_PROMPT_TEMPLATE.format(today=today)

    # Load signal ledger and inject context (best-effort: use last known VIX)
    ledger = load_signal_ledger()
    if ledger:
        # Try to get last VIX from ledger metadata or use a placeholder
        last_vix = "N/A"
        try:
            latest_path = DATA_DIR / "latest.json"
            if latest_path.exists():
                latest_data = json.loads(latest_path.read_text(encoding="utf-8"))
                last_vix = latest_data.get("summary", {}).get("vix", "N/A")
        except Exception:
            pass
        signal_ctx = build_signal_context(ledger, last_vix)
        if signal_ctx:
            user_prompt = signal_ctx + "\n" + user_prompt
            print("  📋 Injected signal ledger context into prompt")

    print(f"🤖 Calling Claude {MODEL} with web_search tool...")
    start = time.time()

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": MAX_SEARCH,
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )

    elapsed = time.time() - start
    print(f"   ⏱️  API call completed in {elapsed:.1f}s")

    # Extract text from response
    raw_json = ""
    for block in response.content:
        if block.type == "text":
            raw_json += block.text

    print(f"   📦 Raw response length: {len(raw_json)} chars")

    # Strip markdown code fences if Claude added them
    raw_json = raw_json.strip()
    if raw_json.startswith("```"):
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)
        raw_json = raw_json.strip()

    # Parse JSON
    try:
        data = json.loads(raw_json)
        print("   ✅ JSON parsed successfully")
    except json.JSONDecodeError as e:
        print(f"   ⚠️  JSON parse error: {e}")
        if HAS_JSON_REPAIR:
            print("   🔧 Attempting json-repair...")
            try:
                repaired = repair_json(raw_json)
                data = json.loads(repaired)
                print("   ✅ Repaired JSON parsed successfully")
            except Exception as e2:
                print(f"   ❌ json-repair also failed: {e2}")
                raise ValueError(f"Could not parse Claude response as JSON: {e}") from e
        else:
            print("   💡 Install json-repair for auto-fix: pip install json-repair")
            raise

    return data


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🏦 金融日报 — Financial Daily Content Fetcher")
    print("=" * 60)

    # Ensure directories exist
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)

    # Verify ANTHROPIC_API_KEY
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    # Fetch data from Claude
    try:
        data = fetch_data_from_claude()
    except Exception as e:
        print(f"❌ Failed to fetch data from Claude: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Determine date strings
    now      = datetime.datetime.now()
    date_iso = now.strftime("%Y-%m-%d")
    date_cn  = data.get("date", now.strftime("%Y年%-m月%-d日"))

    print(f"\n📅 Processing data for: {date_cn} ({date_iso})")

    # ─── Post-process: add macro_regime, pre_catalyst_alerts, sector_rotation_signal ───
    vix_str = data.get("summary", {}).get("vix", "N/A")

    # A) Macro Regime
    macro_regime = compute_macro_regime(vix_str)
    data["macro_regime"] = macro_regime
    print(f"   🏧 Macro regime: {macro_regime['status']} (VIX {vix_str})")

    # B) Pre-catalyst alerts from calendar
    pre_catalyst_alerts = generate_pre_catalyst_alerts(
        data.get("calendar", []), date_iso
    )
    data["pre_catalyst_alerts"] = pre_catalyst_alerts
    active_alerts = sum(1 for a in pre_catalyst_alerts if a.get("active"))
    print(f"   ⚡ Pre-catalyst alerts: {len(pre_catalyst_alerts)} total, {active_alerts} active")

    # C) Sector rotation signal
    sector_rotation = generate_sector_rotation_signal(data.get("sectors", []))
    data["sector_rotation_signal"] = sector_rotation
    print(f"   🔄 Sector rotation: strong={sector_rotation.get('strong_sectors',[])}")

    # D) Load signal ledger for HTML rendering
    ledger = load_signal_ledger()
    data["_signal_ledger"] = ledger  # injected for HTML rendering, not persisted to JSON

    # Save raw JSON (without the private _signal_ledger key)
    json_path = DATA_DIR / f"{date_iso}.json"
    latest_path = DATA_DIR / "latest.json"
    # Strip private rendering keys before saving JSON
    save_data = {k: v for k, v in data.items() if not k.startswith("_")}
    json_str = json.dumps(save_data, ensure_ascii=False, indent=2)
    json_path.write_text(json_str, encoding="utf-8")
    latest_path.write_text(json_str, encoding="utf-8")
    print(f"\n💾 Saved: data/{date_iso}.json + data/latest.json")

    # Update data/index.json
    update_data_index(DATA_DIR, {
        "date_iso":     date_iso,
        "date_cn":      date_cn,
        "indices_count": len(data.get("indices", [])),
        "news_count":    len(data.get("news", [])),
    })

    # Read index.html
    if not INDEX_HTML.exists():
        print(f"❌ {INDEX_HTML} not found")
        sys.exit(1)
    html = INDEX_HTML.read_text(encoding="utf-8")

    # Inject content
    print("\n🔧 Injecting sections:")
    html = inject_into_html(html, data)

    # Write updated index.html
    INDEX_HTML.write_text(html, encoding="utf-8")
    print("\n✅ Updated index.html")

    # Generate archive snapshot
    archive_html_path = ARCHIVE_DIR / f"{date_iso}.html"
    generate_archive_page(html, date_cn, archive_html_path)
    rebuild_archive_index(DATA_DIR, ARCHIVE_DIR)

    # Generate RSS feed
    generate_rss(data, FEED_FILE)

    print("\n🎉 Done! Summary:")
    print(f"   📊 Indices:      {len(data.get('indices', []))}")
    print(f"   👔 Leaders:      {len(data.get('leaders', []))}")
    print(f"   📰 News:         {len(data.get('news', []))}")
    print(f"   📈 Macro:        {len(data.get('macro', []))}")
    print(f"   💼 Earnings:     {len(data.get('earnings', []))}")
    print(f"   🏭 Sectors:      {len(data.get('sectors', []))}")
    print(f"   🏦 Central Banks:{len(data.get('central_banks', []))}")
    print("=" * 60)


if __name__ == "__main__":
    main()
