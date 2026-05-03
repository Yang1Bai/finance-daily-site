#!/usr/bin/env python3
"""
Telegram 订阅处理器
每 30 分钟由 GitHub Actions 触发，处理 @GGmanm_bot 的订阅/退订请求。

用户命令：
  /subscribe 或 /start subscribe  →  加入每日投资早报
  /unsubscribe                    →  取消订阅
  /start                          →  欢迎消息 + 订阅说明
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ─── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8518954174:AAHIWuxR4DDTtqxqjFzeUi33WxFUtnLyQQc")
API_BASE       = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

ROOT_DIR     = Path(__file__).resolve().parent.parent
SUBS_FILE    = ROOT_DIR / "data" / "investment-subscribers.json"
OFFSET_FILE  = ROOT_DIR / "data" / "telegram-offset.json"

WELCOME_MSG = """👋 欢迎关注 *金融日报* @GGmanm_bot！

📊 每个交易日自动推送每日投资早报，包含：
• 全球主要指数涨跌
• AI & 科技 / 新能源 / 加密行情
• A股 / 港股最新动态
• 大宗商品（黄金、原油、铜）
• 趋势判断 + 市场情绪

发送 /subscribe 开始接收每日推送
发送 /unsubscribe 取消订阅

🌐 网页版：https://yang1bai.github.io/finance-daily-site/"""

SUBSCRIBE_OK  = "✅ 订阅成功！明天早上 8:30 Toronto 时间，你将收到第一份每日投资早报。\n\n发送 /unsubscribe 可随时取消。"
ALREADY_SUBBED = "👌 你已经订阅了，无需重复操作。\n\n发送 /unsubscribe 可取消订阅。"
UNSUBSCRIBE_OK = "🔕 已取消订阅，你不会再收到每日早报推送。\n\n发送 /subscribe 可随时重新订阅。"
NOT_SUBBED    = "ℹ️ 你当前没有活跃订阅。\n\n发送 /subscribe 开始接收每日投资早报。"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_subscribers() -> dict:
    try:
        return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"subscribers": {}, "stats": {}}


def save_subscribers(data: dict):
    SUBS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_offset() -> int:
    try:
        return json.loads(OFFSET_FILE.read_text())["offset"]
    except Exception:
        return 0


def save_offset(offset: int):
    OFFSET_FILE.write_text(json.dumps({"offset": offset}))


def send_message(chat_id: str | int, text: str):
    resp = requests.post(f"{API_BASE}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=10)
    return resp.ok


def get_updates(offset: int) -> list:
    resp = requests.get(f"{API_BASE}/getUpdates", params={
        "offset": offset,
        "timeout": 5,
        "limit": 100,
    }, timeout=15)
    if resp.ok:
        return resp.json().get("result", [])
    return []


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("🤖 Telegram Subscription Handler starting...")
    offset  = load_offset()
    updates = get_updates(offset)

    if not updates:
        print("  ✅ No new updates")
        return

    data    = load_subscribers()
    subs    = data.setdefault("subscribers", {})
    changed = False

    for update in updates:
        update_id = update["update_id"]
        offset    = update_id + 1

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        chat_id = str(msg["chat"]["id"])
        text    = (msg.get("text") or "").strip()
        name    = msg.get("from", {}).get("first_name", "朋友")

        print(f"  📨 [{chat_id}] {text!r}")

        # /start (with optional deep-link payload)
        if text.startswith("/start"):
            payload = text[6:].strip()
            if payload in ("subscribe", "sub"):
                # Deep-link subscribe
                if subs.get(chat_id, {}).get("active"):
                    send_message(chat_id, ALREADY_SUBBED)
                else:
                    subs[chat_id] = {"active": True, "joined": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "name": name}
                    send_message(chat_id, SUBSCRIBE_OK)
                    changed = True
                    print(f"    ✅ Subscribed: {chat_id}")
            else:
                send_message(chat_id, WELCOME_MSG)

        # /subscribe
        elif text in ("/subscribe", "/sub"):
            if subs.get(chat_id, {}).get("active"):
                send_message(chat_id, ALREADY_SUBBED)
            else:
                subs[chat_id] = {"active": True, "joined": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "name": name}
                send_message(chat_id, SUBSCRIBE_OK)
                changed = True
                print(f"    ✅ Subscribed: {chat_id}")

        # /unsubscribe
        elif text in ("/unsubscribe", "/unsub", "/stop"):
            if subs.get(chat_id, {}).get("active"):
                subs[chat_id]["active"] = False
                subs[chat_id]["left"]   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                send_message(chat_id, UNSUBSCRIBE_OK)
                changed = True
                print(f"    🔕 Unsubscribed: {chat_id}")
            else:
                send_message(chat_id, NOT_SUBBED)

        # /status
        elif text == "/status":
            sub_info = subs.get(chat_id)
            if sub_info and sub_info.get("active"):
                stats = data.get("stats", {})
                last_bc = stats.get("last_broadcast", "暂无")
                msg_text = f"✅ 你已订阅每日投资早报\n📅 加入时间：{sub_info.get('joined','未知')}\n📡 上次推送：{last_bc}"
            else:
                msg_text = "❌ 你当前未订阅\n\n发送 /subscribe 开始接收每日早报"
            send_message(chat_id, msg_text)

    save_offset(offset)
    if changed:
        save_subscribers(data)
        count = sum(1 for s in subs.values() if s.get("active"))
        print(f"  💾 Saved subscribers ({count} active)")
    else:
        print(f"  ℹ️  No subscription changes ({sum(1 for s in subs.values() if s.get('active'))} active)")


if __name__ == "__main__":
    main()
