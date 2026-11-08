"""Sweep every platform fetcher in tools/sources/, keep real-cash contests closing soon, sort by deadline.

Usage:  python -I tools/sweep.py [--days 45] [--min-cash 500] [--keep-unknown] [--only devpost,bounties]
Writes leads/sweep-<date>.md (human list) and leads/sweep-<date>.json (all raw leads).

Each module in tools/sources/ exposes SOURCES (zero-arg functions returning sources.common.lead dicts). A broken
platform logs to stderr and returns nothing; it never kills the sweep. Leads not seen in an earlier sweep are
marked NEW (leads/sweep-seen.json). Anything already in TRACKER.md is marked 'tracked'.
"""
import argparse, datetime as dt, importlib, json, pkgutil, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))  # python -I does not add the script dir


def load_sources(only=None):
    import sources
    fns = []
    for m in pkgutil.iter_modules(sources.__path__):
        if m.name == "common" or (only and m.name not in only):
            continue
        try:
            mod = importlib.import_module(f"sources.{m.name}")
            fns += [(m.name, f) for f in getattr(mod, "SOURCES", [])]
        except Exception as e:
            print(f"[{m.name}] import failed: {type(e).__name__}: {e}", file=sys.stderr)
    return fns


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45, help="keep deadlines within this many days (plus undated bounties)")
    ap.add_argument("--min-cash", type=float, default=500)
    ap.add_argument("--keep-unknown", action="store_true", help="also list leads whose cash is unknown")
    ap.add_argument("--include-offline", action="store_true")
    ap.add_argument("--only", default="", help="comma list of module names in tools/sources")
    a = ap.parse_args()

    today = dt.date.today()
    horizon = (today + dt.timedelta(days=a.days)).isoformat()
    fns = load_sources(set(filter(None, a.only.split(","))) or None)
    print(f"running {len(fns)} fetchers ...", file=sys.stderr)

    leads, stats, t0 = [], {}, time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(f): (mod, f.__name__) for mod, f in fns}
        for fut in as_completed(futs):
            mod, name = futs[fut]
            try:
                rows = fut.result() or []
            except Exception as e:
                print(f"[{mod}.{name}] crashed: {e}", file=sys.stderr)
                rows = []
            stats[f"{mod}.{name}"] = len(rows)
            leads += rows

    # dedupe: same URL, or same normalised title + deadline
    seen_keys, uniq = set(), []
    for r in leads:
        k1, k2 = (r.get("url") or "").rstrip("/").lower(), norm(r.get("title")) + (r.get("deadline") or "")
        if k1 in seen_keys or k2 in seen_keys:
            continue
        seen_keys |= {k1, k2}
        uniq.append(r)

    tracker = (ROOT / "TRACKER.md").read_text(encoding="utf-8").lower() if (ROOT / "TRACKER.md").exists() else ""
    tracker_n = norm(tracker)
    seen_path = ROOT / "leads" / "sweep-seen.json"
    seen = set(json.loads(seen_path.read_text(encoding="utf-8"))) if seen_path.exists() else set()

    keep = []
    for r in uniq:
        d, cash = r.get("deadline"), r.get("cash_usd")
        if d and (d < today.isoformat() or d > horizon):
            continue
        if not d and r.get("kind") not in ("bounty",):  # undated: only open bounties make sense
            continue
        if cash is None and not a.keep_unknown:
            continue
        if cash is not None and cash < a.min_cash:
            continue
        if r.get("online") is False and not a.include_offline:
            continue
        r["new"] = r["url"] not in seen
        url_l = (r.get("url") or "").lower().rstrip("/")
        r["tracked"] = bool(url_l and url_l in tracker) or (len(norm(r["title"])) > 8 and norm(r["title"])[:10] in tracker_n)
        n = r.get("entrants")
        r["per_entrant"] = round(cash / max(n, 1)) if (cash and n is not None) else None
        keep.append(r)

    keep.sort(key=lambda r: (r.get("deadline") or "9999", -(r.get("cash_usd") or 0)))

    out = ROOT / "leads"
    out.mkdir(exist_ok=True)
    (out / f"sweep-{today}.json").write_text(json.dumps(uniq, indent=1, ensure_ascii=False), encoding="utf-8")
    seen_path.write_text(json.dumps(sorted(seen | {r["url"] for r in uniq if r.get("url")})), encoding="utf-8")

    lines = [f"# Sweep {today}",
             f"{len(uniq)} unique leads from {len(fns)} fetchers in {time.time() - t0:.0f}s; "
             f"{len(keep)} with cash >= ${a.min_cash:,.0f} closing by {horizon} (online only).",
             "Unvetted: cash is the platform's figure (Devpost headlines include credits). Vet each with the README checklist.",
             "", "| | Closes | Cash (USD) | Entrants | $/entrant | Kind | Lead | Source | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in keep:
        tag = "tracked" if r["tracked"] else ("NEW" if r["new"] else "")
        cash = f"{r['cash_usd']:,.0f}" if r.get("cash_usd") is not None else "?"
        ent = r["entrants"] if r.get("entrants") is not None else "?"
        pe = f"{r['per_entrant']:,}" if r.get("per_entrant") is not None else "?"
        title = (r["title"] or "").replace("|", "/")
        notes = (r.get("notes") or "").replace("|", "/")[:120]
        lines.append(f"| {tag} | {r.get('deadline') or 'open'} | {cash} | {ent} | {pe} | {r['kind']} | "
                     f"[{title}]({r['url']}) | {r['source']} | {notes} |")
    lines += ["", "## Fetcher counts", ""] + [f"- {k}: {v}" for k, v in sorted(stats.items())]
    (out / f"sweep-{today}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:80]))
    print(f"\nwrote leads/sweep-{today}.md ({len(keep)} rows)", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
