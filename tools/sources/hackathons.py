"""Online hackathon platforms other than Devpost (Devpost lives in devpost.py / scan_devpost.py).

One @safe fetcher per platform. Each keeps open, online-eligible events (deadline today or later) and
fills cash_usd only with real cash (USD/USDC/USDT/fiat). Prize text is parsed best-effort, so notes say
"cash est." where the number came from free text. Red flags (student-only, India-only, in-person,
team-required, token payout) go in notes.

Endpoints (all public, found from each site's JS bundle, Nov 2026):
  DoraHacks   https://dorahacks.io/api/v1/hub/hackathons?status=ongoing|upcoming     (+ /hackathons/<uname>)
  lablab      https://lablab.build/ai-hackathons  (Next RSC payload)  + event page for prize text
  Devfolio    POST https://api.devfolio.co/api/search/hackathons {"type":"application_open"}
  TAIKAI      https://api.taikai.network/api/graphql  challenges(where:{isClosed:{equals:false}})
  HackerEarth https://www.hackerearth.com/chrome-extension/events/
  Unstop      https://unstop.com/api/public/opportunity/search-result?opportunity=...&region=online
  Hack2skill  https://hack2skill.com/api/v1/innovator/public/event/public-list  (+ /event/<url>/event-details)
  ETHGlobal   https://ethglobal.com/events (HTML "Upcoming" block)
  HackQuest   https://api.hackquest.io/graphql  listHackathons
  Akindo      https://api.akindo.io/public/wave-hacks , /public/hackathons
  Encode Club Supabase REST (anon key shipped in the site bundle) table "Encode Programmes"
  Devnovate   https://devnovate.co/api/v1/events
  MLH         https://mlh.io/seasons/<year>/events (embedded upcomingEvents JSON)
Skipped: Colosseum (two fixed seasons, Fall 2026 already in judging; Eternal is a funding track),
Kaggle (other cluster).
"""
import os, re, sys, json, datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/ (python -I skips script dir)
from sources.common import http, get_json, text, to_date, lead, safe, selftest, FX, TODAY

TODAY_S = TODAY.isoformat()


def _open(d):
    """Keep events whose deadline is unknown or today/later."""
    d = to_date(d)
    return d is None or d >= TODAY_S


def _pmap(fn, items, n=8):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))


def _try(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:
        return None


# ---------- money + red-flag parsing from free text ----------
_CUR = r"(US\$|A\$|AU\$|\$|USDC|USDT|USD|€|EUR|£|GBP|₹|INR|Rs\.?|AUD)"
_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(k|K|lakhs?|Lakhs?|L|crores?|cr|million|M)?\b"
_MONEY = re.compile(_CUR + r"\s?" + _NUM + r"|" + _NUM + r"\s?(USDC|USDT|USD|EUR|INR|AUD)\b")
_CURMAP = {"US$": "USD", "$": "USD", "A$": "AUD", "AU$": "AUD", "₹": "INR", "Rs": "INR", "Rs.": "INR",
           "€": "EUR", "£": "GBP"}
_MULT = {"k": 1e3, "K": 1e3, "lakh": 1e5, "lakhs": 1e5, "Lakh": 1e5, "Lakhs": 1e5, "L": 1e5, "crore": 1e7,
         "crores": 1e7, "cr": 1e7, "million": 1e6, "M": 1e6}
_POOL = re.compile(r"(?i)prize ?pool|in prizes|total prizes?|prizes? worth|worth of prizes|prize money|in cash|"
                   r"cash prize|reward pool|total rewards?|in bounties")
_NONCASH = re.compile(r"(?i)^[^$€£₹\d+]{0,30}(credits?|vouchers?|coupons?|goodies|swag|tokens? of|worth of (cloud|api))")


def money(s, default_cur="USD"):
    """[(usd_amount, start, end)] for every currency amount in s."""
    out = []
    for m in _MONEY.finditer(s or ""):
        if m.group(1):
            cur, num, mult = m.group(1), m.group(2), m.group(3)
        else:
            num, mult, cur = m.group(4), m.group(5), m.group(6)
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        v *= _MULT.get(mult or "", 1)
        cur = _CURMAP.get(cur, cur)
        v *= FX.get(cur, FX.get(default_cur, 1))
        if v >= 50:
            out.append((round(v), m.start(), m.end()))
    return out


def cash_from_text(s, default_cur="USD"):
    """Best-effort real-cash total from prize text. Priority: the largest amount labelled cash ('$9,000 cash
    prizes'), else an explicit 'prize pool / in prizes' amount, else the largest amount not followed by
    'credits/vouchers'. None if nothing found."""
    s = s or ""
    ms = [(v, a, b) for v, a, b in money(s, default_cur) if not _NONCASH.match(s[b:b + 40])]
    if not ms:
        return None
    cash = [v for v, a, b in ms if re.match(r"(?i)\s*(in |of )?(total )?cash", s[b:b + 20])]
    if cash:
        return max(cash)
    pool = [v for v, a, b in ms if _POOL.search(s[max(0, a - 40):b + 40])]
    return max(pool) if pool else max(v for v, a, b in ms)


_FLAGS = [
    (r"(?i)\b(only|exclusively) (for |open to )?(college |university |school )?students\b|students only|"
     r"must be (a )?(currently )?(enrolled|student)|valid (student|college) id", "student-only?"),
    (r"(?i)indian (citizens|nationals|residents|students)|residents? of india|(across|from|in) india only|"
     r"only (for |open to )?india|pan[- ]india|indian (college|universit)", "India-only?"),
    (r"(?i)\b(in[- ]person|on[- ]?site|offline (round|final|hackathon)|grand finale at|venue:)", "in-person part?"),
    (r"(?i)team(s)? of (2|3|4)\b|minimum (of )?(2|3|4) (team )?members|(2|3|4)\s*[-–]\s*\d members", "team-required?"),
    (r"(?i)\bKYC\b", "KYC"),
    (r"(?i)paid (out )?in [A-Z][A-Za-z]{1,8} tokens?|in (native |project )?tokens\b", "token payout?"),
    (r"(?i)\b(women|female)[- ]only\b", "women-only"),
    (r"(?i)(open|only) to (residents|citizens) of [A-Z][\w ]{2,30}", "country-restricted?"),
]


def flags(*parts):
    s = " ".join(p for p in parts if p)
    return [f for rx, f in _FLAGS if re.search(rx, s)]


def notes_join(*bits):
    return "; ".join(b for b in bits if b)


# ---------- DoraHacks ----------
@safe
def dorahacks():
    base = "https://dorahacks.io/api/v1/hub/hackathons"
    rows = []
    for st in ("ongoing", "upcoming"):
        page = 1
        while page <= 5:
            d = get_json(f"{base}?page={page}&page_size=100&status={st}&sort_by=timeline_end")
            rows += d.get("results") or []
            if not d.get("next"):
                break
            page += 1
    seen, keep = set(), []
    for x in rows:
        if x["id"] in seen:
            continue
        seen.add(x["id"])
        end = to_date(x.get("timeline_end"))
        if x.get("venue_form") != "Virtual" or not _open(end) or not (x.get("bonus_price") or 0) > 0:
            continue
        keep.append(x)

    def detail(x):
        return _try(get_json, f"{base}/{x.get('uname') or x['id']}") or {}

    out = []
    for x, dd in zip(keep, _pmap(detail, keep)):
        desc = (dd.get("description") or "") + " " + json.dumps(dd.get("tracks") or [])
        tok = (x.get("bonus_token") or "USD").upper()
        cash = x["bonus_price"] * FX.get(tok, 0) if tok in FX else None
        n = x.get("buidls_count") or 0
        hackers = x.get("hackers_count")
        out.append(lead("dorahacks", x["title"], f"https://dorahacks.io/hackathon/{x.get('uname') or x['id']}/",
                        deadline=x.get("timeline_end"), cash_usd=cash, entrants=n or hackers, kind="hackathon",
                        online=True,
                        notes=notes_join(f"{hackers} hackers registered, {n} BUIDLs" if hackers is not None else "",
                                         None if tok in FX else f"prize in {tok}",
                                         "prize stated in USD; check payout currency",
                                         *flags(desc))))
    return out


# ---------- lablab ----------
@safe
def lablab():
    s = http("https://lablab.build/ai-hackathons")
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', s, re.S)
    p = "".join(json.loads('"' + c + '"') for c in chunks)
    dec, evs = json.JSONDecoder(), {}
    for m in re.finditer(r'\{"id":"[a-z0-9]{20,30}","name":', p):
        try:
            o, _ = dec.raw_decode(p, m.start())
        except ValueError:
            continue
        if o.get("slug") and "endAt" in o and o.get("type") in ("HACKATHON", None):
            evs[o["slug"]] = o
    keep = [o for o in evs.values() if o.get("endAt") and _open(o["endAt"]) and o.get("eventType") != "OFFLINE"]

    def prize(o):
        h = _try(http, f"https://lablab.build/ai-hackathons/{o['slug']}") or ""
        h = h.replace("\\u003c", "<").replace("\\u003e", ">").replace('\\"', '"')
        t = text(re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)) + " " + text(h)
        return cash_from_text(t), t

    out = []
    for o, (cash, t) in zip(keep, _pmap(prize, keep)):
        etype = o.get("eventType")
        out.append(lead("lablab", o["name"], f"https://lablab.build/ai-hackathons/{o['slug']}", deadline=o["endAt"],
                        cash_usd=cash, entrants=(o.get("_count") or {}).get("participants"), kind="hackathon",
                        online=True,
                        notes=notes_join("cash est. from page text (lablab mixes cash and credits)",
                                         "hybrid: online build + on-site final" if etype == "HYBRID" else "",
                                         "registrants (lablab counts are large; submissions far fewer)",
                                         *flags((o.get("description") or "")))))
    return out


# ---------- Devfolio ----------
@safe
def devfolio():
    hits = []
    for t in ("application_open", "upcoming"):
        d = get_json("https://api.devfolio.co/api/search/hackathons", data={"type": t, "from": 0, "size": 100},
                     method="POST")
        hits += [h["_source"] for h in d.get("hits", {}).get("hits", [])]
    out, seen = [], set()
    for s in hits:
        if s["slug"] in seen or not s.get("is_online"):
            continue
        seen.add(s["slug"])
        hs = s.get("hackathon_setting") or {}
        deadline = s.get("ends_at")
        if not _open(deadline):
            continue
        ptxt = " ".join(f"{p.get('name', '')} {p.get('desc', '')}" for p in s.get("prizes") or [])
        cash = cash_from_text(ptxt + " " + (s.get("desc") or ""), default_cur="INR")
        has_cash = bool(cash) or re.search(r"(?i)\bcash\b", ptxt)
        out.append(lead("devfolio", s["name"], f"https://{hs.get('subdomain') or s['slug']}.devfolio.co/",
                        deadline=deadline, cash_usd=cash, entrants=s.get("projects_submitted") or s.get("participants_count"),
                        kind="hackathon", online=True,
                        notes=notes_join(f"apply by {to_date(hs.get('reg_ends_at'))}",
                                         f"{s.get('participants_count')} registered",
                                         "cash est." if cash else ("cash mentioned, amount not listed" if has_cash else "no cash amount listed"),
                                         "Devfolio is mostly Indian student hackathons",
                                         *flags(s.get("desc") or "", s.get("tagline") or ""))))
    return out


# ---------- TAIKAI ----------
_TK_Q = ("{challenges(page:%d,where:{isClosed:{equals:false}}){name slug organization{slug name} prize "
         "prizeCurrency{name} participantsCount projectsCount allowedParticipants shortDescription "
         "steps{name startDate}}}")
_TK_END = re.compile(r"(?i)submission|evaluation|judging|voting|pitch|results|demo day|jury|award|final")


@safe
def taikai():
    out = []
    for page in range(0, 5):
        d = get_json("https://api.taikai.network/api/graphql", data={"query": _TK_Q % page},
                     headers={"Origin": "https://taikai.network"})
        cs = (d.get("data") or {}).get("challenges") or []
        for c in cs:
            steps = sorted((s for s in c.get("steps") or [] if s.get("startDate")), key=lambda s: s["startDate"])
            dl = next((s["startDate"] for s in steps if _TK_END.search(s["name"] or "")), None)
            dl = dl or (steps[-1]["startDate"] if steps else None)
            if not _open(dl) or not (c.get("prize") or 0) > 0:
                continue
            cur = ((c.get("prizeCurrency") or {}).get("name") or "EUR").upper()
            cash = round(c["prize"] * FX[cur]) if cur in FX else None
            desc = c.get("shortDescription") or ""
            out.append(lead("taikai", c["name"],
                            f"https://taikai.network/{c['organization']['slug']}/hackathons/{c['slug']}",
                            deadline=dl, cash_usd=cash, entrants=c.get("projectsCount") or c.get("participantsCount"),
                            kind="hackathon", online=None,
                            notes=notes_join(f"{c.get('participantsCount')} participants, {c.get('projectsCount')} projects",
                                             None if cur in FX else f"prize in {cur}",
                                             "invite/selected participants only" if c.get("allowedParticipants") == "SELECTED" else "",
                                             "check online vs in-person (TAIKAI hosts many local events)",
                                             *flags(desc))))
        if len(cs) < 20:
            break
    return out


# ---------- HackerEarth ----------
@safe
def hackerearth():
    d = get_json("https://www.hackerearth.com/chrome-extension/events/")
    out = []
    for x in d.get("response") or []:
        end = x.get("end_utc_tz") or x.get("end_tz")
        if not _open(end):
            continue
        h = _try(http, x["url"]) or ""
        t = text(re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S))
        cash = cash_from_text(t, default_cur="INR")
        out.append(lead("hackerearth", x["title"], x["url"], deadline=end, cash_usd=cash, entrants=None,
                        kind="hackathon", online=True,
                        notes=notes_join(x.get("challenge_type"), "college-only" if x.get("college") else "",
                                         "cash est. from page" if cash else "no cash amount found", *flags(t))))
    return out


# ---------- Unstop ----------
def _unstop_cash(prizes):
    tot = 0
    for p in prizes or []:
        c = p.get("cash") or 0
        if not c or (p.get("prize_type") not in (None, "cash")):
            continue
        cur = (p.get("currencyCode") or p.get("currency") or "").lower()
        rate = FX["INR"] if ("rupee" in cur or "inr" in cur or not cur) else FX.get(cur.upper().replace("FA-", ""), 1)
        tot += c * rate
    return round(tot) or None


@safe
def unstop():
    out, seen = [], set()
    for opp in ("hackathons", "competitions"):
        for page in range(1, 8):
            d = get_json("https://unstop.com/api/public/opportunity/search-result"
                         f"?opportunity={opp}&per_page=100&oppstatus=open&region=online&page={page}")["data"]
            for x in d.get("data") or []:
                if x["id"] in seen:
                    continue
                seen.add(x["id"])
                rr = x.get("regnRequirements") or {}
                dl = rr.get("end_regn_dt") or x.get("end_date")
                cash = _unstop_cash(x.get("prizes"))
                if not cash or cash < 50 or not _open(dl) or x.get("region") == "offline":
                    continue
                try:
                    el = json.loads(rr.get("eligibility") or "{}")
                except ValueError:
                    el = {}
                sector = el.get("sector") or []
                fl = []
                if sector and set(sector) <= {"students", "school"}:
                    fl.append("students only")
                if x.get("isPaid"):
                    fl.append("entry fee")
                tmin = rr.get("min_team_size") or 1
                if tmin > 1:
                    fl.append(f"team {tmin}+ required")
                cty = (((x.get("address_with_country_logo") or {}).get("country") or {}).get("name"))
                out.append(lead("unstop", x["title"], x.get("seo_url") or f"https://unstop.com/{x['public_url']}",
                                deadline=dl, cash_usd=cash, entrants=x.get("registerCount"),
                                kind="hackathon" if x.get("type") == "hackathons" else "other", online=True,
                                notes=notes_join(f"{x.get('subtype')}", f"reg closes; event ends {to_date(x.get('end_date'))}",
                                                 f"organiser in {cty}" if cty else "", "Unstop is India-centric, check eligibility",
                                                 *fl, *flags(text(x.get("details") or "")))))
            if page >= (d.get("last_page") or 1):
                break
    return out


# ---------- Hack2skill ----------
@safe
def hack2skill():
    base = "https://hack2skill.com/api/v1"
    evs = []
    for page in range(1, 8):
        d = get_json(f"{base}/innovator/public/event/public-list?page={page}&limit=10")
        L = d.get("data") or []
        evs += L
        if not L or page >= (d.get("pages") or 1) or all(not _open(x.get("submissionEnd")) for x in L):
            break
    keep = [x for x in evs if x.get("mode") in ("VIRTUAL", "HYBRID") and _open(x.get("submissionEnd") or x.get("registrationEnd"))]

    def detail(x):
        return (_try(get_json, f"{base}/event/{x['eventUrl']}/event-details") or {}).get("data") or {}

    out = []
    for x, dd in zip(keep, _pmap(detail, keep)):
        blob = json.dumps(dd.get("sections") or dd, ensure_ascii=False)
        t = text(re.sub(r"<style.*?</style>", " ", blob.replace("\\n", " "), flags=re.S))
        cash = cash_from_text(t, default_cur="INR")
        reg = ((dd.get("tags") or {}).get("registrations") or {}).get("value")
        out.append(lead("hack2skill", x["title"], f"https://hack2skill.com/event/{x['eventUrl']}/",
                        deadline=x.get("submissionEnd") or x.get("registrationEnd"), cash_usd=cash, entrants=reg,
                        kind="hackathon", online=True,
                        notes=notes_join(x.get("mode"), x.get("participation"), x.get("ticket") if x.get("ticket") != "FREE" else "",
                                         "cash est. from page text" if cash else "no cash amount found",
                                         "Hack2skill events are mostly India-only", *flags(t))))
    return out


# ---------- ETHGlobal ----------
@safe
def ethglobal():
    s = http("https://ethglobal.com/events")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    t = re.sub(r"\s*<[^>]+>\s*", "|", t)
    t = re.sub(r"(\s*\|\s*)+", "|", text(t.replace("|", " | ")))
    up = t.split("|Upcoming|", 1)[-1].split("|Past|", 1)[0]
    slugs = re.findall(r'href="(/events/[a-z0-9-]+)"', s)
    out = []
    # entries look like: Month|d1|d2|Dow|—|Dow|Name|City|, |Country|Type|Apply ...
    for m in re.finditer(r"([A-Z][a-z]+)\|(\d{1,2})\|(\d{1,2})\|[A-Z][a-z]{2}\|—\|[A-Z][a-z]{2}\|([^|]+)\|([^|]+)\|", up):
        name, where = m.group(4), m.group(5)
        tail = up[m.end():m.end() + 80]
        if "Hackathon" not in tail.split("|Apply", 1)[0] or not re.search(r"(?i)online|async", name + where + tail):
            continue
        slug = next((u for u in slugs if name.lower().split()[-1] in u), "/events")
        dl = _try(lambda: dt.datetime.strptime(f"{m.group(1)} {m.group(3)} {TODAY.year}", "%B %d %Y").date())
        if dl and dl < TODAY - dt.timedelta(days=60):
            dl = dl.replace(year=TODAY.year + 1)
        out.append(lead("ethglobal", name, "https://ethglobal.com" + slug, deadline=dl and dl.isoformat(), cash_usd=None,
                        kind="hackathon", online=True, notes=f"{m.group(1)} {m.group(2)}-{m.group(3)}; sponsor prizes, check page"))
    return out


# ---------- HackQuest ----------
_HQ_Q = ("query L($page: Int, $limit: Int, $orderBy: [HackathonOrderByWithRelationInput!]){ listHackathons(page:$page,"
         "limit:$limit,orderBy:$orderBy){ total data { id name alias participants projectCount totalRewards "
         "timeline { submissionClose registrationClose } info { mode host levelTag } rewards { name } } } }")


@safe
def hackquest():
    d = get_json("https://api.hackquest.io/graphql", data={"query": _HQ_Q, "variables": {
        "page": 1, "limit": 60, "orderBy": [{"createdAt": "desc"}]}}, headers={"Origin": "https://www.hackquest.io"})
    out = []
    for x in d["data"]["listHackathons"]["data"]:
        tl = x.get("timeline") or {}
        dl = tl.get("submissionClose")
        mode = (x.get("info") or {}).get("mode")
        tot = float(x.get("totalRewards") or 0)
        if not _open(dl) or mode not in ("ONLINE", "HYBRID") or not tot > 0:
            continue
        rn = " / ".join(r["name"] for r in x.get("rewards") or [])
        out.append(lead("hackquest", x["name"], f"https://www.hackquest.io/hackathons/{x['alias']}", deadline=dl,
                        cash_usd=round(tot), entrants=x.get("projectCount") or x.get("participants"),
                        kind="hackathon", online=True,
                        notes=notes_join(f"{x.get('participants')} registered, {x.get('projectCount')} projects",
                                         f"reg closes {to_date(tl.get('registrationClose'))}", mode,
                                         f"tracks: {rn}"[:120] if rn else "",
                                         "total may include grants/tokens" if re.search(r"(?i)grant|token", rn) else "")))
    return out


# ---------- Akindo (WaveHacks + hackathons) ----------
@safe
def akindo():
    out = []
    for page in range(1, 4):
        d = get_json(f"https://api.akindo.io/public/wave-hacks?page={page}")
        items = d.get("items") or []
        for x in items:
            aw = x.get("activeWave") or {}
            if not aw.get("startedAt") or aw.get("completedAt"):
                continue
            end = dt.datetime.fromisoformat(aw["startedAt"][:19]) + dt.timedelta(days=x.get("buildingDays") or 0)
            if not _open(end.date().isoformat()):
                continue
            den = ((x.get("grantDenomination") or {}).get("name") or "").upper()
            amt = float(aw.get("grantAmount") or 0)
            cash = round(amt * FX[den]) if den in FX and amt else None
            out.append(lead("akindo", f"[WaveHack wave {aw.get('waveCount')}] {x['title']}",
                            f"https://app.akindo.io/wave-hacks/{x['id']}", deadline=end.date().isoformat(),
                            cash_usd=cash, entrants=x.get("builderCount"), kind="hackathon", online=True,
                            notes=notes_join(f"wave grant {amt:,.0f} {den} split among selected builds",
                                             f"{x.get('builderCount')} builders across all waves",
                                             "recurring waves; next wave follows" ,
                                             "title mentions project tokens" if re.search(r"(?i)\bin [A-Z]{3,6}\b", x["title"]) and not cash else "")))
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
    d = get_json("https://api.akindo.io/public/hackathons?page=1")
    for x in d.get("items") or []:
        if not _open(x.get("expiredAt")):
            continue
        amt = float(x.get("rewardAmount") or 0)
        out.append(lead("akindo", x["title"], f"https://app.akindo.io/hackathons/{x['id']}", deadline=x.get("expiredAt"),
                        cash_usd=round(amt) or None, entrants=x.get("builderCount"), kind="hackathon", online=None,
                        notes="Akindo hackathon; reward currency/venue: check page"))
    return out


# ---------- Encode Club ----------
_ENC = "https://bwrubpasqybdchurwjhs.supabase.co/rest/v1"


def _encode_key():
    h = http("https://www.encodeclub.com/")
    js = re.search(r'src="(/assets/index-[^"]+\.js)"', h).group(1)
    b = http("https://www.encodeclub.com" + js)
    return re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}", b).group(0)


@safe
def encode():
    k = _encode_key()
    hd = {"apikey": k, "Authorization": "Bearer " + k}
    progs = get_json(f"{_ENC}/Encode%20Programmes?select=id,slug,type,published,location,end_date,"
                     "project_creation_deadline,total_registrations,checkpoints,%22programme%20name%22,tagline"
                     "&type=eq.Hackathon&published=is.true", headers=hd)
    keep = []
    for p in progs:
        cps = [c.get("deadline") for c in p.get("checkpoints") or [] if c.get("submission_required")]
        dl = max(cps) if cps else (p.get("project_creation_deadline") or p.get("end_date"))
        if _open(dl) and (p.get("location") or "").lower() != "in-person":
            keep.append((p, dl))

    def extra(pd):
        p = pd[0]
        ch = _try(get_json, f"{_ENC}/rpc/get_programme_challenges_details", data={"p_programme_id": p["id"]},
                  headers=hd, method="POST") or []
        cash = sum((pr.get("amount") or 0) for c in ch for pr in c.get("prizes") or [])
        projs = _try(get_json, f"{_ENC}/projects?select=id&programme_id=eq.{p['id']}", headers=hd)
        return cash, ch, (len(projs) if isinstance(projs, list) else None)

    out = []
    for (p, dl), (cash, ch, nproj) in zip(keep, _pmap(extra, keep)):
        out.append(lead("encode", p["programme name"], f"https://www.encodeclub.com/programmes/{p['slug']}",
                        deadline=dl, cash_usd=cash or None, entrants=nproj or p.get("total_registrations"),
                        kind="hackathon", online=True,
                        notes=notes_join(f"{p.get('total_registrations')} registered, {nproj} projects created",
                                         f"{len(ch)} sponsor challenges" if ch else "",
                                         "" if cash else "prizes not yet listed in API, check page")))
    return out


# ---------- Devnovate ----------
def _dmy(s):
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else to_date(s)


@safe
def devnovate():
    out = []
    for x in get_json("https://devnovate.co/api/v1/events"):
        if x.get("status") != "ONLINE" or x.get("openStatus") != "OPEN":
            continue
        dl = _dmy(x.get("endDate")) or to_date(x.get("registrationDeadline"))
        if not _open(dl):
            continue
        pp = x.get("prizePool") or ""
        cash = cash_from_text(pp, default_cur="INR")
        if not cash:
            continue
        out.append(lead("devnovate", x["name"], f"https://devnovate.co/event/{x['hackathon']}", deadline=dl,
                        cash_usd=cash, entrants=x.get("numberOfRegistrations"), kind="hackathon", online=True,
                        notes=notes_join(f"prize: {pp[:80]}", f"reg by {to_date(x.get('registrationDeadline'))}",
                                         f"team {x.get('teamSize')}" if x.get("teamSize") else "", "Indian platform",
                                         *flags(x.get("subtitle") or ""))))
    return out


# ---------- MLH ----------
@safe
def mlh():
    yr = TODAY.year + (1 if TODAY.month >= 7 else 0)
    s = http(f"https://mlh.io/seasons/{yr}/events")
    i = s.find('"upcomingEvents":')
    evs, _ = json.JSONDecoder().raw_decode(s, i + len('"upcomingEvents":'))
    out = []
    for e in evs:
        if e.get("formatType") != "digital" or not _open(e.get("endsAt")):
            continue
        url = e.get("websiteUrl") or ("https://www.mlh.com" + (e.get("url") or ""))
        out.append(lead("mlh", e["name"], url, deadline=e.get("endsAt"), cash_usd=None, entrants=None,
                        kind="hackathon", online=True,
                        notes="MLH digital weekend hackathon: student-focused, prizes mostly sponsor swag; check page"))
    return out


SOURCES = [dorahacks, lablab, devfolio, taikai, hackerearth, unstop, hack2skill, ethglobal, hackquest, akindo,
           encode, devnovate, mlh]

if __name__ == "__main__":
    selftest(SOURCES)
