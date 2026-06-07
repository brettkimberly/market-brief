#!/usr/bin/env python3
"""
market_brief.py — Daily market intelligence memo.

Sun 6pm CST  : weekly preview
Mon-Fri 7:45am CST : daily brief
Sat 11am CST : weekly recap

Usage: python market_brief.py [sunday|weekday|saturday]
       Auto-detects from current day/time if no argument given.
"""

import os, sys, json, random, smtplib, requests, feedparser, yfinance as yf, anthropic
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_USER        = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS    = os.environ.get("GMAIL_APP_PASS", "")
RECIPIENT         = os.environ.get("RECIPIENT", "")

SCHWAB_APP_KEY    = os.environ.get("SCHWAB_APP_KEY", "")
SCHWAB_SECRET     = os.environ.get("SCHWAB_SECRET", "")
SCHWAB_CALLBACK   = os.environ.get("SCHWAB_CALLBACK", "https://127.0.0.1:8182")
SCHWAB_TOKEN_PATH = os.environ.get("SCHWAB_TOKEN_PATH", "./token.json")

CST = ZoneInfo("America/Chicago")

WATCH_LIST = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA",
    "JPM","BAC","GS","MS","WFC","C",
    "AMD","INTC","MU","QCOM","AVGO",
    "NFLX","DIS","SBUX","NKE",
    "V","MA","PYPL",
    "UNH","LLY","JNJ","PFE",
    "XOM","CVX","HD","WMT","TGT",
]

SAT_WATCH = ["SPY","QQQ","META","NVDA","TSLA","AAPL","MSFT","MU"]

# ── Schwab client (optional — only needed for GEX) ────────────────────────────

_schwab = None
if SCHWAB_APP_KEY and SCHWAB_SECRET and os.path.exists(SCHWAB_TOKEN_PATH):
    try:
        import schwab
        _schwab = schwab.auth.easy_client(
            SCHWAB_APP_KEY, SCHWAB_SECRET, SCHWAB_CALLBACK, SCHWAB_TOKEN_PATH,
            enforce_enums=False
        )
    except Exception as e:
        print(f"Schwab init skipped: {e}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _day(dt):
    """Day number without leading zero — cross-platform."""
    return str(dt.day)

def fmt(n, d=2, prefix=""):
    return "—" if n is None else f"{prefix}{n:,.{d}f}"

def fmt_pct(p):
    if p is None: return "—"
    return f"{'+'if p>=0 else ''}{p:.2f}%"

def fmt_vol(v):
    if not v: return "—"
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}K"
    return str(v)

def _pct_cls(p):
    return "up" if (p or 0) >= 0 else "dn"

# ── Market data ───────────────────────────────────────────────────────────────

def get_market_snapshot():
    """Quotes for SPX, NDX, DOW, Gold, VIX, Bitcoin via yfinance."""
    tickers = {
        "^GSPC":   "SPX",
        "^NDX":    "NDX",
        "^DJI":    "DOW",
        "GC=F":    "Gold",
        "^VIX":    "VIX",
        "BTC-USD": "Bitcoin",
    }
    result = {}
    for yf_sym, label in tickers.items():
        try:
            hist   = yf.Ticker(yf_sym).history(period="3d", interval="1d", auto_adjust=True)
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                prev = float(closes.iloc[-2])
                last = float(closes.iloc[-1])
                pct  = (last - prev) / prev * 100
            elif len(closes) == 1:
                last = float(closes.iloc[-1])
                prev, pct = last, 0.0
            else:
                continue
            result[label] = {"last": last, "prev": prev, "pct": pct}
        except Exception as e:
            print(f"{label} snapshot error: {e}")
    return result


def get_price_history(symbol, days=5):
    """Daily OHLCV for a single symbol via yfinance."""
    try:
        hist = yf.Ticker(symbol).history(
            period=f"{days + 4}d", interval="1d", auto_adjust=True
        ).tail(days)
        return [
            {
                "date":   f"{idx.strftime('%a')} {idx.month}/{idx.day}",
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": int(row["Volume"]),
            }
            for idx, row in hist.iterrows()
        ]
    except Exception as e:
        print(f"{symbol} history error: {e}")
        return []


def get_multi_history(symbols, days=5):
    """Daily history for multiple symbols in parallel."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(ex.map(lambda s: (s, get_price_history(s, days)), symbols))


def compute_gex(symbol="SPY"):
    """
    Approximate dealer GEX from Schwab options chain.
    Requires local Schwab token. Returns empty dict if unavailable.
    """
    if _schwab is None:
        return {}
    try:
        to_date = date.today() + timedelta(days=45)
        r    = _schwab.get_option_chain(symbol, from_date=date.today(), to_date=to_date)
        data = r.json()
        spot = float(data.get("underlyingPrice", 0) or 0)
        if not spot:
            return {}

        call_gex, put_gex = {}, {}
        call_oi,  put_oi  = {}, {}

        for _, strikes in data.get("callExpDateMap", {}).items():
            for sk_str, contracts in strikes.items():
                sk = float(sk_str)
                for c in contracts:
                    g  = c.get("gamma", 0.0) or 0.0
                    oi = c.get("openInterest", 0) or 0
                    call_gex[sk] = call_gex.get(sk, 0) + g * oi * 100 * spot * spot / 100
                    call_oi[sk]  = call_oi.get(sk, 0) + oi

        for _, strikes in data.get("putExpDateMap", {}).items():
            for sk_str, contracts in strikes.items():
                sk = float(sk_str)
                for c in contracts:
                    g  = c.get("gamma", 0.0) or 0.0
                    oi = c.get("openInterest", 0) or 0
                    put_gex[sk] = put_gex.get(sk, 0) + g * oi * 100 * spot * spot / 100
                    put_oi[sk]  = put_oi.get(sk, 0) + oi

        call_wall = max(call_oi, key=call_oi.get) if call_oi else None
        put_wall  = max(put_oi,  key=put_oi.get)  if put_oi  else None

        all_strikes   = sorted(set(call_gex) | set(put_gex))
        net_by_strike = {s: call_gex.get(s, 0) - put_gex.get(s, 0) for s in all_strikes}
        total_net_gex = sum(net_by_strike.values())

        gex_flip = None
        above = [(s, net_by_strike[s]) for s in all_strikes if s >= spot]
        below = [(s, net_by_strike[s]) for s in reversed(all_strikes) if s < spot]
        for seq in [above, below]:
            for i in range(len(seq) - 1):
                if seq[i][1] * seq[i + 1][1] < 0:
                    gex_flip = seq[i + 1][0]
                    break
            if gex_flip:
                break

        return {
            "spot":             spot,
            "call_wall":        call_wall,
            "put_wall":         put_wall,
            "gex_flip":         gex_flip,
            "net_gex":          total_net_gex,
            "dealer_condition": "long gamma" if total_net_gex >= 0 else "short gamma",
        }
    except Exception as e:
        print(f"GEX error: {e}")
        return {}


def _fetch_earnings_for(sym):
    try:
        df = yf.Ticker(sym).earnings_dates
        if df is None or df.empty:
            return sym, None
        df.index = df.index.tz_convert("America/New_York")
        return sym, df
    except Exception:
        return sym, None


def get_upcoming_earnings(days_ahead=7):
    now     = datetime.now(ZoneInfo("America/New_York"))
    cutoff  = now + timedelta(days=days_ahead)
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for sym, df in ex.map(_fetch_earnings_for, WATCH_LIST):
            if df is None:
                continue
            for dt, row in df[(df.index > now) & (df.index <= cutoff)].iterrows():
                est = row.get("EPS Estimate")
                results.append({
                    "symbol":  sym,
                    "date":    f"{dt.strftime('%b')} {dt.day}",
                    "eps_est": float(est) if est and str(est) != "nan" else None,
                })
    results.sort(key=lambda x: x["date"])
    return results[:12]


def get_recent_earnings(days_back=3):
    now     = datetime.now(ZoneInfo("America/New_York"))
    cutoff  = now - timedelta(days=days_back)
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for sym, df in ex.map(_fetch_earnings_for, WATCH_LIST):
            if df is None:
                continue
            for dt, row in df[(df.index >= cutoff) & (df.index <= now)].iterrows():
                act = row.get("Reported EPS")
                if act is None or str(act) == "nan":
                    continue
                est   = row.get("EPS Estimate")
                est_f = float(est) if est and str(est) != "nan" else None
                act_f = float(act)
                surp  = row.get("Surprise(%)")
                results.append({
                    "symbol":   sym,
                    "date":     f"{dt.strftime('%b')} {dt.day}",
                    "eps_est":  est_f,
                    "eps_act":  act_f,
                    "beat":     (act_f >= est_f) if est_f is not None else None,
                    "surprise": surp,
                })
    results.sort(key=lambda x: x["date"], reverse=True)
    return results[:10]


_FEED_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

def get_news_headlines():
    """Financial news from Yahoo Finance and CNBC RSS."""
    feeds = [
        "https://finance.yahoo.com/news/rssindex",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    ]
    headlines = []
    for url in feeds:
        try:
            r    = requests.get(url, timeout=10, headers={"User-Agent": _FEED_UA})
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:10]:
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "")[:140].strip()
                if title:
                    headlines.append(f"{title}. {summary}" if summary else title)
        except Exception as e:
            print(f"Feed error ({url}): {e}")
    return headlines[:20]

# ── Claude narrative synthesis ────────────────────────────────────────────────

_ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

_RULES = """Style rules — follow exactly:
- No em-dashes. Use commas or periods instead.
- No "notably", "it's worth noting", "in today's market", "amid", "amidst", "navigate", "headwinds", "tailwinds".
- No filler openers. Start with the most important fact or number.
- Active voice. Short sentences. 1-5 sentences total.
- Journalistic, not newsletter. Write like a desk analyst, not a retail report.
- Do not reference yourself or these instructions."""

def synthesize(prompt, data):
    if _ai is None:
        return "[ANTHROPIC_API_KEY not configured]"
    try:
        msg = _ai.messages.create(
            model="claude-opus-4-8",
            max_tokens=512,
            system=_RULES,
            messages=[{
                "role": "user",
                "content": f"{prompt}\n\nDATA:\n{json.dumps(data, default=str, indent=2)}"
            }]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"[Synthesis error: {e}]"

# ── Quotes ────────────────────────────────────────────────────────────────────

_WEEKDAY_Q = [
    ("Risk comes from not knowing what you're doing.", "Warren Buffett"),
    ("In investing, what is comfortable is rarely profitable.", "Robert Arnott"),
    ("It's not whether you're right or wrong that's important, but how much money you make when you're right and how much you lose when you're wrong.", "George Soros"),
    ("The four most dangerous words in investing are: this time it's different.", "John Templeton"),
    ("Price is what you pay. Value is what you get.", "Warren Buffett"),
    ("The market can remain irrational longer than you can remain solvent.", "John Maynard Keynes"),
    ("Know what you own, and know why you own it.", "Peter Lynch"),
    ("Confronting reality is what separates the great from the near-great.", "Jim Collins"),
    ("Every battle is won or lost before it is ever fought.", "Sun Tzu"),
    ("An investment in knowledge pays the best interest.", "Benjamin Franklin"),
    ("The key to making money in stocks is not to get scared out of them.", "Peter Lynch"),
    ("Discipline is the bridge between goals and accomplishment.", "Jim Rohn"),
    ("Opportunities come infrequently. When it rains gold, put out the bucket, not the thimble.", "Warren Buffett"),
    ("The big money is not in the buying and the selling, but in the waiting.", "Charlie Munger"),
    ("The stock market is a device for transferring money from the impatient to the patient.", "Warren Buffett"),
]

_WEEKEND_Q = [
    ("Almost everything will work again if you unplug it for a few minutes, including you.", "Anne Lamott"),
    ("Rest is not idleness, and to lie sometimes on the grass under trees on a summer's day is by no means a waste of time.", "John Lubbock"),
    ("Take rest; a field that has rested gives a bountiful crop.", "Ovid"),
    ("There is virtue in work and there is virtue in rest. Use both and overlook neither.", "Alan Cohen"),
    ("Your calm mind is the ultimate weapon against your challenges. So relax.", "Bryant McGill"),
    ("True silence is the rest of the mind; it is to the spirit what sleep is to the body.", "William Penn"),
    ("What we do in life echoes in eternity.", "Marcus Aurelius"),
    ("The present moment is the only moment available to us, and it is the door to all moments.", "Thich Nhat Hanh"),
    ("Simplicity is the ultimate sophistication.", "Leonardo da Vinci"),
]

# ── HTML / CSS ────────────────────────────────────────────────────────────────

_CSS = """
body{margin:0;padding:0;background:#fff;font-family:Georgia,'Times New Roman',serif;color:#1a1a1a}
.wrap{max-width:620px;margin:0 auto;padding:32px 24px}
h1{font-size:21px;font-weight:normal;letter-spacing:.4px;margin:0 0 4px 0}
.meta{font-size:12px;color:#999;font-family:-apple-system,Arial,sans-serif;margin:0 0 28px 0}
h2{font-size:11px;text-transform:uppercase;letter-spacing:1.6px;font-family:-apple-system,Arial,sans-serif;
   font-weight:600;color:#777;margin:28px 0 8px 0}
hr{border:none;border-top:1px solid #ececec;margin:0 0 12px 0}
p{font-size:15px;line-height:1.7;margin:0 0 4px 0;color:#222}
table{width:100%;border-collapse:collapse;font-family:-apple-system,Arial,sans-serif;font-size:13px;margin:4px 0 0 0}
th{text-align:left;color:#aaa;font-weight:500;padding:3px 10px 3px 0;border-bottom:1px solid #ececec;
   font-size:10px;text-transform:uppercase;letter-spacing:.6px}
td{padding:6px 10px 6px 0;border-bottom:1px solid #f5f5f5;vertical-align:top}
.mono{font-family:'Courier New',Courier,monospace}
.up{color:#1a7f37}.dn{color:#cf222e}
.qb{border-left:2px solid #ddd;padding:10px 16px;margin:8px 0 0 0}
.qt{font-size:15px;line-height:1.7;font-style:italic;color:#333;margin:0}
.qa{font-size:12px;color:#999;font-family:-apple-system,Arial,sans-serif;margin:5px 0 0 0}
.foot{font-size:11px;color:#ccc;font-family:-apple-system,Arial,sans-serif;margin-top:32px;
      padding-top:14px;border-top:1px solid #ececec}
"""

def _sec(title, body):
    return f"<h2>{title}</h2><hr>{body}\n"

def _wrap(title, body, now):
    date_str = f"{now.strftime('%A, %B')} {now.day}, {now.year}"
    return (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f'<style>{_CSS}</style></head><body><div class="wrap">'
        f'<h1>{title}</h1><p class="meta">{date_str}</p>'
        f'{body}'
        f'<p class="foot">market_brief.py</p>'
        f'</div></body></html>'
    )

def _quote(text, attr):
    return f'<div class="qb"><p class="qt">{text}</p><p class="qa">— {attr}</p></div>'

# ── Table builders ────────────────────────────────────────────────────────────

def _snapshot_table(snap):
    order = ["SPX","NDX","DOW","Gold","VIX","Bitcoin"]
    rows  = ""
    for label in order:
        d = snap.get(label)
        if not d:
            continue
        last   = d.get("last", 0)
        prev   = d.get("prev", last)
        net    = last - prev
        pct    = d.get("pct", 0)
        cls    = _pct_cls(pct)
        sign   = "+" if net >= 0 else ""
        dp     = 0 if label in ("Bitcoin","SPX","NDX","DOW") else 2
        net_dp = 0 if label in ("Bitcoin","SPX","NDX","DOW") else 2
        pfx    = "$" if label == "Bitcoin" else ""
        rows  += (
            f"<tr><td>{label}</td>"
            f"<td class='mono'>{fmt(last,dp,pfx)}</td>"
            f"<td class='mono {cls}'>{sign}{fmt(net,net_dp,pfx)}</td>"
            f"<td class='mono {cls}'>{fmt_pct(pct)}</td></tr>"
        )
    return "<table><tr><th>Market</th><th>Last</th><th>Net Chg</th><th>% Chg</th></tr>" + rows + "</table>"


def _spy_table(history):
    if not history:
        return "<p>Price history unavailable.</p>"
    avg_vol = sum(d["volume"] for d in history) / len(history) or 1
    rows = ""
    for d in history:
        chg = (d["close"] - d["open"]) / d["open"] * 100 if d["open"] else 0
        cls = _pct_cls(chg)
        vr  = d["volume"] / avg_vol
        rows += (
            f"<tr><td>{d['date']}</td>"
            f"<td class='mono'>{fmt(d['open'])}</td>"
            f"<td class='mono'>{fmt(d['high'])}</td>"
            f"<td class='mono'>{fmt(d['low'])}</td>"
            f"<td class='mono {cls}'>{fmt(d['close'])}</td>"
            f"<td class='mono {cls}'>{fmt_pct(chg)}</td>"
            f"<td class='mono'>{fmt_vol(d['volume'])} "
            f"<span style='color:#bbb'>({vr:.1f}x)</span></td></tr>"
        )
    return (
        "<table><tr><th>Day</th><th>Open</th><th>High</th>"
        f"<th>Low</th><th>Close</th><th>Chg</th><th>Volume</th></tr>{rows}</table>"
    )


def _upcoming_earnings_table(earnings):
    if not earnings:
        return "<p>No notable earnings in the next 7 days.</p>"
    rows = "".join(
        f"<tr><td><b>{e['symbol']}</b></td><td>{e['date']}</td>"
        f"<td class='mono'>{fmt(e['eps_est']) if e['eps_est'] is not None else '—'}</td></tr>"
        for e in earnings
    )
    return f"<table><tr><th>Ticker</th><th>Date</th><th>EPS Est</th></tr>{rows}</table>"


def _recent_earnings_table(earnings):
    if not earnings:
        return "<p>No results with actuals in the last 3 days.</p>"
    rows = ""
    for e in earnings:
        est = fmt(e["eps_est"]) if e["eps_est"] is not None else "—"
        act = fmt(e["eps_act"]) if e["eps_act"] is not None else "—"
        if e["beat"] is True:
            verdict = "<span class='up'>Beat</span>"
        elif e["beat"] is False:
            verdict = "<span class='dn'>Miss</span>"
        else:
            verdict = "—"
        surp = e.get("surprise")
        surp_str = fmt_pct(float(surp)) if surp and str(surp) != "nan" else "—"
        rows += (
            f"<tr><td><b>{e['symbol']}</b></td><td>{e['date']}</td>"
            f"<td class='mono'>{est}</td><td class='mono'>{act}</td>"
            f"<td>{verdict}</td><td class='mono'>{surp_str}</td></tr>"
        )
    return (
        "<table><tr><th>Ticker</th><th>Date</th><th>Est</th>"
        f"<th>Act</th><th></th><th>Surprise</th></tr>{rows}</table>"
    )


def _gex_table(gex):
    if not gex:
        return "<p>Options data unavailable. (Requires local Schwab token.)</p>"
    cond = gex.get("dealer_condition", "—")
    cls  = "up" if "long" in cond else "dn"
    note = (
        "Dealers will fade sharp moves — expect mean-reversion behavior."
        if "long" in cond
        else "Dealers amplify directional moves — expect trending, wider intraday swings."
    )
    rows = (
        f"<tr><td>SPY Spot</td><td class='mono'>{fmt(gex.get('spot'))}</td></tr>"
        f"<tr><td>Call Wall</td><td class='mono'>{fmt(gex.get('call_wall'),0)}</td></tr>"
        f"<tr><td>Put Wall</td><td class='mono'>{fmt(gex.get('put_wall'),0)}</td></tr>"
        f"<tr><td>GEX Flip</td><td class='mono'>"
        f"{fmt(gex.get('gex_flip'),0) if gex.get('gex_flip') else '—'}</td></tr>"
        f"<tr><td>Dealer Condition</td><td class='{cls}'>{cond.title()}</td></tr>"
    )
    return (
        f"<table><tr><th>Level</th><th>Price</th></tr>{rows}</table>"
        f"<p style='font-size:13px;color:#555;font-family:-apple-system,Arial,sans-serif;"
        f"margin:10px 0 0 0;line-height:1.5'>{note}</p>"
    )


def _notable_moves_table(histories):
    rows = ""
    for sym, days in histories.items():
        for d in days:
            if not d.get("open"):
                continue
            chg    = (d["close"] - d["open"]) / d["open"] * 100
            range_ = (d["high"]  - d["low"])  / d["open"] * 100
            if abs(chg) >= 2.0 or range_ >= 3.0:
                cls = _pct_cls(chg)
                rows += (
                    f"<tr><td><b>{sym}</b></td><td>{d['date']}</td>"
                    f"<td class='mono'>{fmt(d['open'])}</td>"
                    f"<td class='mono'>{fmt(d['close'])}</td>"
                    f"<td class='mono {cls}'>{fmt_pct(chg)}</td>"
                    f"<td class='mono'>{fmt_pct(range_)} range</td></tr>"
                )
    if not rows:
        return "<p>No notable intraday moves (&ge;2% close change or &ge;3% range) this week.</p>"
    return (
        "<table><tr><th>Symbol</th><th>Day</th><th>Open</th>"
        f"<th>Close</th><th>Chg</th><th>Range</th></tr>{rows}</table>"
    )

# ── Report builders ───────────────────────────────────────────────────────────

def build_sunday_report():
    now      = datetime.now(CST)
    snapshot = get_market_snapshot()
    spy_hist = get_price_history("SPY", days=5)
    earnings = get_upcoming_earnings(days_ahead=7)
    headlines = get_news_headlines()

    macro = synthesize(
        "Write 2-4 sentences summarizing the key macro drivers and market events "
        "traders should keep in mind for the coming week. Use the news headlines and "
        "the current market snapshot. Lead with the single most important driver. "
        "Reference specific data where possible.",
        {"snapshot": snapshot, "headlines": headlines}
    )

    body = (
        _sec("Macro Catalysts & Market Drivers", f"<p>{macro}</p>")
        + _sec("Notable Earnings This Week", _upcoming_earnings_table(earnings))
        + _sec("Market Snapshot", _snapshot_table(snapshot))
        + _sec("SPY — 5-Day Price & Volume", _spy_table(spy_hist))
        + _sec("Going Into the Week", _quote(*random.choice(_WEEKDAY_Q)))
    )
    return _wrap("Weekly Market Preview", body, now)


def build_weekday_report():
    now      = datetime.now(CST)
    snapshot = get_market_snapshot()
    gex      = compute_gex("SPY")
    upcoming = get_upcoming_earnings(days_ahead=3)
    results  = get_recent_earnings()
    headlines = get_news_headlines()

    news = synthesize(
        "Write 3-5 sentences covering the most market-significant news from these "
        "headlines. Include any macro policy, Fed, geopolitical, or sector-specific "
        "items that a trader monitoring equities and rates would consider material. "
        "Start with the most important story. No filler.",
        {"headlines": headlines}
    )

    gex_note = synthesize(
        "Write 1-3 sentences interpreting the SPY GEX data for today's trading session. "
        "Mention the call wall and put wall as levels to watch. Explain the dealer "
        "condition in plain terms without using the word 'gamma' more than once.",
        {"gex": gex}
    )

    body = (
        _sec("Morning Brief", f"<p>{news}</p>")
        + _sec("Market Snapshot", _snapshot_table(snapshot))
        + _sec("Recent Earnings Results", _recent_earnings_table(results))
        + _sec("Upcoming Earnings", _upcoming_earnings_table(upcoming))
        + _sec("GEX Snapshot — SPY", _gex_table(gex))
        + _sec("Dealer Positioning", f"<p>{gex_note}</p>")
        + _sec("Today", _quote(*random.choice(_WEEKDAY_Q)))
    )
    return _wrap("Daily Market Brief", body, now)


def build_saturday_report():
    now      = datetime.now(CST)
    snapshot = get_market_snapshot()
    spy_hist = get_price_history("SPY", days=5)
    histories = get_multi_history(SAT_WATCH, days=5)
    headlines = get_news_headlines()

    recap = synthesize(
        "Write 3-5 sentences recapping the week in markets. Use the SPY price history "
        "and market snapshot to summarize performance. Reference the single most "
        "significant macro or market-moving event from the headlines. "
        "Be specific with numbers. Start with the most important thing that happened.",
        {"spy_history": spy_hist, "snapshot": snapshot, "headlines": headlines}
    )

    body = (
        _sec("Week in Review", f"<p>{recap}</p>")
        + _sec("Market Snapshot", _snapshot_table(snapshot))
        + _sec("SPY — 5-Day Performance", _spy_table(spy_hist))
        + _sec("Notable Intraday Moves", _notable_moves_table(histories))
        + _sec("Enjoy the Weekend", _quote(*random.choice(_WEEKEND_Q)))
    )
    return _wrap("Weekly Market Recap", body, now)

# ── Email delivery ────────────────────────────────────────────────────────────

def send_email(subject, html):
    if not GMAIL_APP_PASS or not GMAIL_USER:
        out = "preview.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Email not configured. Preview saved to {out}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, [RECIPIENT], msg.as_string())
        print(f"Sent: {subject}")
    except Exception as e:
        print(f"Send failed: {e}")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    now = datetime.now(CST)
    dow = now.weekday()  # 0=Mon ... 5=Sat, 6=Sun

    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    elif dow == 6:
        mode = "sunday"
    elif dow == 5:
        mode = "saturday"
    else:
        mode = "weekday"

    print(f"Running {mode} report — {now.strftime('%Y-%m-%d %H:%M')} CST")

    if mode == "sunday":
        html    = build_sunday_report()
        subject = f"Weekly Preview — {now.strftime('%b')} {now.day}"
    elif mode == "saturday":
        html    = build_saturday_report()
        subject = f"Weekly Recap — {now.strftime('%b')} {now.day}"
    else:
        html    = build_weekday_report()
        subject = f"Market Brief — {now.strftime('%b')} {now.day}, {now.strftime('%a')}"

    send_email(subject, html)


if __name__ == "__main__":
    main()
