"""Generate a source-based podcast with offline TTS; no model/API credentials."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / 'audio'


def build_script(data):
    fetched = datetime.datetime.fromisoformat(data['fetched_at'].replace('Z', '+00:00'))
    age = datetime.datetime.now(datetime.timezone.utc) - fetched
    if not datetime.timedelta(minutes=-5) <= age <= datetime.timedelta(hours=36):
        raise ValueError('Digest is stale; refusing to label old data as today')
    lines = [f"金融日报，{data['date']}。以下为公开来源数据播报，报价可能延迟。"]
    for row in data.get('indices', [])[:6]:
        lines.append(f"{row['name']}，{row['value']}，变动百分之{row['change_pct'].replace('%', '')}。")
    for row in data.get('news', [])[:3]:
        lines.append(f"{row.get('date', '')}，{row.get('source', '公开来源')}：{row['title']}。")
    lines.append('原文链接和具体报价时间见网站。以上不构成投资建议。')
    return '\n'.join(lines)


def main():
    data = json.loads((ROOT / 'data/latest.json').read_text(encoding='utf-8'))
    script = build_script(data)
    if not shutil.which('espeak-ng') or not shutil.which('ffmpeg'):
        raise RuntimeError('Install espeak-ng and ffmpeg; the workflow installs both')
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    AUDIO.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        source = tmp / 'script.txt'
        source.write_text(script, encoding='utf-8')
        subprocess.run(['espeak-ng', '-v', 'cmn', '-s', '160', '-f', str(source), '-w', str(tmp/'speech.wav')], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp/'speech.wav'), '-codec:a', 'libmp3lame', '-q:a', '4', str(tmp/'speech.mp3')], check=True)
        if (tmp/'speech.mp3').stat().st_size < 1024:
            raise RuntimeError('Empty synthesized audio')
        shutil.copyfile(tmp/'speech.mp3', AUDIO/f'{day}.mp3')
        shutil.copyfile(tmp/'speech.mp3', AUDIO/'latest.mp3')
    (AUDIO/f'{day}-script.txt').write_text(script, encoding='utf-8')
    meta = {'date': day, 'script_chars': len(script), 'segments': len(script.splitlines()),
            'audio_kb': (AUDIO/'latest.mp3').stat().st_size//1024, 'mode': 'offline_source_narration',
            'source_fetched_at': data['fetched_at'], 'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (AUDIO/'latest-meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Published {day}: offline Mandarin narration, no API key')


if __name__ == '__main__':
    main()
