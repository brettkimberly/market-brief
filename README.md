# market_brief

Automated daily market intelligence memo delivered via email. Built with Python, yfinance, and Claude (Anthropic).

## Schedule

| Day | Time (CST) | Report |
|-----|-----------|--------|
| Sunday | 6:00 PM | Weekly Preview — macro catalysts, upcoming earnings, market snapshot |
| Mon–Fri | 7:45 AM | Daily Brief — news, GEX snapshot, recent/upcoming earnings |
| Saturday | 11:00 AM | Weekly Recap — performance review, notable intraday moves |

## What's in each report

**Daily Brief (weekdays)** — Morning news synthesis, SPX/NDX/DOW/VIX/Gold/BTC snapshot, recent earnings results, upcoming earnings, SPY GEX levels (call wall, put wall, flip point, dealer condition).

**Weekly Preview (Sunday)** — Macro drivers for the coming week, earnings calendar, market snapshot, SPY 5-day price/volume table.

**Weekly Recap (Saturday)** — Week-in-review narrative, market snapshot, SPY 5-day performance, notable intraday moves across the watch list.

## Requirements

- Python 3.9+
- Anthropic API key
- Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) enabled
- (Optional) Schwab developer account + local token for the GEX section

## Setup

```bash
git clone https://github.com/brettkimberly/market-brief.git
cd market-brief
python -m venv .venv

# Mac/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
# Fill in your credentials in .env
```

## Configuration

Copy `.env.example` to `.env` and fill in:

```
ANTHROPIC_API_KEY=     # Required
GMAIL_USER=            # Sender Gmail address
GMAIL_APP_PASS=        # Gmail App Password (not your account password)
RECIPIENT=             # Destination email address
CLAUDE_MODEL=          # Optional, defaults to claude-opus-4-5

# Optional — GEX section only
SCHWAB_APP_KEY=
SCHWAB_SECRET=
SCHWAB_TOKEN_PATH=./token.json
```

If email isn't configured, the report saves locally as `preview.html`.

## Running manually

```bash
python market_brief.py            # auto-detects from current day/time
python market_brief.py weekday
python market_brief.py sunday
python market_brief.py saturday
```

## Scheduling

**Mac** — create a `run.sh` (gitignored) and add it to cron:
```bash
crontab -e
# Weekdays 7:45am CST:   45 7 * * 1-5 /path/to/run.sh
# Saturday 11am CST:     0 11 * * 6   /path/to/run.sh
# Sunday 6pm CST:        0 18 * * 0   /path/to/run.sh
```

**Windows** — use `run.bat`. Task Scheduler commands are in the comments at the top of that file.

## Watch list

Covers major tech, financials, semis, consumer, healthcare, and energy names. Edit `WATCH_LIST` in `market_brief.py` to customize.

## GEX section

Requires a local Schwab OAuth token (`token.json`). If not present, the GEX section is skipped gracefully and the rest of the report still sends. See [schwab-py docs](https://schwab-py.readthedocs.io) for token setup.
