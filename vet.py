"""Fineprint: is this contest's prize real?

  python -I vet.py check URL [--user NAME]     vet one contest and write reports/<slug>.md and .json
  python -I vet.py batch LEADS.json --top N    vet the soonest leads from a sweep
  python -I vet.py eval                        compare verdicts with data/vetted.json, a hand-checked set
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)  # python -I does not add the script's folder

from vet.gateway import Gateway, load_env  # noqa: E402
from vet.pipeline import Pipeline  # noqa: E402


def slug(url):
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"^https?://(www\.)?", "", url.lower())).strip("-")[:80]


def render(r):
    lines = [f"# {r.get('verdict') or 'STOPPED'}: {(r.get('facts') or {}).get('title') or r['url']}", "", r["url"], "",
             r.get("reason") or "", ""]
    c = r.get("computed") or {}
    if c:
        lines += [f"- Real cash: ${float(c.get('real_cash_usd') or 0):,.0f}; other value: ${float(c.get('other_value_usd') or 0):,.0f}",
                  f"- Closes (your time): {c.get('deadline_local') or 'unknown'}"]
    if r.get("effort_hours"):
        lines.append(f"- Effort: about {r['effort_hours']} h")
    for d in r.get("doubts") or []:
        lines.append(f"- Doubt: {d}")
    if r.get("evidence"):
        lines += ["", "## From the rules"] + [f"> {q}" for q in r["evidence"]]
    if r.get("unverified_quotes"):
        lines += ["", "## Quotes not found on the page (ignored)"] + [f"- {q}" for q in r["unverified_quotes"]]
    lines += ["", "## How it ran", "| Stage | Model or source | Fallback | Cost | Tools |", "|---|---|---|---|---|"]
    for s in r.get("steps") or []:
        lines.append(f"| {s['stage']} | {s['model']} | {'yes' if s['fallback'] else ''} | ${s['cost_usd']:.4f} | {', '.join(map(str, s['tools']))} |")
    lines.append(f"\nTotal: ${r.get('cost_usd', 0):.4f}")
    return "\n".join(lines) + "\n"


def save(r):
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    base = os.path.join(ROOT, "reports", slug(r["url"]))
    json.dump(r, open(base + ".json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(base + ".md", "w", encoding="utf-8").write(render(r))
    return base + ".md"


def make_pipeline(a):
    gw = Gateway(user=a.user, end_user_budget=os.environ.get("FINEPRINT_END_USER_BUDGET"))
    return Pipeline(gw, mcp_url=os.environ.get("FINEPRINT_MCP_URL"), mcp_token=os.environ.get("FINEPRINT_MCP_TOKEN"),
                    guard_url=os.environ.get("FINEPRINT_GUARD_URL"),
                    country=a.country, utc_offset=a.utc_offset)


def check(a):
    p = make_pipeline(a)
    print(a.url)
    r = p.run(a.url)
    print(f"\n{r.get('verdict') or 'STOPPED'}: {r.get('reason')}\ncost ${r['cost_usd']:.4f}\nreport: {save(r)}")
    return r


def batch(a):
    rows = json.load(open(a.leads, encoding="utf-8"))
    rows = [x for x in rows if x.get("deadline") and x.get("cash_usd") and x.get("online") is not False and x["deadline"] >= a.after]
    rows.sort(key=lambda x: x["deadline"])
    p = make_pipeline(a)
    done, spent = [], 0.0
    for lead in rows[: a.top]:
        print(f"\n{lead['deadline']} {lead['title'][:70]}\n{lead['url']}")
        r = p.run(lead["url"])
        save(r)
        spent += r["cost_usd"]
        done.append(r)
        print(f"  => {r.get('verdict') or 'STOPPED'}: {r.get('reason')}")
        if r.get("stopped") == "budget":
            print("Budget reached; stopping the batch.")
            break
    print(f"\n{len(done)} vetted, ${spent:.4f}")
    for v in ("GO", "MAYBE", "NO", None):
        print(f"  {v or 'STOPPED'}: {sum(1 for r in done if r.get('verdict') == v)}")


def evaluate(a):
    cases = json.load(open(os.path.join(ROOT, "data", "vetted.json"), encoding="utf-8"))
    if a.top:
        cases = cases[: a.top]
    p = make_pipeline(a)
    hits, spent, misses = 0, 0.0, []
    for c in cases:
        print(f"\n{c['expected']:5} expected  {c['name']}")
        r = p.run(c["url"])
        save(r)
        spent += r["cost_usd"]
        got = r.get("verdict")
        if got == c["expected"]:
            hits += 1
        else:
            misses.append((c["name"], c["expected"], got, r.get("reason")))
        print(f"  => {got}: {r.get('reason')}")
        if r.get("stopped") == "budget":
            print("Budget reached; stopping.")
            break
    print(f"\nAgreed on {hits}/{len(cases)}, ${spent:.4f}")
    for m in misses:
        print(f"  miss: {m[0]}: expected {m[1]}, got {m[2]}: {m[3]}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env(os.path.join(ROOT, ".env"))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=os.environ.get("FINEPRINT_USER"), help="End user the spend is billed and capped against.")
    ap.add_argument("--country", default=os.environ.get("FINEPRINT_COUNTRY", "Australia"))
    ap.add_argument("--utc-offset", type=float, default=float(os.environ.get("FINEPRINT_UTC_OFFSET", 10.5)))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("url")
    b = sub.add_parser("batch")
    b.add_argument("leads")
    b.add_argument("--top", type=int, default=10)
    b.add_argument("--after", default="0000")
    e = sub.add_parser("eval")
    e.add_argument("--top", type=int)
    a = ap.parse_args()
    {"check": check, "batch": batch, "eval": evaluate}[a.cmd](a)


if __name__ == "__main__":
    main()
