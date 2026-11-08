"""Devpost (public JSON API, no key). All open/upcoming online hackathons, with the submission end date."""
import os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/ (python -I skips script dir)
from sources.common import get_json, lead, safe, selftest, text, FX

API = "https://devpost.com/api/hackathons?status[]=open&status[]=upcoming&order_by=deadline&per_page=40&page={}"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def end_date(s):
    """'Oct 26 - Nov 10, 2026' / 'Nov 07 - 13, 2026' / 'Nov 03, 2026 - Feb 01, 2027' -> '2026-11-10'."""
    s = s or ""
    m = re.search(r"(?:([A-Z][a-z]{2})\s+)?(\d{1,2}),\s*(\d{4})\s*$", s)
    if not m:
        return None
    mon = m.group(1)
    if not mon:  # 'Nov 07 - 13, 2026': month comes from the start of the range
        mm = re.match(r"([A-Z][a-z]{2})", s)
        mon = mm.group(1) if mm else None
    if mon not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS.index(mon) + 1:02d}-{int(m.group(2)):02d}"


@safe
def devpost():
    out, page = [], 1
    while page <= 15:
        d = get_json(API.format(page))
        hs = d.get("hackathons", [])
        if not hs:
            break
        for h in hs:
            raw = text(h.get("prize_amount"))
            cur = re.sub(r"[\d,.\s]", "", raw) or "$"
            digits = re.sub(r"[^\d.]", "", raw)
            cash = float(digits) * FX.get(cur, FX.get(cur[-1:], 1)) if digits else None
            loc = ((h.get("displayed_location") or {}).get("location") or "").strip()
            online = loc.lower() in ("online", "")
            themes = ", ".join(t["name"] for t in h.get("themes", []))
            notes = f"Devpost headline prize (often includes credits/swag; check real cash). {themes}"
            if h.get("invite_only"):
                notes = "INVITE-ONLY. " + notes
            if re.search(r"\b(student|university|college|high school)\b", (h["title"] + " " + themes).lower()):
                notes = "maybe student-only. " + notes
            out.append(lead("devpost", h["title"], h["url"], end_date(h.get("submission_period_dates")), cash,
                            h.get("registrations_count"), "hackathon", online, notes))
        if page * 40 >= d["meta"]["total_count"]:
            break
        page += 1
        time.sleep(0.5)
    return out


SOURCES = [devpost]

if __name__ == "__main__":
    selftest(SOURCES)
