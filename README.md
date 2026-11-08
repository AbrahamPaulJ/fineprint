# Fineprint

Is that contest's prize real? Fineprint finds cash-prize contests closing soon, then reads each one's rules with open-weight
models on [Otari](https://otari.ai) and answers GO, MAYBE or NO, with quotes from the rules.

A listing's headline number is often not cash. It can be project tokens, credits, a grant that depends on user numbers, or a
student-only prize. Fineprint reads the fine print so you don't have to.

    python -I vet.py check https://dev.to/challenges/presage-2026-11-04

## How it works

| Stage | Model role | Otari feature |
|---|---|---|
| Read the rules page and any linked rules | none | Direct web fetch |
| Extract prizes, eligibility and deadline | Reader: DeepSeek V4.1 Flash | Prompt-injection guardrail; a flagged page reaches no model |
| Total the real cash, convert the deadline | Calculator: GLM 5.3 Flash | Code execution in the sandbox, cross-checked locally |
| Is the sponsor real? Have we seen it before? | Investigator: MiniMax M3 | Web search, plus MCP over our own lead history |
| Verdict, with quotes checked against the page | Judge: GLM 5.2 | Open-weight model in its own role |

A role can be a routing policy with a fallback (`FINEPRINT_MODEL_<ROLE>`). With `--user`, spend is billed to that user through a
service key, under their own budget and the key's ceiling. A budget stop ends a run cleanly and says why.

Hosted Otari has no guardrail service of its own, so `vet/mcp_leads.py` is ours: a rule-based check for text aimed at AI
readers, passed by URL on each request, sharing a server with the MCP tool.

## Setup

Stdlib only, Python 3.11+. Copy `.env.example` to `.env` and add your key.

Otari connects from its own network, so the service needs a public https URL:

    python -I vet/mcp_leads.py --port 8765
    tailscale funnel --bg 8765

## Commands

    python -I tools/sweep.py --days 45 --min-cash 500     # find contests closing soon
    python -I vet.py check URL [--user NAME]              # vet one contest
    python -I vet.py batch leads/sweep-DATE.json --top 10 # vet the soonest leads
    python -I vet.py eval                                 # compare with data/vetted.json
    python -I tests/test_pipeline.py                      # tests, no network or key

## Accuracy

`data/vetted.json` holds 16 contests with known answers. Those answers came from AI agents (Claude Code) reading each contest's
rules page on Nov 8, 2026, not from a person, so treat them as a check rather than ground truth.

Result on Nov 8, 2026: agreed on 15 of 16, for $0.73 in total, about 5 cents a contest. Read that number with these in mind:

- It is not a held-out score. I fixed bugs on these same contests while building, so expect worse on new ones.
- The miss: Aevum Builder Challenge answered GO where the label says MAYBE. It didn't weigh the whitelist and eligibility checks.
- Two of the matches had weak reasoning. SLAY HACKS reached NO by guessing "students only" from the organiser's name, because it never
  read the separate eligibility page. Midnight WaveHack reached NO correctly on its pro-rata grants, but also wrongly called USDT a token.

## Limits

- The guardrail is a set of rules, not a trained model, so a cleverly worded injection can get past it.
- It judges whether a prize is real, not how strong the competition is.
- Some sites block the gateway's fetcher; Fineprint then reads them directly or through their public API.

Built for Mozilla's Developers Wilding hackathon, with heavy use of AI coding tools (Claude Code).
