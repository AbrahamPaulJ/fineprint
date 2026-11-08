"""Shared helpers for the per-platform fetchers (stdlib only; run everything with python -I)."""
import json, re, sys, html, urllib.request, urllib.error, gzip, datetime as dt

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TODAY = dt.date.today()

def http(url, headers=None, data=None, timeout=30, method=None):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode(); h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")

def get_json(url, **kw):
    return json.loads(http(url, **kw))

def text(s):
    """Strip HTML tags/entities and collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def to_date(v):
    """Best-effort: ISO string / epoch seconds or ms / datetime -> 'YYYY-MM-DD' or None."""
    if v in (None, "", 0):
        return None
    try:
        if isinstance(v, (int, float)):
            v = v / 1000 if v > 1e11 else v
            return dt.datetime.utcfromtimestamp(v).date().isoformat()
        s = str(v).strip()
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return "-".join(m.groups())
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
            try:
                return dt.datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
    except Exception:
        pass
    return None

def lead(source, title, url, deadline=None, cash_usd=None, entrants=None, kind="other", online=None, notes=""):
    """The one record shape every fetcher returns.
    deadline: 'YYYY-MM-DD' (submission close) or None. cash_usd: real cash in USD (best effort) or None.
    entrants: participants/registrants/teams count or None. kind: hackathon|ml|bounty|audit|ctf|writing|other."""
    return {"source": source, "title": (title or "").strip()[:160], "url": url, "deadline": to_date(deadline),
            "cash_usd": cash_usd, "entrants": entrants, "kind": kind, "online": online, "notes": (notes or "")[:300]}

def safe(fn):
    """Wrap a fetcher so one broken platform never kills the sweep."""
    def run():
        try:
            return fn() or []
        except Exception as e:
            print(f"[{fn.__name__}] failed: {type(e).__name__}: {e}", file=sys.stderr)
            return []
    run.__name__ = fn.__name__
    return run

FX = {"USD": 1, "$": 1, "EUR": 1.08, "€": 1.08, "GBP": 1.27, "£": 1.27, "INR": 0.012, "₹": 0.012, "CNY": 0.14,
      "RMB": 0.14, "¥": 0.14, "JPY": 0.0067, "AUD": 0.65, "CAD": 0.72, "CHF": 1.12, "USDC": 1, "USDT": 1}

def selftest(sources):
    """python -I tools/sources/<module>.py prints what each fetcher returns."""
    sys.stdout.reconfigure(encoding="utf-8")
    for fn in sources:
        rows = fn()
        print(f"\n== {fn.__name__}: {len(rows)} leads")
        for r in sorted(rows, key=lambda r: r["deadline"] or "9999")[:40]:
            print(f"  {r['deadline'] or '?':10} ${r['cash_usd'] or 0:>9,.0f}  n={r['entrants'] if r['entrants'] is not None else '?':>5}  {r['kind']:9} {r['title'][:70]}  {r['url']}")
