#!/usr/bin/env python3
"""
金融日报 — Daily Podcast Generator
每天用 Claude 生成播客脚本，OpenAI TTS 合成双主持人音频
"""

import os, sys, json, datetime, subprocess, tempfile, shutil
from pathlib import Path

import anthropic
from openai import OpenAI

ROOT   = Path(__file__).resolve().parent.parent
DATA   = ROOT / "data"
AUDIO  = ROOT / "audio"
AUDIO.mkdir(exist_ok=True)

CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
# Host voices: alloy=女主持, echo=男主持
VOICE_A = "shimmer"   # 女主持（活泼）
VOICE_B = "echo"      # 男主持（沉稳）

MAX_SCRIPT_TOKENS = 2000   # 控制音频时长约 3-4 分钟

SCRIPT_SYSTEM = """你是一档面向北美华人投资者的金融播客节目的编剧。
节目名：《金融日报早报》，双主持人对话风格。

主持人设定：
- 甲（欣欣）：女性，活泼、亲切，善于把复杂数据说成大白话，偶尔幽默
- 乙（建国）：男性，沉稳、专业，擅长宏观分析，会在关键处泼冷水

规则：
1. 输出纯文本，格式严格为：每行以 [欣欣] 或 [建国] 开头
2. 总长度控制在 600-800 字，约 3-4 分钟播放
3. 开头10秒必须抓住注意力（重大结论或反常现象）
4. 覆盖：今日大势、1-2条核心新闻、1个AI推荐标的、结尾一句行动建议
5. 说话自然、口语化，不念数字时不念小数点后太多位
6. 结尾固定：欣欣说"我是欣欣"，建国说"我是建国"，一起说"我们明天见"
"""


def load_today_data() -> dict:
    """Load today's market data JSON"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    path = DATA / f"{today}.json"
    if not path.exists():
        path = DATA / "latest.json"
    if not path.exists():
        print("❌ No data file found")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_context(data: dict) -> str:
    """Summarize market data into a compact context for script generation"""
    lines = []

    # Date
    lines.append(f"日期：{data.get('date', '今日')}")

    # Summary
    s = data.get("summary", {})
    if s:
        lines.append(f"\n今日大势：{s.get('headline','')}（{s.get('sentiment','')}，VIX {s.get('vix','')}）")
        kp = s.get("key_points", [])
        if kp:
            lines.append("关键点：" + " / ".join(kp[:3]))

    # Top indices
    indices = data.get("indices", [])[:6]
    if indices:
        lines.append("\n主要指数：")
        for i in indices:
            lines.append(f"  {i['name']} {i['value']} {i.get('change_pct','')}")

    # Top 3 news
    news = data.get("news", [])[:4]
    if news:
        lines.append("\n重大新闻：")
        for n in news:
            lines.append(f"  [{n.get('importance','').upper()}] {n['title']}")
            if n.get("tldr"):
                lines.append(f"    → {n['tldr']}")

    # AI watchlist top pick
    wl = data.get("watchlist_analysis", [])
    if wl:
        # Find BUY with highest score
        buys = [x for x in wl if x.get("signal") == "BUY"]
        top = sorted(buys, key=lambda x: x.get("score", 0), reverse=True)[:1] or wl[:1]
        if top:
            t = top[0]
            lines.append(f"\nAI今日推荐：{t['name']}({t['ticker']}) 信号:{t.get('signal','')} 评分:{t.get('score','')} — {t.get('conclusion','')}")
            lines.append(f"  目标位: ↑{t.get('target_up','')} ↓{t.get('target_down','')} 风险:{t.get('risk_level','')}")

    # Market debate verdict
    debate = data.get("market_debate", {})
    if debate:
        lines.append(f"\n多空裁判：{debate.get('verdict_lean','').upper()} — {debate.get('verdict','')}")

    # 1 macro indicator
    macro = data.get("macro", [])[:2]
    if macro:
        lines.append("\n宏观数据：")
        for m in macro:
            lines.append(f"  {m['indicator']} {m['value']}（前值{m.get('prev','')}）{m.get('description','')[:40]}")

    return "\n".join(lines)


def generate_script(context: str, client_claude) -> str:
    """Use Claude to write the podcast script"""
    print("📝 Generating podcast script with Claude...")
    resp = client_claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_SCRIPT_TOKENS,
        system=SCRIPT_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"请根据以下今日市场数据，生成今天的《金融日报早报》播客脚本：\n\n{context}"
        }]
    )
    script = resp.content[0].text.strip()
    print(f"   ✅ Script: {len(script)} chars")
    return script


def parse_script(script: str) -> list[tuple[str, str]]:
    """Parse [欣欣] / [建国] lines → [(speaker, text), ...]"""
    segments = []
    for line in script.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[欣欣]"):
            segments.append(("A", line[4:].strip()))
        elif line.startswith("[建国]"):
            segments.append(("B", line[4:].strip()))
    return segments


def synthesize_segment(text: str, voice: str, client_oai, outpath: Path):
    """Synthesize one TTS segment"""
    resp = client_oai.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
        speed=1.05,
    )
    resp.stream_to_file(outpath)


def merge_audio(segment_files: list[Path], output: Path):
    """Concatenate MP3 files using ffmpeg"""
    if not shutil.which("ffmpeg"):
        # Fallback: just copy first segment
        shutil.copy(segment_files[0], output)
        print("   ⚠️  ffmpeg not found, using first segment only")
        return
    # Create concat list
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for seg in segment_files:
            f.write(f"file '{seg.resolve()}'\n")
        list_file = f.name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c", "copy", str(output)],
        check=True, capture_output=True
    )
    os.unlink(list_file)


def main():
    print("=" * 60)
    print("🎙️  金融日报 — Podcast Generator")
    print("=" * 60)

    # API clients
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key    = os.environ.get("OPENAI_API_KEY")
    if not anthropic_key:
        print("❌ ANTHROPIC_API_KEY not set"); sys.exit(1)
    if not openai_key:
        print("❌ OPENAI_API_KEY not set"); sys.exit(1)

    claude = anthropic.Anthropic(api_key=anthropic_key)
    oai    = OpenAI(api_key=openai_key)

    # Load data
    data    = load_today_data()
    context = build_context(data)
    print(f"📊 Loaded market data: {data.get('date','')}")

    # Generate script
    script   = generate_script(context, claude)
    segments = parse_script(script)
    print(f"   🎬 {len(segments)} dialogue segments")

    if not segments:
        print("❌ No dialogue segments parsed"); sys.exit(1)

    # Save script
    today     = datetime.datetime.now().strftime("%Y-%m-%d")
    script_path = AUDIO / f"{today}-script.txt"
    script_path.write_text(script, encoding="utf-8")
    print(f"   💾 Script saved: audio/{today}-script.txt")

    # TTS synthesis
    print("\n🔊 Synthesizing audio...")
    tmpdir = Path(tempfile.mkdtemp())
    seg_files = []
    for i, (speaker, text) in enumerate(segments):
        if not text: continue
        voice  = VOICE_A if speaker == "A" else VOICE_B
        outf   = tmpdir / f"seg_{i:03d}.mp3"
        print(f"   [{i+1}/{len(segments)}] {'欣欣' if speaker=='A' else '建国'}: {text[:40]}...")
        synthesize_segment(text, voice, oai, outf)
        seg_files.append(outf)

    # Merge
    print("\n🔀 Merging audio segments...")
    final_mp3 = AUDIO / f"{today}.mp3"
    latest_mp3 = AUDIO / "latest.mp3"
    merge_audio(seg_files, final_mp3)
    shutil.copy(final_mp3, latest_mp3)
    shutil.rmtree(tmpdir)

    size_kb = final_mp3.stat().st_size // 1024
    print(f"   ✅ Audio: audio/{today}.mp3 ({size_kb} KB)")

    # Save metadata
    meta = {
        "date": today,
        "script_chars": len(script),
        "segments": len(segments),
        "audio_kb": size_kb,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z"
    }
    (AUDIO / "latest-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"\n🎉 Done! Podcast ready: audio/{today}.mp3")
    print("=" * 60)


if __name__ == "__main__":
    main()
