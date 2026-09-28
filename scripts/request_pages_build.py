"""Avoid duplicate Pages rebuilds when GitHub already queued the current main."""
import json
import os
import subprocess


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    head = api(f'repos/{repo}/commits/main')['sha']
    try:
        build = api(f'repos/{repo}/pages/builds/latest')
    except subprocess.CalledProcessError:
        build = {}
    if build.get('commit') == head and build.get('status') in ('built', 'building', 'queued'):
        print(f"Pages already {build['status']} for current main; no duplicate request")
        return
    subprocess.run(['gh', 'api', '--method', 'POST', f'repos/{repo}/pages/builds'], check=True)


if __name__ == '__main__':
    main()
