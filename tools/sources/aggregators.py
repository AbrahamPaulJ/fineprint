"""Aggregators and other people's ledgers: open cash contests found off the big hackathon/ML/bounty platforms.

- anurag629/opportunities: another AI scout's ledger (opportunities.json, re-checked against official pages daily).
- HeroX: crowdsourced prize challenges (NASA, DOE, foundations); internal search JSON, stage=enter.
- InnoCentive (Wazoku Crowd): corporate R&D / ideation challenges, $5k-$25k, HTML cards.
- Apart Research sprints: weekend AI-safety research hackathons, $2k-$5k cash, ~100 sign-ups.
- aifilmcontests.com: AI film contests (AKOOL, Showrunner, Chroma...), Next.js RSC payload.
Not here on purpose: mlcontests (ml_platforms.py), Mzee04/small-hackathon-finder (Devpost + Superteam only),
munimtechnologies/ml-competition-radar (mlcontests + Zindi only), challenge.gov (sunset March 2026).
"""
import os, re, sys, json, datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/ (python -I skips script dir)
from sources.common import http, get_json, lead, safe, selftest, text, FX, TODAY

FX2 = dict(FX, **{"US$": 1, "A$": 0.65, "AU$": 0.65, "C$": 0.72, "CA$": 0.72, "RS": 0.012, "RS.": 0.012,
                  "ZAR": 0.055, "R": 0.055, "SGD": 0.77, "S$": 0.77, "NZD": 0.6, "NZ$": 0.6})
_CUR = r"(US\$|A\$|AU\$|NZ\$|S\$|C\$|CA\$|\$|€|£|₹|¥|USDC|USDT|USD|EUR|GBP|INR|AUD|CAD|CHF|RMB|CNY|SGD|ZAR|Rs\.?|R(?=\s?\d))"
_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{1,3}(?: \d{3})+|\d+(?:\.\d+)?)"
_SUF = r"(?:\s*(k|K|m|M|million|Million|lakh|lakhs|crore|bn|billion)\b)?"
_POST = r"\s*(USDC|USDT|USD|EUR|GBP|INR|AUD|CAD|CHF|RMB|CNY|SGD|ZAR)\b"
_MULT = {"k": 1e3, "m": 1e6, "million": 1e6, "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "bn": 1e9, "billion": 1e9}


def money(s):
    """First money amount in a prize string, in USD. '$100K' -> 100000, 'Rs 18 lakh' -> 21600, '€10,000' -> 10800."""
    s = s or ""
    best = None
    for m in re.finditer(_CUR + r"\s?" + _NUM + _SUF, s):
        best = (m.start(), m.group(1), m.group(2), m.group(3)); break
    m2 = re.search(_NUM + _SUF + _POST, s)
    if m2 and (best is None or m2.start() < best[0]):
        best = (m2.start(), m2.group(3), m2.group(1), m2.group(2))
    if not best:
        return None
    _, cur, num, suf = best
    try:
        v = float(num.replace(",", "").replace(" ", ""))
    except ValueError:
        return None
    v *= _MULT.get((suf or "").lower(), 1)
    rate = FX2.get(cur.upper(), FX2.get(cur, 1))
    return round(v * rate)


def _open(d):
    return d is None or d >= TODAY.isoformat()


# ---------------------------------------------------------------- anurag629/opportunities (AI-scout ledger)
@safe
def anurag629():
    rows = get_json("https://raw.githubusercontent.com/anurag629/opportunities/HEAD/opportunities.json")
    out = []
    for r in rows:
        if r.get("kind") == "fellowship":
            continue
        dl = (r.get("deadline") or "")[:10] or None
        if not _open(dl):
            continue
        link = r.get("link") or ""
        kind = {"hackathon": "hackathon"}.get(r.get("kind"), "other")
        if re.search(r"kaggle\.com|zindi|codabench|drivendata|aicrowd", link):
            kind = "ml"
        amount = r.get("amount") or ""
        cash = None if re.search(r"^\s*(trip|travel|credits?|swag)", amount, re.I) else money(amount)
        notes = f"{r.get('org', '')} | {amount} | who: {r.get('who', '')} | india={r.get('india')} checked {r.get('checked')}"
        out.append(lead("anurag629", r.get("title"), link, dl, cash, None, kind, None, notes))
    return out


# ---------------------------------------------------------------- HeroX
@safe
def herox():
    base = "https://www.herox.com/async/api-internals/public/challenge/search?stage=enter&page_size=50"
    out, url, seen = [], base, set()
    while url and len(seen) < 400:
        d = get_json(url, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
        for c in d.get("results") or []:
            if c.get("id") in seen:
                continue
            seen.add(c.get("id"))
            if (c.get("stage_title") or "").lower() not in ("enter", "pre registration", "open"):
                continue
            if c.get("days_left") is None:  # no countdown = rolling or stale (2022-era listings still say "Enter")
                continue
            dl = (TODAY + dt.timedelta(days=int(c["days_left"]))).isoformat()
            prize = c.get("prize_short") or ""
            notes = (f"by {c.get('creator_title', '')} | prize {prize} | {c.get('total_followers', '?')} followers | "
                     f"{text(c.get('promo'))} | US-gov prizes are often US-only: check rules")
            out.append(lead("herox", c.get("title"), c.get("url"), dl, money(prize), None, "other", True, notes))
        url = d.get("next")
    return out


# ---------------------------------------------------------------- InnoCentive / Wazoku Crowd
_ORD = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Z][a-z]{2})[a-z]*\.?\s+(\d{4})")


@safe
def innocentive():
    out, seen = [], set()
    for page in range(1, 5):
        url = "https://www.innocentive.com/challenges/" + (f"page/{page}/" if page > 1 else "")
        try:
            h = http(url)
        except Exception:
            break
        cards = re.split(r'<div class="flex overflow-hidden flex-col bg-white rounded-lg border shadow-lg">', h)[1:]
        live = 0
        for c in cards:
            t = text(c)
            if "View Closed Challenge" in t or "View Challenge" not in t:
                continue
            href = re.search(r'href="([^"]+)"', c)
            title = text((re.search(r'(?s)<p class="text-lg[^"]*">(.*?)</p>', c) or [None, ""])[1])
            if not href or href.group(1) in seen:
                continue
            seen.add(href.group(1)); live += 1
            m = _ORD.search(t.split("Closing date:")[-1]) if "Closing date:" in t else None
            dl = dt.datetime.strptime(" ".join(m.groups()), "%d %b %Y").date().isoformat() if m else None
            award = t.split("Award:")[-1].replace("View Challenge", "").strip() if "Award:" in t else ""
            if not _open(dl):
                continue
            out.append(lead("innocentive", title, href.group(1), dl, money(award), None, "other", True,
                            f"Wazoku/InnoCentive challenge | award: {award[:120]} | most are written proposals; check IP terms"))
        if not live:
            break
    return out


# ---------------------------------------------------------------- Apart Research sprints
_SKIP_SLUGS = {"all", "collaborations", "locations", "opengraph-image", "prize-terms", "projects"}


def _apart_one(slug):
    url = f"https://apartresearch.com/sprints/{slug}"
    h = http(url)
    ev = {}
    for blob in re.findall(r'(?s)<script type="application/ld\+json"[^>]*>(.*?)</script>', h):
        try:
            o = json.loads(blob)
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("@type") == "Event":
            ev = o
    if not ev:
        return None
    t = text(re.sub(r"(?s)<(script|style).*?</\1>", " ", h))
    m = re.search(r"(\$[\d,]+k?)\s+in\s+cash\s+prizes", t, re.I)
    cash = money(m.group(1)) if m else None
    n = re.search(r"([\d,]+)\s+signed up", t)
    entrants = int(n.group(1).replace(",", "")) if n else None
    dl = (ev.get("endDate") or "")[:10] or None
    online = "Online" in (ev.get("eventAttendanceMode") or "")
    notes = f"weekend research sprint {ev.get('startDate', '')}..{dl}; report + code; {text(ev.get('description'))}"
    return lead("apart", ev.get("name"), url, dl, cash, entrants, "hackathon", online, notes)


@safe
def apart():
    h = http("https://apartresearch.com/sprints")
    slugs = sorted({s for s in re.findall(r'href="/sprints/([a-z0-9-]+)"', h) if s not in _SKIP_SLUGS})
    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(lambda s: safe(lambda: [_apart_one(s)])(), slugs))
    return [r for rs in rows for r in rs if r and _open(r["deadline"])]


# ---------------------------------------------------------------- aifilmcontests.com (Next.js RSC payload)
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def _rsc_objects(h, start=r'\{"id":"[a-z0-9-]+","name":'):
    blob = "".join(json.loads(s) for s in _PUSH.findall(h))
    dec, out = json.JSONDecoder(), {}
    for m in re.finditer(start, blob):
        try:
            o, _ = dec.raw_decode(blob, m.start())
        except ValueError:
            continue
        if isinstance(o, dict) and "deadline" in o:
            out[o["id"]] = o
    return out


def _itemized(details):
    """Sum a prize breakdown: ['Grand Prize: $50,000', '10 Finalist Awards: $2,500 each', '$100 per entry'] -> 75000.
    A line that states a total/pool wins outright."""
    total, s = None, 0
    for line in details or []:
        v = money(line)
        if not v or re.search(r"deadline|entry fee|\bfees?\b|credits|per project|\bup to\b", line, re.I):
            continue
        if re.search(r"\btotal\b|\bpool\b|in cash|in prizes", line, re.I):
            total = max(total or 0, v); continue
        if re.search(r"\bper\s+(qualified\s+)?(entry|submission|video|view|account)", line, re.I):
            continue
        m = re.match(r"\s*(\d+)\s+[A-Za-z]", line)
        k = int(m.group(1)) if m and re.search(r"\beach\b", line, re.I) and int(m.group(1)) < 100 else 1
        s += v * k
    return total or s or None


@safe
def aifilm():
    objs = _rsc_objects(http("https://aifilmcontests.com/"))  # the home page payload carries every contest (~280)
    out, urls = [], set()
    for o in sorted(objs.values(), key=lambda o: o.get("updatedAt") or "", reverse=True):  # newest copy wins
        if o.get("url") and o["url"] in urls:
            continue
        urls.add(o.get("url"))
        dl = (o.get("deadline") or "")[:10] or None
        if (o.get("status") or "open") not in ("open", "upcoming") or not _open(dl):
            continue
        prize = o.get("prize") or ""
        if re.search(r"no cash", prize, re.I):
            continue
        cash = _itemized(o.get("prizeDetails")) or money(prize)
        if not cash:
            continue  # laurels / screenings only
        fee = o.get("entryFee") or ""
        notes = (f"AI film | {o.get('organizer', '')} | headline: {prize} | itemized ~${cash:,} | fee: {fee} | "
                 f"elig: {text(str(o.get('eligibility') or ''))}")
        out.append(lead("aifilmcontests", o.get("name"), o.get("url") or f"https://aifilmcontests.com/contests/{o['id']}",
                        dl, cash, None, "other", None, notes))
    return out


SOURCES = [anurag629, herox, innocentive, apart, aifilm]

if __name__ == "__main__":
    selftest(SOURCES)
