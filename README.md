# Fineprint

Finds cash-prize contests that close soon. `tools/sweep.py` runs one fetcher per platform (Devpost, DoraHacks, Kaggle,
Zindi, Codabench, Superteam, dev.to and more), keeps online contests with cash closing within `--days`, and sorts by deadline.

    python -I tools/sweep.py --days 45 --min-cash 500

Stdlib only. Writes `leads/sweep-<date>.md` and `.json`.
