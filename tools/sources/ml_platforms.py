"""ML / data-science / algorithm competition platforms.

Working: Kaggle (API token in ~/.kaggle/kaggle.json or KAGGLE_USERNAME/KAGGLE_KEY), Zindi, Codabench, Topcoder (v6),
AIcrowd (HTML), DrivenData (HTML), Tianchi, CrunchDAO, ThinkOnward (Next.js RSC payload), mlcontests.com (GitHub JSON).
Skipped: clist.by (needs a free API key), Hugging Face competitions (no listing with deadlines/prizes),
Signate (API 403), Bitgrit/Solafune (no public listing found), Numerai (ongoing staking tournament).
Every fetcher returns only contests still open (deadline today or later, or no fixed deadline).
"""
import os, re, sys, json, time, base64, datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/ (python -I skips script dir)
from sources.common import http, get_json, lead, safe, selftest, text, to_date, FX, TODAY

TODAY_S = TODAY.isoformat()
# Extra currencies seen on these platforms (FX in common.py covers the majors).
_FX = dict(FX, **{"ZAR": 0.055, "R": 0.055, "KRW": 0.00073, "₩": 0.00073, "SGD": 0.75, "NGN": 0.00065,
                  "KES": 0.0077, "BRL": 0.18, "TWD": 0.031, "HKD": 0.13, "￥": 0.14, "円": 0.0067, "元": 0.14})
_CUR = r"(USD|US\$|EUR|GBP|INR|CNY|RMB|JPY|AUD|CAD|CHF|ZAR|KRW|SGD|NGN|KES|BRL|TWD|HKD|USDC|USDT|\$|€|£|₹|¥|￥|₩|円|元)"
_AMT = re.compile(
    r"(?:(?P<pre>" + _CUR + r")\s?)?(?P<num>\d{1,3}(?:[ ,. ]\d{3})+|\d+(?:\.\d+)?)\s?(?P<k>[kKmM](?![a-zA-Z]))?\s?"
    r"(?P<post>" + _CUR + r"|usd|Usd|dollars)?", re.I)


def cash(s):
    """Largest money amount in a free-text prize string, in USD. None if no amount with a currency."""
    best = None
    for m in _AMT.finditer(s or ""):
        cur = (m.group("pre") or m.group("post") or "")
        if not cur:
            continue
        cur = {"US$": "USD", "DOLLARS": "USD", "RMB": "CNY"}.get(cur.upper(), cur.upper() if cur.isalpha() else cur)
        num = re.sub(r"[ , ]", "", m.group("num"))
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", num):  # 1.000.000 style
            num = num.replace(".", "")
        try:
            v = float(num)
        except ValueError:
            continue
        if m.group("k"):
            v *= 1e3 if m.group("k").lower() == "k" else 1e6
        rate = _FX.get(cur)
        if rate is None or v < 1:
            continue
        v *= rate
        best = v if best is None or v > best else best
    return round(best) if best else None


def is_open(deadline):
    return deadline is None or deadline >= TODAY_S


def unmojibake(s):
    """Codabench double-encodes UTF-8 ('â€“' for '–')."""
    try:
        return s.encode("cp1252").decode("utf-8")
    except Exception:
        return s


def http_retry(url, tries=4, timeout=10):
    """Tianchi (Aliyun) stalls TLS handshakes/reads from AU about 1 time in 3; short timeouts + retries."""
    err = None
    for _ in range(tries):
        try:
            return http(url, headers={"Accept": "application/json"}, timeout=timeout)
        except Exception as e:
            err = e
    raise err


# ---------------------------------------------------------------- Kaggle
def _kaggle_auth():
    user, key = os.environ.get("KAGGLE_USERNAME"), os.environ.get("KAGGLE_KEY")
    p = os.path.join(os.path.expanduser("~"), ".kaggle", "kaggle.json")
    if not key and os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        user, key = d.get("username"), d.get("key")
    if not key:
        return None
    if key.startswith("KGAT_"):  # new-style API token
        return {"Authorization": "Bearer " + key}
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{key}".encode()).decode()}


@safe
def kaggle():
    h = _kaggle_auth()
    if not h:
        print("[kaggle] no ~/.kaggle/kaggle.json or KAGGLE_KEY; skipped", file=sys.stderr)
        return []
    out, seen = [], set()
    for page in range(1, 6):
        rows = get_json(f"https://www.kaggle.com/api/v1/competitions/list?page={page}", headers=h)
        for c in rows:
            if c.get("id") in seen:
                continue
            seen.add(c.get("id"))
            reward = c.get("reward") or ""
            usd = cash(reward)
            if usd is None:  # Knowledge / Swag / Kudos: no cash
                continue
            dl = to_date(c.get("deadline"))
            if not is_open(dl):
                continue
            notes = [c.get("category") or ""]
            if c.get("newEntrantDeadline") and to_date(c["newEntrantDeadline"]) != dl:
                notes.append(f"entry/merger deadline {to_date(c['newEntrantDeadline'])}")
            if c.get("onlyAllowKernelSubmissions"):
                notes.append("notebook-only submissions")
            if c.get("organizationName"):
                notes.append(c["organizationName"])
            out.append(lead("kaggle", c.get("title"), c.get("url") or c.get("ref"), dl, usd, c.get("teamCount"),
                            "ml", True, "; ".join(n for n in notes if n)))
        if len(rows) < 20:
            break
    return out


# ---------------------------------------------------------------- Zindi
def _zindi_elig(cid):
    try:
        d = get_json(f"https://api.zindi.africa/v1/competitions/{cid}")["data"]
        blob = json.dumps(d.get("rules") or "") + json.dumps(d.get("pages") or "")
        blob = blob.encode().decode("unicode_escape", "ignore") if "\\u" in blob else blob
        m = re.search(r"(Who can compete:[^.\"]{0,220}|open to [^.\"]{0,200}(citizens?|residents?|nationals?)[^.\"]{0,120}"
                      r"|(citizens?|residents?|nationals?) of [^.\"]{0,160})", blob, re.I)
        return text(m.group(0))[:180] if m else ""
    except Exception:
        return ""


@safe
def zindi():
    rows = []
    for page in range(0, 3):
        d = get_json(f"https://api.zindi.africa/v1/competitions?page={page}&per_page=50")
        data = d.get("data") or []
        rows += [c for c in data if c.get("open")]
        if len(data) < 50 or not any(c.get("open") for c in data):
            break
    rows = [c for c in rows if is_open(to_date(c.get("end_time")))]
    with ThreadPoolExecutor(8) as ex:
        elig = list(ex.map(lambda c: _zindi_elig(c["id"]), rows))
    out = []
    for c, el in zip(rows, elig):
        usd = cash(c.get("reward")) if c.get("reward_type") == "prize" else None
        notes = [f"reward: {c.get('reward')} ({c.get('reward_type')})"]
        if c.get("is_access_restricted") or c.get("secret_code_required"):
            notes.append("ACCESS RESTRICTED (invite/code/region)")
        if c.get("kind") and c["kind"] != "competition":
            notes.append(c["kind"])
        if el:
            notes.append("eligibility: " + el)
        out.append(lead("zindi", c.get("title"), f"https://zindi.africa/competitions/{c['id']}",
                        c.get("end_time"), usd, c.get("participations_count"), "ml", True, "; ".join(notes)))
    return out


# ---------------------------------------------------------------- Codabench
CODA_H = {"User-Agent": "competition-scout/1.0 (python urllib)", "Accept": "application/json"}  # browser UA gets a tarpit


def _coda_detail(cid):
    d = get_json(f"https://www.codabench.org/api/competitions/{cid}/", headers=CODA_H, timeout=15)
    phases = d.get("phases") or []
    ends = [to_date(p.get("end")) for p in phases if p.get("end")]
    final = [to_date(p.get("end")) for p in phases if p.get("is_final_phase") and p.get("end")]
    live = any(p.get("status") in ("Current", "Next") for p in phases)
    return (final[-1] if final else (max(ends) if ends else None)), live


@safe
def codabench():
    # The server is slow (2-6 s/request) and times out under parallel load, so: few big serial pages, a small
    # detail pool, and a time budget so one slow day cannot blow the sweep's ~3 min limit.
    t0 = time.time()
    cands, pages = [], []
    for p in range(1, 4):  # newest first, 100/page; 3 pages ~ the last 4-5 months of new competitions
        if time.time() - t0 > 40:
            print(f"[codabench] time budget hit after {p - 1} pages", file=sys.stderr)
            break
        try:
            pages.append(get_json(f"https://www.codabench.org/api/competitions/public/?page={p}&page_size=100",
                                  headers=CODA_H, timeout=30))
        except Exception as e:
            print(f"[codabench] page {p} failed: {e}", file=sys.stderr)
    seen = set()
    for d in pages:
        for r in d.get("results") or []:
            rw = unmojibake(r.get("reward") or "")
            if not rw or r["id"] in seen:
                continue
            seen.add(r["id"])
            usd = cash(rw)
            if usd is None and not re.search(r"cash|prize (pool|money)|\bmoney\b", rw, re.I):
                continue  # certificates, bonus points, subscriptions, chocolate
            cands.append((r, rw, usd))

    def one(item):
        r, rw, usd = item
        if time.time() - t0 > 80:
            return r, rw, usd, None, None
        try:
            dl, live = _coda_detail(r["id"])
        except Exception as e:
            print(f"[codabench] detail {r['id']} failed: {e}", file=sys.stderr)
            dl, live = None, None
        return r, rw, usd, dl, live

    out = []
    with ThreadPoolExecutor(3) as ex:
        for r, rw, usd, dl, live in ex.map(one, cands):
            if live is False or not is_open(dl):
                continue
            if live is None and usd is None:
                continue  # unknown dates and no clear cash: not worth a row
            notes = "reward: " + rw[:200] + ("" if usd else " (cash unclear)")
            if live is None:
                notes += "; deadline unknown (detail fetch failed/skipped), may be closed"
            out.append(lead("codabench", unmojibake(r.get("title") or ""), f"https://www.codabench.org/competitions/{r['id']}/",
                            dl, usd, r.get("participants_count"), "ml", True, notes))
    return out


# ---------------------------------------------------------------- Topcoder
@safe
def topcoder():
    out = []
    for page in range(1, 11):
        rows = get_json(f"https://api.topcoder.com/v6/challenges?status=ACTIVE&perPage=100&page={page}")
        for c in rows:
            phases = set(c.get("currentPhaseNames") or [])
            if not phases & {"Registration", "Submission", "Checkpoint Submission", "Open"}:
                continue
            dl = to_date(c.get("submissionEndDate") or c.get("endDate"))
            if not is_open(dl):
                continue
            usd = 0
            for ps in c.get("prizeSets") or []:
                if ps.get("type") == "PLACEMENT":
                    usd += sum(p.get("value") or 0 for p in ps.get("prizes") or [] if (p.get("type") or "USD") == "USD")
            track = c.get("track")
            track = track.get("name") if isinstance(track, dict) else (track or "")
            ctype = c.get("type")
            ctype = ctype.get("name") if isinstance(ctype, dict) else (ctype or "")
            notes = f"{track} {ctype}; phases {','.join(sorted(phases))}"
            if c.get("funChallenge"):
                notes += "; fun challenge (no cash)"
            if c.get("groups"):
                notes += "; restricted group (may be invite-only)"
            out.append(lead("topcoder", c.get("name"), f"https://www.topcoder.com/challenges/{c['id']}", dl,
                            usd or None, c.get("numOfRegistrants"), "ml" if "Data Science" in track else "other",
                            True, notes))
        if len(rows) < 100:
            break
    return out


# ---------------------------------------------------------------- AIcrowd
def _num(s):
    s = (s or "").strip().lower().replace(",", "")
    m = re.match(r"([\d.]+)\s*([km]?)", s)
    if not m:
        return None
    return int(float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2)])


@safe
def aicrowd():
    out, seen = [], set()
    for page in range(1, 6):
        h = http(f"https://www.aicrowd.com/challenges?challenge_filter=active&page={page}")
        cards = h.split('class="card card-challenge"')[1:]
        new = 0
        for card in cards:
            m = re.search(r'card-title"><a href="(/challenges/[^"]+)">(.*?)</a>', card, re.S)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1)); new += 1
            if re.fullmatch(r"(?i)test(\s+\w{1,3})?", text(m.group(2))):  # placeholder cards like 'Test P'
                continue
            dm = re.search(r'title="(\d{4}-\d{2}-\d{2})[^"]*UTC"', card)
            dl = dm.group(1) if dm else None
            badge = text((re.search(r'icon-time.svg" />(.*?)</span>', card, re.S) or [None, ""])[1])
            if not is_open(dl) or not dl:
                continue
            if dl > (TODAY + dt.timedelta(days=400)).isoformat():
                continue  # perpetual benchmarks / post-challenge rounds
            prize_txt = text(" ".join(re.findall(r'prize-item-cash">(.*?)</div>', card, re.S)))
            other = text(" ".join(re.findall(r'prize-item-(?:travel|misc|academic)">(.*?)</div>', card, re.S)))
            teams = re.search(r'title="No\. of teams">.*?/>\s*([\d.,kKmM]+)', card, re.S)
            users = re.search(r'title="No\. of users">.*?/>\s*([\d.,kKmM]+)', card, re.S)
            n = _num(teams.group(1)) if teams else None
            if not n and users:
                n = _num(users.group(1))
            usd = cash(prize_txt)
            notes = "; ".join(x for x in (badge, ("cash: " + prize_txt) if prize_txt else "no cash prize listed", other[:80]) if x)
            out.append(lead("aicrowd", text(m.group(2)), "https://www.aicrowd.com" + m.group(1), dl, usd, n, "ml", True, notes))
        if not cards or not new:
            break
    return out


# ---------------------------------------------------------------- DrivenData
@safe
def drivendata():
    h = http("https://www.drivendata.org/competitions/")
    out = []
    for blk in re.split(r'(?=<div class="[^"]*panel-container )', h)[1:]:
        head = blk[:blk.find(">")]
        if "active-comp" not in head or "prize-comp" not in head:
            continue
        g = lambda k: (re.search(k + r'="([^"]*)"', head) or [None, None])[1]
        dl_raw = g("data-deadline") or ""
        dl = f"{dl_raw[-8:-4]}-{dl_raw[-4:-2]}-{dl_raw[-2:]}" if re.search(r"\d{8}$", dl_raw) else None
        purse = float(g("data-total_prize_purse") or 0)
        joined = g("data-joined")
        a = re.search(r'href="([^"]+)"\s*(?:target="_blank")?\s*title="([^"]+)"', blk) or \
            re.search(r'<a href="([^"]+)"[^>]*>([^<]{5,})</a>', blk)
        if not a or not is_open(dl) or (purse <= 0 and "/benchmarks/" in a.group(1)):
            continue
        url = a.group(1) if a.group(1).startswith("http") else "https://www.drivendata.org" + a.group(1)
        title = text(a.group(2))
        notes = "check eligibility (US-government prize challenges are often US-only)" if "Prize Challenge" in title else ""
        if purse <= 0:
            notes = (notes + "; " if notes else "") + "prize amount not listed"
        out.append(lead("drivendata", title, url, dl, round(purse) or None, int(joined) if joined and joined.isdigit() else None,
                        "ml", True, notes))
    return out


# ---------------------------------------------------------------- Tianchi (Alibaba)
@safe
def tianchi():
    api = ("https://tianchi.aliyun.com/competition/proxy/api/competition/api/race/listBrief"
           "?pageNum={}&pageSize=10&type=1&state=1")  # page size is capped at 10
    first = json.loads(http_retry(api.format(1)))
    total = (first.get("data") or {}).get("total") or 0
    pages = [first]
    if total > 10:
        with ThreadPoolExecutor(4) as ex:
            pages += list(ex.map(lambda p: json.loads(http_retry(api.format(p))), range(2, min(6, (total + 9) // 10 + 1))))
    out, seen = [], set()
    for d in pages:
        lst = (d.get("data") or {}).get("list") or []
        for c in lst:
            if c.get("raceId") in seen:
                continue
            seen.add(c.get("raceId"))
            dl = to_date(c.get("currentSeasonEnd"))
            if not is_open(dl):
                continue
            rate = 1 if c.get("currency") == 1 else _FX["CNY"]
            usd = round((c.get("bonus") or 0) * rate) or None
            notes = "China platform: rules/forms often Chinese-only; prize tax/ID requirements likely"
            if c.get("needStudent"):
                notes = "STUDENTS ONLY; " + notes
            if re.search(r"高校|大学生|在校", (c.get("raceName") or "") + (c.get("brief") or "")[:200]):
                notes = "likely university students only (高校/大学生); " + notes
            title = c.get("raceNameEn") or c.get("raceName")
            out.append(lead("tianchi", title, f"https://tianchi.aliyun.com/competition/entrance/{c['raceId']}", dl, usd,
                            c.get("teamNum"), "ml", True, notes))
    return out


# ---------------------------------------------------------------- CrunchDAO
@safe
def crunchdao():
    out = []
    for c in get_json("https://api.hub.crunchdao.com/v1/competitions"):
        if c.get("status") in ("CLOSED", "SCORING", "ENDED", "FINISHED") or c.get("visibility") not in (None, "PUBLIC"):
            continue
        dl = to_date(c.get("end"))
        if not is_open(dl):
            continue
        st = c.get("statistics") or {}
        notes = f"status {c.get('status')}"
        if c.get("continuous") or not dl:
            notes += "; continuous tournament (payouts over time)"
        if c.get("status") == "PENDING":
            notes += f"; starts {to_date(c.get('start'))}"
        if c.get("prizePoolText"):
            notes += "; " + text(c["prizePoolText"])[:120]
        out.append(lead("crunchdao", text(c.get("displayName") or c.get("name")),
                        f"https://hub.crunchdao.com/competitions/{c['name']}", dl, c.get("prizePoolUsd"),
                        st.get("participants") or st.get("entrants"), "ml", True, notes))
    return out


# ---------------------------------------------------------------- ThinkOnward
@safe
def thinkonward():
    h = http("https://thinkonward.com/app/c/challenges")
    parts = [json.loads('"' + m.group(1) + '"')
             for m in re.finditer(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', h)]
    s = "".join(parts)
    i = s.find('"challenges":[')
    if i < 0:
        raise ValueError("challenges payload not found")
    arr, _ = json.JSONDecoder().raw_decode(s[i + len('"challenges":'):])
    out = []
    for c in arr:
        if c.get("status") == "COMPLETED_STATUS" or c.get("sandboxMode"):
            continue
        dl = to_date(c.get("endDate"))
        if not is_open(dl):
            continue
        p = ((c.get("compensation") or {}).get("prize") or {})
        usd = round((p.get("amount") or 0) * _FX.get(p.get("currencyCode") or "USD", 1)) or None
        out.append(lead("thinkonward", c.get("title"), f"https://thinkonward.com/app/c/challenges/{c['slug']}", dl, usd,
                        (c.get("participationStats") or {}).get("totalTeams"), "ml", True,
                        f"{c.get('status')}; check country eligibility in rules"))
    return out


# ---------------------------------------------------------------- mlcontests.com (aggregator)
COVERED = {"kaggle", "zindi", "codabench", "aicrowd", "drivendata", "crunchdao", "tianchi", "thinkonward", "topcoder"}


@safe
def mlcontests():
    d = get_json("https://raw.githubusercontent.com/mlcontests/mlcontests.github.io/master/competitions.json")
    out = []
    for c in d.get("data") or []:
        dl = to_date(c.get("deadline"))
        if not dl or not is_open(dl):
            continue
        plat = (c.get("platform") or "").strip()
        if plat.lower() in COVERED:
            continue  # fetched directly above
        notes = f"via mlcontests; platform {plat or '?'}; sponsor {c.get('sponsor') or '?'}"
        if c.get("registration-deadline") and to_date(c["registration-deadline"]) != dl:
            notes += f"; register by {to_date(c['registration-deadline'])}"
        if c.get("note"):
            notes += "; " + c["note"]
        url = re.sub(r"[?&]ref=mlcontests$", "", c.get("url") or "")
        out.append(lead("mlcontests", c.get("name"), url, dl, cash(c.get("prize") or ""), None, "ml", True, notes))
    return out


SOURCES = [kaggle, zindi, codabench, topcoder, aicrowd, drivendata, tianchi, crunchdao, thinkonward, mlcontests]

if __name__ == "__main__":
    selftest(SOURCES)
