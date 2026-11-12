# Automation operations

All scheduled content jobs use no paid model API. The daily dashboard consumes dated
public feeds. Live quotes use per-symbol observation times and no longer overwrite
newer quotes with stale cache values. Both explicitly rebuild GitHub Pages.

The podcast runs after a successful daily dashboard workflow (or by manual dispatch),
uses current source data, and synthesizes Mandarin offline with espeak-ng and ffmpeg.
Speech is more mechanical than neural TTS, but needs neither a model API nor a speech
service credential. Audio is published only after complete successful synthesis.

AI Trading Decision now runs the existing **rule-based paper simulation only**.
Paid model portfolios are paused and labelled `paused_no_paid_api`; no model decisions
are fabricated. Re-running the same day does not place duplicate simulated trades.
This repository has no brokerage execution in this workflow.

## Chinese editorial schedule

Codex's daily 09:00 America/Toronto task writes `data/editorial/YYYY-MM-DD.json`.
Each edit identifies an existing source URL and changes title/body only. Numeric
market data is never edited by this path. Run:

```powershell
python -m unittest discover -s tests
python scripts/publish_editorial.py
```

The latest public digest must be from today. Later public-source updates retain matching
dated edits. If a source item disappears, its old edit is not reintroduced. Commit the
editorial JSON, rendered HTML, RSS and daily archive together and verify Pages publication.
